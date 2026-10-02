"""
maintenance.py - משימות תחזוקה של האחסון.

    python -m server.maintenance rescan [--limit N]
        סריקה חוזרת לקבצים במצב pending/failed. הקובץ מפוענח
        בזיכרון ונשלח לסורק מהזיכרון - אינו נכתב גלוי לשום מקום.
        נגוע: מסומן infected והצופן נמחק. מיועד ל-timer מחזורי.

    python -m server.maintenance sweep [--apply]
        ניקוי .part שנשארו מכתיבה שנקטעה, וקבצים מוצפנים שאין להם
        שורה במסד. ברירת המחדל היא דיווח בלבד.

    python -m server.maintenance encrypt-legacy [--apply]
        הצפנה חד-פעמית של קבצים שנשמרו גלויים לפני שההצפנה נוספה.
        ברירת המחדל היא דיווח בלבד.

הפלט ASCII בלבד וספירות בלבד: אין שמות קבצים, כי שם קובץ שלקוח
העלה עלול לזהות אותו.
"""

import argparse
import sys

from . import audit, crypto, scan, storage
from .db.pool import cursor


def rescan(limit=200):
    scanner = scan.get_scanner()
    if not scanner.production_safe:
        return {"skipped": "no scanner configured"}

    with cursor() as cur:
        cur.execute(
            """select id, firm_id, storage_key from document_files
                where scan_status in ('pending', 'failed')
                order by uploaded_at limit %s""", (limit,))
        rows = cur.fetchall()

    counts = {"clean": 0, "infected": 0, "failed": 0, "pending": 0,
              "missing": 0, "unreadable": 0}
    for row in rows:
        try:
            data = storage.read(row["storage_key"])
        except FileNotFoundError:
            counts["missing"] += 1
            continue
        except (crypto.DecryptionFailed, crypto.KeysUnavailable):
            # קובץ גלוי מלפני ההצפנה, או מפתח חסר. לא נסרק עד
            # שיטופל (encrypt-legacy / שחזור מפתח) - ולכן גם לא usable.
            counts["unreadable"] += 1
            continue
        result = scanner.scan(data)
        del data
        counts[result.status] += 1
        with cursor(commit=True) as cur:
            cur.execute("update document_files set scan_status = %s where id = %s",
                        (result.status, row["id"]))
        if result.status == "infected":
            storage.delete(row["storage_key"])
        if result.status in ("clean", "infected"):
            audit.record_actor(row["firm_id"], "system", None, "system.file_rescanned",
                               metadata={"file_id": str(row["id"]),
                                         "scan_status": result.status,
                                         "scan_signature": result.detail})
    return counts


def _known_keys():
    with cursor() as cur:
        cur.execute("select storage_key from document_files")
        return {r["storage_key"] for r in cur.fetchall()}


def sweep(apply=False):
    return storage.sweep(_known_keys(), dry_run=not apply)


def sweep_parts_only():
    """בעלייה: רק .part. אינו נוגע במסד ולכן בטוח גם כשהמסד איטי."""
    return storage.sweep(None)


def encrypt_legacy(apply=False):
    counts = {"plaintext": 0, "encrypted": 0, "already": 0}
    for key, path in list(storage.iter_files()):
        blob = path.read_bytes()
        if crypto.is_encrypted(blob):
            counts["already"] += 1
            continue
        counts["plaintext"] += 1
        if apply:
            storage.store_encrypted(blob, key)
            counts["encrypted"] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m server.maintenance")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("rescan")
    p.add_argument("--limit", type=int, default=200)
    p = sub.add_parser("sweep")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("encrypt-legacy")
    p.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if args.cmd == "rescan":
        result = rescan(args.limit)
    elif args.cmd == "sweep":
        result = sweep(args.apply)
    else:
        result = encrypt_legacy(args.apply)
    print(" ".join("%s=%s" % kv for kv in result.items()))
    if getattr(args, "apply", True) is False:
        print("(dry run - nothing changed; add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
