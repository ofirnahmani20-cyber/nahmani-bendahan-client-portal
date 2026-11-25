"""
הגבלת קצב במסד - server/throttle.py ו-api_auth.py.

מה נבדק: שהמונה משותף (לא תלוי בתהליך), שהוא לפי חשבון ולא רק
לפי IP, ושבטבלה לא נשמר שום מזהה גלוי.
"""

import pytest
from starlette.testclient import TestClient

from server import api_auth, throttle
from server.app import app
from server.db.pool import cursor


@pytest.fixture
def api():
    return TestClient(app)


def test_blocks_after_limit_and_does_not_record_blocked_hits():
    for _ in range(3):
        assert throttle.hit("t", "subject", 3, 60)
    assert not throttle.hit("t", "subject", 3, 60)
    assert not throttle.hit("t", "subject", 3, 60)
    with cursor() as cur:
        cur.execute("select count(*) as n from rate_limit_hits")
        assert cur.fetchone()["n"] == 3


def test_scopes_and_subjects_are_separate():
    for _ in range(2):
        assert throttle.hit("a", "x", 2, 60)
    assert not throttle.hit("a", "x", 2, 60)
    assert throttle.hit("a", "y", 2, 60)
    assert throttle.hit("b", "x", 2, 60)


def test_window_expires():
    assert throttle.hit("w", "s", 1, 60)
    assert not throttle.hit("w", "s", 1, 60)
    with cursor(commit=True) as cur:
        cur.execute("update rate_limit_hits set hit_at = now() - interval '2 minutes'")
    assert throttle.hit("w", "s", 1, 60)


def test_purge_removes_only_old_rows():
    throttle.hit("p", "fresh", 5, 60)
    throttle.hit("p", "old", 5, 60)
    with cursor(commit=True) as cur:
        cur.execute("""update rate_limit_hits set hit_at = now() - interval '2 days'
                        where id = (select max(id) from rate_limit_hits)""")
    assert throttle.purge() == 1
    with cursor() as cur:
        cur.execute("select count(*) as n from rate_limit_hits")
        assert cur.fetchone()["n"] == 1


def test_table_holds_no_identifier_in_clear():
    email = "someone@example.com"
    throttle.hit("acct:staff", email, 5, 60)
    with cursor() as cur:
        cur.execute("select key from rate_limit_hits")
        key = cur.fetchone()["key"]
    assert email not in key and "staff" not in key
    assert len(key) == 64


def test_staff_login_is_limited_per_account_across_ips(api, monkeypatch):
    """הרבה כתובות IP על חשבון אחד עדיין נעצרות."""
    monkeypatch.setattr(api_auth, "ACCOUNT_MAX_TRIES", 3)
    # מגבלת ה-IP מוסרת מהדרך, כדי לבדוק רק את מגבלת החשבון
    monkeypatch.setattr(api_auth, "LOGIN_MAX_TRIES", 1000)
    body = {"email": "nobody@example.com", "password": "wrong"}
    codes = [api.post("/api/office/auth/login", json=body).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    # חשבון אחר מאותו IP אינו נחסם
    r = api.post("/api/office/auth/login", json={"email": "other@example.com", "password": "x"})
    assert r.status_code == 401


def test_otp_requests_are_limited_per_id(api, monkeypatch):
    """כל בקשה היא SMS בתשלום. התשובה זהה לת"ז קיימת ושאינה קיימת."""
    monkeypatch.setattr(api_auth, "OTP_SEND_MAX", 2)
    body = {"national_id": "000000018"}
    codes = [api.post("/api/client/auth/request-otp", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
