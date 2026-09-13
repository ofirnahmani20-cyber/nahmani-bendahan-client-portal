"""
נקודות ה-AI אינן פתוחות.

זו נקודת הקצה היחידה שעולה כסף, והיא הייתה פתוחה לחלוטין: בלי
אימות, בלי הגבלת קצב, ובלי בדיקת מקור.

היסטוריה של החוזה
------------------
מנה א' סגרה אותה מאחורי היתר loopback - שער זמני, כי עוד לא היה
אימות. מנה ב' החליפה אותו ב-staff session אמיתי מה-DB. הבדיקות
כאן בודקות את החוזה הנוכחי, שהוא חזק יותר: מיקום ברשת כבר אינו
מזכה בגישה.
"""

import importlib

import pytest
from starlette.testclient import TestClient

from server.db.pool import cursor

LOCAL = ("127.0.0.1", 45123)
REMOTE = ("203.0.113.9", 51234)

CASE = {"claimType": "נכות כללית", "currentStage": 3, "documents": []}
BODY = {"case": CASE, "preset": "next"}

STAFF_EMAIL = "nahmani@nahmani-bendahan.co.il"
STAFF_PASSWORD = "office2026"


@pytest.fixture
def api():
    from server.app import app
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


def _staff_login(api):
    r = api.post("/api/office/auth/login",
                 json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD})
    assert r.status_code == 200, r.text
    return api.cookies.get("portal_csrf")


# ================================================================
#  ללא זהות
# ================================================================

@pytest.mark.parametrize("path", ["/api/office/assist", "/api/office/assist/preview"])
def test_anonymous_is_refused(api, path):
    """מיקום ברשת כבר אינו מספיק - זה מה שהשתנה במנה ב'."""
    assert api.post(path, json=BODY).status_code == 401


def test_client_session_is_refused(api):
    """לקוח מחובר אינו רשאי להפעיל את הכלי של המשרד."""
    from server import auth
    with cursor() as cur:
        cur.execute("select id, firm_id from clients limit 1")
        client = cur.fetchone()

    class _Req:
        client = type("c", (), {"host": "127.0.0.1"})()
        headers = {}

    class _Resp:
        def __init__(self): self.jar = {}
        def set_cookie(self, name, value, **kw): self.jar[name] = value

    resp = _Resp()
    auth.create_session(resp, firm_id=client["firm_id"], subject_type="client",
                        subject_id=client["id"], request=_Req(),
                        hours=auth.CLIENT_SESSION_HOURS)
    for name, value in resp.jar.items():
        api.cookies.set(name, value)

    assert api.post("/api/office/assist/preview", json=BODY).status_code == 403


# ================================================================
#  עם זהות צוות
# ================================================================

def test_staff_preview_is_allowed(api):
    csrf = _staff_login(api)
    r = api.post("/api/office/assist/preview", json=BODY,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["blocked"] is False


def test_staff_without_csrf_is_refused(api):
    _staff_login(api)
    assert api.post("/api/office/assist/preview", json=BODY).status_code == 403


def test_foreign_origin_is_refused(api):
    csrf = _staff_login(api)
    r = api.post("/api/office/assist/preview", json=BODY,
                 headers={"X-CSRF-Token": csrf,
                          "Origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_rate_limit_kicks_in(api, monkeypatch):
    """הגבלת הקצב עוצרת הצפה של הנקודה שעולה כסף."""
    import server.app as app_module
    monkeypatch.setattr(app_module, "AI_MAX_CALLS", 3)
    app_module._ai_calls.clear()

    csrf = _staff_login(api)
    codes = [api.post("/api/office/assist", json=BODY,
                      headers={"X-CSRF-Token": csrf}).status_code
             for _ in range(5)]
    assert 429 in codes, "הגבלת הקצב לא נאכפה: %s" % codes


# ================================================================
#  פרודקשן
# ================================================================

def test_production_blocks_ai_entirely(monkeypatch):
    """בפרודקשן ה-AI חסום גם לצוות מחובר. אין fallback."""
    monkeypatch.setenv("PORTAL_MODE", "production")
    import server.app
    module = importlib.reload(server.app)
    try:
        # בלי context manager במכוון: lifespan מפעיל את שער הייצור,
        # שמסרב לעלות כל עוד חשבונות ההדגמה במסד. הסירוב הזה נבדק
        # ב-test_production_gate; כאן נבדק שער ה-AI עצמו.
        c = TestClient(module.app, client=LOCAL, base_url="http://127.0.0.1")
        for path in ("/api/office/assist", "/api/office/assist/preview"):
            assert c.post(path, json=BODY).status_code == 503, path
    finally:
        monkeypatch.setenv("PORTAL_MODE", "demo")
        importlib.reload(server.app)


def test_health_does_not_leak_internals(api):
    body = api.get("/api/health").json()
    assert "model" not in body
    assert "policy" not in body
