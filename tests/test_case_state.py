"""
מצב התיק - השדות שנוספו לתשובת התיק בשלב ב'.

דורש מסד נתונים חי עם נתוני הזרע.

שלב ב' אינו מוסיף טבלאות ואינו מוסיף נקודות קצה: הוא מרחיב את
GET /api/office/cases/{id} בשדות שנשלפו כבר בשאילתה ולא הוחזרו,
ועוד אחד - assigned_user_id - שהיה עמודה מתה.
"""

import pytest
from starlette.testclient import TestClient

from server.app import app
from server.db.pool import cursor

LOCAL = ("127.0.0.1", 45123)

STAFF_EMAIL = "nahmani@nahmani-bendahan.co.il"
STAFF_PASSWORD = "office2026"

NEW_FIELDS = ["status", "assignee", "assigneeId", "openedAt",
              "nextHearing", "stageEnteredAt", "isTerminal", "totalStages"]


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
    """מדלג על ה-SMS: יוצר session ישירות, כמו אחרי OTP תקין."""
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


def seed_case_id():
    with cursor() as cur:
        cur.execute("select id from cases order by case_number limit 1")
        return str(cur.fetchone()["id"])


# ================================================================
#  1. השדות החדשים
# ================================================================

def test_case_returns_every_new_field(api):
    """כל שמונת השדות חוזרים. חסר אחד - מסך מצב התיק נשבר."""
    staff_login(api)
    body = api.get("/api/office/cases/%s" % seed_case_id()).json()
    for key in NEW_FIELDS:
        assert key in body, "חסר השדה %s בתשובת התיק" % key


def test_status_and_assignee_come_from_the_database(api):
    """
    עד שלב ב' הכותרת הציגה "פעיל" ו"לא שויך" קבועים.
    עכשיו שניהם מגיעים מהמסד, ו-assigned_user_id נקרא לראשונה.
    """
    case_id = seed_case_id()
    with cursor() as cur:
        cur.execute("""select c.status, u.full_name
                         from cases c
                         left join users u on u.id = c.assigned_user_id
                        where c.id = %s""", (case_id,))
        row = cur.fetchone()

    staff_login(api)
    body = api.get("/api/office/cases/%s" % case_id).json()
    assert body["status"] == row["status"]
    assert body["assignee"] == row["full_name"]


def test_total_stages_matches_the_claim_route(api):
    staff_login(api)
    body = api.get("/api/office/cases/%s" % seed_case_id()).json()
    assert body["totalStages"] == len(body["stageOptions"])
    assert body["totalStages"] > 0


def test_stage_entered_at_comes_from_the_event_log(api):
    """
    "בשלב הזה מאז" נגזר מהאירוע האחרון דרך case_current_stage,
    ואינו שדה שמתוחזק בנפרד.
    """
    case_id = seed_case_id()
    staff_login(api)
    body = api.get("/api/office/cases/%s" % case_id).json()

    with cursor() as cur:
        cur.execute("""select in_stage_since from case_current_stage
                        where case_id = %s""", (case_id,))
        row = cur.fetchone()

    if row is None:
        assert body["stageEnteredAt"] is None
    else:
        assert body["stageEnteredAt"].startswith(
            row["in_stage_since"].isoformat()[:10])


def test_assignee_is_null_when_nobody_is_assigned(api, temp_case):
    """temp_case נוצר בלי אחראי - והשדה חייב להיות null ולא להישבר."""
    staff_login(api)
    body = api.get("/api/office/cases/%s" % temp_case["case_id"]).json()
    assert body["assignee"] is None
    assert body["assigneeId"] is None
    assert body["status"] == "active"


# ================================================================
#  2. אבטחה - החוזה שלא זז
# ================================================================

def test_extended_case_still_requires_a_session(api):
    assert api.get("/api/office/cases/%s" % seed_case_id()).status_code == 401


def test_client_cannot_read_the_extended_case(api):
    case_id = seed_case_id()
    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s", (case_id,))
        client_id = cur.fetchone()["client_id"]

    api.cookies.clear()
    client_login(api, client_id)
    assert api.get("/api/office/cases/%s" % case_id).status_code == 403


def test_office_only_fields_never_reach_the_client_view(api):
    """
    מתוך שמונת השדות שנוספו, חמישה כבר היו בתשובת הלקוח מזמן -
    openedAt, nextHearing, stageEnteredAt, totalStages ו-isTerminal
    (האחרון לכל שלב במסלול, לא לתיק). אלה עובדות על התיק שלו.

    שלושה הם משרדיים בלבד ואסור להם לחצות: מי מטפל בתיק, מזהה
    איש הצוות, וסטטוס הניהול הפנימי.
    """
    case_id = seed_case_id()
    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s", (case_id,))
        client_id = cur.fetchone()["client_id"]

    api.cookies.clear()
    client_login(api, client_id)
    r = api.get("/api/client/cases/%s" % case_id)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ["assignee", "assigneeId", "status"]:
        assert key not in body, "שדה משרדי (%s) הופיע בתשובה ללקוח" % key

    # isTerminal אצל הלקוח הוא תכונה *של שלב במסלול* ולא של התיק.
    # אותו שם, שני דברים שונים - ולכן נבדק במפורש.
    assert "isTerminal" not in body
    assert all("isTerminal" in s for s in body["stages"])


def test_new_fields_do_not_reach_the_ai(api):
    """
    build_context הוא allowlist חיובי. השדות החדשים לא נוספו לו,
    ולכן הם אינם יכולים לצאת למודל - גם אם הפרונט ישלח אותם.
    """
    from server import policy
    context = policy.build_context({
        "claimType": "נכות כללית", "currentStage": 2,
        "status": "active", "assignee": "עו\"ד פלוני",
        "assigneeId": "0f45d610-cb31-4bfe-8f30-4cb5a3e28a5f",
        "isTerminal": False, "documents": [],
    })
    for key in ["status", "assignee", "assigneeId", "isTerminal"]:
        assert key not in context, "השדה %s הגיע להקשר ה-AI" % key
    policy.assert_clean(context)


# ================================================================
#  3. היסטוריה - אף מפתח טכני אינו מוצג
# ================================================================

def test_every_case_action_has_a_human_label():
    """
    כל מחרוזת פעולה שנכתבת עם case_id חייבת ניסוח עברי ב-admin.js.
    בלעדיו המשתמש היה רואה מחרוזת כמו office.task_reopened.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    server_src = "\n".join(
        (root / "server" / name).read_text(encoding="utf-8")
        for name in ["api_office.py", "api_client.py", "app.py"]
    )
    emitted = set(re.findall(r'audit\.record\([^,]+,\s*"([a-z_.]+)"', server_src))

    js = (root / "assets" / "js" / "admin.js").read_text(encoding="utf-8")
    labels = set(re.findall(r"'([a-z]+\.[a-z_]+)':", js))

    missing = sorted(emitted - labels)
    assert not missing, "פעולות בלי ניסוח עברי: %s" % missing


def test_unknown_action_falls_back_to_a_generic_phrase():
    """
    פעולה שאין לה ניסוח מקבלת נוסח כללי לפי המשטח, ולעולם לא את
    המפתח הגולמי. הבדיקה מאמתת שהמפה קיימת ומכסה את שלושתם.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    js = (root / "assets" / "js" / "admin.js").read_text(encoding="utf-8")
    block = js.split("FALLBACK_BY_SURFACE = {")[1].split("}")[0]
    for surface in ["office", "client", "staff"]:
        assert "'%s'" % surface in block, "אין נוסח כללי למשטח %s" % surface

    # entry.action מותר כמפתח חיפוש, אסור כערך מוחזר.
    body = js.split("function describe(entry)")[1].split("\n  }")[0]
    for bad in ["return entry.action", "|| entry.action"]:
        assert bad not in body, "describe מחזיר מפתח טכני למשתמש: %s" % bad

    # וגם renderLog עצמו כבר אינו נופל חזרה על המפתח.
    log = js.split("function renderLog(")[1].split("\n  }")[0]
    assert "|| entry.action" not in log, \
        "renderLog מציג את מחרוזת הפעולה הגולמית"
