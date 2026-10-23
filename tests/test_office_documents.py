"""
קלסר המסמכים והורדת קובץ בצד המשרד - שלב ד'.

הפער שנסגר
-----------
1. לצד המשרד לא הייתה נקודת הורדה כלל. הצד הלקוחי החזיק
   אחת מאז שההעלאות נבנו; הצד המשרדי מעולם לא. בפועל
   עורך הדין ראה "ממתין לבדיקה" ולחץ "אישור" או "דחייה"
   בלי לפתוח את הקובץ - כי לא הייתה דרך.

2. admin.js בדק doc.file, ותשובת התיק מעולם לא נשאה את
   השדה. כלומר גם שם הקובץ לא הוצג.

3. מסמך היה נגיש דרך התיק בלבד. "מה ממתין לבדיקה השבוע"
   חייב לפתוח תיקים אחד אחד.

למה הבדיקות כאן כותבות scan_status ידנית
-----------------------------------------
במצב demo אין מנוע סריקה, ולכן כל קובץ נשאר pending לנצח
ואינו ניתן להורדה. זה מצב מכוון ומתועד ב-scan.py, ואסור
לקוד האפליקציה לסמן clean בלי סורק.

הבדיקה היא לא קוד אפליקציה. היא מזריקה שורה עם clean כדי
לאמת שהנתיב המוגן עובד בפועל - ומיד אחריה מאמתת שהנתיב
החוסם עובד גם הוא.
"""

import pytest
from starlette.testclient import TestClient

from server import storage
from server.app import app
from server.db.pool import cursor

# ה-fixture חי ב-test_firm_isolation ולא ב-conftest. הייבוא
# הוא מה שהופך אותו לזמין כאן, ולכן הוא אינו מיותר.
from .test_firm_isolation import second_firm  # noqa: F401

LOCAL = ("127.0.0.1", 45123)
STAFF_EMAIL = "nahmani@nahmani-bendahan.co.il"
STAFF_PASSWORD = "office2026"

PDF = b"%PDF-1.4\n% test\n"


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


def staff_login(api):
    r = api.post("/api/office/auth/login",
                 json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD})
    assert r.status_code == 200, r.text
    return api.cookies.get("portal_csrf")


@pytest.fixture
def clean_file(temp_case):
    """
    קובץ אמיתי באחסון, עם שורה שמסומנת clean.

    הסימון נעשה כאן ולא על ידי האפליקציה - ראה ההסבר בראש
    הקובץ. בסוף הבדיקה השורה והקובץ נמחקים.
    """
    meta = storage.validate_and_store(
        PDF, firm_id=str(temp_case["firm_id"]),
        case_id=str(temp_case["case_id"]), original_name="raaya.pdf")
    key = meta["storage_key"]

    with cursor(commit=True) as cur:
        cur.execute(
            """insert into document_files
                 (firm_id, document_id, storage_key, original_filename,
                  mime_type, size_bytes, scan_status, is_current)
               values (%s, %s, %s, %s, %s, %s, 'clean', true)
               returning id""",
            (temp_case["firm_id"], temp_case["document_id"], key,
             meta["original_filename"], meta["mime_type"], meta["size_bytes"]),
        )
        file_id = cur.fetchone()["id"]

    yield {"file_id": str(file_id), "document_id": str(temp_case["document_id"]),
           "case_id": str(temp_case["case_id"]), "key": key}

    with cursor(commit=True) as cur:
        cur.execute("delete from document_files where id = %s", (file_id,))
    try:
        storage.delete(key)
    except Exception:
        pass


# ================================================================
#  1. הורדה בצד המשרד
# ================================================================

def test_the_office_can_download_a_clean_file(api, clean_file):
    """
    זו הסיבה שהנקודה נבנתה: בלעדיה הביקורת עיוורת.
    """
    staff_login(api)
    r = api.get("/api/office/documents/%s/files/%s"
                % (clean_file["document_id"], clean_file["file_id"]))

    assert r.status_code == 200, r.text
    assert r.content == PDF, "הקובץ שהוחזר אינו הקובץ שנשמר"
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.headers.get("x-content-type-options") == "nosniff", (
        "בלי nosniff הדפדפן עלול לנחש סוג ולהריץ תוכן שהועלה")


def test_an_unscanned_file_is_not_downloadable(api, temp_case):
    """
    ברירת המחדל היא pending, והיא חוסמת. 409 ולא 403:
    ההרשאה תקינה, הקובץ פשוט טרם נסרק.
    """
    meta = storage.validate_and_store(
        PDF, firm_id=str(temp_case["firm_id"]),
        case_id=str(temp_case["case_id"]), original_name="pending.pdf")
    key = meta["storage_key"]
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into document_files
                 (firm_id, document_id, storage_key, original_filename,
                  mime_type, size_bytes, is_current)
               values (%s, %s, %s, 'pending.pdf', 'application/pdf', %s, true)
               returning id""",
            (temp_case["firm_id"], temp_case["document_id"], key, len(PDF)),
        )
        fid = cur.fetchone()["id"]

    staff_login(api)
    r = api.get("/api/office/documents/%s/files/%s"
                % (temp_case["document_id"], fid))
    assert r.status_code == 409, r.text

    with cursor(commit=True) as cur:
        cur.execute("delete from document_files where id = %s", (fid,))
    try:
        storage.delete(key)
    except Exception:
        pass


def test_downloading_needs_a_staff_session(api, clean_file):
    r = api.get("/api/office/documents/%s/files/%s"
                % (clean_file["document_id"], clean_file["file_id"]))
    assert r.status_code == 401


def test_a_file_of_another_firm_is_not_found(api, clean_file, second_firm):
    """
    404 ולא 403. תשובה שמבדילה ביניהם מאשרת שהמזהה קיים.
    """
    staff_login(api)
    r = api.get("/api/office/documents/%s/files/%s"
                % (second_firm["document_id"], clean_file["file_id"]))
    assert r.status_code == 404, r.text


def test_the_download_is_recorded_in_the_audit_log(api, clean_file):
    """
    מי מהצוות פתח איזה קובץ ומתי - זו בדיוק השאלה שיומן
    ביקורת אמור לענות עליה.
    """
    with cursor() as cur:
        cur.execute("select count(*) as n from audit_log "
                    "where action = 'office.file_downloaded'")
        before = cur.fetchone()["n"]

    staff_login(api)
    api.get("/api/office/documents/%s/files/%s"
            % (clean_file["document_id"], clean_file["file_id"]))

    with cursor() as cur:
        cur.execute("select count(*) as n from audit_log "
                    "where action = 'office.file_downloaded'")
        assert cur.fetchone()["n"] == before + 1


# ================================================================
#  2. הקבצים בתשובת התיק
# ================================================================

def test_the_case_response_carries_the_uploaded_files(api, clean_file):
    """
    admin.js בדק doc.file והשדה מעולם לא הגיע. הבדיקה נועלת
    את המבנה שהתצוגה נשענת עליו.
    """
    staff_login(api)
    data = api.get("/api/office/cases/%s" % clean_file["case_id"]).json()

    doc = [d for d in data["documents"] if d["id"] == clean_file["document_id"]]
    assert doc, "המסמך נעלם מתשובת התיק"
    files = doc[0]["files"]
    assert files, "הקובץ שהועלה אינו מופיע בתשובת התיק"

    f = files[0]
    assert f["name"] == "raaya.pdf"
    assert f["ready"] is True, "קובץ נקי אמור להיות ניתן לפתיחה"
    for key in ["id", "mime", "size", "scan", "at"]:
        assert key in f, "השדה %s נעלם" % key


# ================================================================
#  3. הקלסר
# ================================================================

def test_the_binder_crosses_cases(api):
    """
    זו הסיבה שהקלסר נבנה: מסמך נמצא בלי לדעת באיזה תיק הוא.
    """
    staff_login(api)
    data = api.get("/api/office/documents").json()

    assert data["documents"], "הקלסר ריק - אין נתוני זרע לבדיקה"
    cases = {d["caseId"] for d in data["documents"]}
    assert len(cases) > 1, "הקלסר מציג תיק אחד בלבד"

    for d in data["documents"]:
        assert d["clientName"], "שורה בלי שם לקוח חסרת הקשר"
        assert d["caseNumber"], "שורה בלי מספר תיק חסרת הקשר"


def test_the_binder_counts_the_whole_firm_not_the_page(api):
    """
    המונים חייבים לתאר את המשרד ולא את הדף שהוחזר, אחרת
    השבב יאמר "8 ממתינים" כשיש 40 והרשימה נחתכה.
    """
    staff_login(api)
    page = api.get("/api/office/documents?limit=1").json()

    with cursor() as cur:
        cur.execute("""select status, count(*) as n from case_documents
                        where firm_id = (select firm_id from users
                                          where lower(email) = lower(%s))
                        group by status""", (STAFF_EMAIL,))
        real = {r["status"]: r["n"] for r in cur.fetchall()}

    assert len(page["documents"]) == 1
    assert page["truncated"] is True
    for status, n in real.items():
        assert page["counts"].get(status) == n, (
            "המונה של %s אינו תואם את המסד" % status)


def test_the_binder_filters_by_status(api):
    staff_login(api)
    data = api.get("/api/office/documents?status=approved").json()
    assert {d["status"] for d in data["documents"]} <= {"approved"}


def test_the_binder_never_crosses_firms(api, second_firm):
    """
    הקלסר נוגע בארבע טבלאות. די באחת ששוכחת firm_id.
    """
    staff_login(api)

    with cursor() as cur:
        cur.execute("select case_number from cases where firm_id = %s",
                    (second_firm["firm_id"],))
        theirs = {r["case_number"] for r in cur.fetchall()}

    mine = {d["caseNumber"] for d in api.get("/api/office/documents").json()["documents"]}
    assert not (mine & theirs), "תיקים של משרד אחר דלפו לקלסר"


def test_the_binder_needs_a_staff_session(api):
    assert api.get("/api/office/documents").status_code == 401
