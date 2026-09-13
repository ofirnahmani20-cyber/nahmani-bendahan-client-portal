"""
api_office.py - ממשק הניהול.

כל שאילתה כאן מסוננת ב-firm_id מה-session. משרד אינו יכול לראות
תיק, לקוח, מסמך או הודעה של משרד אחר, גם אם ינחש מזהה - התנאי
אינו ניתן להשפעה מהבקשה.

לפני 13.09 כל הפעולות האלה כתבו ל-localStorage בדפדפן, כלומר
"אישור מסמך" היה שינוי מקומי שהלקוח לעולם לא ראה.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit
from .auth import require_csrf, require_staff
from .db.pool import cursor

router = APIRouter()


def _owned_case(cur, case_id, identity):
    cur.execute(
        "select id, firm_id, claim_type_id from cases where id = %s and firm_id = %s",
        (case_id, identity.firm_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="התיק לא נמצא.")
    return row


def _owned_document(cur, document_id, identity):
    cur.execute(
        """select id, case_id, firm_id, name, status
             from case_documents where id = %s and firm_id = %s""",
        (document_id, identity.firm_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="המסמך לא נמצא.")
    return row


# ================================================================
#  קריאה
# ================================================================

@router.get("/api/office/cases")
def list_cases(request: Request, identity=Depends(require_staff)):
    with cursor() as cur:
        cur.execute(
            """select c.id, c.case_number, cl.full_name as client_name,
                      ct.name as claim_type, st.stage_position, st.stage_title,
                      (select count(*) from case_documents d
                        where d.case_id = c.id and d.status = 'pending_review')
                        as awaiting_review,
                      (select count(*) from case_documents d
                        where d.case_id = c.id and d.status in ('missing','rejected'))
                        as open_for_client
                 from cases c
                 join clients cl on cl.id = c.client_id
                 join claim_types ct on ct.id = c.claim_type_id
                 left join case_current_stage st on st.case_id = c.id
                where c.firm_id = %s
                order by c.opened_at desc""",
            (identity.firm_id,),
        )
        rows = cur.fetchall()
    return {"cases": [{
        "id": str(r["id"]), "caseNumber": r["case_number"],
        "clientName": r["client_name"], "claimType": r["claim_type"],
        "currentStage": r["stage_position"], "stageTitle": r["stage_title"],
        "awaitingReview": r["awaiting_review"],
        "openForClient": r["open_for_client"],
    } for r in rows]}


@router.get("/api/office/cases/{case_id}")
def get_case(case_id: str, request: Request, identity=Depends(require_staff)):
    with cursor() as cur:
        _owned_case(cur, case_id, identity)
        cur.execute(
            """select c.id, c.case_number, c.branch, c.opened_at, c.next_hearing_at,
                      cl.full_name as client_name, cl.phone, cl.email,
                      ct.name as claim_type, ct.id as claim_type_id,
                      st.stage_position, st.stage_title
                 from cases c
                 join clients cl on cl.id = c.client_id
                 join claim_types ct on ct.id = c.claim_type_id
                 left join case_current_stage st on st.case_id = c.id
                where c.id = %s and c.firm_id = %s""",
            (case_id, identity.firm_id),
        )
        case = cur.fetchone()

        cur.execute(
            """select id, name, guidance, is_required, status, reject_reason, position
                 from case_documents where case_id = %s and firm_id = %s
                order by position""",
            (case_id, identity.firm_id),
        )
        docs = cur.fetchall()

        cur.execute(
            """select s.id, s.position, s.title
                 from stage_templates s where s.claim_type_id = %s
                order by s.position""",
            (case["claim_type_id"],),
        )
        stages = cur.fetchall()

        cur.execute(
            """select decided_at, outcome, percent, is_permanent,
                      appeal_deadline, office_note
                 from case_decisions
                where case_id = %s and firm_id = %s
                order by decided_at desc, created_at desc limit 1""",
            (case_id, identity.firm_id),
        )
        decision = cur.fetchone()

    audit.record(identity, "office.case_viewed", entity_type="case",
                 entity_id=case_id, case_id=case_id, request=request)

    return {
        "id": str(case["id"]), "caseNumber": case["case_number"],
        "clientName": case["client_name"], "phone": case["phone"],
        "email": case["email"], "claimType": case["claim_type"],
        "branch": case["branch"],
        "currentStage": case["stage_position"], "stageTitle": case["stage_title"],
        "documents": [{
            "id": str(d["id"]), "name": d["name"], "note": d["guidance"],
            "required": d["is_required"], "status": d["status"],
            "rejectReason": d["reject_reason"],
        } for d in docs],
        "stageOptions": [{
            "id": str(s["id"]), "position": s["position"], "title": s["title"],
        } for s in stages],
        "decision": ({
            "date": decision["decided_at"].isoformat(),
            "outcome": decision["outcome"],
            "percent": decision["percent"],
        } if decision else None),
    }


# ================================================================
#  כתיבה
# ================================================================

class StageEvent(BaseModel):
    stage_template_id: str
    note: str | None = Field(default=None, max_length=2000)


@router.post("/api/office/cases/{case_id}/stage-events")
def add_stage_event(case_id: str, body: StageEvent, request: Request,
                    identity=Depends(require_staff)):
    """
    שינוי שלב הוא *הוספת אירוע*, לא עדכון שדה. ההיסטוריה נשמרת
    במלואה, ו-case_current_stage גוזרת ממנה את השלב הנוכחי.
    """
    require_csrf(request)
    with cursor(commit=True) as cur:
        case = _owned_case(cur, case_id, identity)

        # השלב חייב להשתייך למסלול של התיק. גם המסד אוכף זאת דרך
        # המפתח הזר המורכב, וזו בדיקה מקדימה עם הודעה ברורה.
        cur.execute(
            "select id, position, title from stage_templates where id = %s and claim_type_id = %s",
            (body.stage_template_id, case["claim_type_id"]),
        )
        stage = cur.fetchone()
        if stage is None:
            raise HTTPException(status_code=400, detail="השלב אינו שייך למסלול של התיק.")

        cur.execute(
            """insert into case_stage_events
                 (firm_id, case_id, stage_template_id, claim_type_id, note,
                  created_by_user_id)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, case_id, stage["id"], case["claim_type_id"],
             body.note, identity.subject_id),
        )
        event_id = cur.fetchone()["id"]

    audit.record(identity, "office.stage_changed", entity_type="case",
                 entity_id=case_id, case_id=case_id, request=request,
                 metadata={"stage_template_id": str(stage["id"]),
                           "to_stage": stage["position"]})
    return {"ok": True, "eventId": str(event_id), "stage": stage["position"]}


class DocumentReview(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reject_reason: str | None = Field(default=None, max_length=1000)


@router.post("/api/office/documents/{document_id}/review")
def review_document(document_id: str, body: DocumentReview, request: Request,
                    identity=Depends(require_staff)):
    require_csrf(request)
    if body.decision == "reject":
        reason = (body.reject_reason or "").strip()
        if len(reason) < 5:
            # המסד אוכף זאת גם הוא (reject_needs_reason); כאן
            # ההודעה ברורה במקום שגיאת אילוץ גולמית.
            raise HTTPException(status_code=400,
                                detail="דחייה מחייבת סיבה שתוצג ללקוח.")

    with cursor(commit=True) as cur:
        doc = _owned_document(cur, document_id, identity)
        new_status = "approved" if body.decision == "approve" else "rejected"
        cur.execute(
            """update case_documents
                  set status = %s,
                      reject_reason = %s,
                      reviewed_by_user_id = %s,
                      reviewed_at = now()
                where id = %s and firm_id = %s""",
            (new_status,
             body.reject_reason if new_status == "rejected" else None,
             identity.subject_id, document_id, identity.firm_id),
        )

    audit.record(identity, "office.document_reviewed", entity_type="document",
                 entity_id=document_id, case_id=str(doc["case_id"]), request=request,
                 metadata={"decision": body.decision,
                           "reason_given": bool(body.reject_reason)})
    return {"ok": True, "status": new_status}


class NewDocument(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    guidance: str = Field(min_length=1, max_length=2000)
    is_required: bool = True


@router.post("/api/office/cases/{case_id}/documents")
def add_document(case_id: str, body: NewDocument, request: Request,
                 identity=Depends(require_staff)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        _owned_case(cur, case_id, identity)
        cur.execute(
            "select coalesce(max(position), 0) + 1 as next from case_documents where case_id = %s",
            (case_id,),
        )
        position = cur.fetchone()["next"]
        cur.execute(
            """insert into case_documents
                 (firm_id, case_id, name, guidance, is_required, position)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, case_id, body.name, body.guidance,
             body.is_required, position),
        )
        document_id = cur.fetchone()["id"]

    audit.record(identity, "office.document_requested", entity_type="document",
                 entity_id=str(document_id), case_id=case_id, request=request)
    return {"ok": True, "documentId": str(document_id)}


class NewMessage(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    is_important: bool = False


@router.post("/api/office/cases/{case_id}/messages")
def add_message(case_id: str, body: NewMessage, request: Request,
                identity=Depends(require_staff)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        _owned_case(cur, case_id, identity)
        cur.execute(
            """insert into messages
                 (firm_id, case_id, title, body, is_important, sent_by_user_id)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, case_id, body.title, body.body,
             body.is_important, identity.subject_id),
        )
        message_id = cur.fetchone()["id"]

    audit.record(identity, "office.message_sent", entity_type="message",
                 entity_id=str(message_id), case_id=case_id, request=request)
    return {"ok": True, "messageId": str(message_id)}


# ================================================================
#  החלטת ועדה
# ================================================================

class Decision(BaseModel):
    decided_at: str
    outcome: str = Field(pattern="^(below-threshold|grant|pension|rejected)$")
    percent: int | None = Field(default=None, ge=0, le=100)
    is_permanent: bool = False
    appeal_deadline: str | None = None
    office_note: str | None = Field(default=None, max_length=2000)


@router.post("/api/office/cases/{case_id}/decisions")
def record_decision(case_id: str, body: Decision, request: Request,
                    identity=Depends(require_staff)):
    """
    רישום החלטה הוא *הוספת שורה*, לא עדכון.

    לתיק יכולות להיות כמה החלטות - ועדה ראשונה, ועדת עררים,
    ועדה חוזרת. דריסה הייתה מוחקת את מה שקדם, ובתיק משפטי זו
    בדיוק ההיסטוריה שצריך לשמור.
    """
    require_csrf(request)
    with cursor(commit=True) as cur:
        _owned_case(cur, case_id, identity)
        cur.execute(
            """insert into case_decisions
                 (firm_id, case_id, decided_at, outcome, percent,
                  is_permanent, appeal_deadline, office_note,
                  recorded_by_user_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, case_id, body.decided_at, body.outcome,
             body.percent, body.is_permanent, body.appeal_deadline,
             body.office_note, identity.subject_id),
        )
        decision_id = cur.fetchone()["id"]

    audit.record(identity, "office.decision_recorded", entity_type="decision",
                 entity_id=str(decision_id), case_id=case_id, request=request,
                 metadata={"outcome": body.outcome})
    return {"ok": True, "decisionId": str(decision_id)}


# ================================================================
#  סגירת דרישת מסמך
# ================================================================

@router.post("/api/office/documents/{document_id}/cancel")
def cancel_document(document_id: str, request: Request,
                    identity=Depends(require_staff)):
    """
    סוגר דרישה בלי למחוק אותה.

    המחיקה הייתה מוחקת גם את document_files שתלויים בשורה
    (ON DELETE CASCADE) - כלומר קובץ שהלקוח כבר העלה. לכן
    הסטטוס עובר ל-cancelled, השורה נשארת, וההיסטוריה איתה.
    """
    require_csrf(request)
    with cursor(commit=True) as cur:
        doc = _owned_document(cur, document_id, identity)
        if doc["status"] == "approved":
            raise HTTPException(
                status_code=400,
                detail="לא ניתן לסגור דרישה למסמך שכבר אושר.")
        cur.execute(
            """update case_documents
                  set status = 'cancelled',
                      reviewed_by_user_id = %s,
                      reviewed_at = now()
                where id = %s and firm_id = %s""",
            (identity.subject_id, document_id, identity.firm_id),
        )

    audit.record(identity, "office.document_cancelled", entity_type="document",
                 entity_id=document_id, case_id=str(doc["case_id"]),
                 request=request, metadata={"from_stage": doc["status"]})
    return {"ok": True, "status": "cancelled"}


# ================================================================
#  קטלוג המסמכים
# ================================================================

@router.get("/api/office/cases/{case_id}/document-templates")
def document_templates(case_id: str, request: Request,
                       identity=Depends(require_staff)):
    """
    הקטלוג של סוג התביעה של התיק, מהמסד.

    היה קבוע ב-JavaScript עד 13.09. שם הוא לא היה ניתן לעריכה
    בלי פריסה, והדפדפן החזיק עותק שיכול לסטות מהמסד.
    """
    with cursor() as cur:
        case = _owned_case(cur, case_id, identity)
        cur.execute(
            """select id, name, guidance, is_required, position
                 from required_document_templates
                where claim_type_id = %s and firm_id = %s
                order by position""",
            (case["claim_type_id"], identity.firm_id),
        )
        rows = cur.fetchall()
    return {"templates": [{
        "id": str(r["id"]), "name": r["name"], "guidance": r["guidance"],
        "required": r["is_required"], "position": r["position"],
    } for r in rows]}


# ================================================================
#  יומן פעולות
# ================================================================

@router.get("/api/office/audit-log")
def audit_log(request: Request, case_id: str | None = None,
              limit: int = 50, identity=Depends(require_staff)):
    """
    יומן הפעולות של המשרד, מ-audit_log.

    עד 13.09 הממשק הציג יומן שנשמר ב-localStorage של הדפדפן -
    כלומר כל עובד ראה רק את מה שהוא עצמו עשה באותו מחשב.
    """
    limit = max(1, min(limit, 200))
    with cursor() as cur:
        if case_id:
            _owned_case(cur, case_id, identity)
            cur.execute(
                """select a.action, a.entity_type, a.created_at, a.metadata,
                          coalesce(u.full_name, '') as actor_name
                     from audit_log a
                     left join users u on u.id = a.actor_id
                                      and a.actor_type = 'user'
                    where a.firm_id = %s and a.case_id = %s
                    order by a.created_at desc limit %s""",
                (identity.firm_id, case_id, limit),
            )
        else:
            cur.execute(
                """select a.action, a.entity_type, a.created_at, a.metadata,
                          coalesce(u.full_name, '') as actor_name
                     from audit_log a
                     left join users u on u.id = a.actor_id
                                      and a.actor_type = 'user'
                    where a.firm_id = %s
                    order by a.created_at desc limit %s""",
                (identity.firm_id, limit),
            )
        rows = cur.fetchall()
    return {"entries": [{
        "action": r["action"],
        "actor": r["actor_name"],
        "entity": r["entity_type"],
        "at": r["created_at"].isoformat(),
        "metadata": r["metadata"],
    } for r in rows]}
