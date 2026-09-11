"""
connect.py - חיבור למסד ומפתחות ההצפנה.

כל מה שסודי נקרא ממשתני סביבה. אין כאן ברירת מחדל שקטה בכוונה:
ברירת מחדל שקטה היא בדיוק איך מפתח פיתוח מגיע לייצור.
"""

import os
import hmac
import hashlib
import base64
import pathlib

import psycopg
from cryptography.fernet import Fernet


# ------------------------------------------------------------------
#  טעינת .env
#  קובץ מקומי בלבד, שאינו נכנס ל-git. משתנה שכבר קיים בסביבה
#  גובר עליו, כדי שסביבת הרצה אמיתית לא תידרס בקובץ פיתוח.
# ------------------------------------------------------------------

def _load_env() -> None:
    path = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()


# ------------------------------------------------------------------
#  חיבור
# ------------------------------------------------------------------

DEFAULT_DSN = "postgresql://postgres@127.0.0.1:55432/portal"


def dsn() -> str:
    """מחרוזת החיבור. DATABASE_URL גובר על ברירת המחדל המקומית."""
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


def connect():
    """חיבור חדש. המבצע אחראי לסגור אותו, או להשתמש ב-with."""
    return psycopg.connect(dsn())


# ------------------------------------------------------------------
#  תעודת זהות
# ------------------------------------------------------------------
#  ת"ז היא גם מזהה כניסה, ולכן חייבת להיות ניתנת לחיפוש - מה
#  שמונע hash עם מלח פר-שורה. הפתרון הוא שני שדות:
#
#    national_id_lookup - HMAC עם מפתח שרת. ניתן לאינדוקס
#                         ולהשוואה, ולא הפיך בלי המפתח.
#    national_id_enc    - מוצפן, לתצוגה בלבד.
#
#  שני המפתחות חייבים להיות שונים זה מזה. אם אותו מפתח משמש
#  לשניהם, מי שמשיג אותו מקבל גם חיפוש וגם פענוח.
# ------------------------------------------------------------------

class MissingKey(RuntimeError):
    """מפתח חסר. נזרק במקום ליפול לברירת מחדל."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MissingKey(
            f"משתנה הסביבה {name} חסר.\n"
            f"אין ברירת מחדל בכוונה - מפתח ברירת מחדל שנשכח בייצור\n"
            f"שווה לאי-הצפנה. ליצירת מפתחות לפיתוח:\n"
            f"    python -m server.db.keygen"
        )
    return value


def id_lookup(national_id: str) -> bytes:
    """HMAC של ת\"ז, לאינדוקס ולכניסה. דטרמיניסטי."""
    key = _require("PORTAL_ID_HMAC_KEY").encode("utf-8")
    normalised = national_id.strip()
    return hmac.new(key, normalised.encode("utf-8"), hashlib.sha256).digest()


def id_encrypt(national_id: str) -> bytes:
    """הצפנת ת\"ז לצורכי תצוגה. אינה דטרמיניסטית ואינה לחיפוש."""
    f = Fernet(_require("PORTAL_ID_ENC_KEY").encode("utf-8"))
    return f.encrypt(national_id.strip().encode("utf-8"))


def id_decrypt(blob: bytes) -> str:
    """פענוח לתצוגה. משמש רק היכן שההרשאה כבר נבדקה."""
    f = Fernet(_require("PORTAL_ID_ENC_KEY").encode("utf-8"))
    return f.decrypt(bytes(blob)).decode("utf-8")


def new_keys() -> dict:
    """זוג מפתחות חדש לפיתוח. אינו נשמר לשום מקום."""
    return {
        "PORTAL_ID_HMAC_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
        "PORTAL_ID_ENC_KEY": Fernet.generate_key().decode(),
    }
