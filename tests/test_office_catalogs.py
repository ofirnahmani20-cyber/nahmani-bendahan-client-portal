"""
שלב ו' - לקוחות, קטלוגים ותיקים שהושלמו.

שלושת האזורים האחרונים שהציגו "ייבנה בשלב ו'". כולם קריאה
בלבד, ושלוש הבדיקות המרכזיות כאן נועלות בדיוק את זה:

1. תעודת זהות אינה יוצאת מהשרת בשום צורה. היא שמורה
   מוצפנת ומשמשת כמפתח ההתחברות של הלקוח, ואין לה שום
   שימוש במסך רשימה. דליפה שלה היא אירוע פרטיות.

2. אין בשום נקודה כאן כתיבה. אזור שנראה כמו ניהול ואינו
   מנהל הוא פחות מסוכן מאזור שמנהל בלי שהוחלט איך.

3. שכר טרחה מוצהר כחסר ואינו מומצא. feesAvailable הוא
   השדה שמכריח את הממשק לומר זאת במקום למלא מספר.
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


# ================================================================
#  1. לקוחות
# ================================================================

def test_the_client_list_never_returns_a_national_id(api):
    """
    זו הבדיקה החשובה בקובץ. תעודת הזהות היא גם מזהה מוצפן
    וגם מפתח ההתחברות, ואין לה שימוש במסך רשימה.
    """
    staff_login(api)
    body = api.get("/api/office/clients").text.lower()

    for forbidden in ["national", "nationalid", "national_id", "תעודת זהות", "tz"]:
        assert forbidden not in body, (
            "השדה %s הופיע בתשובה - תעודת זהות אינה אמורה לצאת" % forbidden)


def test_the_client_list_carries_case_counts(api):
    """
    "שלושה תיקים" ו"אחד מהם פעיל" הן שתי עובדות שונות.
    איחוד שלהן מסתיר את השנייה.
    """
    staff_login(api)
    clients = api.get("/api/office/clients").json()["clients"]
    assert clients, "אין לקוחות זרע לבדיקה"

    for c in clients:
        assert c["name"], "לקוח בלי שם"
        assert isinstance(c["totalCases"], int)
        assert isinstance(c["activeCases"], int)
        assert c["activeCases"] <= c["totalCases"], (
            "יותר תיקים פעילים מסך התיקים - הספירה שגויה")


def test_the_client_search_narrows(api):
    staff_login(api)
    all_clients = api.get("/api/office/clients").json()["clients"]
    assert len(all_clients) > 1, "צריך יותר מלקוח אחד לבדיקה"

    name = all_clients[0]["name"]
    found = api.get("/api/office/clients?q=%s" % name[:4]).json()["clients"]
    assert found, "החיפוש לא מצא לקוח קיים"
    assert len(found) < len(all_clients) or len(all_clients) == 1


def test_a_one_character_search_returns_everyone(api):
    """
    תו אחד אינו חיפוש. במקום להחזיר כמעט הכול כאילו סוננו,
    הפרמטר פשוט מתעלם - וזה מה שהבדיקה נועלת.
    """
    staff_login(api)
    everyone = api.get("/api/office/clients").json()["clients"]
    one_char = api.get("/api/office/clients?q=א").json()["clients"]
    assert len(one_char) == len(everyone)


def test_the_client_list_never_crosses_firms(api, second_firm):
    staff_login(api)

    with cursor() as cur:
        cur.execute("select full_name from clients where firm_id = %s",
                    (second_firm["firm_id"],))
        theirs = {r["full_name"] for r in cur.fetchall()}

    mine = {c["name"] for c in api.get("/api/office/clients").json()["clients"]}
    assert not (mine & theirs), "לקוחות של משרד אחר דלפו"


def test_the_client_list_needs_a_staff_session(api):
    assert api.get("/api/office/clients").status_code == 401


# ================================================================
#  2. הקטלוגים
# ================================================================

def test_the_catalogs_expose_what_already_drives_the_system(api):
    """
    מסלול השלבים קובע מה הלקוח רואה ב"מפת ההליך", וקטלוג
    המסמכים קובע מה נדרש ממנו. עד כה לא הייתה דרך לראות
    אותם מהממשק בכלל.
    """
    staff_login(api)
    data = api.get("/api/office/catalogs").json()

    assert data["claimTypes"], "אין סוגי תביעה"
    assert data["taskTypes"], "אין סוגי משימה"

    with_stages = [t for t in data["claimTypes"] if t["stages"]]
    assert with_stages, "אף סוג תביעה לא נושא מסלול שלבים"

    for t in data["claimTypes"]:
        assert isinstance(t["cases"], int), "מונה התיקים חסר"
        positions = [s["position"] for s in t["stages"]]
        assert positions == sorted(positions), "השלבים אינם לפי סדר"


def test_the_catalogs_declare_that_editing_is_not_built(api):
    """
    editable=False הוא מה שמכריח את הממשק לומר "קריאה בלבד"
    במקום להציג מסך שנראה כמו ניהול ואינו מנהל.
    """
    staff_login(api)
    assert api.get("/api/office/catalogs").json()["editable"] is False


def test_the_catalogs_never_cross_firms(api, second_firm):
    staff_login(api)

    with cursor() as cur:
        cur.execute("select name from claim_types where firm_id = %s",
                    (second_firm["firm_id"],))
        theirs = {r["name"] for r in cur.fetchall()}

    mine = {t["name"] for t in api.get("/api/office/catalogs").json()["claimTypes"]}
    assert not (mine & theirs), "סוגי תביעה של משרד אחר דלפו"


def test_the_catalogs_need_a_staff_session(api):
    assert api.get("/api/office/catalogs").status_code == 401


# ================================================================
#  3. תיקים שהושלמו
# ================================================================

def test_closed_cases_declare_that_fees_do_not_exist(api):
    """
    אין במסד טבלה, עמודה או נתון שאפשר לגזור ממנו שכר טרחה.
    feesAvailable=False הוא ההצהרה שמונעת מהמסך להציג מספר
    משוער - נתון כספי שגוי גרוע מנתון חסר.
    """
    staff_login(api)
    data = api.get("/api/office/closed-cases").json()

    assert data["feesAvailable"] is False
    body = api.get("/api/office/closed-cases").text.lower()
    for invented in ["fee", "amount", "invoice", "payment", "total_due"]:
        assert '"%s"' % invented not in body, (
            "השדה %s הופיע - אין לו מקור במסד" % invented)


def test_only_closed_cases_are_listed(api, temp_case):
    """
    התיק הזמני פעיל, ולכן אסור שיופיע.
    """
    staff_login(api)
    cases = api.get("/api/office/closed-cases").json()["cases"]

    ids = {c["id"] for c in cases}
    assert str(temp_case["case_id"]) not in ids, "תיק פעיל הופיע ברשימת הסגורים"

    for c in cases:
        assert c["caseNumber"], "תיק בלי מספר"
        assert c["clientName"], "תיק בלי שם לקוח"


def test_a_closed_case_shows_its_decision(api, temp_case):
    """
    התוצאה נלקחת מההחלטה האחרונה שנרשמה, ולא מחושבת מחדש.
    """
    staff_login(api)

    with cursor(commit=True) as cur:
        cur.execute("update cases set status = 'closed_accepted' where id = %s",
                    (temp_case["case_id"],))
        cur.execute(
            """insert into case_decisions
                 (firm_id, case_id, decided_at, outcome, percent, is_permanent)
               values (%s, %s, current_date, 'pension', 42, true)
               returning id""",
            (temp_case["firm_id"], temp_case["case_id"]),
        )
        decision_id = cur.fetchone()["id"]

    try:
        cases = api.get("/api/office/closed-cases").json()["cases"]
        mine = [c for c in cases if c["id"] == str(temp_case["case_id"])]
        assert mine, "התיק הסגור אינו ברשימה"

        d = mine[0]["decision"]
        assert d is not None, "ההחלטה לא הוחזרה"
        assert d["outcome"] == "pension"
        assert d["percent"] == 42
        assert d["permanent"] is True
    finally:
        with cursor(commit=True) as cur:
            cur.execute("delete from case_decisions where id = %s", (decision_id,))
            cur.execute("update cases set status = 'active' where id = %s",
                        (temp_case["case_id"],))


def test_closed_cases_never_cross_firms(api, second_firm):
    staff_login(api)

    with cursor(commit=True) as cur:
        cur.execute("update cases set status = 'closed_rejected' where id = %s",
                    (second_firm["case_id"],))

    numbers = {c["caseNumber"]
               for c in api.get("/api/office/closed-cases").json()["cases"]}
    with cursor() as cur:
        cur.execute("select case_number from cases where id = %s",
                    (second_firm["case_id"],))
        theirs = cur.fetchone()["case_number"]

    assert theirs not in numbers, "תיק סגור של משרד אחר דלף"


def test_closed_cases_need_a_staff_session(api):
    assert api.get("/api/office/closed-cases").status_code == 401


# ================================================================
#  4. אין כתיבה בשלושת האזורים
# ================================================================

@pytest.mark.parametrize("path", [
    "/api/office/clients",
    "/api/office/catalogs",
    "/api/office/closed-cases",
])
def test_the_new_areas_are_read_only(api, path):
    """
    אזור שנראה כמו ניהול ואינו מנהל פחות מסוכן מאזור
    שמנהל בלי שהוחלט איך. הבדיקה נועלת את הגבול.
    """
    staff_login(api)
    for method in ("post", "put", "patch", "delete"):
        r = getattr(api, method)(path)
        assert r.status_code == 405, (
            "%s %s החזיר %d - נוספה כתיבה בלי שנקבעה מדיניות"
            % (method.upper(), path, r.status_code))
