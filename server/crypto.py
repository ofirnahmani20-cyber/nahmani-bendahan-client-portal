"""
crypto.py - הצפנה במנוחה של קבצים ותוכן נגזר.

המודל
-----
מפתח-על (KEK) אחד לכל ייעוד ולכל גרסה, ושום מפתח אחר אינו נשמר.
מפתח ההצפנה של כל רשומה נגזר בכל פעם מחדש:

    DEK = HKDF-SHA256(KEK, salt=firm_id, info=purpose + ":" + record_id)

ולכן אין "טבלת מפתחות" שאפשר לגנוב, ואין מפתח במסד או בקוד.

איפה המפתחות יושבים
--------------------
קבצי סוד בתיקייה PORTAL_SECRETS_DIR, קובץ לכל מפתח:

    files_kek_v1      32 בתים אקראיים, base64
    text_kek_v1       (שמור לשלב העיבוד; נוצר כבר עכשיו כדי שייכנס לנאמנות)

ב-Linux זו /run/secrets (Docker secrets) או LoadCredential של
systemd. בפיתוח - תיקייה מחוץ לריפו. ממשק KeyProvider מאפשר
להחליף ל-Vault/KMS בלי לגעת בקוראים.

אין ברירת מחדל שקטה: בלי PORTAL_SECRETS_DIR אין הצפנה, ובלי
הצפנה אין שמירה. מפתח פיתוח שמגיע לייצור בשקט הוא בדיוק
התרחיש שאסור שיקרה.

מבנה צופן
---------
    MAGIC(6) | purpose_id(1) | version(2) | kcv(8) | nonce(12) | ciphertext+tag

הכותרת כולה, יחד עם מזהה הרשומה, נכנסת ל-AAD. צופן שהועתק
לרשומה אחרת, או שהכותרת שלו שונתה, נכשל בפענוח.

KCV
---
ערך בדיקה של המפתח: HMAC(KEK, קבוע), 8 בתים. אינו מאפשר לשחזר
את המפתח, אבל מאפשר לוודא אחרי שחזור מאסון שנטען המפתח הנכון -
לפני שמנסים לפענח ונכשלים על כל קובץ.
"""

import base64
import hashlib
import hmac
import os
import pathlib
import re
import secrets
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"NBENC1"
PURPOSES = {"files": 1, "text": 2}
_PURPOSE_BY_ID = {v: k for k, v in PURPOSES.items()}

_HEADER = struct.Struct(">6sBH8s12s")
HEADER_LEN = _HEADER.size

_KEY_FILE = re.compile(r"^(?P<purpose>[a-z]+)_kek_v(?P<version>\d+)$")
_KCV_LABEL = b"nahmani-bendahan/kcv/v1"


class KeysUnavailable(RuntimeError):
    """אין מפתחות זמינים. נזרק במקום לשמור בלי הצפנה."""


class DecryptionFailed(ValueError):
    """הצופן אינו תקין, שונה, או שייך לרשומה אחרת."""


def kcv(key: bytes) -> bytes:
    return hmac.new(key, _KCV_LABEL, hashlib.sha256).digest()[:8]


# ================================================================
#  מקור המפתחות
# ================================================================

class KeyProvider:
    """הממשק. מימוש של Vault/KMS יממש את שתי הפונקציות האלה."""

    def keys(self, purpose: str) -> dict:
        """{version: key_bytes} לכל הגרסאות הזמינות של הייעוד."""
        raise NotImplementedError

    def active(self, purpose: str):
        """(version, key) - הגרסה שבה מצפינים עכשיו: הגבוהה ביותר."""
        available = self.keys(purpose)
        if not available:
            raise KeysUnavailable(
                "אין מפתח הצפנה לייעוד '%s'. הצפנה במנוחה אינה מוגדרת." % purpose)
        version = max(available)
        return version, available[version]


class FileKeyProvider(KeyProvider):
    """קורא קבצי סוד מתיקייה. התיקייה נקראת בכל קריאה - לא נשמרת בזיכרון
    גלובלי - כדי שרוטציה ושחזור יחולו בלי אתחול ובלי מצב נסתר."""

    def __init__(self, directory):
        self.directory = pathlib.Path(directory)

    def _check_permissions(self, path: pathlib.Path) -> None:
        # ב-POSIX: קובץ סוד שקריא לקבוצה או לכולם הוא סוד שדלף.
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise KeysUnavailable(
                "הרשאות קובץ המפתח %s פתוחות מדי (נדרש 600)." % path.name)

    def all_keys(self) -> dict:
        """{(purpose, version): key} - לכל המפתחות בתיקייה."""
        if not self.directory.is_dir():
            raise KeysUnavailable("תיקיית הסודות אינה קיימת: %s" % self.directory)
        found = {}
        for path in sorted(self.directory.iterdir()):
            m = _KEY_FILE.match(path.name)
            if not m or m.group("purpose") not in PURPOSES:
                continue
            self._check_permissions(path)
            raw = base64.b64decode(path.read_text(encoding="ascii").strip(),
                                   validate=True)
            if len(raw) != 32:
                raise KeysUnavailable("המפתח %s אינו באורך 256 ביט." % path.name)
            found[(m.group("purpose"), int(m.group("version")))] = raw
        return found

    def keys(self, purpose: str) -> dict:
        return {v: k for (p, v), k in self.all_keys().items() if p == purpose}


def provider() -> KeyProvider:
    directory = os.environ.get("PORTAL_SECRETS_DIR", "").strip()
    if not directory:
        raise KeysUnavailable(
            "PORTAL_SECRETS_DIR אינו מוגדר. הצפנה במנוחה אינה זמינה, "
            "ולכן לא נשמרים קבצים.")
    return FileKeyProvider(directory)


# ================================================================
#  יצירת מפתחות
# ================================================================

def write_key_file(directory, purpose: str, version: int, key: bytes) -> pathlib.Path:
    """כותב קובץ מפתח עם הרשאות 600. מסרב לדרוס מפתח קיים."""
    if purpose not in PURPOSES:
        raise ValueError("ייעוד לא מוכר: %s" % purpose)
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("%s_kek_v%d" % (purpose, version))
    if path.exists():
        existing = base64.b64decode(path.read_text(encoding="ascii").strip())
        if existing != key:
            raise FileExistsError(
                "קיים כבר מפתח שונה בשם %s. דריסה הייתה מאבדת נתונים." % path.name)
        return path
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(base64.b64encode(key).decode("ascii") + "\n")
    return path


def generate_keys(directory) -> dict:
    """מפתח v1 לכל ייעוד שעוד אין לו מפתח. מחזיר {name: kcv_hex} בלבד."""
    prov = FileKeyProvider(directory)
    pathlib.Path(directory).mkdir(parents=True, exist_ok=True)
    existing = prov.all_keys()
    created = {}
    for purpose in PURPOSES:
        if any(p == purpose for p, _ in existing):
            continue
        key = secrets.token_bytes(32)
        write_key_file(directory, purpose, 1, key)
        created["%s_kek_v1" % purpose] = kcv(key).hex()
    return created


# ================================================================
#  הצפנה ופענוח
# ================================================================

def _derive(kek: bytes, firm_id: str, purpose: str, record_id: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=str(firm_id).encode("utf-8"),
        info=("%s:%s" % (purpose, record_id)).encode("utf-8"),
    ).derive(kek)


def encrypt(plaintext: bytes, *, purpose: str, firm_id, record_id: str,
            keys: KeyProvider | None = None) -> bytes:
    keys = keys or provider()
    version, kek = keys.active(purpose)
    nonce = secrets.token_bytes(12)
    header = _HEADER.pack(MAGIC, PURPOSES[purpose], version, kcv(kek), nonce)
    dek = _derive(kek, firm_id, purpose, record_id)
    aad = header + record_id.encode("utf-8")
    return header + AESGCM(dek).encrypt(nonce, plaintext, aad)


def parse_header(blob: bytes):
    """(purpose, version, kcv) או None אם אין כותרת הצפנה."""
    if len(blob) < HEADER_LEN or not blob.startswith(MAGIC):
        return None
    _, pid, version, key_check, _ = _HEADER.unpack(blob[:HEADER_LEN])
    return _PURPOSE_BY_ID.get(pid), version, key_check


def is_encrypted(blob: bytes) -> bool:
    return parse_header(blob) is not None


def decrypt(blob: bytes, *, purpose: str, firm_id, record_id: str,
            keys: KeyProvider | None = None) -> bytes:
    header = parse_header(blob)
    if header is None:
        raise DecryptionFailed("הנתונים אינם מוצפנים או שהכותרת פגומה.")
    blob_purpose, version, key_check = header
    if blob_purpose != purpose:
        raise DecryptionFailed("הצופן שייך לייעוד אחר.")

    keys = keys or provider()
    kek = keys.keys(purpose).get(version)
    if kek is None:
        raise KeysUnavailable(
            "מפתח %s גרסה %d אינו זמין. ייתכן שנדרש שחזור מהנאמנות."
            % (purpose, version))
    if not hmac.compare_digest(kcv(kek), key_check):
        raise KeysUnavailable(
            "המפתח %s גרסה %d אינו המפתח שבו הוצפנו הנתונים (KCV שונה)."
            % (purpose, version))

    nonce = blob[HEADER_LEN - 12:HEADER_LEN]
    dek = _derive(kek, firm_id, purpose, record_id)
    aad = blob[:HEADER_LEN] + record_id.encode("utf-8")
    try:
        return AESGCM(dek).decrypt(nonce, blob[HEADER_LEN:], aad)
    except InvalidTag:
        raise DecryptionFailed("אימות הצופן נכשל - הנתונים שונו או שייכים לרשומה אחרת.")
