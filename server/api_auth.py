"""
api_auth.py - נקודות הקצה של ההתחברות.

המשטח לפי "06 - מודל נתונים ו-API" §3.1 ו-§3.3:
    POST /api/client/auth/request-otp
    POST /api/client/auth/verify-otp
    POST /api/client/auth/logout
    POST /api/office/auth/login
    POST /api/office/auth/verify-2fa
    POST /api/office/auth/mfa/enroll/start
    POST /api/office/auth/mfa/enroll/confirm
    POST /api/office/staff/{user_id}/mfa/reset
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

from . import audit, auth, mfa
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
            """select id, firm_id, full_name, password_hash, role, status,
                      totp_enabled_at
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

    # 23.10: סיסמה נכונה אינה מספיקה (docs/10). היא מנפיקה כרטיס,
    # וה-session נפתח רק אחרי הקוד - ראו server/mfa.py.
    enrolled = user["totp_enabled_at"] is not None
    if enrolled or mfa.mfa_required_for_unenrolled():
        purpose = "verify" if enrolled else "enroll"
        with cursor(commit=True) as cur:
            ticket = mfa.issue_ticket(cur, firm_id=user["firm_id"], user_id=user["id"],
                                      purpose=purpose, ip=_ip(request))
        audit.record_actor(user["firm_id"], "user", user["id"],
                           "staff.mfa_challenged", request=request,
                           metadata={"purpose": purpose})
        return {"ok": True, "mfa": purpose, "ticket": ticket}

    return _open_staff_session(user, request, response)


def _ip(request):
    return request.client.host if request.client else None


def _open_staff_session(user, request, response, *, metadata=None):
    auth.create_session(
        response, firm_id=user["firm_id"], subject_type="user",
        subject_id=user["id"], request=request, hours=auth.STAFF_SESSION_HOURS,
    )

    with cursor(commit=True) as cur:
        cur.execute("update users set last_login_at = now() where id = %s", (user["id"],))

    # 26.09: מיוחס ולא אנונימי. הסיסמה כבר אומתה, ולכן אנחנו
    # יודעים מי נכנס - ודוח "כניסות למערכת" יכול סוף סוף לומר
    # את זה. חל על התחברויות חדשות בלבד; 1963 השורות שנכתבו
    # קודם נשארות בלי מזהה ואינן משתנות למפרע.
    audit.record_actor(user["firm_id"], "user", user["id"],
                       "staff.login_success", request=request, metadata=metadata)
    return {"ok": True, "name": user["full_name"], "role": user["role"]}


# ================================================================
#  צוות - אימות דו-שלבי
# ================================================================

class MfaCode(BaseModel):
    ticket: str | None = Field(default=None, max_length=200)
    code: str = Field(min_length=6, max_length=20)


class MfaStart(BaseModel):
    ticket: str | None = Field(default=None, max_length=200)


_MFA_FAILED = "הקוד שגוי או שפג תוקפו. אם עברו כמה דקות - התחבר מחדש."


def _as_user(t):
    return {"id": t["user_id"], "firm_id": t["firm_id"],
            "full_name": t["full_name"], "role": t["role"]}


@router.post("/api/office/auth/verify-2fa")
def verify_2fa(body: MfaCode, request: Request, response: Response):
    """קוד מהאפליקציה, או קוד גיבוי חד-פעמי."""
    _throttle("mfa", request)
    method = None
    with cursor(commit=True) as cur:
        t = mfa.load_ticket(cur, body.ticket, "verify")
        if t is None:
            raise HTTPException(status_code=401, detail=_MFA_FAILED)

        secret = mfa.decrypt_secret(t["totp_secret"] or "")
        step = (mfa.match_step(secret, body.code, after_step=t["totp_last_step"])
                if secret else None)
        if step is not None:
            method = "totp"
            cur.execute("update users set totp_last_step = %s where id = %s",
                        (step, t["user_id"]))
        elif mfa.use_backup_code(cur, t["user_id"], body.code):
            method = "backup"

        if method is None:
            mfa.fail_ticket(cur, t["id"])
        else:
            mfa.consume_ticket(cur, t["id"])
            left = mfa.remaining_backup_codes(cur, t["user_id"])

    if method is None:
        audit.record_actor(t["firm_id"], "user", t["user_id"],
                           "staff.mfa_failed", request=request)
        raise HTTPException(status_code=401, detail=_MFA_FAILED)

    out = _open_staff_session(_as_user(t), request, response, metadata={"mfa": method})
    if method == "backup":
        out["backupCodesLeft"] = left
    return out


def _enroll_subject(cur, ticket, request):
    """
    מי נרשם: בעל כרטיס הרשמה (מתוך ההתחברות), או איש צוות מחובר
    שמוסיף אימות דו-שלבי מיוזמתו. מחזיר (שורת המשתמש, מזהה הכרטיס).
    """
    if ticket:
        t = mfa.load_ticket(cur, ticket, "enroll")
        if t is None:
            raise HTTPException(status_code=401, detail=_MFA_FAILED)
        return t, t["id"]

    identity = auth.require_staff(request)
    auth.require_csrf(request)
    cur.execute(
        """select id as user_id, firm_id, email, full_name, role,
                  totp_pending, totp_enabled_at
             from users where id = %s for update""",
        (identity.subject_id,),
    )
    row = cur.fetchone()
    if row["totp_enabled_at"] is not None:
        # החלפת מכשיר עוברת דרך איפוס של מנהל, לא דרך session פתוח:
        # מי שגנב session לא יוכל להחליף את הסוד לשלו.
        raise HTTPException(status_code=409,
                            detail="אימות דו-שלבי כבר פעיל. להחלפת מכשיר פנה למנהל.")
    return row, None


@router.post("/api/office/auth/mfa/enroll/start")
def enroll_start(body: MfaStart, request: Request):
    _throttle("mfa", request)
    with cursor(commit=True) as cur:
        user, _ = _enroll_subject(cur, body.ticket, request)
        secret = mfa.new_secret()
        cur.execute("update users set totp_pending = %s where id = %s",
                    (mfa.encrypt_secret(secret), user["user_id"]))
    uri = mfa.provisioning_uri(secret, user["email"])
    return {
        "qrSvg": mfa.qr_svg(uri),
        # להקלדה ידנית, כשאי אפשר לסרוק. בקבוצות של ארבעה לקריאוּת.
        "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
        "issuer": mfa.ISSUER,
    }


@router.post("/api/office/auth/mfa/enroll/confirm")
def enroll_confirm(body: MfaCode, request: Request, response: Response):
    """הקוד הראשון מוכיח שהאפליקציה מחזיקה את הסוד. רק אז הוא נכנס לתוקף."""
    _throttle("mfa", request)
    codes = None
    with cursor(commit=True) as cur:
        user, ticket_id = _enroll_subject(cur, body.ticket, request)
        pending = mfa.decrypt_secret(user["totp_pending"] or "")
        step = mfa.match_step(pending, body.code) if pending else None
        if step is None:
            if ticket_id:
                mfa.fail_ticket(cur, ticket_id)
        else:
            cur.execute(
                """update users set totp_secret = totp_pending, totp_pending = null,
                                    totp_enabled_at = now(), totp_last_step = %s
                    where id = %s""",
                (step, user["user_id"]))
            codes = mfa.new_backup_codes(cur, user["user_id"])
            if ticket_id:
                mfa.consume_ticket(cur, ticket_id)

    if codes is None:
        raise HTTPException(status_code=400,
                            detail="הקוד אינו תואם. ודא שהשעון בטלפון מכוון ונסה את הקוד הבא.")

    audit.record_actor(user["firm_id"], "user", user["user_id"],
                       "staff.mfa_enrolled", request=request)
    out = {"ok": True}
    if ticket_id:
        out = _open_staff_session(_as_user(user), request, response,
                                  metadata={"mfa": "enrolled"})
    # מוצגים פעם אחת. במסד נשמר hash בלבד.
    out["backupCodes"] = codes
    return out


@router.post("/api/office/staff/{user_id}/mfa/reset")
def reset_mfa(user_id: str, request: Request):
    """
    טלפון שאבד, בלי קודי גיבוי. מנהל מאפס, וכל ה-sessions של
    המשתמש נסגרים - כולל אחד שאולי פתוח במכשיר שאבד. בכניסה הבאה
    הוא יידרש להירשם מחדש (בייצור).
    """
    identity = auth.require_admin(request)
    auth.require_csrf(request)
    with cursor(commit=True) as cur:
        cur.execute(
            """update users set totp_secret = null, totp_pending = null,
                                totp_enabled_at = null, totp_last_step = null
                where id = %s and firm_id = %s returning id""",
            (user_id, identity.firm_id))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="איש הצוות לא נמצא.")
        cur.execute("delete from mfa_backup_codes where user_id = %s", (user_id,))
        cur.execute(
            """update sessions set revoked_at = now()
                where subject_type = 'user' and subject_id = %s and revoked_at is null""",
            (user_id,))
    audit.record(identity, "office.mfa_reset", entity_type="user",
                 entity_id=user_id, request=request)
    return {"ok": True}


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
        # אם להציג את מסך הסיווג. השרת אוכף בעצמו (require_medical);
        # זה רק כדי לא להציג כפתור שיחזיר 403.
        "canViewMedical": bool(identity.is_staff and identity.can_view_medical),
        # האם להציע הפעלת אימות דו-שלבי. רלוונטי רק לצוות.
        "mfa": _mfa_enabled(identity) if identity.is_staff else None,
    }


def _mfa_enabled(identity):
    with cursor() as cur:
        cur.execute("select totp_enabled_at is not null as on from users where id = %s",
                    (identity.subject_id,))
        return bool(cur.fetchone()["on"])
