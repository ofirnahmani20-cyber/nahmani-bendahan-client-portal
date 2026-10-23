"""
api_binder.py - PDF נקי לקובץ, וקלסר להגשה לתיק (docs/10, v1 סעיף 4).

    GET /api/office/documents/{document_id}/files/{file_id}/clean.pdf
    GET /api/office/cases/{case_id}/binder.pdf

שניהם נבנים מהעמודים הנקיים (document_clean_pages) ב-pdfbuild.py,
בלי לפענח את הקובץ המקורי ובלי parser של תמונות בתהליך השרת.

הקלסר: דף שער עם תוכן עניינים, ואחריו כל המסמכים *המאושרים* של התיק
לפי סדר הדרישות, עם מספור עמודים רץ. מסמך מאושר שאין לו עותק נקי
(עוד לא עובד, או שהעיבוד נכשל) - מופיע בתוכן העניינים עם הסבר, ולא
נעלם בשקט: קלסר שחסר בו מסמך בלי שאיש יודע הוא בדיוק מה שאסור להגיש.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import audit, pdfbuild, storage
from .auth import case_scope, require_staff
from .db.pool import cursor

router = APIRouter()

MAX_BINDER_PAGES = 400


def _pdf_response(data, filename):
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": storage.content_disposition(filename),
        "X-Content-Type-Options": "nosniff",
        # מידע רפואי: לא נשמר במטמון של דפדפן או של שרת ביניים
        "Cache-Control": "no-store",
    })


@router.get("/api/office/documents/{document_id}/files/{file_id}/clean.pdf")
def clean_pdf(document_id: str, file_id: str, request: Request,
              identity=Depends(require_staff)):
    with cursor() as cur:
        cur.execute(
            """select f.original_filename, d.case_id
                 from document_files f
                 join case_documents d on d.id = f.document_id and d.firm_id = f.firm_id
                 join cases c on c.id = d.case_id and c.firm_id = d.firm_id
                where f.id = %s and f.document_id = %s and f.firm_id = %s
                  and """ + case_scope(identity),
            (file_id, document_id, identity.firm_id))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="הקובץ לא נמצא.")
        cur.execute("""select storage_key from document_clean_pages
                        where file_id = %s and firm_id = %s order by page_no""",
                    (file_id, identity.firm_id))
        keys = [r["storage_key"] for r in cur.fetchall()]

    if not keys:
        raise HTTPException(status_code=409,
                            detail="אין עדיין עותק נקי לקובץ הזה. הוא נוצר אחרי עיבוד הקובץ.")

    data = pdfbuild.build([storage.read(k) for k in keys])
    audit.record(identity, "office.clean_pdf_downloaded", entity_type="document",
                 entity_id=document_id, case_id=str(row["case_id"]), request=request,
                 metadata={"file_id": file_id, "pages": len(keys)})
    base = (row["original_filename"] or "document").rsplit(".", 1)[0]
    return _pdf_response(data, base + " - נקי.pdf")


@router.get("/api/office/cases/{case_id}/binder.pdf")
def binder_pdf(case_id: str, request: Request, identity=Depends(require_staff)):
    with cursor() as cur:
        cur.execute(
            """select c.case_number, cl.full_name, ct.name as claim_type
                 from cases c
                 join clients cl on cl.id = c.client_id
                 join claim_types ct on ct.id = c.claim_type_id
                where c.id = %s and c.firm_id = %s and """ + case_scope(identity),
            (case_id, identity.firm_id))
        case = cur.fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="התיק לא נמצא.")

        cur.execute(
            """select d.id, d.name,
                      coalesce(array_agg(p.storage_key order by p.page_no)
                               filter (where p.storage_key is not null), '{}') as keys
                 from case_documents d
                 left join document_files f on f.document_id = d.id
                                           and f.firm_id = d.firm_id and f.is_current
                 left join document_clean_pages p on p.file_id = f.id
                where d.case_id = %s and d.firm_id = %s and d.status = 'approved'
                group by d.id, d.name, d.position
                order by d.position""",
            (case_id, identity.firm_id))
        docs = cur.fetchall()

    if not docs:
        raise HTTPException(status_code=409, detail="אין בתיק מסמכים מאושרים להכניס לקלסר.")

    total = sum(len(d["keys"]) for d in docs)
    if total + 1 > MAX_BINDER_PAGES:
        raise HTTPException(status_code=413,
                            detail="הקלסר ארוך מ-%d עמודים. יש לפצל אותו." % MAX_BINDER_PAGES)

    # עמוד 1 הוא השער; המסמכים מתחילים בעמוד 2.
    rows, page, missing = [], 2, 0
    for d in docs:
        n = len(d["keys"])
        if n == 0:
            rows.append((d["name"], "לא נכלל - אין עותק נקי"))
            missing += 1
            continue
        rows.append((d["name"], str(page) if n == 1 else "%d-%d" % (page, page + n - 1)))
        page += n

    cover = pdfbuild.binder_cover(
        ["קלסר מסמכים - תיק %s" % case["case_number"],
         "%s · %s" % (case["full_name"], case["claim_type"]),
         "הופק ב-%s" % date.today().strftime("%d.%m.%Y")],
        rows)
    pages = [cover] + [storage.read(k) for d in docs for k in d["keys"]]
    data = pdfbuild.build(pages)

    audit.record(identity, "office.binder_downloaded", entity_type="case",
                 entity_id=case_id, case_id=case_id, request=request,
                 metadata={"documents": len(docs), "pages": len(pages),
                           "missing_clean_copy": missing})
    return _pdf_response(data, "קלסר %s.pdf" % case["case_number"])
