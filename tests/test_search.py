"""
חיפוש רוחבי במשרד.

דורש מסד נתונים חי עם נתוני הזרע.

הפער שנסגר: מסמך, משימה ודרישה היו נגישים דרך התיק בלבד,
ולכן "איפה ה-EMG של ישראל" חייב לפתוח תיקים אחד אחד.

הבדיקות כאן נועלות שלושה דברים: שהחיפוש באמת חוצה מקורות,
שהוא אינו חוצה משרדים, ושאין בו AI ולא תוכן מסמכים.
"""

import pytest
from starlette.testclient import TestClient

from server.app import app
from server.db.pool import cursor

# ה-fixture חי ב-test_firm_isolation ולא ב-conftest. הייבוא
# הוא מה שהופך אותו לזמין כאן, ולכן הוא אינו מיותר.
from .test_firm_isolation import second_firm  # noqa: F401

LOCAL = ("127.0.0.1", 45123)

STAFF_EMAIL = "nahmani@nahmani-bendahan.co.il"
STAFF_PASSWORD = "office2026"


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


def staff_login(api):
    r = api.post("/api/office/auth/login",
                 json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD})
    assert r.status_code == 200, r.text
    return api.cookies.get("portal_csrf")


def find(api, q, kinds=None):
    url = "/api/office/search?q=%s" % q
    if kinds:
        url += "&kinds=" + kinds
    r = api.get(url)
    assert r.status_code == 200, r.text
    return r.json()


# ================================================================
#  1. החיפוש חוצה מקורות
# ================================================================

def test_search_finds_a_document_by_name(api, temp_case):
    """
    זו הסיבה שהנקודה נבנתה: מסמך נמצא בלי לדעת באיזה תיק הוא.
    """
    staff_login(api)
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into case_documents (firm_id, case_id, name, position)
               values (%s, %s, 'בדיקת EMG יד ימין', 90) returning id""",
            (temp_case["firm_id"], temp_case["case_id"]))
        doc_id = cur.fetchone()["id"]

    hits = find(api, "EMG")["results"]
    mine = [h for h in hits if h["id"] == str(doc_id)]
    assert mine, "המסמך לא נמצא בחיפוש רוחבי"

    hit = mine[0]
    assert hit["kind"] == "document"
    assert hit["caseId"] == str(temp_case["case_id"])
    assert hit["tab"] == "docs", "התוצאה חייבת לדעת לאיזו לשונית לקפוץ"
    assert hit["clientName"], "בלי שם הלקוח התוצאה חסרת הקשר"

    with cursor(commit=True) as cur:
        cur.execute("delete from case_documents where id = %s", (doc_id,))


def test_search_covers_all_five_sources(api):
    """
    הקטלוג והתוצאות חייבים להסכים: כל סוג שהקטלוג מכריז עליו
    הוא סוג שאפשר באמת לבקש.
    """
    staff_login(api)
    kinds = [k["kind"] for k in api.get("/api/office/search/kinds").json()["kinds"]]
    assert set(kinds) == {"case", "client", "document", "task", "requirement"}

    for kind in kinds:
        data = find(api, "a", kinds=kind)
        assert "results" in data


def test_kinds_narrows_the_search(api):
    """
    הפרמטר קיים כדי ששכבת השפה הטבעית העתידית תשתמש באותה
    נקודת קצה. אם הוא אינו מסנן באמת, היא תצטרך נתיב משלה.
    """
    staff_login(api)
    only_docs = find(api, "ייפוי", kinds="document")["results"]
    assert only_docs, "אין נתוני זרע לבדיקה הזאת"
    assert {h["kind"] for h in only_docs} == {"document"}


# ================================================================
#  2. בידוד, הרשאות וקלט
# ================================================================

def test_search_never_crosses_firms(api, second_firm):
    """
    החיפוש נוגע בחמש טבלאות. די בטבלה אחת שתשכח את firm_id
    כדי שמשרד אחד יראה נתונים של אחר.

    השמות נשלפים מהמסד ולא מנוחשים, כדי שהבדיקה תמשיך לתפוס
    דליפה גם אם ה-fixture ישנה את הנוסח שלו.
    """
    staff_login(api)
    with cursor() as cur:
        cur.execute("select full_name from clients where firm_id = %s",
                    (second_firm["firm_id"],))
        names = [r["full_name"] for r in cur.fetchall()]
        cur.execute("select case_number from cases where firm_id = %s",
                    (second_firm["firm_id"],))
        names += [r["case_number"] for r in cur.fetchall()]
        cur.execute("select name from case_documents where firm_id = %s",
                    (second_firm["firm_id"],))
        names += [r["name"] for r in cur.fetchall()]
        cur.execute("select title from case_tasks where firm_id = %s",
                    (second_firm["firm_id"],))
        names += [r["title"] for r in cur.fetchall()]

    other_case = str(second_firm["case_id"])
    leaked = []
    for q in names:
        if not q or len(q) < 2:
            continue
        for hit in find(api, q)["results"]:
            if hit["caseId"] == other_case or hit["id"] == other_case:
                leaked.append(q)
    assert not leaked, "נתונים של משרד אחר דלפו לחיפוש: %s" % leaked


def test_search_needs_a_staff_session(api):
    assert api.get("/api/office/search?q=test").status_code == 401
    assert api.get("/api/office/search/kinds").status_code == 401


def test_a_single_character_returns_nothing(api):
    """תו אחד מחזיר כמעט הכול, וזה אינו חיפוש."""
    staff_login(api)
    data = find(api, "a"[:1])
    assert data["results"] == []
    assert data["minLength"] == 2


def test_wildcards_in_the_query_are_escaped(api):
    """
    בלי בריחה, חיפוש "%" היה מחזיר את כל המשרד - כל הלקוחות,
    כל התיקים וכל המסמכים - למי שהקליד תו אחד.
    """
    staff_login(api)
    for bad in ["%", "_", "%%", "\\"]:
        assert find(api, bad + bad)["results"] == [], (
            "התבנית %r לא נוטרלה" % bad)


def test_the_answer_is_capped(api):
    """שאילתה רחבה לא תגרור אלפי שורות אל הדפדפן."""
    staff_login(api)
    data = find(api, "ב")          # תו עברי נפוץ, אך קצר מהמינימום
    assert data["results"] == []
    data = find(api, "בל")
    assert len(data["results"]) <= 30


# ================================================================
#  3. התנאי: אין כאן AI ואין תוכן מסמכים
# ================================================================

def test_search_returns_no_document_content(api):
    """
    שלב א' מחפש בשמות ובכותרות בלבד. תוכן מסמך, טקסט שחולץ
    או metadata רפואי אינם קיימים כאן - הם שכבה נפרדת שתתוכנן
    בנפרד, יחד עם ההשלכות על METADATA_ONLY.
    """
    staff_login(api)
    body = api.get("/api/office/search?q=ייפוי").text
    for forbidden in ["content", "text", "ocr", "extract", "body", "snippet"]:
        assert forbidden not in body.lower(), (
            "השדה %s הופיע בתשובה - שלב א' אינו אמור להחזיר תוכן" % forbidden)


def test_search_does_not_flood_the_audit_log(api):
    """
    חיפוש הוא פעולת קריאה בתדירות גבוהה. רישום שלו היה מציף
    את היומן ומאבד לו את הערך, בדיוק כמו office.case_viewed.
    """
    staff_login(api)
    with cursor() as cur:
        cur.execute("select count(*) as n from audit_log")
        before = cur.fetchone()["n"]

    for _ in range(5):
        find(api, "ייפוי")

    with cursor() as cur:
        cur.execute("select count(*) as n from audit_log")
        after = cur.fetchone()["n"]

    assert after == before, "החיפוש כתב שורות אודיט"
