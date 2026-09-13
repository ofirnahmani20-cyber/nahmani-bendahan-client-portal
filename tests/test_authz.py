"""
אימות והרשאה - הבדיקות שמוכיחות שהדפדפן כבר אינו שומר הסף.

כל בדיקה כאן הייתה נכשלת לפני 13.09, כשהאימות חי ב-data.js.
דורש מסד נתונים חי עם נתוני הזרע.
"""

import uuid

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


def _seed_ids():
    """שני הלקוחות ושני התיקים מנתוני הזרע."""
    with cursor() as cur:
        cur.execute(
            """select c.id as case_id, c.client_id, cl.full_name
                 from cases c join clients cl on cl.id = c.client_id
                order by c.case_number"""
        )
        return cur.fetchall()


def staff_login(api):
    r = api.post("/api/office/auth/login",
                 json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD})
    assert r.status_code == 200, r.text
    return api.cookies.get("portal_csrf")


def client_login(api, client_id):
    """מדלג על ה-SMS: יוצר session ישירות, כמו שהיה קורה אחרי OTP תקין."""
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


# ================================================================
#  אימות
# ================================================================

def test_unauthenticated_requests_are_refused(api):
    for path in ["/api/me", "/api/client/cases", "/api/office/cases"]:
        assert api.get(path).status_code == 401, path


def test_staff_login_succeeds_and_me_reports_staff(api):
    staff_login(api)
    body = api.get("/api/me").json()
    assert body["type"] == "user"
    assert body["role"] in ("staff", "admin")


def test_wrong_password_is_refused(api):
    r = api.post("/api/office/auth/login",
                 json={"email": STAFF_EMAIL, "password": "not-the-password"})
    assert r.status_code == 401


def test_unknown_email_gives_same_answer_as_wrong_password(api):
    a = api.post("/api/office/auth/login",
                 json={"email": "nobody@example.com", "password": "x"})
    b = api.post("/api/office/auth/login",
                 json={"email": STAFF_EMAIL, "password": "wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_logout_revokes_the_session(api):
    staff_login(api)
    assert api.get("/api/me").status_code == 200
    api.post("/api/office/auth/logout")
    assert api.get("/api/me").status_code == 401


def test_revoked_session_is_rejected_even_with_cookie(api):
    staff_login(api)
    token = api.cookies.get("portal_session")
    with cursor(commit=True) as cur:
        cur.execute("update sessions set revoked_at = now() where revoked_at is null")
    api.cookies.set("portal_session", token)
    assert api.get("/api/me").status_code == 401


def test_expired_session_is_rejected(api):
    staff_login(api)
    with cursor(commit=True) as cur:
        cur.execute("update sessions set expires_at = now() - interval '1 hour' "
                    "where revoked_at is null")
    assert api.get("/api/me").status_code == 401


def test_forged_cookie_is_worthless(api):
    """הניסיון שעבד לפני התיקון: להמציא session."""
    api.cookies.set("portal_session", "made-up-token-" + uuid.uuid4().hex)
    assert api.get("/api/me").status_code == 401


# ================================================================
#  בידוד בין לקוחות
# ================================================================

def test_client_cannot_read_another_clients_case(api):
    rows = _seed_ids()
    assert len(rows) >= 2, "נדרשים שני תיקים בנתוני הזרע"
    mine, theirs = rows[0], rows[1]

    client_login(api, mine["client_id"])

    ok = api.get("/api/client/cases/%s" % mine["case_id"])
    assert ok.status_code == 200

    # אותו נתיב בדיוק, מזהה של מישהו אחר
    denied = api.get("/api/client/cases/%s" % theirs["case_id"])
    assert denied.status_code == 404, (
        "לקוח קיבל תיק של לקוח אחר - זו בדיוק הפרצה שתוקנה")


def test_client_listing_returns_only_own_cases(api):
    rows = _seed_ids()
    mine = rows[0]
    client_login(api, mine["client_id"])
    listed = api.get("/api/client/cases").json()["cases"]
    assert {str(c["id"]) for c in listed} == {str(mine["case_id"])}


def test_client_cannot_reach_office_endpoints(api):
    rows = _seed_ids()
    client_login(api, rows[0]["client_id"])
    assert api.get("/api/office/cases").status_code == 403
    assert api.get("/api/office/cases/%s" % rows[0]["case_id"]).status_code == 403


def test_staff_cannot_reach_client_endpoints(api):
    staff_login(api)
    assert api.get("/api/client/cases").status_code == 403


# ================================================================
#  CSRF
# ================================================================

def _a_document_id():
    with cursor() as cur:
        cur.execute("select id from case_documents order by position limit 1")
        return str(cur.fetchone()["id"])


def test_state_change_without_csrf_header_is_refused(api):
    staff_login(api)
    r = api.post("/api/office/documents/%s/review" % _a_document_id(),
                 json={"decision": "approve"})
    assert r.status_code == 403, "פעולה משנת-מצב עברה בלי טוקן CSRF"


def test_state_change_with_wrong_csrf_token_is_refused(api):
    staff_login(api)
    r = api.post("/api/office/documents/%s/review" % _a_document_id(),
                 json={"decision": "approve"},
                 headers={"X-CSRF-Token": "not-the-right-token"})
    assert r.status_code == 403


def test_state_change_with_foreign_origin_is_refused(api):
    csrf = staff_login(api)
    r = api.post("/api/office/documents/%s/review" % _a_document_id(),
                 json={"decision": "approve"},
                 headers={"X-CSRF-Token": csrf,
                          "Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_state_change_with_valid_csrf_succeeds(api):
    csrf = staff_login(api)
    r = api.post("/api/office/documents/%s/review" % _a_document_id(),
                 json={"decision": "approve"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text


# ================================================================
#  כללי עסק שנאכפים בשרת
# ================================================================

def test_reject_without_reason_is_refused(api):
    csrf = staff_login(api)
    r = api.post("/api/office/documents/%s/review" % _a_document_id(),
                 json={"decision": "reject", "reject_reason": "קצר"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_stage_change_keeps_history(api):
    """שינוי שלב מוסיף אירוע ואינו מוחק את הקודמים."""
    csrf = staff_login(api)
    with cursor() as cur:
        cur.execute("""select c.id as case_id, s.id as stage_id
                         from cases c
                         join stage_templates s on s.claim_type_id = c.claim_type_id
                        where s.position = 2 limit 1""")
        row = cur.fetchone()
        cur.execute("select count(*) as n from case_stage_events where case_id = %s",
                    (row["case_id"],))
        before = cur.fetchone()["n"]

    r = api.post("/api/office/cases/%s/stage-events" % row["case_id"],
                 json={"stage_template_id": str(row["stage_id"]), "note": "בדיקה"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    with cursor() as cur:
        cur.execute("select count(*) as n from case_stage_events where case_id = %s",
                    (row["case_id"],))
        after = cur.fetchone()["n"]
    assert after == before + 1, "היסטוריית השלבים לא נשמרה"


def test_stage_from_another_claim_type_is_refused(api):
    """שלב חייב להשתייך למסלול של התיק."""
    csrf = staff_login(api)
    with cursor() as cur:
        cur.execute("""select c.id as case_id, c.claim_type_id from cases c limit 1""")
        case = cur.fetchone()
        cur.execute("""select id from stage_templates
                        where claim_type_id <> %s limit 1""", (case["claim_type_id"],))
        foreign_stage = cur.fetchone()

    r = api.post("/api/office/cases/%s/stage-events" % case["case_id"],
                 json={"stage_template_id": str(foreign_stage["id"])},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_ai_endpoint_requires_staff(api):
    """ה-AI כבר אינו נפתח לפי מיקום הרשת בלבד."""
    body = {"case": {"claimType": "x", "documents": []}, "preset": "next"}
    assert api.post("/api/office/assist/preview", json=body).status_code == 401

    rows = _seed_ids()
    client_login(api, rows[0]["client_id"])
    assert api.post("/api/office/assist/preview", json=body).status_code == 403
