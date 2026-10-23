"""
mfa.py - אימות דו-שלבי לצוות (TOTP, RFC 6238).

למה כאן ולא בספרייה
--------------------
TOTP הוא HMAC-SHA1 על מונה זמן, וחיתוך של 6 ספרות. עשרים שורות
מעל hmac של הספרייה הסטנדרטית, ונבדקות מול וקטורי הבדיקה של
ה-RFC (tests/test_mfa.py). תלות נוספת הייתה מוסיפה קוד שצריך לעקוב
אחרי עדכוני האבטחה שלו, בשביל פחות ממה שכתוב כאן.

הזרימה
-------
1. סיסמה נכונה אינה פותחת session. היא מנפיקה כרטיס קצר-מועד
   (mfa_tickets), ועליו לבקש:
     verify - למשתמש שכבר רשום: קוד מהאפליקציה או קוד גיבוי
     enroll - למשתמש שחייב להירשם: QR, ואז קוד ראשון שמאשר אותו
2. רק קוד תקין על כרטיס תקין פותח session.

מתי זה חובה
------------
משתמש רשום - תמיד, בכל מצב. משתמש שלא נרשם - חייב להירשם לפני
כניסה ב-production, או כש-PORTAL_REQUIRE_MFA=1. במצב demo בלי
הדגל הוא נכנס בסיסמה, כדי שחשבונות ההדגמה ימשיכו לעבוד; שער
הייצור כבר חוסם את חשבונות ההדגמה בכל מקרה.
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from .auth import IS_PRODUCTION
from .db.connect import _require

ISSUER = "Nahmani-Bendahan"
DIGITS = 6
PERIOD = 30
# צעד אחד לכל כיוון: שעון טלפון שסוטה עד 30 שניות עדיין מתקבל.
DRIFT_STEPS = 1

TICKET_MINUTES = 5
# הרשמה לוקחת יותר: להתקין אפליקציה, לסרוק, להקליד.
ENROLL_TICKET_MINUTES = 15
TICKET_MAX_ATTEMPTS = 5
BACKUP_CODES = 10


def mfa_required_for_unenrolled() -> bool:
    if IS_PRODUCTION:
        return True
    return os.environ.get("PORTAL_REQUIRE_MFA", "").strip() == "1"


# ================================================================
#  TOTP
# ================================================================

def new_secret() -> str:
    """160 ביט, כפי שממליץ RFC 4226. base32 בלי ריפוד - מה שאפליקציות מצפות לו."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret_b32: str) -> bytes:
    pad = "=" * (-len(secret_b32) % 8)
    return base64.b32decode(secret_b32.upper() + pad)


def hotp(secret_b32: str, counter: int) -> str:
    digest = hmac.new(_key(secret_b32), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // PERIOD)


def match_step(secret_b32: str, code: str, *, now: float | None = None,
               after_step: int | None = None) -> int | None:
    """
    מחזיר את צעד הזמן שהקוד תואם לו, או None.

    after_step: הצעד האחרון שכבר התקבל. קוד מצעד זה ומטה נדחה, כדי
    שקוד שנצפה מעבר לכתף לא יעבוד פעם שנייה.
    """
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for s in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if after_step is not None and s <= after_step:
            continue
        if hmac.compare_digest(hotp(secret_b32, s), code):
            return s
    return None


def provisioning_uri(secret_b32: str, email: str) -> str:
    label = quote("%s:%s" % (ISSUER, email))
    return ("otpauth://totp/%s?secret=%s&issuer=%s&digits=%d&period=%d"
            % (label, secret_b32, quote(ISSUER), DIGITS, PERIOD))


def qr_svg(uri: str) -> str:
    """SVG בשורה אחת. נוצר בשרת: ה-URI מכיל את הסוד ואינו נשלח לשום שירות QR חיצוני."""
    import segno
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#1d2733", light="#ffffff")


# ================================================================
#  הצפנת הסוד במסד
# ================================================================
#  אותו מפתח שמצפין ת"ז לתצוגה (PORTAL_ID_ENC_KEY): שניהם שדות
#  רגישים בשורה, שנקראים רק אחרי בדיקת הרשאה. מפתח נפרד היה מוסיף
#  סוד שלישי לנהל, בלי להפריד בין מי שיכול לקרוא אותם.

def encrypt_secret(secret_b32: str) -> str:
    f = Fernet(_require("PORTAL_ID_ENC_KEY").encode("utf-8"))
    return f.encrypt(secret_b32.encode("ascii")).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    f = Fernet(_require("PORTAL_ID_ENC_KEY").encode("utf-8"))
    try:
        return f.decrypt(token.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError):
        return None


# ================================================================
#  כרטיסים וקודי גיבוי
# ================================================================

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue_ticket(cur, *, firm_id, user_id, purpose, ip) -> str:
    ticket = secrets.token_urlsafe(32)
    cur.execute(
        """insert into mfa_tickets (firm_id, user_id, ticket_hash, purpose, expires_at, ip)
           values (%s, %s, %s, %s, %s, %s)""",
        (firm_id, user_id, _hash(ticket), purpose,
         datetime.now(timezone.utc) + timedelta(
             minutes=ENROLL_TICKET_MINUTES if purpose == "enroll" else TICKET_MINUTES),
         ip),
    )
    return ticket


def load_ticket(cur, ticket: str, purpose: str):
    """כרטיס פעיל, לא פג, לא נוצל, ולא מיצה את מכסת הניסיונות. נעול לעדכון."""
    cur.execute(
        """select t.id, t.user_id, t.firm_id, t.attempts,
                  u.email, u.full_name, u.role, u.status,
                  u.totp_secret, u.totp_pending, u.totp_last_step
             from mfa_tickets t join users u on u.id = t.user_id
            where t.ticket_hash = %s and t.purpose = %s
              and t.consumed_at is null and t.expires_at > now()
            for update of t""",
        (_hash(ticket or ""), purpose),
    )
    row = cur.fetchone()
    if row is None or row["attempts"] >= TICKET_MAX_ATTEMPTS or row["status"] != "active":
        return None
    return row


def fail_ticket(cur, ticket_id):
    cur.execute("update mfa_tickets set attempts = attempts + 1 where id = %s", (ticket_id,))


def consume_ticket(cur, ticket_id):
    cur.execute("update mfa_tickets set consumed_at = now() where id = %s", (ticket_id,))


def _format_backup(raw: str) -> str:
    return raw[:5] + "-" + raw[5:]


def new_backup_codes(cur, user_id) -> list[str]:
    """מחליף את כל קודי הגיבוי. הקודים עצמם מוחזרים פעם אחת ולא נשמרים."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # בלי 0/O ו-1/I
    codes = ["".join(secrets.choice(alphabet) for _ in range(10)) for _ in range(BACKUP_CODES)]
    cur.execute("delete from mfa_backup_codes where user_id = %s", (user_id,))
    for c in codes:
        cur.execute("insert into mfa_backup_codes (user_id, code_hash) values (%s, %s)",
                    (user_id, _hash(c)))
    return [_format_backup(c) for c in codes]


def use_backup_code(cur, user_id, code: str) -> bool:
    raw = (code or "").strip().upper().replace("-", "").replace(" ", "")
    if len(raw) != 10:
        return False
    cur.execute(
        """update mfa_backup_codes set used_at = now()
            where id = (select id from mfa_backup_codes
                         where user_id = %s and code_hash = %s and used_at is null
                         limit 1)
        returning id""",
        (user_id, _hash(raw)),
    )
    return cur.fetchone() is not None


def remaining_backup_codes(cur, user_id) -> int:
    cur.execute("select count(*) as n from mfa_backup_codes where user_id = %s and used_at is null",
                (user_id,))
    return cur.fetchone()["n"]
