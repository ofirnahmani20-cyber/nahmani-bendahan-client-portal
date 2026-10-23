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
import uuid
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


def _csrf_for(token: str) -> str:
    """
    טוקן CSRF נגזר מה-session, ולא נשמר בשום מקום.

    למה לא במפה בזיכרון (כפי שהיה עד 13.09): הפעלה מחדש של השרת
    מחקה את המפה, ואז כל משתמש מחובר קיבל "בדיקת CSRF נכשלה" על
    כל פעולה - בלי שום רמז למה. גזירה דטרמיניסטית שורדת הפעלה
    מחדש ואינה דורשת מצב משותף בין תהליכים.

    הסוד הוא מפתח השרת. מי שאין לו את ה-session cookie (שהוא
    HttpOnly) אינו יכול לגזור את הטוקן.
    """
    key = os.environ.get("PORTAL_ID_HMAC_KEY", "").encode("utf-8")
    return hmac.new(key, ("csrf:" + token).encode("utf-8"),
                    hashlib.sha256).hexdigest()


# ================================================================
#  יצירת session
# ================================================================

def create_session(response: Response, *, firm_id, subject_type, subject_id,
                   request: Request, hours: int):
    """פותח session, כותב אותו למסד ומצמיד cookies לתשובה."""
    token = _new_token()
    csrf = _csrf_for(token)
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
        # הרשאה לתוכן רפואי מחולץ. נקראת מהמסד בכל בקשה, ולכן
        # שלילתה חלה מיד - לא בהתחברות הבאה.
        self.can_view_medical = bool(row.get("can_view_medical"))

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
                      u.role, coalesce(u.full_name, c.full_name) as display_name,
                      coalesce(u.can_view_medical, false) as can_view_medical
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
    expected = _csrf_for(token) if token else None

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


def require_medical(request: Request):
    """
    צוות עם הרשאה מפורשת לתוכן רפואי מחולץ. admin אינו מקבל אותה
    אוטומטית: ההרשאה היא החלטה על אדם, לא על תפקיד.
    """
    identity = require_staff(request)
    if not identity.can_view_medical:
        raise HTTPException(status_code=403, detail="אין הרשאה לצפייה בתוכן רפואי.")
    return identity


def require_admin(request: Request):
    identity = require_staff(request)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="נדרשת הרשאת מנהל.")
    return identity


# ================================================================
#  היקף התיקים של איש צוות
# ================================================================
#  החלטת מוצר 23.10 (docs/10): איש צוות רואה רק את התיקים שהוא
#  האחראי עליהם, והמנהל רואה את כל תיקי המשרד. חובת החיסיון היא
#  כלפי הלקוח, לא כלפי המשרד כמקשה אחת.
#
#  לפני כן firm_id היה הגבול היחיד, וכל עובד ראה כל תיק. הבדיקה
#  יושבת כאן ולא בכל שאילתה בנפרד, כדי ששאילתה חדשה לא תוכל
#  "לשכוח" אותה בשקט: כל נתיב משרדי שנוגע בתיק עובר דרך
#  case_scope או can_see_case.
#
#  תיק בלי אחראי גלוי למנהל בלבד. המנהל משייך אותו.
# ================================================================

def sees_all_cases(identity) -> bool:
    return identity.is_staff and identity.role == "admin"


def case_scope(identity, alias="c") -> str:
    """
    תנאי SQL שמגביל לתיקים הגלויים לאיש הצוות. מוחזר כטקסט כדי
    שיתאים גם לשאילתות עם פרמטרים ממוספרים וגם לשמיים.

    הזרקה אינה אפשרית: alias הוא קבוע בקוד, ומזהה המשתמש עובר
    דרך uuid.UUID - כלומר רק 32 ספרות הקס ומקפים מגיעים ל-SQL.
    """
    if sees_all_cases(identity):
        return "true"
    if not alias.isidentifier():
        raise ValueError("alias לא חוקי")
    return "%s.assigned_user_id = '%s'::uuid" % (alias, uuid.UUID(str(identity.subject_id)))


def can_see_case(identity, assigned_user_id) -> bool:
    if sees_all_cases(identity):
        return True
    return assigned_user_id is not None and str(assigned_user_id) == str(identity.subject_id)


# ================================================================
#  סיסמאות
# ================================================================

def verify_password(plain, stored_hash):
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
