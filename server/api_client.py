"""
api_client.py - נתוני הלקוח.

הכלל היחיד שחשוב כאן
---------------------
התיק נגזר מה-session, לא מהבקשה. אין פרמטר client_id ואין
firm_id בגוף. לקוח שישנה URL, ישלח בקשה ידנית או יערוך
JavaScript - יקבל את התיקים שלו בלבד, כי השאילתה לעולם אינה
מקבלת מזהה מהדפדפן.

לפני 13.09 CaseStore.load(idNumber) בדפדפן קיבל כל ת"ז והחזיר
תיק מלא, גם בלי התחברות.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import audit, scan, storage
from .auth import require_client, require_csrf
from .db.pool import cursor

router = APIRouter()

# מיפוי סטטוס המסד לערכים שהממשק הקיים מכיר, כדי שהפרונט
# לא יצטרך להשתנות באותה נשימה שבה מתחלף מקור הנתונים.
STATUS_TO_UI = {
    "missing": "missing",
    "pending_review": "pending-review",
    "approved": "approved",
    "rejected": "rejected",
    "cancelled": "cancelled",
}


def _case_row(cur, case_id, identity):
    """
    שולף תיק *בתוך* גבולות הזהות.

    firm_id ו-client_id באים מה-session. אם התיק שייך למישהו אחר
    השאילתה פשוט לא מחזירה שורה, ואין הבדל בין "לא קיים" ל"לא
    שלך" - זה מונע דליפת קיום.
    """
    cur.execute(
        """select c.id, c.case_number, c.branch, c.opened_at, c.next_hearing_at,
                  c.status, ct.name as claim_type,
                  st.stage_position, st.stage_title, st.stage_description,
                  st.in_stage_since,
                  (select count(*) from stage_templates s
                    where s.claim_type_id = c.claim_type_id) as total_stages
             from cases c
             join claim_types ct on ct.id = c.claim_type_id
             left join case_current_stage st on st.case_id = c.id
            where c.id = %s and c.client_id = %s and c.firm_id = %s""",
        (case_id, identity.subject_id, identity.firm_id),
    )
    return cur.fetchone()


@router.get("/api/client/cases")
def list_cases(request: Request, identity=Depends(require_client)):
    with cursor() as cur:
        cur.execute(
            """select c.id, c.case_number, ct.name as claim_type,
                      st.stage_position, st.stage_title
                 from cases c
                 join claim_types ct on ct.id = c.claim_type_id
                 left join case_current_stage st on st.case_id = c.id
                where c.client_id = %s and c.firm_id = %s
                order by c.opened_at desc""",
            (identity.subject_id, identity.firm_id),
        )
        rows = cur.fetchall()
    return {"cases": [dict(r) for r in rows]}


@router.get("/api/client/cases/{case_id}")
def get_case(case_id: str, request: Request, identity=Depends(require_client)):
    with cursor() as cur:
        case = _case_row(cur, case_id, identity)
        if case is None:
            # 404 ולא 403 - "אין לך גישה" מאשר שהתיק קיים.
            raise HTTPException(status_code=404, detail="התיק לא נמצא.")

        cur.execute(
            """select id, name, guidance, is_required, status, reject_reason,
                      position
                 from case_documents
                where case_id = %s and firm_id = %s
                order by position""",
            (case_id, identity.firm_id),
        )
        documents = cur.fetchall()

        cur.execute(
            """select df.document_id, df.original_filename, df.uploaded_at,
                      df.scan_status
                 from document_files df
                 join case_documents cd on cd.id = df.document_id
                where cd.case_id = %s and df.firm_id = %s and df.is_current
            """,
            (case_id, identity.firm_id),
        )
        files = {str(r["document_id"]): r for r in cur.fetchall()}

        cur.execute(
            """select id, title, body, is_important, sent_at
                 from messages
                where case_id = %s and firm_id = %s
                order by sent_at desc""",
            (case_id, identity.firm_id),
        )
        messages = cur.fetchall()

        cur.execute(
            """select id, kind, title, guidance, due_at, status
                 from case_requirements
                where case_id = %s and firm_id = %s
                  and kind <> 'document'
                  and status in ('open', 'sent')
                order by due_at nulls last, created_at""",
            (case_id, identity.firm_id),
        )
        requirements = cur.fetchall()

        cur.execute(
            """select s.position, s.title, s.description, s.is_terminal,
                      (select max(e.occurred_at)
                         from case_stage_events e
                        where e.stage_template_id = s.id
                          and e.case_id = c.id) as occurred_at
                 from stage_templates s
                 join cases c on c.claim_type_id = s.claim_type_id
                where c.id = %s
                order by s.position""",
            (case_id,),
        )
        stages = cur.fetchall()

        # ההחלטה האחרונה. ההיסטוריה נשמרת, אך ללקוח מוצגת
        # ההחלטה העדכנית - היא זו שממנה נגזר מה עליו לעשות.
        cur.execute(
            """select decided_at, outcome, percent, is_permanent,
                      appeal_deadline, office_note
                 from case_decisions
                where case_id = %s and firm_id = %s
                order by decided_at desc, created_at desc limit 1""",
            (case_id, identity.firm_id),
        )
        decision = cur.fetchone()

        cur.execute(
            """select r.document_id, r.kind, r.text, r.created_at
                 from document_replies r
                 join case_documents d on d.id = r.document_id
                where d.case_id = %s and r.firm_id = %s
                order by r.created_at desc""",
            (case_id, identity.firm_id),
        )
        replies = cur.fetchall()

    audit.record(identity, "client.case_viewed", entity_type="case",
                 entity_id=case_id, case_id=case_id, request=request)

    docs_out = []
    for d in documents:
        # דרישה שהמשרד סגר אינה מוצגת ללקוח: היא כבר לא נדרשת
        # ממנו, והצגתה הייתה רק מבלבלת. השורה נשמרת במסד.
        if d["status"] == "cancelled":
            continue
        f = files.get(str(d["id"]))
        docs_out.append({
            "id": str(d["id"]),
            "name": d["name"],
            "note": d["guidance"],
            "required": d["is_required"],
            "status": STATUS_TO_UI.get(d["status"], d["status"]),
            "rejectReason": d["reject_reason"],
            # שם הקובץ מוצג ללקוח שהעלה אותו; scan_status קובע אם
            # ניתן יהיה להוריד אותו בהמשך.
            "file": f["original_filename"] if f else None,
            "date": f["uploaded_at"].date().isoformat() if f else None,
            "scanStatus": f["scan_status"] if f else None,
        })

    return {
        "id": str(case["id"]),
        "caseNumber": case["case_number"],
        "claimType": case["claim_type"],
        "branch": case["branch"],
        "openedAt": case["opened_at"].isoformat() if case["opened_at"] else None,
        "nextHearing": (case["next_hearing_at"].date().isoformat()
                        if case["next_hearing_at"] else None),
        "currentStage": case["stage_position"],
        "totalStages": case["total_stages"],
        "stageTitle": case["stage_title"],
        "stageEnteredAt": (case["in_stage_since"].date().isoformat()
                           if case["in_stage_since"] else None),
        "documents": docs_out,
        "messages": [{
            "id": str(m["id"]), "title": m["title"], "body": m["body"],
            "important": m["is_important"],
            "date": m["sent_at"].date().isoformat(),
        } for m in messages],
        # דרישות שאינן מסמך. דרישת מסמך כבר מוצגת ברשימת
        # המסמכים, ולכן היא מסוננת כאן - אחרת אותו דבר היה
        # מופיע פעמיים באותו מסך.
        "requirements": [{
            "id": str(q["id"]), "kind": q["kind"], "title": q["title"],
            "guidance": q["guidance"],
            "dueAt": q["due_at"].isoformat() if q["due_at"] else None,
            "status": q["status"],
        } for q in requirements],
        "stages": [{
            "position": s["position"], "title": s["title"],
            "desc": s["description"], "isTerminal": s["is_terminal"],
            "occurredAt": s["occurred_at"].date().isoformat() if s["occurred_at"] else None,
        } for s in stages],
        "decision": ({
            "date": decision["decided_at"].isoformat(),
            "outcome": decision["outcome"],
            "percent": decision["percent"],
            "permanent": decision["is_permanent"],
            "appealDeadline": (decision["appeal_deadline"].isoformat()
                               if decision["appeal_deadline"] else None),
            "officeNote": decision["office_note"],
        } if decision else None),
        "clientReplies": [{
            "documentId": str(r["document_id"]), "kind": r["kind"],
            "text": r["text"], "date": r["created_at"].date().isoformat(),
        } for r in replies],
    }


# ================================================================
#  העלאת מסמך
# ================================================================

@router.post("/api/client/documents/{document_id}/files")
async def upload_file(document_id: str, request: Request,
                      file: UploadFile = File(...),
                      identity=Depends(require_client)):
    """
    מקבל קובץ מהלקוח.

    סדר הפעולות מכוון: קודם הרשאה, אחר כך זמינות סורק, ורק
    בסוף קריאת הקובץ. אין טעם לקרוא 12MB לזיכרון לפני שברור
    שמותר לקבל אותם.
    """
    require_csrf(request)

    try:
        scan.assert_upload_allowed()
    except scan.ScannerNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    with cursor() as cur:
        # המסמך חייב להיות של תיק ששייך ללקוח המחובר.
        cur.execute(
            """select d.id, d.case_id, d.name
                 from case_documents d
                 join cases c on c.id = d.case_id
                where d.id = %s and c.client_id = %s and d.firm_id = %s""",
            (document_id, identity.subject_id, identity.firm_id),
        )
        doc = cur.fetchone()
    if doc is None:
        raise HTTPException(status_code=404, detail="המסמך לא נמצא.")

    data = await file.read()
    try:
        meta = storage.validate_and_store(
            data, firm_id=identity.firm_id, case_id=doc["case_id"],
            original_name=file.filename,
        )
    except storage.RejectedFile as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = scan.get_scanner().scan(data)

    with cursor(commit=True) as cur:
        # גרסה חדשה מחליפה את הקודמת כנוכחית, אך ההיסטוריה נשמרת.
        cur.execute(
            "update document_files set is_current = false where document_id = %s",
            (document_id,),
        )
        cur.execute(
            """insert into document_files
                 (firm_id, document_id, storage_key, original_filename,
                  mime_type, size_bytes, checksum, scan_status,
                  uploaded_by_client_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, document_id, meta["storage_key"],
             meta["original_filename"], meta["mime_type"], meta["size_bytes"],
             meta["checksum"], result.status, identity.subject_id),
        )
        file_id = cur.fetchone()["id"]
        cur.execute(
            "update case_documents set status = 'pending_review' where id = %s",
            (document_id,),
        )

    audit.record(identity, "client.file_uploaded", entity_type="document",
                 entity_id=document_id, case_id=str(doc["case_id"]),
                 request=request,
                 metadata={"mime_type": meta["mime_type"],
                           "size_bytes": meta["size_bytes"],
                           "scan_status": result.status})

    return {
        "ok": True,
        "fileId": str(file_id),
        "filename": meta["original_filename"],
        "scanStatus": result.status,
        # הלקוח צריך לדעת שהקובץ התקבל אך טרם נסרק, כדי שלא
        # יופתע מכך שאינו יכול לפתוח אותו.
        "note": result.detail,
    }


@router.get("/api/client/documents/{document_id}/files/{file_id}")
def download_file(document_id: str, file_id: str, request: Request,
                  identity=Depends(require_client)):
    """
    הורדה. שתי בדיקות לפני שבייט אחד יוצא: בעלות וסטטוס סריקה.
    """
    with cursor() as cur:
        cur.execute(
            """select f.storage_key, f.original_filename, f.mime_type,
                      f.scan_status, d.case_id
                 from document_files f
                 join case_documents d on d.id = f.document_id
                 join cases c on c.id = d.case_id
                where f.id = %s and f.document_id = %s
                  and c.client_id = %s and f.firm_id = %s""",
            (file_id, document_id, identity.subject_id, identity.firm_id),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="הקובץ לא נמצא.")

    if not scan.downloadable(row["scan_status"]):
        # 409 ולא 403: הקובץ שלך, פשוט עדיין לא נסרק.
        raise HTTPException(
            status_code=409,
            detail="הקובץ טרם עבר סריקת אבטחה ואינו זמין להורדה.",
        )

    audit.record(identity, "client.file_downloaded", entity_type="document",
                 entity_id=document_id, case_id=str(row["case_id"]),
                 request=request)

    return Response(
        content=storage.read(row["storage_key"]),
        media_type=row["mime_type"],
        headers={
            # attachment + nosniff: הדפדפן לא ינחש סוג ולא יריץ
            # תוכן שהועלה כאילו הוא חלק מהאתר.
            "Content-Disposition": 'attachment; filename="%s"'
                                   % row["original_filename"],
            "X-Content-Type-Options": "nosniff",
        },
    )


# ================================================================
#  תגובת לקוח למסמך
# ================================================================

REPLY_KINDS = {
    "dont-have":   "אין לי את המסמך",
    "need-help":   "צריך עזרה בהשגתו",
    "sent-mail":   "שלחתי בדואר",
    "gave-office": "כבר מסרתי למשרד",
}


class DocumentReply(BaseModel):
    kind: str
    text: str | None = Field(default=None, max_length=1000)


@router.post("/api/client/documents/{document_id}/replies")
def add_reply(document_id: str, body: DocumentReply, request: Request,
              identity=Depends(require_client)):
    """
    תגובת לקוח על מסמך שביקשו ממנו.

    המסמך נבדק מול התיק של הלקוח המחובר: client_id מגיע מה-session
    ולא מהבקשה, ולכן אי אפשר להגיב על מסמך של מישהו אחר.
    """
    require_csrf(request)

    if body.kind not in REPLY_KINDS:
        raise HTTPException(status_code=400, detail="סוג תגובה לא מוכר.")

    with cursor(commit=True) as cur:
        cur.execute(
            """select d.id, d.case_id
                 from case_documents d
                 join cases c on c.id = d.case_id
                where d.id = %s and c.client_id = %s and d.firm_id = %s""",
            (document_id, identity.subject_id, identity.firm_id),
        )
        doc = cur.fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="המסמך לא נמצא.")

        cur.execute(
            """insert into document_replies (firm_id, document_id, kind, text)
               values (%s, %s, %s, %s) returning id""",
            (identity.firm_id, document_id, body.kind,
             (body.text or "").strip() or None),
        )
        reply_id = cur.fetchone()["id"]

    audit.record(identity, "client.document_replied", entity_type="document",
                 entity_id=document_id, case_id=str(doc["case_id"]),
                 request=request)
    return {"ok": True, "replyId": str(reply_id)}
