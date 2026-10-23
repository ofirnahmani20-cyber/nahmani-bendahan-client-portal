"""
api_mailbox.py - מה שהגיע במייל, ושיוכו לתיק (docs/10, v1 סעיף 4).

    GET  /api/office/mailbox                 פריטים שממתינים לשיוך
    GET  /api/office/mailbox/{id}/file       צפייה בקובץ (אחרי סריקה)
    POST /api/office/mailbox/{id}/assign     שיוך לדרישת מסמך בתיק
    POST /api/office/mailbox/{id}/discard    לא רלוונטי - נמחק

היקף: פריט שהמערכת התאימה לתיק - גלוי למי שרואה את התיק. פריט בלי
תיק מוצע - למנהל בלבד, כמו תיק בלי אחראי. השיוך עצמו בודק את התיק
של הדרישה שנבחרה, לא את ההצעה: מותר לתקן הצעה שגויה.

השיוך יוצר document_files (source='email') על אותו קובץ מוצפן - בלי
העתקה - ומכניס אותו לאותו מסלול של העלאה מהפורטל: "ממתין לבדיקה",
עיבוד, וגם ההחלטה האוטומטית.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import audit, processing, scan, storage
from .auth import case_scope, require_csrf, require_staff, sees_all_cases
from .db.pool import cursor

router = APIRouter()


def _visible(identity, alias="c"):
    """פריט עם תיק מוצע בהיקף, או - למנהל - גם בלי תיק מוצע."""
    if sees_all_cases(identity):
        return "true"
    return "(%s.id is not null and %s)" % (alias, case_scope(identity, alias))


def _item(cur, item_id, identity, lock=False):
    cur.execute(
        """select i.* from intake_items i
             left join cases c on c.id = i.matched_case_id and c.firm_id = i.firm_id
            where i.id = %s and i.firm_id = %s and """ + _visible(identity)
        + (" for update of i" if lock else ""),
        (item_id, identity.firm_id))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="הפריט לא נמצא.")
    return row


@router.get("/api/office/mailbox")
def mailbox(request: Request, identity=Depends(require_staff)):
    with cursor() as cur:
        cur.execute(
            """select i.id, i.received_at, i.from_address, i.original_filename,
                      i.mime_type, i.size_bytes, i.scan_status,
                      c.id as case_id, c.case_number, cl.full_name as client_name
                 from intake_items i
                 left join cases c on c.id = i.matched_case_id and c.firm_id = i.firm_id
                 left join clients cl on cl.id = i.matched_client_id
                where i.firm_id = %s and i.status = 'new' and """ + _visible(identity) + """
                order by i.received_at""",
            (identity.firm_id,))
        rows = cur.fetchall()
    return {"items": [{
        "id": str(r["id"]),
        "at": r["received_at"].isoformat(),
        "from": r["from_address"],
        "name": r["original_filename"],
        "mime": r["mime_type"],
        "size": r["size_bytes"],
        "scan": r["scan_status"],
        "ready": scan.usable(r["scan_status"]),
        # הצעה בלבד - ראו ראש הקובץ
        "caseId": str(r["case_id"]) if r["case_id"] else None,
        "caseNumber": r["case_number"],
        "clientName": r["client_name"],
    } for r in rows]}


@router.get("/api/office/mailbox/{item_id}/file")
def mailbox_file(item_id: str, request: Request, identity=Depends(require_staff)):
    with cursor() as cur:
        item = _item(cur, item_id, identity)
    if item["status"] == "discarded":
        raise HTTPException(status_code=404, detail="הפריט לא נמצא.")
    if not scan.usable(item["scan_status"]):
        raise HTTPException(status_code=409,
                            detail="הקובץ טרם עבר סריקת אבטחה ואינו זמין לצפייה.")
    audit.record(identity, "office.mailbox_file_viewed", entity_type="intake",
                 entity_id=item_id, request=request,
                 metadata={"mime_type": item["mime_type"]})
    return Response(content=storage.read(item["storage_key"]), media_type=item["mime_type"],
                    headers={"Content-Disposition": storage.content_disposition(
                                 item["original_filename"] or "file"),
                             "X-Content-Type-Options": "nosniff",
                             "Cache-Control": "no-store"})


class Assign(BaseModel):
    document_id: str


@router.post("/api/office/mailbox/{item_id}/assign")
def assign(item_id: str, body: Assign, request: Request, identity=Depends(require_staff)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        item = _item(cur, item_id, identity, lock=True)
        if item["status"] != "new":
            raise HTTPException(status_code=409, detail="הפריט כבר טופל.")
        # הדרישה שנבחרה - בהיקף של מי שמשייך, בלי קשר להצעה
        cur.execute(
            """select d.id, d.case_id from case_documents d
                 join cases c on c.id = d.case_id and c.firm_id = d.firm_id
                where d.id = %s and d.firm_id = %s and d.status <> 'cancelled'
                  and """ + case_scope(identity),
            (body.document_id, identity.firm_id))
        doc = cur.fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="הדרישה לא נמצאה.")

        cur.execute("update document_files set is_current = false where document_id = %s",
                    (doc["id"],))
        cur.execute(
            """insert into document_files
                 (firm_id, document_id, storage_key, original_filename, mime_type,
                  size_bytes, checksum, scan_status, source)
               values (%s, %s, %s, %s, %s, %s, %s, %s, 'email') returning id""",
            (identity.firm_id, doc["id"], item["storage_key"], item["original_filename"],
             item["mime_type"], item["size_bytes"], item["checksum"], item["scan_status"]))
        file_id = cur.fetchone()["id"]
        cur.execute("update case_documents set status = 'pending_review' where id = %s",
                    (doc["id"],))
        if scan.usable(item["scan_status"]):
            processing.enqueue(cur, file_id, identity.firm_id)
        cur.execute(
            """update intake_items set status = 'assigned', assigned_file_id = %s,
                      handled_by_user_id = %s, handled_at = now()
                where id = %s""",
            (file_id, identity.subject_id, item_id))

    audit.record(identity, "office.mailbox_assigned", entity_type="document",
                 entity_id=str(doc["id"]), case_id=str(doc["case_id"]), request=request,
                 metadata={"file_id": str(file_id),
                           "matched": str(item["matched_case_id"]) == str(doc["case_id"])})
    return {"ok": True, "fileId": str(file_id), "caseId": str(doc["case_id"])}


@router.post("/api/office/mailbox/{item_id}/discard")
def discard(item_id: str, request: Request, identity=Depends(require_staff)):
    """לא רלוונטי (חתימה, לוגו, מסמך של מישהו אחר) - הקובץ נמחק, השורה נשארת לתיעוד."""
    require_csrf(request)
    with cursor(commit=True) as cur:
        item = _item(cur, item_id, identity, lock=True)
        if item["status"] != "new":
            raise HTTPException(status_code=409, detail="הפריט כבר טופל.")
        cur.execute(
            """update intake_items set status = 'discarded',
                      handled_by_user_id = %s, handled_at = now()
                where id = %s""",
            (identity.subject_id, item_id))
    storage.delete(item["storage_key"])
    audit.record(identity, "office.mailbox_discarded", entity_type="intake",
                 entity_id=item_id, request=request)
    return {"ok": True}
