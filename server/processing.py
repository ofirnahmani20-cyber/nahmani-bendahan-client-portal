"""
processing.py - תור העיבוד והאחסון של מה שנגזר ממסמך.

התור הוא טבלה (document_processing), לא שירות נוסף: Postgres כבר
כאן, ו-FOR UPDATE SKIP LOCKED מאפשר לכמה workers לעבוד במקביל בלי
לקחת אותה עבודה פעמיים.

מצבים
-----
    queued -> running -> done
                      -> rejected   (הקובץ עצמו בעייתי: פצצה, פגום, לא clean.
                                     אין טעם לנסות שוב.)
                      -> queued     (תקלה זמנית: timeout, קריסה. ניסיון נוסף
                                     בהשהיה גדלה, עד max_attempts)
                      -> failed     (נגמרו הניסיונות)

הכלל שאינו בקוד בלבד: המסד מסרב להכניס לתור או להריץ קובץ שאינו
clean (טריגר בסכמה, סעיף 10).

טקסט ועובדות נשמרים כאן מוצפנים בלבד (crypto, ייעוד "text"), ו-
read_pages מפענח אותם רק לקורא שכבר עבר require_medical.
"""

import datetime
import json

import psycopg

from . import crypto
from .db.pool import cursor

# קודים שמשמעותם "הקובץ בעייתי" - סופי, בלי ניסיון חוזר.
REJECT_CODES = frozenset({
    "corrupt_pdf", "empty_pdf", "too_many_pages", "page_too_large",
    "animated_image", "image_too_large", "corrupt_image", "unsupported_type",
    "input_too_large", "memory_limit", "native_fault", "not_clean",
})

STALE_AFTER = datetime.timedelta(minutes=10)


def backoff(attempt: int) -> datetime.timedelta:
    return datetime.timedelta(minutes=2 ** attempt)        # 2, 4, 8 ...


def enqueue(cur, file_id, firm_id) -> bool:
    """
    מכניס לתור בתוך הטרנזקציה של הקורא. True אם נוצרה עבודה חדשה.
    קובץ שאינו clean נדחה במסד עצמו (check_violation).
    """
    cur.execute(
        """insert into document_processing (firm_id, file_id)
           values (%s, %s) on conflict (file_id) do nothing returning id""",
        (firm_id, file_id))
    return cur.fetchone() is not None


def enqueue_missing(limit=1000) -> int:
    """כל קובץ clean שאין לו עבודה - למשל קבצים שנסרקו לפני שלב 2."""
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into document_processing (firm_id, file_id)
               select f.firm_id, f.id from document_files f
                where f.scan_status = 'clean'
                  and not exists (select 1 from document_processing p where p.file_id = f.id)
                limit %s
               on conflict (file_id) do nothing""", (limit,))
        return cur.rowcount


def claim(worker_id: str, file_ids=None):
    """
    לוקח עבודה אחת מוכנה, או None. ה-attempts עולה כאן ולא בסוף,
    כדי שעבודה שמפילה את ה-worker תיספר.

    file_ids מצמצם לקבצים מסוימים - לעיבוד חוזר יזום ולבדיקות.

    אם הטריגר מסרב (הקובץ כבר אינו clean), העבודה נסגרת כ-rejected
    והחיפוש ממשיך. בלי זה, עבודה אחת כזו בראש התור הייתה נלקחת
    שוב ושוב ותוקעת את כל התור.
    """
    while True:
        refused = None
        try:
            with cursor(commit=True) as cur:
                cur.execute(
                    """select id from document_processing
                        where status = 'queued' and next_attempt_at <= now()
                          and (%s::uuid[] is null or file_id = any(%s::uuid[]))
                        order by next_attempt_at
                        for update skip locked limit 1""",
                    (file_ids, file_ids))
                row = cur.fetchone()
                if row is None:
                    return None
                refused = row["id"]
                cur.execute(
                    """update document_processing
                          set status = 'running', locked_by = %s, locked_at = now(),
                              started_at = now(), attempts = attempts + 1
                        where id = %s
                    returning id, file_id, firm_id, attempts, max_attempts""",
                    (worker_id, row["id"]))
                return cur.fetchone()
        except psycopg.errors.CheckViolation:
            with cursor(commit=True) as cur:
                cur.execute(
                    """update document_processing
                          set status = 'rejected', error_code = 'not_clean',
                              finished_at = now()
                        where id = %s and status = 'queued'""", (refused,))


def complete(job_id, result: dict, duration_ms: int):
    pages = result.get("pages", [])
    with cursor(commit=True) as cur:
        cur.execute(
            """update document_processing
                  set status = 'done', finished_at = now(), locked_by = null,
                      locked_at = null, error_code = null, duration_ms = %s,
                      page_count = %s, text_layer_pages = %s, ocr_needed_pages = %s,
                      engine = %s
                where id = %s and status = 'running'""",
            (duration_ms, len(pages), sum(1 for p in pages if not p["needs_ocr"]),
             sum(1 for p in pages if p["needs_ocr"]),
             json.dumps(result.get("engine", {})), job_id))


def fail(job, code: str, duration_ms: int):
    """rejected לקובץ בעייתי; אחרת ניסיון חוזר או failed."""
    if code in REJECT_CODES:
        status, next_at = "rejected", None
    elif job["attempts"] >= job["max_attempts"]:
        status, next_at = "failed", None
    else:
        status, next_at = "queued", datetime.datetime.now(datetime.timezone.utc) \
            + backoff(job["attempts"])
    with cursor(commit=True) as cur:
        cur.execute(
            """update document_processing
                  set status = %s, error_code = %s, duration_ms = %s,
                      locked_by = null, locked_at = null,
                      next_attempt_at = coalesce(%s, next_attempt_at),
                      finished_at = case when %s in ('rejected', 'failed') then now() end
                where id = %s and status = 'running'""",
            (status, code, duration_ms, next_at, status, job["id"]))
    return status


def recover_stale(older_than=STALE_AFTER) -> int:
    """
    עבודות "running" שה-worker שלהן מת (אין עדכון מעל 10 דקות) חוזרות
    לתור - או ל-failed אם נגמרו הניסיונות.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            """update document_processing
                  set status = case when attempts >= max_attempts then 'failed' else 'queued' end,
                      error_code = 'worker_lost', locked_by = null, locked_at = null,
                      finished_at = case when attempts >= max_attempts then now() end
                where status = 'running' and locked_at < now() - %s""",
            (older_than,))
        return cur.rowcount


# ----------------------------------------------------------------
#  טקסט מוצפן
# ----------------------------------------------------------------

def store_page_text(cur, *, firm_id, file_id, page_no, text, source,
                    ocr_confidence=None, model="layer", layout=None):
    """
    שומר טקסט עמוד מוצפן, ואת הפריסה שלו (layout) מוצפנת בנפרד.
    מזהה הרשומה נוצר כאן כדי שישמש גם ב-AAD: צופן שיועתק לעמוד אחר
    או לקובץ אחר לא יפוענח.
    """
    cur.execute("select gen_random_uuid() as id")
    page_id = str(cur.fetchone()["id"])
    blob = crypto.encrypt(text.encode("utf-8"), purpose="text", firm_id=str(firm_id),
                          record_id="page:" + page_id)
    layout_blob = None
    if layout is not None:
        layout_blob = crypto.encrypt(json.dumps(layout).encode("utf-8"), purpose="text",
                                     firm_id=str(firm_id), record_id="layout:" + page_id)
    cur.execute(
        """insert into document_pages (id, firm_id, file_id, page_no, source, model,
                                       ocr_confidence, char_count, text_enc, layout_enc)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (page_id, firm_id, file_id, page_no, source, model, ocr_confidence, len(text),
         blob, layout_blob))
    return page_id


def replace_pages(file_id, firm_id, pages):
    """
    כל עמודי הקובץ בטרנזקציה אחת: עיבוד חוזר מחליף, ואין מצב ביניים
    שבו חלק מהעמודים חדשים וחלק ישנים.
    """
    with cursor(commit=True) as cur:
        cur.execute("delete from document_pages where file_id = %s and firm_id = %s",
                    (file_id, firm_id))
        for p in pages:
            store_page_text(cur, firm_id=firm_id, file_id=file_id, page_no=p["page"],
                            text=p["text"], source=p["source"], model=p["model"],
                            ocr_confidence=p.get("confidence"),
                            layout={"segments": p.get("segments", []),
                                    "alternatives": p.get("alternatives", []),
                                    "icd": p.get("icd", []), "size": p.get("size"),
                                    "fixes": p.get("fixes", []),
                                    "flattened": p.get("flattened", False)})


def read_pages(firm_id, file_id):
    """
    מפענח את עמודי הקובץ. הקורא אחראי להרשאה ולרישום ביומן.

    requiresHumanVerification: עמוד שנקרא ב-OCR. כל מספר בו - אחוז,
    סכום, תאריך, מספר זהות - הוא קריאה ולא עובדה (25% נקרא 75%
    ב-spike). שלב 5 לא יאפשר להעביר מספר כזה לתיק בלי אישור אדם.
    """
    with cursor() as cur:
        cur.execute(
            """select id, page_no, source, model, ocr_confidence, text_enc, layout_enc
                 from document_pages where file_id = %s and firm_id = %s
                order by page_no""", (file_id, firm_id))
        rows = cur.fetchall()
    out = []
    for r in rows:
        layout = None
        if r["layout_enc"] is not None:
            layout = json.loads(crypto.decrypt(
                bytes(r["layout_enc"]), purpose="text", firm_id=str(firm_id),
                record_id="layout:" + str(r["id"])))
        out.append({
            "page": r["page_no"], "source": r["source"], "model": r["model"],
            "ocrConfidence": (float(r["ocr_confidence"])
                              if r["ocr_confidence"] is not None else None),
            "requiresHumanVerification": r["source"] == "ocr",
            "text": crypto.decrypt(bytes(r["text_enc"]), purpose="text",
                                   firm_id=str(firm_id),
                                   record_id="page:" + str(r["id"])).decode("utf-8"),
            "segments": (layout or {}).get("segments", []),
            # קריאה שנייה (best) של שורה חלשה. לא הוחלפה - שלב 5 יסמן
            # מספר ששתי הקריאות חלוקות עליו.
            "alternatives": (layout or {}).get("alternatives", []),
        })
    return out
