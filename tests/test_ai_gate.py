"""
נקודות ה-AI אינן פתוחות.

זו נקודת הקצה היחידה שעולה כסף, והיא הייתה פתוחה לחלוטין: בלי
אימות, בלי הגבלת קצב, ובלי בדיקת מקור.
"""

import importlib

import pytest
from starlette.testclient import TestClient

CASE = {"claimType": "נכות כללית", "currentStage": 3, "documents": []}


# TestClient שולח host="testclient" ולא כתובת IP, והשער - בצדק -
# דוחה כל מקור שאינו IP חוקי. לכן כל בדיקה מציינת כתובת במפורש.
LOCAL = ("127.0.0.1", 45123)
REMOTE = ("203.0.113.9", 51234)


def _app_with_mode(monkeypatch, mode):
    """טוען מחדש את app.py עם PORTAL_MODE אחר - הדגל נקרא בזמן ייבוא."""
    monkeypatch.setenv("PORTAL_MODE", mode)
    import server.app
    return importlib.reload(server.app)


def test_production_blocks_ai_entirely(monkeypatch):
    """בפרודקשן ה-AI חסום, גם מ-localhost. אין fallback ל-demo."""
    module = _app_with_mode(monkeypatch, "production")
    with TestClient(module.app, client=LOCAL) as c:
        for path in ("/api/office/assist", "/api/office/assist/preview"):
            r = c.post(path, json={"case": CASE, "preset": "next"})
            assert r.status_code == 503, path


def test_non_loopback_is_refused(monkeypatch):
    """בקשה שאינה מ-loopback נדחית גם ב-demo."""
    module = _app_with_mode(monkeypatch, "demo")
    with TestClient(module.app, client=REMOTE) as c:
        r = c.post("/api/office/assist/preview", json={"case": CASE, "preset": "next"})
        assert r.status_code == 503


def test_foreign_origin_is_refused(monkeypatch):
    module = _app_with_mode(monkeypatch, "demo")
    with TestClient(module.app, client=LOCAL) as c:
        r = c.post(
            "/api/office/assist/preview",
            json={"case": CASE, "preset": "next"},
            headers={"Origin": "https://evil.example.com"},
        )
        assert r.status_code == 403


def test_local_demo_preview_is_allowed(monkeypatch):
    """ההיתר המקומי עדיין עובד - לא שברנו את הפיתוח."""
    module = _app_with_mode(monkeypatch, "demo")
    with TestClient(module.app, client=LOCAL) as c:
        r = c.post("/api/office/assist/preview", json={"case": CASE, "preset": "next"})
        assert r.status_code == 200
        assert r.json()["blocked"] is False


def test_rate_limit_kicks_in(monkeypatch):
    """הגבלת הקצב עוצרת הצפה של נקודת הקצה שעולה כסף."""
    monkeypatch.setenv("PORTAL_AI_RATE_LIMIT", "3")
    module = _app_with_mode(monkeypatch, "demo")
    with TestClient(module.app, client=LOCAL) as c:
        codes = [
            c.post("/api/office/assist", json={"case": CASE, "preset": "next"}).status_code
            for _ in range(5)
        ]
    assert 429 in codes, "הגבלת הקצב לא נאכפה: %s" % codes


def test_health_does_not_leak_internals(monkeypatch):
    """health אינו חושף את שם המודל או את מצב המדיניות."""
    module = _app_with_mode(monkeypatch, "demo")
    with TestClient(module.app, client=LOCAL) as c:
        body = c.get("/api/health").json()
    assert "model" not in body
    assert "policy" not in body
