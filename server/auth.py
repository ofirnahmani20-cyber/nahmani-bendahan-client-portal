"""
auth.py - אימות והרשאה בצד השרת.

העיקרון שמנחה את הקובץ
-----------------------
הזהות מגיעה מה-session בלבד. שום endpoint אינו לוקח client_id או
firm_id מגוף הבקשה או מה-URL, כי כל ערך שהדפדפן שולח הוא ערך
שהמשתמש שולט בו. לפני 13.09 האימות כולו חי ב-JavaScript:
sessionStorage.setItem('bl_session_v1', ...) הספיק כדי להיכנס
כל לקוח, וזה מה שהקובץ הזה מחליף.

מה נשמר
--------
ב-DB נשמר hash של הטוקן בלבד (sessions.token_hash). מי שמשיג
גישת קריאה למסד אינו מקבל טוקן שמיש.
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import bcrypt
from fastapi import HTTPException, Request, Response

from .db.pool import cursor

SESSION_COOKIE = "portal_session"
CSRF_COOKIE = "portal_csrf"
CSRF_HEADER = "X-CSRF-Token"

CLIENT_SESSION_HOURS = 2
STAFF_SESSION_HOURS = 8

IS_PRODUCTION = os.environ.get("PORTAL_MODE", "demo").strip().lower() == "production"


# ================================================================
#  טוקנים
# ================================================================

def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    """SHA-256 ולא bcrypt: הטוקן כבר אקראי ב-256 ביט, ואין כאן
    סיסמה חלשה שצריך להאט את ניחושה. bcrypt על כל בקשה היה עולה
    זמן מיותר."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ה-CSRF נקשר ל-session. נשמר בזיכרון התהליך ולא במסד: הוא חסר
# ערך בלי ה-session עצמו, ואינו צריך לשרוד הפעלה מחדש.
_csrf_by_session = {}


# ================================================================
#  יצירת session
# ================================================================

def create_session(response: Response, *, firm_id, subject_type, subject_id,
                   request: Request, hours: int):
    """פותח session, כותב אותו למסד ומצמיד cookies לתשובה."""
    token = _new_token()
    csrf = _new_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=hours)

    with cursor(commit=True) as cur:
        cur.execute(
            """insert into sessions
                 (firm_id, subject_type, subject_id, token_hash,
                  expires_at, ip, user_agent)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            (firm_id, subject_type, subject_id, _hash_token(token), expires,
             request.client.host if request.client else None,
             request.headers.get("user-agent", "")[:500]),
        )

    common = {
        "httponly": True,
        "secure": IS_PRODUCTION,
        "samesite": "lax",
        "max_age": hours * 3600,
        "path": "/",
    }
    response.set_cookie(SESSION_COOKIE, token, **common)
    # ה-CSRF cookie קריא ל-JS במכוון - זו תבנית double-submit,
    # וה-JS חייב להיות מסוגל להחזיר אותו ככותרת.
    response.set_cookie(CSRF_COOKIE, csrf, **{**common, "httponly": False})
    _csrf_by_session[_hash_token(token)] = csrf
    return csrf


# ================================================================
#  קריאת session
# ================================================================

class Identity:
    """מי המשתמש. נבנה מהמסד בלבד, לעולם לא מגוף הבקשה."""

    def __init__(self, row):
        self.session_id = row["id"]
        self.firm_id = row["firm_id"]
        self.subject_type = row["subject_type"]
        self.subject_id = row["subject_id"]
        self.role = row.get("role")
        self.display_name = row.get("display_name")

    @property
    def is_staff(self):
        return self.subject_type == "user"

    @property
    def is_client(self):
        return self.subject_type == "client"


def _load_session(token):
    """מחזיר את ה-session רק אם הוא קיים, לא פג ולא בוטל."""
    with cursor() as cur:
        cur.execute(
            """select s.id, s.firm_id, s.subject_type, s.subject_id,
                      u.role, coalesce(u.full_name, c.full_name) as display_name
                 from sessions s
                 left join users   u on s.subject_type = 'user'   and u.id = s.subject_id
                 left join clients c on s.subject_type = 'client' and c.id = s.subject_id
                where s.token_hash = %s
                  and s.revoked_at is null
                  and s.expires_at > now()""",
            (_hash_token(token),),
        )
        return cur.fetchone()


def current_identity(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = _load_session(token)
    return Identity(row) if row else None


def revoke_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return
    with cursor(commit=True) as cur:
        cur.execute(
            "update sessions set revoked_at = now() where token_hash = %s",
            (_hash_token(token),),
        )
    _csrf_by_session.pop(_hash_token(token), None)


def clear_cookies(response: Response):
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")


# ================================================================
#  CSRF
# ================================================================
#  SameSite=Lax לבדו אינו הגנה: הוא אינו מכסה כל דפדפן, ואינו
#  תקף כשהתוקף יושב על תת-דומיין. לכן שתי שכבות יחד -
#  double-submit token, ובדיקת מקור.
# ================================================================

ALLOWED_ORIGIN_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _origin_ok(request: Request):
    raw = request.headers.get("origin") or request.headers.get("referer")
    if not raw:
        # בקשה בלי Origin וללא Referer אינה יכולה להישלח מטופס
        # חוצה-אתר בדפדפן מודרני. היא כן יכולה להגיע מכלי CLI,
        # ולכן היא עדיין חייבת לעבור את בדיקת ה-CSRF token.
        return True
    host = urlparse(raw).hostname
    if IS_PRODUCTION:
        allowed = {h.strip() for h in
                   os.environ.get("PORTAL_ALLOWED_ORIGINS", "").split(",") if h.strip()}
        return host in allowed
    return host in ALLOWED_ORIGIN_HOSTS


def require_csrf(request: Request):
    """נאכף על כל פעולה משנת-מצב."""
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="מקור הבקשה אינו מורשה.")

    token = request.cookies.get(SESSION_COOKIE)
    sent = request.headers.get(CSRF_HEADER)
    expected = _csrf_by_session.get(_hash_token(token)) if token else None

    if not sent or not expected or not hmac.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="בדיקת CSRF נכשלה.")


# ================================================================
#  תלויות ההרשאה
# ================================================================

def require_identity(request: Request):
    identity = current_identity(request)
    if identity is None:
        raise HTTPException(status_code=401, detail="נדרשת התחברות.")
    return identity


def require_client(request: Request):
    identity = require_identity(request)
    if not identity.is_client:
        raise HTTPException(status_code=403, detail="נתיב זה מיועד ללקוחות.")
    return identity


def require_staff(request: Request):
    identity = require_identity(request)
    if not identity.is_staff:
        raise HTTPException(status_code=403, detail="נתיב זה מיועד לצוות המשרד.")
    return identity


def require_admin(request: Request):
    identity = require_staff(request)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="נדרשת הרשאת מנהל.")
    return identity


# ================================================================
#  סיסמאות
# ================================================================

def verify_password(plain, stored_hash):
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
