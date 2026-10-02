"""
api_medical.py - גישה למה שנגזר ממסמך (Layer 2).

שתי רמות, בכוונה:

  /processing   מצב העיבוד: סטטוס, מספר עמודים, קוד שגיאה. אין בו
                תוכן, ולכן כל איש צוות במשרד רואה אותו.
  /pages        הטקסט שחולץ - תוכן רפואי. require_medical (הרשאה
                מפורשת על המשתמש), הקובץ חייב להיות clean, וכל צפייה
                נרשמת ביומן - בלי הטקסט עצמו.

אין כאן נתיב /api/client. הלקוח אינו רואה דבר מהשכבה הזו.
בידוד משרדים: כל שאילתה מסננת לפי firm_id מה-session, ותשובה של
משרד אחר היא 404 ולא 403.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from . import audit, processing, scan
from .auth import require_medical, require_staff
from .db.pool import cursor

router = APIRouter()


def _file(cur, identity, document_id, file_id):
    cur.execute(
        """select f.id, f.scan_status, d.case_id
             from document_files f
             join case_documents d on d.id = f.document_id and d.firm_id = f.firm_id
            where f.id = %s and f.document_id = %s and f.firm_id = %s""",
        (file_id, document_id, identity.firm_id))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="הקובץ לא נמצא.")
    return row


@router.get("/api/office/documents/{document_id}/files/{file_id}/processing")
def processing_status(document_id: str, file_id: str,
                      identity=Depends(require_staff)):
    with cursor() as cur:
        _file(cur, identity, document_id, file_id)
        cur.execute(
            """select status, attempts, error_code, page_count, text_layer_pages,
                      ocr_needed_pages, queued_at, finished_at
                 from document_processing where file_id = %s and firm_id = %s""",
            (file_id, identity.firm_id))
        job = cur.fetchone()
    if job is None:
        return {"status": "none"}
    return {
        "status": job["status"], "attempts": job["attempts"],
        "errorCode": job["error_code"], "pageCount": job["page_count"],
        "textLayerPages": job["text_layer_pages"],
        "ocrNeededPages": job["ocr_needed_pages"],
        "queuedAt": job["queued_at"].isoformat() if job["queued_at"] else None,
        "finishedAt": job["finished_at"].isoformat() if job["finished_at"] else None,
    }


@router.get("/api/office/documents/{document_id}/files/{file_id}/pages")
def file_pages(document_id: str, file_id: str, request: Request,
               identity=Depends(require_medical)):
    with cursor() as cur:
        row = _file(cur, identity, document_id, file_id)

    if not scan.usable(row["scan_status"]):
        raise HTTPException(status_code=409,
                            detail="הקובץ טרם עבר סריקת אבטחה ואינו זמין.")

    pages = processing.read_pages(identity.firm_id, file_id)

    # מי צפה בתוכן רפואי של איזה קובץ ומתי - בלי התוכן.
    audit.record(identity, "office.document_text_viewed", entity_type="document",
                 entity_id=document_id, case_id=str(row["case_id"]), request=request,
                 metadata={"file_id": str(file_id), "page_count": len(pages)})
    return {"pages": pages}
