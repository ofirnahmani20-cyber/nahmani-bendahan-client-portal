"""
אימות דו-שלבי לצוות - server/mfa.py ו-api_auth.py (docs/10, 23.10).

דורש מסד נתונים חי עם נתוני הזרע. כל בדיקה עובדת על bendahan@
ומחזירה אותו למצב "לא רשום" בסופה.
"""

import base64
import time

import pytest
from starlette.testclient import TestClient

from server import api_auth, mfa
from server.app import app
from server.db.pool import cursor

LOCAL = ("127.0.0.1", 45123)
ADMIN = ("nahmani@nahmani-bendahan.co.il", "office2026")
USER = ("bendahan@nahmani-bendahan.co.il", "office2026")


# ================================================================
#  TOTP מול וקטורי הבדיקה של RFC 6238 (נספח B, SHA-1)
# ================================================================

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize("t, eight_digits", [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
])
def test_totp_matches_rfc_6238_vectors(t, eight_digits):
    # ה-RFC נותן 8 ספרות; 6 הספרות האחרונות הן אותו חישוב עם mod 10^6.
    assert mfa.hotp(RFC_SECRET, t // 30) == eight_digits[-6:]


def test_match_allows_one_step_of_drift_and_refuses_replay():
    now = 1_700_000_000
    step = mfa.current_step(now)
    assert mfa.match_step(RFC_SECRET, mfa.hotp(RFC_SECRET, step - 1), now=now) == step - 1
    assert mfa.match_step(RFC_SECRET, mfa.hotp(RFC_SECRET, step + 1), now=now) == step + 1
    assert mfa.match_step(RFC_SECRET, mfa.hotp(RFC_SECRET, step + 2), now=now) is None
    # קוד שכבר שימש (או ישן ממנו) נדחה
    assert mfa.match_step(RFC_SECRET, mfa.hotp(RFC_SECRET, step), now=now,
                          after_step=step) is None


def test_match_rejects_malformed_codes():
    for bad in ("", "12345", "1234567", "abcdef", None):
        assert mfa.match_step(RFC_SECRET, bad) is None


# ================================================================
#  הזרימה מול השרת
# ================================================================

@pytest.fixture(autouse=True)
def clean_user():
    api_auth._attempts.clear()
    _reset(USER[0])
    yield
    _reset(USER[0])
    api_auth._attempts.clear()


def _reset(email):
    with cursor(commit=True) as cur:
        cur.execute("""update users set totp_secret = null, totp_pending = null,
                              totp_enabled_at = null, totp_last_step = null
                        where email = %s returning id""", (email,))
        uid = cur.fetchone()["id"]
        cur.execute("delete from mfa_backup_codes where user_id = %s", (uid,))
        cur.execute("delete from mfa_tickets where user_id = %s", (uid,))


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
def require_mfa(monkeypatch):
    monkeypatch.setenv("PORTAL_REQUIRE_MFA", "1")


def _login(api, who=USER):
    r = api.post("/api/office/auth/login", json={"email": who[0], "password": who[1]})
    assert r.status_code == 200, r.text
    return r.json()


def _code(secret_spaced, offset_steps=0):
    secret = secret_spaced.replace(" ", "")
    return mfa.hotp(secret, mfa.current_step() + offset_steps)


def _enroll(api):
    """סיסמה -> כרטיס הרשמה -> QR -> קוד ראשון. מחזיר (סוד, קודי גיבוי)."""
    res = _login(api)
    assert res["mfa"] == "enroll"
    start = api.post("/api/office/auth/mfa/enroll/start", json={"ticket": res["ticket"]})
    assert start.status_code == 200, start.text
    assert start.json()["qrSvg"].startswith("<svg")
    secret = start.json()["secret"]
    done = api.post("/api/office/auth/mfa/enroll/confirm",
                    json={"ticket": res["ticket"], "code": _code(secret)})
    assert done.status_code == 200, done.text
    return secret, done.json()["backupCodes"]


def test_unenrolled_user_in_demo_mode_still_logs_in_with_password(api):
    res = _login(api)
    assert "mfa" not in res
    assert api.get("/api/me").json()["mfa"] is False


def test_password_alone_opens_no_session_when_mfa_is_required(api, require_mfa):
    res = _login(api)
    assert res["mfa"] == "enroll" and res["ticket"]
    assert api.get("/api/me").status_code == 401


def test_enrollment_opens_session_and_returns_backup_codes_once(api, require_mfa):
    _, codes = _enroll(api)
    assert len(codes) == mfa.BACKUP_CODES
    assert api.get("/api/me").json()["mfa"] is True
    with cursor() as cur:
        cur.execute("""select totp_secret, totp_pending from users where email = %s""",
                    (USER[0],))
        row = cur.fetchone()
        cur.execute("""select code_hash from mfa_backup_codes b join users u on u.id = b.user_id
                        where u.email = %s""", (USER[0],))
        hashes = {r["code_hash"] for r in cur.fetchall()}
    assert row["totp_pending"] is None
    # הסוד במסד מוצפן ואינו ה-base32 עצמו
    assert row["totp_secret"] and row["totp_secret"].startswith("gAAAA")
    assert not any(c.replace("-", "") in hashes for c in codes)


def test_enrolled_user_must_pass_second_step_even_in_demo_mode(api, require_mfa, monkeypatch):
    secret, _ = _enroll(api)
    api.post("/api/office/auth/logout")
    monkeypatch.delenv("PORTAL_REQUIRE_MFA")

    res = _login(api)
    assert res["mfa"] == "verify"
    assert api.get("/api/me").status_code == 401

    wrong = api.post("/api/office/auth/verify-2fa", json={"ticket": res["ticket"], "code": "000000"})
    assert wrong.status_code == 401
    # הקוד הנוכחי כבר שימש בהרשמה - הצעד הבא בתוך חלון הסטייה
    ok = api.post("/api/office/auth/verify-2fa",
                  json={"ticket": res["ticket"], "code": _code(secret, +1)})
    assert ok.status_code == 200, ok.text
    assert api.get("/api/me").json()["type"] == "user"


def test_code_cannot_be_replayed(api, require_mfa):
    secret, _ = _enroll(api)
    api.post("/api/office/auth/logout")
    res = _login(api)
    # אותו קוד ששימש בהרשמה
    r = api.post("/api/office/auth/verify-2fa",
                 json={"ticket": res["ticket"], "code": _code(secret)})
    assert r.status_code == 401


def test_ticket_is_single_use(api, require_mfa):
    secret, _ = _enroll(api)
    api.post("/api/office/auth/logout")
    res = _login(api)
    first = api.post("/api/office/auth/verify-2fa",
                     json={"ticket": res["ticket"], "code": _code(secret, +1)})
    assert first.status_code == 200
    api.post("/api/office/auth/logout")
    again = api.post("/api/office/auth/verify-2fa",
                     json={"ticket": res["ticket"], "code": _code(secret, +1)})
    assert again.status_code == 401


def test_ticket_locks_after_too_many_wrong_codes(api, require_mfa):
    secret, _ = _enroll(api)
    api.post("/api/office/auth/logout")
    res = _login(api)
    for _ in range(mfa.TICKET_MAX_ATTEMPTS):
        api.post("/api/office/auth/verify-2fa", json={"ticket": res["ticket"], "code": "000000"})
    api_auth._attempts.clear()   # הבדיקה היא על הכרטיס, לא על מגבלת הקצב
    r = api.post("/api/office/auth/verify-2fa",
                 json={"ticket": res["ticket"], "code": _code(secret, +1)})
    assert r.status_code == 401


def test_backup_code_works_exactly_once(api, require_mfa):
    _, codes = _enroll(api)
    api.post("/api/office/auth/logout")

    res = _login(api)
    r = api.post("/api/office/auth/verify-2fa", json={"ticket": res["ticket"], "code": codes[0]})
    assert r.status_code == 200, r.text
    assert r.json()["backupCodesLeft"] == mfa.BACKUP_CODES - 1
    api.post("/api/office/auth/logout")

    res = _login(api)
    r = api.post("/api/office/auth/verify-2fa", json={"ticket": res["ticket"], "code": codes[0]})
    assert r.status_code == 401


def test_enroll_ticket_cannot_be_used_to_verify(api, require_mfa):
    res = _login(api)
    r = api.post("/api/office/auth/verify-2fa", json={"ticket": res["ticket"], "code": "123456"})
    assert r.status_code == 401


def test_enrolled_session_cannot_swap_the_secret(api, require_mfa):
    """מי שגנב session לא יוכל לרשום את הטלפון שלו במקום."""
    _enroll(api)
    h = {"X-CSRF-Token": api.cookies.get("portal_csrf")}
    r = api.post("/api/office/auth/mfa/enroll/start", json={}, headers=h)
    assert r.status_code == 409


def test_session_enrollment_requires_csrf(api):
    _login(api)
    r = api.post("/api/office/auth/mfa/enroll/start", json={})
    assert r.status_code == 403


def test_logged_in_user_can_enroll_from_session(api):
    _login(api)
    h = {"X-CSRF-Token": api.cookies.get("portal_csrf")}
    start = api.post("/api/office/auth/mfa/enroll/start", json={}, headers=h)
    assert start.status_code == 200
    done = api.post("/api/office/auth/mfa/enroll/confirm",
                    json={"code": _code(start.json()["secret"])}, headers=h)
    assert done.status_code == 200, done.text
    assert len(done.json()["backupCodes"]) == mfa.BACKUP_CODES


def test_admin_reset_clears_mfa_and_revokes_sessions(api):
    # בלי דגל החובה, כדי שהמנהל (שאינו רשום) ייכנס בסיסמה.
    # המשתמש נרשם מתוך ה-session שלו.
    _login(api)
    h = {"X-CSRF-Token": api.cookies.get("portal_csrf")}
    start = api.post("/api/office/auth/mfa/enroll/start", json={}, headers=h)
    api.post("/api/office/auth/mfa/enroll/confirm",
             json={"code": _code(start.json()["secret"])}, headers=h)
    assert api.get("/api/me").json()["mfa"] is True

    admin = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    r = admin.post("/api/office/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert "mfa" not in r.json(), "המנהל רשום ל-MFA במסד הזה"
    with cursor() as cur:
        cur.execute("select id from users where email = %s", (USER[0],))
        uid = cur.fetchone()["id"]
    r = admin.post("/api/office/staff/%s/mfa/reset" % uid,
                   headers={"X-CSRF-Token": admin.cookies.get("portal_csrf")})
    assert r.status_code == 200
    assert api.get("/api/me").status_code == 401
    with cursor() as cur:
        cur.execute("select totp_secret, totp_enabled_at from users where id = %s", (uid,))
        row = cur.fetchone()
    assert row["totp_secret"] is None and row["totp_enabled_at"] is None


def test_non_admin_cannot_reset_mfa(api):
    _login(api)
    with cursor() as cur:
        cur.execute("select id from users where email = %s", (ADMIN[0],))
        aid = cur.fetchone()["id"]
    r = api.post("/api/office/staff/%s/mfa/reset" % aid,
                 headers={"X-CSRF-Token": api.cookies.get("portal_csrf")})
    assert r.status_code == 403


def test_failed_code_is_audited(api, require_mfa):
    _enroll(api)
    api.post("/api/office/auth/logout")
    res = _login(api)
    api.post("/api/office/auth/verify-2fa", json={"ticket": res["ticket"], "code": "000000"})
    with cursor() as cur:
        cur.execute("""select count(*) as n from audit_log a join users u on u.id = a.actor_id
                        where u.email = %s and a.action = 'staff.mfa_failed'
                          and a.created_at > now() - interval '1 minute'""", (USER[0],))
        assert cur.fetchone()["n"] >= 1
