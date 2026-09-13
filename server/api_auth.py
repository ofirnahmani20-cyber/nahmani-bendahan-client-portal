"""
api_auth.py - נקודות הקצה של ההתחברות.

המשטח לפי "06 - מודל נתונים ו-API" §3.1 ו-§3.3:
    POST /api/client/auth/request-otp
    POST /api/client/auth/verify-otp
    POST /api/client/auth/logout
    POST /api/office/auth/login
    POST /api/office/auth/logout
    GET  /api/me

מה שהוחלף
----------
Auth.login ו-StaffAuth.login ב-data.js השוו סיסמאות בדפדפן מול
טבלה שנשלחה לכל מבקר. כאן ההשוואה בשרת, מול password_hash של
bcrypt, והזהות חוזרת ב-cookie מסוג HttpOnly.
"""

import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import audit, auth
from .db.connect import id_lookup
from .db.pool import cursor
from .notify import SmsNotConfigured, get_sender

router = APIRouter()

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

# הגבלת קצב להתחברות, בזיכרון. מונעת ניחוש קוד וסיסמה בכוח.
_attempts = {}
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_TRIES = 10


def _throttle(key, request):
    ip = request.client.host if request.client else "unknown"
    bucket = _attempts.setdefault("%s|%s" % (key, ip), [])
    now = time.monotonic()
    bucket[:] = [t for t in bucket if now - t < LOGIN_WINDOW_SECONDS]
    if len(bucket) >= LOGIN_MAX_TRIES:
        raise HTTPException(status_code=429, detail="יותר מדי ניסיונות. נסה שוב מאוחר יותר.")
    bucket.append(now)


def _hash_code(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


# ================================================================
#  לקוח - קוד חד-פעמי
# ================================================================

class OtpRequest(BaseModel):
    national_id: str = Field(min_length=5, max_length=20)


class OtpVerify(BaseModel):
    national_id: str = Field(min_length=5, max_length=20)
    code: str = Field(min_length=4, max_length=10)


def _find_client(national_id):
    """חיפוש לפי HMAC. הת\"ז עצמה אינה נשמרת ואינה ניתנת לחיפוש טקסטואלי."""
    with cursor() as cur:
        cur.execute(
            """select id, firm_id, full_name, phone, status
                 from clients where national_id_lookup = %s""",
            (id_lookup(national_id),),
        )
        return cur.fetchone()


@router.post("/api/client/auth/request-otp")
def request_otp(body: OtpRequest, request: Request):
    """
    התשובה זהה בין ת\"ז קיימת לשאינה קיימת. אחרת הנקודה הזו הופכת
    לכלי שמאשר אם אדם מסוים הוא לקוח של המשרד - וזה בעצמו מידע
    שאין למסור.
    """
    _throttle("otp", request)
    generic = {"sent": True, "expires_in_minutes": OTP_TTL_MINUTES}

    client = _find_client(body.national_id)
    if not client or client["status"] != "active":
        return generic

    code = "%06d" % secrets.randbelow(1_000_000)
    expires = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)

    try:
        get_sender().send_otp(client["phone"], code)
    except SmsNotConfigured as exc:
        # נכשל סגור: לא נכתב קוד, לא נוצר אתגר, ואין העמדת פנים.
        raise HTTPException(status_code=503, detail=str(exc))

    with cursor(commit=True) as cur:
        cur.execute(
            """insert into otp_challenges (client_id, code_hash, expires_at, requested_ip)
               values (%s, %s, %s, %s)""",
            (client["id"], _hash_code(code), expires,
             request.client.host if request.client else None),
        )

    audit.record_anonymous(client["firm_id"], "client.otp_requested", request=request)
    return generic


@router.post("/api/client/auth/verify-otp")
def verify_otp(body: OtpVerify, request: Request, response: Response):
    _throttle("otp-verify", request)
    client = _find_client(body.national_id)
    if not client or client["status"] != "active":
        raise HTTPException(status_code=401, detail="הקוד שגוי או שפג תוקפו.")

    with cursor(commit=True) as cur:
        cur.execute(
            """select id, code_hash, attempts from otp_challenges
                where client_id = %s and consumed_at is null and expires_at > now()
                order by created_at desc limit 1""",
            (client["id"],),
        )
        challenge = cur.fetchone()

        if challenge is None or challenge["attempts"] >= OTP_MAX_ATTEMPTS:
            audit.record_anonymous(client["firm_id"], "client.login_failed", request=request)
            raise HTTPException(status_code=401, detail="הקוד שגוי או שפג תוקפו.")

        if not secrets.compare_digest(challenge["code_hash"], _hash_code(body.code)):
            cur.execute("update otp_challenges set attempts = attempts + 1 where id = %s",
                        (challenge["id"],))
            audit.record_anonymous(client["firm_id"], "client.login_failed", request=request)
            raise HTTPException(status_code=401, detail="הקוד שגוי או שפג תוקפו.")

        cur.execute("update otp_challenges set consumed_at = now() where id = %s",
                    (challenge["id"],))

    auth.create_session(
        response, firm_id=client["firm_id"], subject_type="client",
        subject_id=client["id"], request=request, hours=auth.CLIENT_SESSION_HOURS,
    )

    identity = auth.current_identity(request)
    audit.record_anonymous(client["firm_id"], "client.login_success", request=request)
    return {"ok": True, "name": client["full_name"]}


@router.post("/api/client/auth/logout")
def client_logout(request: Request, response: Response):
    auth.revoke_session(request)
    auth.clear_cookies(response)
    return {"ok": True}


# ================================================================
#  צוות - אימייל וסיסמה
# ================================================================

class StaffLogin(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


@router.post("/api/office/auth/login")
def staff_login(body: StaffLogin, request: Request, response: Response):
    _throttle("staff", request)

    with cursor() as cur:
        cur.execute(
            """select id, firm_id, full_name, password_hash, role, status
                 from users where lower(email) = lower(%s)""",
            (body.email.strip(),),
        )
        user = cur.fetchone()

    # אותה תשובה לכל כשל, כדי לא להסגיר אילו אימיילים קיימים.
    if (user is None or user["status"] != "active"
            or not auth.verify_password(body.password, user["password_hash"])):
        if user is not None:
            audit.record_anonymous(user["firm_id"], "staff.login_failed", request=request)
        raise HTTPException(status_code=401, detail="פרטי ההתחברות שגויים.")

    auth.create_session(
        response, firm_id=user["firm_id"], subject_type="user",
        subject_id=user["id"], request=request, hours=auth.STAFF_SESSION_HOURS,
    )

    with cursor(commit=True) as cur:
        cur.execute("update users set last_login_at = now() where id = %s", (user["id"],))

    audit.record_anonymous(user["firm_id"], "staff.login_success", request=request)
    return {"ok": True, "name": user["full_name"], "role": user["role"]}


@router.post("/api/office/auth/logout")
def staff_logout(request: Request, response: Response):
    auth.revoke_session(request)
    auth.clear_cookies(response)
    return {"ok": True}


# ================================================================
#  מי אני
# ================================================================

@router.get("/api/me")
def me(request: Request):
    """הפרונט שואל את השרת מי המשתמש. הוא אינו מחליט בעצמו."""
    identity = auth.current_identity(request)
    if identity is None:
        raise HTTPException(status_code=401, detail="נדרשת התחברות.")
    return {
        "type": identity.subject_type,
        "name": identity.display_name,
        "role": identity.role,
    }
