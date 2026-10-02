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

הצפנה במנוחה
-------------
כל קובץ מוצפן *בזיכרון* לפני שבייט אחד נכתב לדיסק (crypto.py).
storage_key הוא מזהה הרשומה בהצפנה, ולכן צופן שהועתק לנתיב אחר
נכשל בפענוח. read() מפענח, ולקוראים אין דרך לקבל צופן או לשמור
טקסט גלוי.

אין קובץ גלוי, גם לא זמני
--------------------------
הכתיבה היא: צופן -> <key>.part -> fsync -> os.replace. קריסה בכל
נקודה משאירה לכל היותר צופן יתום, ו-sweep() מנקה אותו.
"""

import hashlib
import os
import pathlib
import re
import time
import urllib.parse
import uuid

from . import crypto, imagecheck

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

ALLOWED_MIME = {"application/pdf", "image/jpeg", "image/png", "image/webp"}

UNSUPPORTED = "סוג הקובץ אינו נתמך. אפשר להעלות PDF, JPG, PNG או WebP."


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


PART_SUFFIX = ".part"


def validate(data: bytes) -> str:
    """
    בדיקות התוכן בלבד, בלי כתיבה. מחזיר את הסוג המזוהה.

    סדר הבדיקות מכוון: גודל לפני תוכן.
    """
    if not data:
        raise RejectedFile("הקובץ ריק.")
    if len(data) > MAX_BYTES:
        raise RejectedFile("הקובץ גדול מ-%d מגה-בייט." % (MAX_BYTES // (1024 * 1024)))

    mime = sniff_mime(data[:32])
    if mime is None or mime not in ALLOWED_MIME:
        raise RejectedFile(UNSUPPORTED)

    # חתימה נכונה אינה תמונה תקינה: מבנה וממדים נבדקים מהכותרות,
    # בלי פענוח. ראה imagecheck.py.
    try:
        imagecheck.check(data, mime)
    except imagecheck.BadImage as exc:
        raise RejectedFile(str(exc))
    return mime


def _firm_of(storage_key: str) -> str:
    # firms/{firm_id}/cases/... - הסולט של הגזירה הוא המשרד.
    return storage_key.split("/")[1]


def _write_atomic(path: pathlib.Path, blob: bytes) -> None:
    """צופן בלבד. .part, fsync, ואז החלפה אטומית."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + PART_SUFFIX)
    try:
        with open(part, "xb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(part, path)
    except BaseException:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        raise


def store_encrypted(data: bytes, storage_key: str) -> None:
    """מצפין בזיכרון וכותב. נזרק KeysUnavailable אם אין מפתח - ואז
    לא נכתב דבר, ובוודאי שלא טקסט גלוי."""
    blob = crypto.encrypt(data, purpose="files", firm_id=_firm_of(storage_key),
                          record_id=storage_key)
    _write_atomic(_path_for(storage_key), blob)


def validate_and_store(data: bytes, *, firm_id, case_id, original_name):
    """
    בודק, מצפין ושומר. מחזיר את המטא-דאטה לשורת document_files.
    """
    mime = validate(data)
    key = build_key(firm_id, case_id)
    store_encrypted(data, key)

    return {
        "storage_key": key,
        "original_filename": safe_filename(original_name),
        "mime_type": mime,
        "size_bytes": len(data),
        "checksum": hashlib.sha256(data).hexdigest(),
    }


def read(storage_key: str) -> bytes:
    """מפענח ומחזיר את הקובץ המקורי. קובץ לא מוצפן אינו מוגש:
    הוא שריד מלפני ההצפנה, ו-maintenance encrypt-legacy מטפל בו."""
    blob = _path_for(storage_key).read_bytes()
    return crypto.decrypt(blob, purpose="files", firm_id=_firm_of(storage_key),
                          record_id=storage_key)


def delete(storage_key: str) -> None:
    path = _path_for(storage_key)
    if path.exists():
        path.unlink()


# ----------------------------------------------------------------
#  ניקוי שאריות
# ----------------------------------------------------------------
#  שני סוגים: .part שנשאר מכתיבה שנקטעה, וקובץ שלם שאין לו שורה
#  במסד (הכתיבה הצליחה וה-insert נכשל, והפעולה המפצה לא רצה כי
#  התהליך קרס). שניהם צופן ולא טקסט גלוי - הניקוי הוא סדר, לא
#  הגנה - אבל צופן יתום הוא עדיין מידע רפואי שאין לו בעלים.
#
#  מרווח הגיל מונע מירוץ עם העלאה שנמצאת עכשיו בין הכתיבה ל-insert.
# ----------------------------------------------------------------

SWEEP_MIN_AGE_SECONDS = 3600


def iter_files():
    """(storage_key, path) לכל קובץ שלם באחסון."""
    base = _base_dir().resolve()
    if not base.is_dir():
        return
    for path in base.rglob("*"):
        if path.is_file() and not path.name.endswith(PART_SUFFIX):
            yield path.relative_to(base).as_posix(), path


def sweep(known_keys=None, *, min_age=SWEEP_MIN_AGE_SECONDS, dry_run=False):
    """
    מוחק .part ישנים, ואם ניתנה known_keys (קבוצת storage_key שקיימים
    במסד) - גם קבצים ישנים שאינם בה. מחזיר ספירות בלבד.
    """
    base = _base_dir().resolve()
    counts = {"parts": 0, "orphans": 0}
    if not base.is_dir():
        return counts
    cutoff = time.time() - min_age
    for path in base.rglob("*"):
        if not path.is_file() or path.stat().st_mtime > cutoff:
            continue
        if path.name.endswith(PART_SUFFIX):
            kind = "parts"
        elif (known_keys is not None
              and path.relative_to(base).as_posix() not in known_keys):
            kind = "orphans"
        else:
            continue
        counts[kind] += 1
        if not dry_run:
            path.unlink(missing_ok=True)
    return counts


# ----------------------------------------------------------------
#  כותרת ההורדה
# ----------------------------------------------------------------

def content_disposition(filename: str) -> str:
    """
    attachment עם שם בטוח לכל דפדפן.

    כותרות HTTP הן latin-1. שם בעברית שנכנס כמות שהוא מפיל את
    התשובה ב-500, ו-" בשם שובר את הכותרת. לכן שני שדות לפי
    RFC 6266: filename ב-ASCII כגיבוי, ו-filename* ב-UTF-8 לשם
    האמיתי.
    """
    name = safe_filename(filename)
    fallback = re.sub(r'[^A-Za-z0-9._ -]', "_", name).strip() or "document"
    encoded = urllib.parse.quote(name, safe="")
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (fallback, encoded)
