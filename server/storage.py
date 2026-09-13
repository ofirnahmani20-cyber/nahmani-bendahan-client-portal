"""
storage.py - אחסון קבצים שהועלו.

איפה הקבצים יושבים
-------------------
מחוץ ל-webroot, תחת PORTAL_STORAGE_DIR (ברירת מחדל: var/uploads
בשורש הפרויקט). השרת אינו מגיש את התיקייה הזו כקבצים סטטיים -
אין בה mount, והגישה היחידה היא דרך endpoint שבודק הרשאה.

storage_key אינו URL
---------------------
המבנה firms/{firm_id}/cases/{case_id}/{uuid} לפי ההערה בסכמה.
שם הקובץ המקורי אינו חלק מהנתיב: הוא קלט מהמשתמש, והוא יכול
להכיל תווי מסלול, תווי בקרה או שם שמזהה אדם.

⚠️ זהו אחסון בדיסק מקומי, לא object storage. לפני ייצור יש
   להחליף ל-S3/GCS עם signed URLs קצרי-חיים. הממשק כאן מכוון
   לאפשר את ההחלפה בלי לגעת בקוראים.
"""

import hashlib
import os
import pathlib
import re
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent

MAX_BYTES = int(os.environ.get("PORTAL_MAX_UPLOAD_BYTES", 12 * 1024 * 1024))

# ----------------------------------------------------------------
#  זיהוי סוג לפי תוכן
# ----------------------------------------------------------------
#  הסיומת וכותרת Content-Type הן קלט מהמשתמש ואינן ראיה. הבדיקה
#  היחידה שקובעת היא החתימה בתחילת הקובץ.
# ----------------------------------------------------------------

MAGIC = [
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
]

ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png"}


class RejectedFile(ValueError):
    """הקובץ נדחה. ההודעה מיועדת להצגה ללקוח."""


def sniff_mime(head: bytes):
    """מחזיר סוג לפי חתימה, או None אם אינו מזוהה."""
    for signature, mime in MAGIC:
        if head.startswith(signature):
            return mime
    # WebP: RIFF....WEBP
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_filename(name: str) -> str:
    """
    שם לתצוגה בלבד, לא לנתיב.

    מוסרים רכיבי מסלול, תווי בקרה ותווים שמשמשים להתחזות סיומת.
    השם המנוקה נשמר ב-original_filename ומוצג ללקוח; הוא לעולם
    אינו משתתף בבניית מקום האחסון.
    """
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.lstrip(".")            # .htaccess וקבצים נסתרים
    return (name or "document")[:150]


def build_key(firm_id, case_id) -> str:
    return "firms/%s/cases/%s/%s" % (firm_id, case_id, uuid.uuid4())


def _base_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("PORTAL_STORAGE_DIR", ROOT / "var" / "uploads"))


def _path_for(storage_key: str) -> pathlib.Path:
    base = _base_dir().resolve()
    target = (base / storage_key).resolve()
    # רשת ביטחון מול storage_key שמנסה לצאת מהתיקייה.
    if base not in target.parents and target != base:
        raise RejectedFile("נתיב אחסון לא חוקי.")
    return target


def validate_and_store(data: bytes, *, firm_id, case_id, original_name):
    """
    בודק ושומר. מחזיר את המטא-דאטה לשורת document_files.

    סדר הבדיקות מכוון: גודל לפני תוכן, ותוכן לפני כתיבה לדיסק.
    """
    if not data:
        raise RejectedFile("הקובץ ריק.")
    if len(data) > MAX_BYTES:
        raise RejectedFile("הקובץ גדול מ-%d מגה-בייט." % (MAX_BYTES // (1024 * 1024)))

    mime = sniff_mime(data[:32])
    if mime is None or mime not in ALLOWED_MIME:
        raise RejectedFile("סוג הקובץ אינו נתמך. אפשר להעלות PDF, JPG או PNG.")

    key = build_key(firm_id, case_id)
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    return {
        "storage_key": key,
        "original_filename": safe_filename(original_name),
        "mime_type": mime,
        "size_bytes": len(data),
        "checksum": hashlib.sha256(data).hexdigest(),
    }


def read(storage_key: str) -> bytes:
    return _path_for(storage_key).read_bytes()


def delete(storage_key: str) -> None:
    path = _path_for(storage_key)
    if path.exists():
        path.unlink()
