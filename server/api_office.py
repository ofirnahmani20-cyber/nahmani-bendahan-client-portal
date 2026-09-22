"""
api_office.py - ממשק הניהול.

כל שאילתה כאן מסוננת ב-firm_id מה-session. משרד אינו יכול לראות
תיק, לקוח, מסמך או הודעה של משרד אחר, גם אם ינחש מזהה - התנאי
אינו ניתן להשפעה מהבקשה.

לפני 13.09 כל הפעולות האלה כתבו ל-localStorage בדפדפן, כלומר
"אישור מסמך" היה שינוי מקומי שהלקוח לעולם לא ראה.
"""

from datetime import datetime

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
                      c.status, c.assigned_user_id,
                      cl.full_name as client_name, cl.phone, cl.email,
                      ct.name as claim_type, ct.id as claim_type_id,
                      st.stage_position, st.stage_title,
                      st.in_stage_since, st.is_terminal,
                      u.full_name as assignee_name
                 from cases c
                 join clients cl on cl.id = c.client_id
                 join claim_types ct on ct.id = c.claim_type_id
                 left join case_current_stage st on st.case_id = c.id
                 left join users u on u.id = c.assigned_user_id
                                  and u.firm_id = c.firm_id
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
        # ---- מצב התיק ----
        # שבעה מהשדות האלה כבר נשלפו בשאילתה ולא הוחזרו. רק
        # assigned_user_id מצטרף כאן, והוא היה עמודה מתה: נכתב
        # בזריעה ומעולם לא נקרא.
        "status": case["status"],
        "assignee": case["assignee_name"],
        "assigneeId": (str(case["assigned_user_id"])
                       if case["assigned_user_id"] else None),
        "openedAt": case["opened_at"].isoformat() if case["opened_at"] else None,
        "nextHearing": (case["next_hearing_at"].isoformat()
                        if case["next_hearing_at"] else None),
        "stageEnteredAt": (case["in_stage_since"].isoformat()
                           if case["in_stage_since"] else None),
        "isTerminal": bool(case["is_terminal"]),
        "totalStages": len(stages),
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


# ================================================================
#  משימות ומועדי גג
#
#  המיון והמדרגות אינם מחושבים כאן אלא בתצוגה case_task_urgency,
#  כדי שהרשימה, המונים והבאנר ידברו באותה שפה בדיוק.
#
#  מועד משפטי מחייב מקור ואישור אנושי. אין בקובץ הזה, ובאף קובץ
#  אחר, נתיב שמסמן is_legal_deadline בלי שאיש צוות אישר.
# ================================================================

TASK_SELECT = """
    select t.id, t.case_id, t.title, t.description, t.due_at, t.status,
           t.priority, t.is_legal_deadline, t.deadline_source,
           t.completed_at, t.created_at, t.updated_at, t.confirmed_at,
           t.assignee_user_id, t.task_type_id,
           tt.name  as task_type,
           c.case_number,
           cl.full_name as client_name,
           asg.full_name as assignee_name,
           don.full_name as completed_by_name,
           cnf.full_name as confirmed_by_name,
           upd.full_name as updated_by_name,
           crt.full_name as created_by_name,
           g.bucket, g.days_left, g.is_overdue
      from case_tasks t
      join task_types tt on tt.id = t.task_type_id
      join cases      c  on c.id  = t.case_id
      join clients    cl on cl.id = c.client_id
      join case_task_urgency g on g.task_id = t.id
      left join users asg on asg.id = t.assignee_user_id
      left join users don on don.id = t.completed_by_user_id
      left join users cnf on cnf.id = t.confirmed_by_user_id
      left join users upd on upd.id = t.updated_by_user_id
      left join users crt on crt.id = t.created_by_user_id
"""

# באיחור תחילה, אחריו העדיפות שהצוות קבע, ובתוכה המועד הקרוב.
TASK_ORDER = " order by g.overdue_rank, g.priority_rank, t.due_at "

# מה נחשב "דורש טיפול עכשיו": עדיפות קריטית שהצוות קבע, או מועד
# שנותרו לו שלושה ימים ופחות - כולל מועד שחלף.
CRITICAL_WHERE = " and not g.is_closed and (t.priority = 'critical' or g.days_left <= 3) "


def _owned_task(cur, task_id, identity):
    cur.execute(
        """select id, case_id, firm_id, title, status, due_at, priority,
                  assignee_user_id, is_legal_deadline, completed_at
             from case_tasks where id = %s and firm_id = %s""",
        (task_id, identity.firm_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="המשימה לא נמצאה.")
    return row


def _task_out(r):
    return {
        "id": str(r["id"]),
        "caseId": str(r["case_id"]),
        "caseNumber": r["case_number"],
        "clientName": r["client_name"],
        "title": r["title"],
        "description": r["description"],
        "taskTypeId": str(r["task_type_id"]),
        "taskType": r["task_type"],
        "dueAt": r["due_at"].isoformat(),
        "status": r["status"],
        "priority": r["priority"],
        "bucket": r["bucket"],
        "daysLeft": r["days_left"],
        "isOverdue": r["is_overdue"],
        "isLegalDeadline": r["is_legal_deadline"],
        "deadlineSource": r["deadline_source"],
        "confirmedBy": r["confirmed_by_name"],
        "confirmedAt": r["confirmed_at"].isoformat() if r["confirmed_at"] else None,
        "assigneeId": str(r["assignee_user_id"]) if r["assignee_user_id"] else None,
        "assignee": r["assignee_name"],
        "completedAt": r["completed_at"].isoformat() if r["completed_at"] else None,
        "completedBy": r["completed_by_name"],
        "createdBy": r["created_by_name"],
        "createdAt": r["created_at"].isoformat(),
        "updatedBy": r["updated_by_name"],
        "updatedAt": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


def _parse_due(value):
    """
    מועד היעד מגיע מ-input type=datetime-local, כלומר בלי אזור
    זמן. הוא נשמר כ-timestamptz לפי אזור הזמן של השרת, שהוא גם
    אזור הזמן של המשרד - מה שהמשתמש הקליד הוא מה שהוא התכוון לו.
    """
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail="מועד היעד אינו תאריך תקין.")


def _assignee_in_firm(cur, assignee_user_id, identity):
    """
    אחראי חייב להיות איש צוות של אותו משרד. 400 ולא 404, כי
    הבקשה שלך אך הערך בה אינו שמיש - כמו "השלב אינו שייך למסלול".
    """
    if not assignee_user_id:
        return None
    cur.execute(
        "select id from users where id = %s and firm_id = %s and status = 'active'",
        (assignee_user_id, identity.firm_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=400,
                            detail="האחראי שנבחר אינו איש צוות פעיל במשרד.")
    return assignee_user_id


def _legal_deadline(body, identity):
    """
    השער של דרישה 10: המערכת אינה רשאית להמציא מועד משפטי.

    מועד משפטי נכנס רק עם מקור מתועד ועם אישור מפורש של איש
    הצוות, ושם המאשר נשמר. האילוץ legal_deadline_needs_human
    במסד חוסם גם insert שמדלג על הנתיב הזה.
    """
    if not body.is_legal_deadline:
        return False, None, None
    source = (body.deadline_source or "").strip()
    if len(source) < 5:
        raise HTTPException(
            status_code=400,
            detail="מועד משפטי מחייב ציון מקור - מאיזה מכתב או החלטה נגזר התאריך.")
    if not body.confirm_legal_deadline:
        raise HTTPException(
            status_code=400,
            detail="מועד משפטי מחייב אישור מפורש של איש צוות. "
                   "המערכת אינה קובעת מועדים משפטיים בעצמה.")
    return True, source, identity.subject_id


# ---- קריאה ----

@router.get("/api/office/tasks")
def list_tasks(request: Request, assignee: str | None = None,
               status: str | None = None, bucket: str | None = None,
               include_done: bool = False, identity=Depends(require_staff)):
    """
    כל משימות המשרד, ממוינות לפי דחיפות.

    מחזיר שלושה דברים: הרשימה, המונים לשבבי הסיכום, והמשימות
    הקריטיות לבאנר. הבאנר מחושב על כל המשרד ואינו מושפע
    מהסינון, כדי שמועד קריטי לא ייעלם בגלל שסוננה תצוגה.
    """
    where = " where t.firm_id = %s "
    params = [identity.firm_id]

    if not include_done:
        where += " and t.status not in ('done', 'cancelled') "
    if assignee:
        where += " and t.assignee_user_id = %s "
        params.append(assignee)
    if status:
        where += " and t.status = %s "
        params.append(status)
    if bucket:
        where += " and g.bucket = %s "
        params.append(bucket)

    with cursor() as cur:
        cur.execute(TASK_SELECT + where + TASK_ORDER, tuple(params))
        tasks = [_task_out(r) for r in cur.fetchall()]

        cur.execute(
            TASK_SELECT + " where t.firm_id = %s " + CRITICAL_WHERE + TASK_ORDER,
            (identity.firm_id,),
        )
        banner = [_task_out(r) for r in cur.fetchall()]

        cur.execute(
            """select
                 count(*) filter (where g.is_overdue) as overdue,
                 count(*) filter (where not g.is_closed
                                    and (t.priority = 'critical'
                                         or g.days_left <= 3)) as critical,
                 count(*) filter (where not g.is_closed
                                    and t.due_at::date = current_date) as today,
                 count(*) filter (where not g.is_closed
                                    and g.days_left between 0 and 7) as week,
                 count(*) filter (where t.status = 'waiting_client')
                                                        as waiting_client
                 from case_tasks t
                 join case_task_urgency g on g.task_id = t.id
                where t.firm_id = %s""",
            (identity.firm_id,),
        )
        c = cur.fetchone()

    return {
        "tasks": tasks,
        "banner": banner,
        "counts": {
            "overdue": c["overdue"], "critical": c["critical"],
            "today": c["today"], "week": c["week"],
            "waitingClient": c["waiting_client"],
        },
    }


@router.get("/api/office/cases/{case_id}/tasks")
def case_tasks(case_id: str, request: Request, identity=Depends(require_staff)):
    """משימות התיק, כולל שהושלמו - במסך התיק ההיסטוריה היא המידע."""
    with cursor() as cur:
        _owned_case(cur, case_id, identity)
        cur.execute(
            TASK_SELECT + " where t.firm_id = %s and t.case_id = %s " + TASK_ORDER,
            (identity.firm_id, case_id),
        )
        tasks = [_task_out(r) for r in cur.fetchall()]
    return {"tasks": tasks}


@router.get("/api/office/task-types")
def task_types(request: Request, identity=Depends(require_staff)):
    """קטלוג סוגי המשימות של המשרד, מהמסד ולא מקבוע בדפדפן."""
    with cursor() as cur:
        cur.execute(
            """select id, name, code from task_types
                where firm_id = %s and is_active
                order by position""",
            (identity.firm_id,),
        )
        rows = cur.fetchall()
    return {"taskTypes": [{
        "id": str(r["id"]), "name": r["name"], "code": r["code"],
    } for r in rows]}


@router.get("/api/office/staff")
def list_staff(request: Request, identity=Depends(require_staff)):
    """אנשי הצוות של המשרד, לבחירת אחראי."""
    with cursor() as cur:
        cur.execute(
            """select id, full_name, title from users
                where firm_id = %s and status = 'active'
                order by full_name""",
            (identity.firm_id,),
        )
        rows = cur.fetchall()
    return {"staff": [{
        "id": str(r["id"]), "name": r["full_name"], "title": r["title"],
    } for r in rows]}


# ---- כתיבה ----

class NewTask(BaseModel):
    task_type_id: str
    title: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_at: str
    priority: str = Field(default="normal",
                          pattern="^(critical|high|normal|low)$")
    # done אינו ניתן להזנה כאן: הוא מחייב חתימת מי ומתי, ולכן
    # עובר דרך /complete בלבד.
    status: str = Field(default="open",
                        pattern="^(open|in_progress|waiting_client|cancelled)$")
    assignee_user_id: str | None = None
    is_legal_deadline: bool = False
    deadline_source: str | None = Field(default=None, max_length=500)
    confirm_legal_deadline: bool = False


@router.post("/api/office/cases/{case_id}/tasks")
def create_task(case_id: str, body: NewTask, request: Request,
                identity=Depends(require_staff)):
    require_csrf(request)
    due = _parse_due(body.due_at)
    is_legal, source, confirmed_by = _legal_deadline(body, identity)

    with cursor(commit=True) as cur:
        _owned_case(cur, case_id, identity)
        assignee = _assignee_in_firm(cur, body.assignee_user_id, identity)

        cur.execute(
            "select id from task_types where id = %s and firm_id = %s",
            (body.task_type_id, identity.firm_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=400,
                                detail="סוג המשימה אינו מוכר.")

        cur.execute(
            """insert into case_tasks
                 (firm_id, case_id, task_type_id, title, description,
                  assignee_user_id, due_at, status, priority,
                  is_legal_deadline, deadline_source,
                  confirmed_by_user_id, confirmed_at, created_by_user_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       case when %s then now() else null end, %s)
               returning id""",
            (identity.firm_id, case_id, body.task_type_id, body.title.strip(),
             body.description, assignee, due, body.status, body.priority,
             is_legal, source, confirmed_by, is_legal, identity.subject_id),
        )
        task_id = cur.fetchone()["id"]

    audit.record(identity, "office.task_created", entity_type="task",
                 entity_id=str(task_id), case_id=case_id, request=request,
                 metadata={"task_id": str(task_id),
                           "task_type_id": body.task_type_id,
                           "priority": body.priority,
                           "due_at": due.isoformat(),
                           "is_legal_deadline": is_legal})
    return {"ok": True, "taskId": str(task_id)}


@router.post("/api/office/tasks/{task_id}/update")
def update_task(task_id: str, body: NewTask, request: Request,
                identity=Depends(require_staff)):
    """
    עריכת משימה. שינוי המועד נרשם ביומן כפעולה נפרדת, כדי
    שהזזה של מועד גג משפטי תהיה נראית ולא תיבלע ב"עדכון".
    """
    require_csrf(request)
    due = _parse_due(body.due_at)
    is_legal, source, confirmed_by = _legal_deadline(body, identity)

    with cursor(commit=True) as cur:
        task = _owned_task(cur, task_id, identity)
        assignee = _assignee_in_firm(cur, body.assignee_user_id, identity)

        cur.execute(
            "select id from task_types where id = %s and firm_id = %s",
            (body.task_type_id, identity.firm_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=400,
                                detail="סוג המשימה אינו מוכר.")

        # ההשוואה נעשית במסד ולא בפייתון במכוון: due_at הוא
        # timestamptz, והערך שהתקבל מהטופס הוא זמן מקומי בלי אזור
        # זמן. השוואה בפייתון בין השניים מחזירה "שונה" תמיד, ואז
        # כל עריכה הייתה נרשמת כהזזת מועד. המסד הוא זה שמבצע את
        # ההמרה ב-update, ולכן הוא גם זה שצריך להכריע אם היא זזה.
        cur.execute(
            """select due_at is distinct from %s as changed
                 from case_tasks where id = %s and firm_id = %s""",
            (due, task_id, identity.firm_id),
        )
        due_changed = cur.fetchone()["changed"]

        # אישור קיים נשמר כשהמועד לא זז; מועד חדש מחייב אישור חדש,
        # וזה מה ש-_legal_deadline מחזיר.
        cur.execute(
            """update case_tasks
                  set task_type_id = %s, title = %s, description = %s,
                      assignee_user_id = %s, due_at = %s, status = %s,
                      priority = %s, is_legal_deadline = %s,
                      deadline_source = %s,
                      confirmed_by_user_id = %s,
                      confirmed_at = case when %s then now() else null end,
                      updated_by_user_id = %s, updated_at = now()
                where id = %s and firm_id = %s""",
            (body.task_type_id, body.title.strip(), body.description,
             assignee, due, body.status, body.priority, is_legal, source,
             confirmed_by, is_legal, identity.subject_id,
             task_id, identity.firm_id),
        )

    case_id = str(task["case_id"])
    audit.record(identity, "office.task_updated", entity_type="task",
                 entity_id=task_id, case_id=case_id, request=request,
                 metadata={"task_id": task_id, "priority": body.priority,
                           "from_status": task["status"],
                           "to_status": body.status,
                           "due_changed": due_changed,
                           "assignee_changed":
                               str(task["assignee_user_id"] or "") != str(assignee or "")})
    if due_changed:
        audit.record(identity, "office.task_deadline_changed",
                     entity_type="task", entity_id=task_id, case_id=case_id,
                     request=request,
                     metadata={"task_id": task_id, "due_at": due.isoformat(),
                               "is_legal_deadline": is_legal})
    return {"ok": True, "dueChanged": due_changed}


class TaskAssignee(BaseModel):
    assignee_user_id: str | None = None


@router.post("/api/office/tasks/{task_id}/assignee")
def reassign_task(task_id: str, body: TaskAssignee, request: Request,
                  identity=Depends(require_staff)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        task = _owned_task(cur, task_id, identity)
        assignee = _assignee_in_firm(cur, body.assignee_user_id, identity)
        cur.execute(
            """update case_tasks
                  set assignee_user_id = %s,
                      updated_by_user_id = %s, updated_at = now()
                where id = %s and firm_id = %s""",
            (assignee, identity.subject_id, task_id, identity.firm_id),
        )

    audit.record(identity, "office.task_reassigned", entity_type="task",
                 entity_id=task_id, case_id=str(task["case_id"]),
                 request=request,
                 metadata={"task_id": task_id, "assignee_changed": True})
    return {"ok": True}


@router.post("/api/office/tasks/{task_id}/complete")
def complete_task(task_id: str, request: Request,
                  identity=Depends(require_staff)):
    """
    סוגר משימה בלי למחוק אותה.

    המשימה עוברת ל-done עם חתימת מי ומתי, והשורה נשארת. אין
    DELETE על משימות בשום נתיב - בתיק משפטי ההיסטוריה של מה
    נעשה ומתי היא בדיוק מה שצריך לשמור.
    """
    require_csrf(request)
    with cursor(commit=True) as cur:
        task = _owned_task(cur, task_id, identity)
        if task["status"] == "done":
            raise HTTPException(status_code=400, detail="המשימה כבר הושלמה.")
        if task["status"] == "cancelled":
            raise HTTPException(status_code=400,
                                detail="לא ניתן להשלים משימה שבוטלה.")
        cur.execute(
            """update case_tasks
                  set status = 'done', completed_at = now(),
                      completed_by_user_id = %s,
                      updated_by_user_id = %s, updated_at = now()
                where id = %s and firm_id = %s""",
            (identity.subject_id, identity.subject_id,
             task_id, identity.firm_id),
        )

    audit.record(identity, "office.task_completed", entity_type="task",
                 entity_id=task_id, case_id=str(task["case_id"]),
                 request=request,
                 metadata={"task_id": task_id,
                           "from_status": task["status"], "to_status": "done"})
    return {"ok": True, "status": "done"}


@router.post("/api/office/tasks/{task_id}/reopen")
def reopen_task(task_id: str, request: Request,
                identity=Depends(require_staff)):
    """פותח מחדש. חתימת ההשלמה מתנקה, אבל הפעולה עצמה ביומן."""
    require_csrf(request)
    with cursor(commit=True) as cur:
        task = _owned_task(cur, task_id, identity)
        if task["status"] not in ("done", "cancelled"):
            raise HTTPException(status_code=400,
                                detail="המשימה פתוחה ואין מה לפתוח מחדש.")
        cur.execute(
            """update case_tasks
                  set status = 'open', completed_at = null,
                      completed_by_user_id = null,
                      updated_by_user_id = %s, updated_at = now()
                where id = %s and firm_id = %s""",
            (identity.subject_id, task_id, identity.firm_id),
        )

    audit.record(identity, "office.task_reopened", entity_type="task",
                 entity_id=task_id, case_id=str(task["case_id"]),
                 request=request,
                 metadata={"task_id": task_id,
                           "from_status": task["status"], "to_status": "open"})
    return {"ok": True, "status": "open"}
