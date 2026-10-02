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
from pydantic import BaseModel, Field

from . import audit, doc_taxonomy, processing, scan
from .auth import require_csrf, require_medical, require_staff
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


# ================================================================
#  סיווג (שלב 4)
# ================================================================
#  הכול מאחורי require_medical: גם סוג המסמך הוא מידע רפואי ("חוות
#  דעת פסיכיאטרית"). הנימוק מכיל מזהי כללים וביטויים מהמילון בלבד -
#  לא טקסט מהמסמך.
#
#  אישור סיווג אינו מאשר את המסמך ואינו משלים את הדרישה. אלה פעולות
#  נפרדות (POST .../documents/{id}/review), וכאן אין גישה אליהן.
# ================================================================


def _kind_view(code):
    if not code:
        return None
    row = doc_taxonomy.ALL.get(code, {})
    return {"code": code, "label": doc_taxonomy.label(code),
            "category": doc_taxonomy.CATEGORY_LABELS.get(row.get("category"), "")}


@router.get("/api/office/document-kinds")
def document_kinds(identity=Depends(require_medical)):
    return {"version": doc_taxonomy.TAXONOMY_VERSION,
            "categories": [{"code": c, "label": l} for c, l in doc_taxonomy.CATEGORIES],
            "kinds": [dict(_kind_view(code), parent=row["parent"])
                      for code, row in doc_taxonomy.ALL.items()]}


@router.get("/api/office/documents/{document_id}/files/{file_id}/classification")
def classification(document_id: str, file_id: str, request: Request,
                   identity=Depends(require_medical)):
    with cursor() as cur:
        row = _file(cur, identity, document_id, file_id)
        cur.execute("""select c.*, d.accepted_kinds
                         from document_classifications c
                         join document_files f on f.id = c.file_id and f.firm_id = c.firm_id
                         join case_documents d on d.id = f.document_id and d.firm_id = f.firm_id
                        where c.file_id = %s and c.firm_id = %s""", (file_id, identity.firm_id))
        c = cur.fetchone()

    audit.record(identity, "office.classification_viewed", entity_type="document",
                 entity_id=document_id, case_id=str(row["case_id"]), request=request,
                 metadata={"file_id": str(file_id)})
    if c is None:
        return {"status": "none"}

    accepted = list(c["accepted_kinds"])
    effective = c["confirmed_type"] if c["status"] == "confirmed" else c["suggested_type"]
    return {
        "status": c["status"],                      # suggested | confirmed | rejected
        "decision": c["decision"],                  # clear | ambiguous | unknown | unreadable | mixed
        "suggested": _kind_view(c["suggested_type"]),
        "confirmed": _kind_view(c["confirmed_type"]),
        "differsFromConfirmed": c["differs_from_confirmed"],
        "candidates": [dict(_kind_view(x["kind"]), score=x["score"]) for x in c["candidates"]],
        "reasons": [{"kind": r["kind"], "kindLabel": doc_taxonomy.label(r["kind"]),
                     "rule": r["rule"], "phrase": r["phrase"], "page": r["page"],
                     "line": r["line"], "zone": r["zone"], "weight": r["weight"]}
                    for r in c["reasons"]],
        "pages": [dict(p, kind=_kind_view(p.get("kind"))) for p in c["page_kinds"]],
        "requirement": {
            "accepted": [_kind_view(k) for k in accepted],
            # מחושב עכשיו ולא נלקח מהשורה: הדרישה עשויה להשתנות אחרי הסיווג
            "match": processing.requirement_match(accepted, effective),
        },
        "rulesVersion": c["rules_version"],
        "reviewedAt": c["reviewed_at"].isoformat() if c["reviewed_at"] else None,
    }


class ClassificationReview(BaseModel):
    action: str = Field(pattern="^(confirm|choose|reject)$")
    kind: str | None = Field(default=None, max_length=60)


@router.post("/api/office/documents/{document_id}/files/{file_id}/classification/review")
def review_classification(document_id: str, file_id: str, body: ClassificationReview,
                          request: Request, identity=Depends(require_medical)):
    require_csrf(request)
    with cursor() as cur:
        row = _file(cur, identity, document_id, file_id)
    try:
        status, chosen = processing.review_classification(
            file_id, identity.firm_id, identity.subject_id, body.action, body.kind)
    except LookupError:
        raise HTTPException(status_code=404, detail="אין סיווג לקובץ הזה.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # בלי קוד הסוג. יומן הביקורת פתוח לכל הצוות (require_staff), וקוד
    # כמו "psychiatric" הוא מידע רפואי. הסוג שאושר נשמר ב-
    # document_classifications, מאחורי require_medical. (סקירת אבטחה, 2026-10-02.)
    audit.record(identity, "office.classification_reviewed", entity_type="document",
                 entity_id=document_id, case_id=str(row["case_id"]), request=request,
                 metadata={"file_id": str(file_id), "decision": body.action,
                           "to_status": status})
    return {"ok": True, "status": status, "confirmed": _kind_view(chosen)}
