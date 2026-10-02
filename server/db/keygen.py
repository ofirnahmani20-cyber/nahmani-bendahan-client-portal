"""
keygen.py - מפתחות.

    python -m server.db.keygen
        מפתחות ה-ת"ז (PORTAL_ID_*) לפיתוח, מודפסים להעתקה ל-.env.

    python -m server.db.keygen init [--dir DIR]
        מפתחות-על להצפנה במנוחה (files, text) כקבצי סוד בתיקייה.
        אינו מדפיס מפתחות - רק את ה-KCV, שאינו סוד.

    python -m server.db.keygen export-escrow --out FILE
        חבילת נאמנות מוצפנת בסיסמה. ראה server/escrow.py ואת
        "docs/07 - נוהל התאוששות מפתחות.md".

    python -m server.db.keygen import-escrow --in FILE [--dir DIR]
        שחזור מחבילת נאמנות. אינו דורס מפתח קיים ששונה ממנה.

    python -m server.db.keygen verify [--dir DIR]
        האם המפתחות הטעונים פותחים את כל הקבצים באחסון. קורא
        כותרות בלבד, לא מפענח תוכן.

DIR ברירת מחדל: PORTAL_SECRETS_DIR. הסיסמה נקראת מהמסוף (getpass)
או מ-PORTAL_ESCROW_PASSPHRASE - האחרון לתרגילים אוטומטיים בלבד.

⚠️ מפתחות ייצור נוצרים בסביבת הייצור עצמה, ולעולם אינם עוברים
   דרך המסך, דרך git או דרך ערוץ הגיבוי של הנתונים.
   החלפת מפתח ה-HMAC של ת"ז אחרי שיש נתונים מחייבת חישוב מחדש של
   national_id_lookup לכל הלקוחות - זה לא שינוי הפיך בקלות.
"""

import argparse
import getpass
import os
import sys

from .connect import new_keys


def _dir(args):
    directory = args.dir or os.environ.get("PORTAL_SECRETS_DIR", "").strip()
    if not directory:
        sys.exit("PORTAL_SECRETS_DIR not set and --dir not given")
    return directory


def _passphrase(confirm: bool) -> str:
    env = os.environ.get("PORTAL_ESCROW_PASSPHRASE")
    if env:
        return env
    first = getpass.getpass("Escrow passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != first:
        sys.exit("passphrases do not match")
    return first


def _id_keys():
    # הפלט הוא ASCII בלבד במכוון: הוא מופנה היישר אל .env,
    # והקונסולה של Windows כותבת עברית ב-cp1255 - מה שהופך
    # את הקובץ לבלתי קריא כ-UTF-8.
    print("# dev keys only - do not use in production")
    for name, value in new_keys().items():
        print(f"{name}={value}")


def main(argv=None):
    from .. import crypto, escrow, storage

    parser = argparse.ArgumentParser(prog="python -m server.db.keygen")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("init")
    p.add_argument("--dir")
    p = sub.add_parser("export-escrow")
    p.add_argument("--dir")
    p.add_argument("--out", required=True)
    p = sub.add_parser("import-escrow")
    p.add_argument("--dir")
    p.add_argument("--in", dest="src", required=True)
    p = sub.add_parser("verify")
    p.add_argument("--dir")

    args = parser.parse_args(argv)

    if args.cmd is None:
        _id_keys()
        return 0

    # הפלט כולו ASCII, מאותה סיבה כמו _id_keys.
    if args.cmd == "init":
        created = crypto.generate_keys(_dir(args))
        if not created:
            print("keys already exist - nothing created")
        for name, check in created.items():
            print("created %s  kcv=%s" % (name, check))
        print("next: export an escrow bundle before storing any document")
        return 0

    if args.cmd == "export-escrow":
        if os.path.exists(args.out):
            sys.exit("refusing to overwrite %s" % args.out)
        bundle = escrow.export_bundle(_dir(args), _passphrase(confirm=True))
        escrow.write_bundle(bundle, args.out)
        for name, check in bundle["kcv"].items():
            print("escrowed %s  kcv=%s" % (name, check))
        print("store this file OFFLINE, apart from the data backups")
        return 0

    if args.cmd == "import-escrow":
        written = escrow.import_bundle(escrow.read_bundle(args.src),
                                       _passphrase(confirm=False), _dir(args))
        for name in written:
            print("restored %s" % name)
        return 0

    if args.cmd == "verify":
        report = escrow.verify(_dir(args), storage._base_dir())
        for name, check in report["keys"].items():
            print("loaded %s  kcv=%s" % (name, check))
        print("files checked: %d  ok: %d  plaintext(legacy): %d  kcv mismatch: %d"
              % (report["files_checked"], report["files_ok"],
                 report["plaintext"], report["kcv_mismatch"]))
        for name, count in report["missing_key"].items():
            print("MISSING KEY %s for %d files" % (name, count))
        print("OK" if report["ok"] else "NOT OK")
        return 0 if report["ok"] else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
