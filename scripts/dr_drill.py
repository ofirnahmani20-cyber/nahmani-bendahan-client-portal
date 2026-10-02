"""
dr_drill.py - תרגיל התאוששות מפתחות, על סביבה נקייה ונתונים סינתטיים.

    python scripts/dr_drill.py

מריץ את הפקודות שנוהל 07 מורה להריץ, בדיוק כפי שהן כתובות שם,
בתיקייה זמנית שאינה נוגעת בסביבת הפיתוח או הייצור:

  1. keygen init                   - מפתחות חדשים
  2. שמירת 10 מסמכים סינתטיים מוצפנים
  3. keygen export-escrow          - חבילת נאמנות
  4. "אסון": מחיקת תיקיית הסודות
  5. keygen verify                 - חייב להיכשל (מפתח חסר)
  6. keygen import-escrow          - לתיקייה חדשה
  7. keygen verify                 - חייב לעבור
  8. פענוח כל המסמכים והשוואה למקור

יוצא בקוד 0 רק אם כל השלבים עברו. מיועד להרצה תקופתית (NFR-S-10:
"בדיקת שחזור שבוצעה בפועל"), ותוצאתו נרשמת ידנית ביומן התרגילים
שבנוהל.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PASSPHRASE = "synthetic drill passphrase - not a real secret"


def run(step, args, env, expect_ok=True):
    result = subprocess.run([sys.executable, "-m", "server.db.keygen", *args],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    ok = (result.returncode == 0) == expect_ok
    print("[%s] %s" % ("PASS" if ok else "FAIL", step))
    for line in result.stdout.strip().splitlines():
        print("       " + line)
    if not ok:
        print(result.stderr)
        sys.exit(1)


def main():
    work = pathlib.Path(tempfile.mkdtemp(prefix="nb-dr-drill-"))
    secrets_dir = work / "secrets"
    restored = work / "restored-secrets"
    storage_dir = work / "uploads"
    bundle = work / "escrow.json"

    env = dict(os.environ,
               PORTAL_SECRETS_DIR=str(secrets_dir),
               PORTAL_STORAGE_DIR=str(storage_dir),
               PORTAL_ESCROW_PASSPHRASE=PASSPHRASE)
    os.environ.update(PORTAL_SECRETS_DIR=str(secrets_dir),
                      PORTAL_STORAGE_DIR=str(storage_dir))
    sys.path.insert(0, str(ROOT))
    from server import storage

    try:
        run("1. init keys", ["init", "--dir", str(secrets_dir)], env)

        docs = {}
        for i in range(10):
            data = ("%%PDF-1.4\n%% SYNTHETIC drill document %d\n" % i).encode()
            meta = storage.validate_and_store(data, firm_id="drill-firm",
                                              case_id="case-%d" % i,
                                              original_name="drill.pdf")
            docs[meta["storage_key"]] = data
        print("[PASS] 2. stored %d synthetic encrypted documents" % len(docs))

        run("3. export escrow", ["export-escrow", "--dir", str(secrets_dir),
                                 "--out", str(bundle)], env)

        shutil.rmtree(secrets_dir)
        restored.mkdir()
        print("[PASS] 4. disaster: secrets directory destroyed")

        run("5. verify without keys must FAIL", ["verify", "--dir", str(restored)],
            env, expect_ok=False)
        run("6. import escrow", ["import-escrow", "--in", str(bundle),
                                 "--dir", str(restored)], env)
        run("7. verify after restore", ["verify", "--dir", str(restored)], env)

        os.environ["PORTAL_SECRETS_DIR"] = str(restored)
        bad = [k for k, v in docs.items() if storage.read(k) != v]
        if bad:
            print("[FAIL] 8. %d documents did not decrypt to the original" % len(bad))
            sys.exit(1)
        print("[PASS] 8. all %d documents decrypted and match" % len(docs))
        print("\nDRILL PASSED")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
