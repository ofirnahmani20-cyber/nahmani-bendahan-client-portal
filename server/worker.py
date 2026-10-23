"""
worker.py - מעבד את תור המסמכים.

    python -m server.worker              לולאה
    python -m server.worker --once       עבודה אחת ויציאה
    python -m server.worker --enqueue-missing
                                         קבצים clean שנשמרו לפני שלב 2

חלוקת האחריות
--------------
ה-worker מחזיק מפתחות ומסד, ולעולם אינו מפענח קובץ בעצמו.
sandbox.py מפענח, ואין לו מפתחות ולא מסד. ביניהם - pipe:

    worker: storage.read() (פענוח בזיכרון)
        -> stdin של sandbox (תהליך חדש, סביבה ריקה, מגבלות קשיחות)
        -> stdout: שורת JSON אחת
    worker: תוצאה לתור, ספירות ליומן

מגבלת זמן מבחוץ (timeout כאן), מגבלת זיכרון ו-CPU מבפנים
(limits.py), ובייצור גם mem_limit של הקונטיינר.

בשום שלב הקובץ אינו נכתב לדיסק גלוי.
"""

import argparse
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time

from . import audit, classify, crypto, doc_decision, processing, scan, storage
from .db.pool import cursor

ROOT = pathlib.Path(__file__).resolve().parent.parent

TIMEOUT_SECONDS = int(os.environ.get("PORTAL_WORKER_TIMEOUT", "120"))
MEMORY_MB = int(os.environ.get("PORTAL_WORKER_MEMORY_MB", "768"))
MAX_OUTPUT = 16 * 1024 * 1024
BATCH_PAGES = 4

# OCR. הנתיבים מגיעים מהסביבה של ה-worker ומועברים ל-sandbox במפורש.
TESSERACT = os.environ.get("PORTAL_TESSERACT", "tesseract")
TESSDATA = os.environ.get("PORTAL_TESSDATA", "")
OCR_MODE = os.environ.get("PORTAL_OCR_MODE", "fast")      # ראה ocr.py: מדיניות המודלים


def extract_env(pages):
    """
    אצוות עמודים לתהליך sandbox אחד. כל אצווה תחת אותן מגבלות של
    שלב 2 (זיכרון, CPU, timeout) - מסמך ארוך מתחלק, לא מקבל יותר.
    """
    return {"SANDBOX_TASK": "extract",
            "SANDBOX_PAGES": ",".join(str(p) for p in pages),
            "SANDBOX_TESSERACT": TESSERACT, "SANDBOX_TESSDATA": TESSDATA,
            "SANDBOX_OCR_MODE": OCR_MODE}
POLL_SECONDS = 5

# המשתנים היחידים שהתהליך המבודד מקבל. SYSTEMROOT נדרש ב-Windows
# כדי ש-Python בכלל יעלה; כל השאר - מפתחות, DATABASE_URL, .env,
# וגם TEMP/TMP (אין לו סיבה לכתוב קובץ) - אינם עוברים.
_PASS_THROUGH = ("PATH", "SYSTEMROOT", "WINDIR", "LANG")


def clean_env(pages):
    """אצוות עמודים לניקוי (sandbox task clean). אותן מגבלות כמו extract."""
    return {"SANDBOX_TASK": "clean",
            "SANDBOX_PAGES": ",".join(str(p) for p in pages)}


def store_clean_pages(job, storage_key, data, page_numbers):
    """
    עמודים נקיים (cleanpage.py) - נגזרת של הקובץ, לא חלק מהעיבוד
    עצמו: כישלון כאן אינו מכשיל את העבודה, והקובץ פשוט יסומן בקלסר
    כ"אין עותק נקי". מחזיר כמה עמודים נשמרו.

    המפתח נגזר ממפתח המקור, כך שהמחיקה והמפתחות של המשרד זהים.
    """
    import base64

    pages = []
    for i in range(0, len(page_numbers), BATCH_PAGES):
        batch = page_numbers[i:i + BATCH_PAGES]
        outcome, result = run_sandbox(data, extra_env=clean_env(batch))
        if outcome != "ok":
            return 0
        pages += result["pages"]

    stored = []
    try:
        for p in pages:
            jpeg = base64.b64decode(p["jpeg"])
            key = "%s.clean.%d" % (storage_key, p["page"])
            storage.store_encrypted(jpeg, key)
            stored.append((p, key, len(jpeg)))
    except Exception:                                    # noqa: BLE001
        for _, key, _ in stored:
            storage.delete(key)
        return 0

    with cursor(commit=True) as cur:
        cur.execute("select storage_key from document_clean_pages where file_id = %s",
                    (job["file_id"],))
        old = [r["storage_key"] for r in cur.fetchall()]
        cur.execute("delete from document_clean_pages where file_id = %s", (job["file_id"],))
        for p, key, size in stored:
            cur.execute(
                """insert into document_clean_pages
                     (firm_id, file_id, page_no, storage_key, width, height,
                      size_bytes, deskew_deg)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (job["firm_id"], job["file_id"], p["page"], key, p["width"],
                 p["height"], size, p["deskew"]))
    # עיבוד חוזר: הגרסה הקודמת נמחקת רק אחרי שהחדשה נרשמה.
    for key in old:
        if key not in {k for _, k, _ in stored}:
            storage.delete(key)
    return len(stored)


def sandbox_env(extra=None):
    env = {k: os.environ[k] for k in _PASS_THROUGH if k in os.environ}
    env.update({"PYTHONIOENCODING": "utf-8", "SANDBOX_MEMORY_MB": str(MEMORY_MB)})
    env.update(extra or {})
    return env


def run_sandbox(data: bytes, *, timeout=TIMEOUT_SECONDS, extra_env=None):
    """
    (outcome, payload). outcome: ok | rejected | memory_limit | timeout |
    crashed | limits_unavailable. payload: תוצאה או קוד.
    """
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True           # להרוג את כל הקבוצה
    else:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [sys.executable, "-E", "-s", "-m", "server.sandbox"],
        cwd=ROOT, env=sandbox_env(extra_env),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        **kwargs)
    try:
        out, _ = proc.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()          # Job Object עם KILL_ON_JOB_CLOSE מחסל צאצאים
        proc.communicate()
        return "timeout", "timeout"

    line = out[:MAX_OUTPUT].strip().splitlines()[-1:] if out else []
    try:
        message = json.loads(line[0]) if line else None
    except ValueError:
        message = None
    if message is None:
        # נהרג בלי לכתוב (OOM של מערכת ההפעלה, קריסת ספרייה נייטיבית)
        return "crashed", "crashed"
    if message.get("ok"):
        return "ok", message["result"]
    code = message.get("code", "internal_error")
    if code in ("memory_limit", "limits_unavailable"):
        return code, code
    return "rejected", code


def process_one(worker_id: str, file_ids=None) -> bool:
    """עבודה אחת. False אם התור ריק."""
    job = processing.claim(worker_id, file_ids)
    if job is None:
        return False

    started = time.monotonic()

    def elapsed():
        return int((time.monotonic() - started) * 1000)

    with cursor() as cur:
        cur.execute("""select f.storage_key, f.scan_status, d.case_id
                         from document_files f
                         join case_documents d on d.id = f.document_id
                        where f.id = %s and f.firm_id = %s""",
                    (job["file_id"], job["firm_id"]))
        row = cur.fetchone()

    # השער גם כאן, ולא רק במסד: אם משהו השתנה בין התור לריצה.
    if row is None or not scan.usable(row["scan_status"]):
        processing.fail(job, "not_clean", elapsed())
        return True

    try:
        data = storage.read(row["storage_key"])
    except FileNotFoundError:
        processing.fail(job, "file_missing", elapsed())
        return True
    except (crypto.DecryptionFailed, crypto.KeysUnavailable):
        processing.fail(job, "decrypt_failed", elapsed())
        return True

    outcome, payload = run_sandbox(data)                      # inspect
    extracted = []
    if outcome == "ok":
        page_numbers = [p["page"] for p in payload["pages"]]
        for i in range(0, len(page_numbers), BATCH_PAGES):
            batch = page_numbers[i:i + BATCH_PAGES]
            outcome, result = run_sandbox(data, extra_env=extract_env(batch))
            if outcome != "ok":
                payload = result
                break
            extracted += result["pages"]
            payload["engine"].update(result["engine"])
    clean_count = 0
    if outcome == "ok":
        clean_count = store_clean_pages(job, row["storage_key"], data, page_numbers)
    del data

    if outcome == "ok":
        # כל העמודים יחד, או כלום: אצווה שנכשלה אינה משאירה חצי מסמך.
        processing.replace_pages(job["file_id"], job["firm_id"], extracted)
        # שלב 4: הצעת סוג, על הטקסט שכבר בזיכרון. הצעה בלבד - אינה
        # נוגעת בסטטוס המסמך (processing.store_classification).
        result = classify.classify_document(extracted)
        processing.store_classification(job["file_id"], job["firm_id"], result)
        # 23.10: החלטה אוטומטית - רק "לא קריא" או "ברור ותואם". שלב
        # נפרד מהסיווג, וניתן לכיבוי למשרד (server/doc_decision.py).
        doc_decision.decide(job["file_id"], job["firm_id"], result, extracted)
        processing.complete(job["id"], payload, elapsed())
        status, code = "done", None
    else:
        code = payload if outcome == "rejected" else outcome
        status = processing.fail(job, code, elapsed())

    pages = payload.get("pages", []) if outcome == "ok" else []
    audit.record_actor(job["firm_id"], "system", None, "system.document_processed",
                       metadata={"file_id": str(job["file_id"]), "to_status": status,
                                 "outcome": code or "ok", "page_count": len(pages),
                                 "ocr_page_count": sum(1 for p in pages if p["needs_ocr"]),
                                 "clean_page_count": clean_count,
                                 "duration_ms": elapsed()})
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m server.worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--enqueue-missing", action="store_true")
    parser.add_argument("--reclassify", action="store_true",
                        help="סיווג מחדש של קבצים שסווגו בגרסת כללים קודמת")
    args = parser.parse_args(argv)

    if args.reclassify:
        from .classify_rules import RULES_VERSION
        with cursor() as cur:
            cur.execute("""select file_id, firm_id from document_classifications
                            where rules_version <> %s""", (RULES_VERSION,))
            rows = cur.fetchall()
        for r in rows:
            processing.reclassify(r["file_id"], r["firm_id"])
        print("reclassified=%d" % len(rows))
        return 0

    if args.enqueue_missing:
        print("enqueued=%d" % processing.enqueue_missing())
        return 0

    worker_id = "%s:%d" % (socket.gethostname(), os.getpid())
    recovered = processing.recover_stale()
    if recovered:
        print("recovered stale jobs: %d" % recovered)

    if args.once:
        print("processed" if process_one(worker_id) else "queue empty")
        return 0

    while True:
        try:
            if not process_one(worker_id):
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:                     # pragma: no cover
            # סוג החריגה בלבד: ההודעה עלולה להכיל נתונים.
            print("[worker] error: %s" % type(exc).__name__)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
