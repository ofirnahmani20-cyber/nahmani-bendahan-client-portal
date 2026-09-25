"""
שיחה דו-כיוונית בין המשרד ללקוח.

דורש מסד נתונים חי עם נתוני הזרע.

הפער שנסגר: עד 25.09 המשרד לא יכול היה לקרוא דבר שהלקוח כתב.
document_replies הייתה write-only ו-messages חד-כיוונית.

זהו היפוך של OD-3, שהוציאה מענה לקוח מגרסה 1. ההחלטה התקבלה
במפורש, והיא מתועדת ב-REQUIREMENTS.md.
"""

import pytest
from starlette.testclient import TestClient

from server.app import app
from server.db.pool import cursor

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


def client_login(api, client_id):
    from server import auth
    with cursor() as cur:
        cur.execute("select firm_id from clients where id = %s", (client_id,))
        firm_id = cur.fetchone()["firm_id"]

    class _Req:
        client = type("c", (), {"host": "127.0.0.1"})()
        headers = {}

    class _Resp:
        def __init__(self): self.jar = {}
        def set_cookie(self, name, value, **kw): self.jar[name] = value

    resp = _Resp()
    auth.create_session(resp, firm_id=firm_id, subject_type="client",
                        subject_id=client_id, request=_Req(),
                        hours=auth.CLIENT_SESSION_HOURS)
    for name, value in resp.jar.items():
        api.cookies.set(name, value)
    return resp.jar["portal_csrf"]


def client_of(case_id):
    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s", (case_id,))
        return cur.fetchone()["client_id"]


def seed_case_of_client():
    """תיק זרע אמיתי - לקוח הזרע מחובר לתיק שלו, לא ל-temp_case."""
    with cursor() as cur:
        cur.execute("""select id, client_id from cases
                        order by case_number limit 1""")
        return cur.fetchone()


# ================================================================
#  1. הפער שנסגר - המשרד קורא את מה שהלקוח כתב
# ================================================================

def test_client_message_reaches_the_office(api):
    """זו הבדיקה שמוכיחה שהפער נסגר."""
    case = seed_case_of_client()
    marker = "בדיקה: העליתי את הסיכום מהאורתופד"

    api.cookies.clear()
    csrf = client_login(api, case["client_id"])
    r = api.post("/api/client/conversation", json={"body": marker},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    message_id = r.json()["messageId"]

    api.cookies.clear()
    staff_login(api)
    data = api.get("/api/office/cases/%s/conversation" % case["id"]).json()
    found = [m for m in data["messages"] if m["id"] == message_id]

    assert found, "ההודעה של הלקוח לא הגיעה למשרד"
    assert found[0]["direction"] == "inbound"
    assert found[0]["body"] == marker
    assert data["unread"] >= 1

    with cursor(commit=True) as cur:
        cur.execute("delete from case_conversation where id = %s", (message_id,))


def test_office_reply_reaches_the_client(api):
    case = seed_case_of_client()
    marker = "בדיקה: המסמך התקבל, נעדכן לאחר בדיקה"

    csrf = staff_login(api)
    r = api.post("/api/office/cases/%s/conversation" % case["id"],
                 json={"body": marker}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    message_id = r.json()["messageId"]

    api.cookies.clear()
    client_login(api, case["client_id"])
    msgs = api.get("/api/client/conversation").json()["messages"]
    found = [m for m in msgs if m["id"] == message_id]

    assert found, "תשובת המשרד לא הגיעה ללקוח"
    assert found[0]["direction"] == "outbound"

    with cursor(commit=True) as cur:
        cur.execute("delete from case_conversation where id = %s", (message_id,))


def test_marking_a_message_read(api):
    case = seed_case_of_client()
    api.cookies.clear()
    csrf = client_login(api, case["client_id"])
    mid = api.post("/api/client/conversation", json={"body": "בדיקת נקרא"},
                   headers={"X-CSRF-Token": csrf}).json()["messageId"]

    api.cookies.clear()
    csrf = staff_login(api)
    r = api.post("/api/office/conversation/%s/read" % mid,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    data = api.get("/api/office/cases/%s/conversation" % case["id"]).json()
    msg = [m for m in data["messages"] if m["id"] == mid][0]
    assert msg["readAt"] is not None

    with cursor(commit=True) as cur:
        cur.execute("delete from case_conversation where id = %s", (mid,))


# ================================================================
#  2. ההפרדה: תגובה על דרישה מול פנייה כללית
# ================================================================

def test_a_reply_to_a_requirement_is_linked_and_a_general_one_is_not(api,
                                                                     temp_case):
    """
    ההפרדה נאכפת במודל: תגובה על דרישה נושאת requirement_id,
    פנייה כללית נושאת case_id בלבד.
    """
    csrf = staff_login(api)
    rid = api.post("/api/office/cases/%s/requirements" % temp_case["case_id"],
                   json={"kind": "info", "title": "נא למסור פרטים"},
                   headers={"X-CSRF-Token": csrf}).json()["requirementId"]

    linked = api.post("/api/office/cases/%s/conversation" % temp_case["case_id"],
                      json={"body": "בקשר לדרישה", "requirement_id": rid},
                      headers={"X-CSRF-Token": csrf}).json()["messageId"]
    general = api.post("/api/office/cases/%s/conversation" % temp_case["case_id"],
                       json={"body": "פנייה כללית"},
                       headers={"X-CSRF-Token": csrf}).json()["messageId"]

    msgs = api.get("/api/office/cases/%s/conversation"
                   % temp_case["case_id"]).json()["messages"]
    by_id = {m["id"]: m for m in msgs}

    assert by_id[linked]["requirementId"] == rid
    assert by_id[linked]["requirementTitle"] == "נא למסור פרטים"
    assert by_id[general]["requirementId"] is None


def test_a_requirement_from_another_case_is_refused(api, temp_case):
    csrf = staff_login(api)
    other = seed_case_of_client()
    rid = api.post("/api/office/cases/%s/requirements" % other["id"],
                   json={"kind": "info", "title": "דרישה בתיק אחר"},
                   headers={"X-CSRF-Token": csrf}).json()["requirementId"]

    r = api.post("/api/office/cases/%s/conversation" % temp_case["case_id"],
                 json={"body": "ניסיון קישור", "requirement_id": rid},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "הודעה קושרה לדרישה מתיק אחר"

    with cursor(commit=True) as cur:
        cur.execute("delete from case_requirements where id = %s", (rid,))


# ================================================================
#  3. הרשאות, CSRF ואי-דליפה
# ================================================================

def test_conversation_needs_a_session(api, temp_case):
    assert api.get("/api/office/cases/%s/conversation" % temp_case["case_id"]
                   ).status_code == 401
    assert api.get("/api/client/conversation").status_code == 401


def test_client_cannot_read_the_office_conversation_route(api, temp_case):
    api.cookies.clear()
    client_login(api, client_of(temp_case["case_id"]))
    r = api.get("/api/office/cases/%s/conversation" % temp_case["case_id"])
    assert r.status_code == 403


def test_conversation_writes_need_csrf(api, temp_case):
    staff_login(api)
    r = api.post("/api/office/cases/%s/conversation" % temp_case["case_id"],
                 json={"body": "בלי טוקן"})
    assert r.status_code == 403


def test_client_never_sees_which_staff_member_replied(api):
    """
    הלקוח רואה שהמשרד ענה, לא מי מהצוות. שם איש הצוות הוא מידע
    פנימי שאין לו ערך ללקוח.
    """
    case = seed_case_of_client()
    csrf = staff_login(api)
    mid = api.post("/api/office/cases/%s/conversation" % case["id"],
                   json={"body": "תשובה מהמשרד"},
                   headers={"X-CSRF-Token": csrf}).json()["messageId"]

    api.cookies.clear()
    client_login(api, case["client_id"])
    body = api.get("/api/client/conversation").text
    assert "sender" not in body, "שם איש הצוות הודלף ללקוח"
    assert "עו\\\"ד אופיר נחמני" not in body and "עו\"ד אופיר נחמני" not in body

    with cursor(commit=True) as cur:
        cur.execute("delete from case_conversation where id = %s", (mid,))


# ================================================================
#  4. התנאי: תקשורת אינה מגיעה ל-AI
# ================================================================

def test_conversation_never_reaches_the_ai_context():
    """
    build_context הוא allowlist חיובי. תקשורת, דרישות ותוכן
    הודעות אינם בו, ולכן אינם יכולים לצאת למודל - גם אם הפרונט
    ישלח אותם. METADATA_ONLY נשאר ללא שינוי.
    """
    from server import policy

    assert policy.POLICY_MODE == "METADATA_ONLY", \
        "מדיניות ה-AI השתנתה - זה דורש החלטה מפורשת"

    context = policy.build_context({
        "claimType": "נכות כללית", "currentStage": 2, "documents": [],
        "conversation": [{"body": "תוכן שאסור שייצא"}],
        "requirements": [{"title": "דרישה"}],
        "messages": [{"body": "הודעה"}],
    })
    for key in ["conversation", "requirements", "messages"]:
        assert key not in context, "השדה %s הגיע להקשר ה-AI" % key
    policy.assert_clean(context)


# ================================================================
#  5. תיק שטרם נרשם לו שלב
# ================================================================

def test_a_case_with_no_stage_still_returns_requirements_and_stages(api, temp_case):
    """
    רגרסיה ל-25.09: תיק נפתח לפני שנרשם לו אירוע שלב, ולכן
    currentStage הוא null. dashboard.js קרא CLAIM_STAGES[null-1],
    קיבל undefined, וזרק על stage.title. paint() כולו נעצר -
    ומה שמתחתיו, ובכללו הדרישות והשיחה של שלב ג', נשאר ריק
    בלי שום הודעת שגיאה למשתמש.

    הבדיקה נועלת את החוזה: null הוא ערך חוקי, ושאר המפתחות
    ממשיכים להגיע מלאים. הצד הלקוחי מטפל ב-null במפורש.
    """
    case_id = temp_case["case_id"]
    with cursor() as cur:
        cur.execute("select count(*) as n from case_stage_events where case_id = %s",
                    (case_id,))
        assert cur.fetchone()["n"] == 0, "התיק הזמני אמור להיות בלי אירוע שלב"

    api.cookies.clear()
    client_login(api, client_of(case_id))
    data = api.get("/api/client/cases/%s" % case_id).json()

    assert data["currentStage"] is None
    for key in ["stages", "documents", "requirements", "messages"]:
        assert key in data, "המפתח %s נעלם מתשובת הלקוח" % key
    assert len(data["stages"]) == 8, "קטלוג השלבים נחתך יחד עם השלב הנוכחי"
