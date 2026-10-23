"""
escrow.py - גיבוי מפתחות-העל ושחזורם (נאמנות).

למה זה קיים
------------
הצפנה במנוחה הופכת את מפתח-העל לנקודת כשל יחידה: מי שאיבד אותו
איבד את כל המסמכים, גם אם גיבוי המסד והקבצים שלם. ומנגד, מפתח
שיושב ליד הגיבוי מבטל את ההצפנה.

לכן שני גיבויים נפרדים, בשני מקומות ובאחריות שני אנשים:
  1. גיבוי הנתונים (מסד + var/uploads) - מוצפן, בלי מפתחות.
  2. חבילת נאמנות (הקובץ שנוצר כאן) - המפתחות, מוצפנים בסיסמה
     שבידי השותפים. לא בענן הגיבוי, לא במסד, לא בריפו.

ראה "docs/07 - נוהל התאוששות מפתחות.md".

מבנה החבילה
------------
JSON גלוי עם פרמטרי scrypt, ורשימת KCV לכל מפתח - כדי שאפשר
יהיה לדעת אילו מפתחות בחבילה *בלי* הסיסמה. המפתחות עצמם בתוך
שדה ciphertext, מוצפנים ב-AES-256-GCM במפתח שנגזר מהסיסמה.
"""

import base64
import datetime
import json
import pathlib
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import crypto

FORMAT = "nahmani-escrow-v1"
MIN_PASSPHRASE = 20

# 2**17 * 8 * 128 = 128MB זיכרון לניסיון. יקר לתוקף, זניח לשחזור חד-פעמי.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1


class EscrowError(ValueError):
    pass


def _kdf(passphrase: str, salt: bytes, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(
        passphrase.encode("utf-8"))


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def export_bundle(secrets_dir, passphrase: str) -> dict:
    """מחזיר את החבילה כ-dict. הכתיבה לקובץ היא באחריות הקורא."""
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise EscrowError("הסיסמה קצרה מ-%d תווים." % MIN_PASSPHRASE)

    keys = crypto.FileKeyProvider(secrets_dir).all_keys()
    if not keys:
        raise EscrowError("אין מפתחות לייצוא בתיקייה %s." % secrets_dir)

    payload = json.dumps({
        "%s_kek_v%d" % name: _b64(key) for name, key in keys.items()
    }).encode("utf-8")

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    header = {
        "format": FORMAT,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "salt": _b64(salt)},
        "kcv": {"%s_kek_v%d" % name: crypto.kcv(key).hex()
                for name, key in sorted(keys.items())},
    }
    aad = json.dumps(header, sort_keys=True).encode("utf-8")
    ct = AESGCM(_kdf(passphrase, salt)).encrypt(nonce, payload, aad)
    return dict(header, nonce=_b64(nonce), ciphertext=_b64(ct))


def open_bundle(bundle: dict, passphrase: str) -> dict:
    """{name: key_bytes}. נכשל בקול על סיסמה שגויה או חבילה ששונתה."""
    if bundle.get("format") != FORMAT:
        raise EscrowError("פורמט חבילה לא מוכר.")
    header = {k: bundle[k] for k in ("format", "created_at", "kdf", "kcv")}
    kdf = bundle["kdf"]
    key = _kdf(passphrase, base64.b64decode(kdf["salt"]),
               n=kdf["n"], r=kdf["r"], p=kdf["p"])
    aad = json.dumps(header, sort_keys=True).encode("utf-8")
    try:
        payload = AESGCM(key).decrypt(base64.b64decode(bundle["nonce"]),
                                      base64.b64decode(bundle["ciphertext"]), aad)
    except InvalidTag:
        raise EscrowError("הסיסמה שגויה, או שהחבילה שונתה.")

    keys = {name: base64.b64decode(v) for name, v in json.loads(payload).items()}
    for name, raw in keys.items():
        if crypto.kcv(raw).hex() != bundle["kcv"].get(name):
            raise EscrowError("ה-KCV של %s אינו תואם. החבילה פגומה." % name)
    return keys


def import_bundle(bundle: dict, passphrase: str, secrets_dir) -> list:
    """כותב את המפתחות לתיקיית הסודות. אינו דורס מפתח שונה."""
    written = []
    for name, raw in sorted(open_bundle(bundle, passphrase).items()):
        purpose, _, version = name.partition("_kek_v")
        crypto.write_key_file(secrets_dir, purpose, int(version), raw)
        written.append(name)
    return written


# ================================================================
#  בדיקת התאמה: האם המפתחות הטעונים פותחים את מה שבאחסון
# ================================================================

def verify(secrets_dir, storage_dir) -> dict:
    """
    עובר על כותרות הקבצים באחסון ובודק לכל אחד שיש מפתח טעון
    עם אותה גרסה ואותו KCV. קורא כותרות בלבד - אינו מפענח תוכן.

    מחזיר דוח: מפתחות טעונים, ספירות, ורשימת חוסרים.
    """
    loaded = crypto.FileKeyProvider(secrets_dir).all_keys()
    by_id = {(p, v): crypto.kcv(k) for (p, v), k in loaded.items()}

    report = {"keys": {"%s_kek_v%d" % n: by_id[n].hex() for n in sorted(by_id)},
              "files_checked": 0, "files_ok": 0, "plaintext": 0,
              "missing_key": {}, "kcv_mismatch": 0}

    base = pathlib.Path(storage_dir)
    if base.is_dir():
        for path in base.rglob("*"):
            if not path.is_file() or path.name.endswith(".part"):
                continue
            report["files_checked"] += 1
            with path.open("rb") as fh:
                header = crypto.parse_header(fh.read(crypto.HEADER_LEN))
            if header is None:
                report["plaintext"] += 1
                continue
            purpose, version, key_check = header
            expected = by_id.get((purpose, version))
            if expected is None:
                name = "%s_kek_v%d" % (purpose, version)
                report["missing_key"][name] = report["missing_key"].get(name, 0) + 1
            elif expected != key_check:
                report["kcv_mismatch"] += 1
            else:
                report["files_ok"] += 1
    report["ok"] = (report["files_ok"] == report["files_checked"])
    return report


def write_bundle(bundle: dict, path) -> None:
    pathlib.Path(path).write_text(json.dumps(bundle, indent=2), encoding="utf-8")


def read_bundle(path) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
