"""
api_requirements.py - דרישות מהלקוח, משלוח ותזכורות.

שלוש הפרדות שהמודול הזה קיים כדי לשמור:

1. דרישה ≠ משלוח. "מה הלקוח צריך לעשות" הוא רשומה אחת שנשארת
   יציבה; "ניסיון לשלוח לו הודעה" הוא רשומה נפרדת שיכולה לחזור
   בכמה ערוצים בלי ליצור דרישות כפולות.

2. דרישת מסמך אינה טבלה מקבילה ל-case_documents. היא מצביעה על
   שורת המסמך הקיימת, וכך הקובץ שהלקוח יעלה הוא אותו קובץ שיופיע
   בקלסר. אין עותק שני.

3. תזכורת נעצרת ברגע שהדרישה הושלמה או בוטלה. זה אינו תלוי
   ב-worker שירוץ בעתיד: ההשלמה עצמה מבטלת את המשלוחים הממתינים
   ומשהה את הכללים, בתוך אותה טרנזקציה.

⚠️ אין כאן scheduler ואין ספק אמיתי. get_sender() מחזיר
   ConsoleSender בפיתוח ו-NullSender בייצור, ושניהם אינם שולחים
   דבר החוצה. השליחה יזומה בלחיצה בלבד.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, notify
from .auth import require_csrf, require_staff
from .db.pool import cursor

router = APIRouter()

#: סוגי הדרישות. מסמך הוא היחיד שנקשר ל-case_documents.
KINDS = ("document", "info", "signature", "form", "contact", "action", "other")

#: מצבים שבהם דרישה עדיין פתוחה ותזכורות עליה רלוונטיות.
LIVE_STATUSES = ("open", "sent")


def _owned_requirement(cur, requirement_id, identity):
    cur.execute(
        """select id, firm_id, case_id, kind, title, status, document_id
             from case_requirements
            where id = %s and firm_id = %s""",
        (requirement_id, identity.firm_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="הדרישה לא נמצאה.")
    return row


def _owned_case(cur, case_id, identity):
    cur.execute(
        "select id, firm_id from cases where id = %s and firm_id = %s",
        (case_id, identity.firm_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="התיק לא נמצא.")
    return row


def _parse_due(value):
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="מועד היעד אינו תאריך תקין.")


REQUIREMENT_SELECT = """
    select r.id, r.case_id, r.kind, r.title, r.guidance, r.due_at,
           r.priority, r.status, r.document_id, r.created_at,
           r.completed_at,
           d.name   as document_name,
           d.status as document_status,
           crt.full_name as created_by_name,
           don.full_name as completed_by_name,
           (select count(*) from message_deliveries m
             where m.requirement_id = r.id) as delivery_count,
           (select max(created_at) from message_deliveries m
             where m.requirement_id = r.id) as last_delivery_at,
           (select count(*) from reminder_rules rr
             where rr.requirement_id = r.id and not rr.is_paused) as active_reminders
      from case_requirements r
      left join case_documents d on d.id = r.document_id
      left join users crt on crt.id = r.created_by_user_id
      left join users don on don.id = r.completed_by_user_id
"""


def _requirement_out(r):
    return {
        "id": str(r["id"]),
        "caseId": str(r["case_id"]),
        "kind": r["kind"],
        "title": r["title"],
        "guidance": r["guidance"],
        "dueAt": r["due_at"].isoformat() if r["due_at"] else None,
        "priority": r["priority"],
        "status": r["status"],
        "documentId": str(r["document_id"]) if r["document_id"] else None,
        "documentName": r["document_name"],
        "documentStatus": r["document_status"],
        "createdBy": r["created_by_name"],
        "createdAt": r["created_at"].isoformat(),
        "completedAt": r["completed_at"].isoformat() if r["completed_at"] else None,
        "completedBy": r["completed_by_name"],
        "deliveryCount": r["delivery_count"],
        "lastDeliveryAt": (r["last_delivery_at"].isoformat()
                           if r["last_delivery_at"] else None),
        "activeReminders": r["active_reminders"],
    }


# ---- קריאה ----

@router.get("/api/office/cases/{case_id}/requirements")
def list_requirements(case_id: str, request: Request,
                      identity=Depends(require_staff)):
    with cursor() as cur:
        _owned_case(cur, case_id, identity)
        cur.execute(
            REQUIREMENT_SELECT +
            " where r.case_id = %s and r.firm_id = %s order by r.created_at desc",
            (case_id, identity.firm_id),
        )
        rows = cur.fetchall()
    return {"requirements": [_requirement_out(r) for r in rows]}


@router.get("/api/office/requirements/{requirement_id}/deliveries")
def list_deliveries(requirement_id: str, request: Request,
                    identity=Depends(require_staff)):
    """היסטוריית המשלוח של דרישה אחת. גוף ההודעה אינו נשמר."""
    with cursor() as cur:
        _owned_requirement(cur, requirement_id, identity)
        cur.execute(
            """select id, channel, to_address, template_key, status,
                      attempts, created_at, sent_at, error
                 from message_deliveries
                where requirement_id = %s and firm_id = %s
                order by created_at desc""",
            (requirement_id, identity.firm_id),
        )
        rows = cur.fetchall()

        cur.execute(
            """select id, every_days, channel, hours_from, hours_to,
                      weekdays, is_paused, start_at, end_at
                 from reminder_rules
                where requirement_id = %s and firm_id = %s
                order by created_at""",
            (requirement_id, identity.firm_id),
        )
        rules = cur.fetchall()

    return {
        "deliveries": [{
            "id": str(d["id"]), "channel": d["channel"],
            "to": d["to_address"], "template": d["template_key"],
            "status": d["status"], "attempts": d["attempts"],
            "createdAt": d["created_at"].isoformat(),
            "sentAt": d["sent_at"].isoformat() if d["sent_at"] else None,
            "error": d["error"],
        } for d in rows],
        "reminders": [{
            "id": str(r["id"]), "everyDays": r["every_days"],
            "channel": r["channel"],
            "hoursFrom": r["hours_from"], "hoursTo": r["hours_to"],
            "weekdays": r["weekdays"], "isPaused": r["is_paused"],
            "startAt": r["start_at"].isoformat(),
            "endAt": r["end_at"].isoformat() if r["end_at"] else None,
        } for r in rules],
    }


# ---- כתיבה ----

class NewRequirement(BaseModel):
    kind: str = Field(pattern="^(document|info|signature|form|contact|action|other)$")
    title: str = Field(min_length=2, max_length=200)
    guidance: str | None = Field(default=None, max_length=2000)
    due_at: str | None = None
    priority: str = Field(default="normal",
                          pattern="^(critical|high|normal|low)$")
    #: לדרישת מסמך: שורת case_documents קיימת, או ריק כדי ליצור אחת.
    document_id: str | None = None


@router.post("/api/office/cases/{case_id}/requirements")
def create_requirement(case_id: str, body: NewRequirement, request: Request,
                       identity=Depends(require_staff)):
    """
    יוצר דרישה. כשהסוג מסמך ולא סופק document_id, נוצרת גם שורת
    case_documents - ומקושרת דו-כיוונית, כדי שהקובץ שיועלה יהיה
    אותו קובץ בקלסר ולא עותק.
    """
    require_csrf(request)
    due = _parse_due(body.due_at)

    with cursor(commit=True) as cur:
        _owned_case(cur, case_id, identity)
        document_id = body.document_id

        if body.kind == "document":
            if document_id:
                cur.execute(
                    """select id from case_documents
                        where id = %s and case_id = %s and firm_id = %s""",
                    (document_id, case_id, identity.firm_id),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=400,
                                        detail="המסמך אינו שייך לתיק הזה.")
            else:
                cur.execute(
                    """select coalesce(max(position), 0) + 1 as next
                         from case_documents where case_id = %s""",
                    (case_id,),
                )
                position = cur.fetchone()["next"]
                cur.execute(
                    """insert into case_documents
                         (firm_id, case_id, name, guidance, position)
                       values (%s, %s, %s, %s, %s) returning id""",
                    (identity.firm_id, case_id, body.title.strip(),
                     body.guidance or body.title.strip(), position),
                )
                document_id = cur.fetchone()["id"]
        elif document_id:
            raise HTTPException(status_code=400,
                                detail="רק דרישת מסמך יכולה להיות מקושרת למסמך.")

        cur.execute(
            """insert into case_requirements
                 (firm_id, case_id, kind, title, guidance, due_at, priority,
                  document_id, created_by_user_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, case_id, body.kind, body.title.strip(),
             body.guidance, due, body.priority, document_id,
             identity.subject_id),
        )
        requirement_id = cur.fetchone()["id"]

        if document_id:
            cur.execute(
                """update case_documents set requirement_id = %s
                    where id = %s and firm_id = %s""",
                (requirement_id, document_id, identity.firm_id),
            )

    audit.record(identity, "office.requirement_created",
                 entity_type="requirement", entity_id=str(requirement_id),
                 case_id=case_id, request=request,
                 metadata={"requirement_id": str(requirement_id),
                           "kind": body.kind, "priority": body.priority})
    return {"ok": True, "requirementId": str(requirement_id)}


@router.post("/api/office/requirements/{requirement_id}/update")
def update_requirement(requirement_id: str, body: NewRequirement,
                       request: Request, identity=Depends(require_staff)):
    require_csrf(request)
    due = _parse_due(body.due_at)

    with cursor(commit=True) as cur:
        req = _owned_requirement(cur, requirement_id, identity)
        if req["status"] in ("completed", "cancelled"):
            raise HTTPException(status_code=400,
                                detail="דרישה שנסגרה אינה ניתנת לעריכה.")
        if body.kind != req["kind"]:
            raise HTTPException(status_code=400,
                                detail="לא ניתן לשנות את סוג הדרישה.")
        cur.execute(
            """update case_requirements
                  set title = %s, guidance = %s, due_at = %s, priority = %s,
                      updated_by_user_id = %s, updated_at = now()
                where id = %s and firm_id = %s""",
            (body.title.strip(), body.guidance, due, body.priority,
             identity.subject_id, requirement_id, identity.firm_id),
        )

    audit.record(identity, "office.requirement_updated",
                 entity_type="requirement", entity_id=requirement_id,
                 case_id=str(req["case_id"]), request=request,
                 metadata={"requirement_id": requirement_id,
                           "priority": body.priority})
    return {"ok": True}


def _stop_reminders(cur, requirement_id, firm_id):
    """
    עוצר תזכורות ומבטל משלוחים ממתינים.

    נקרא בתוך אותה טרנזקציה של ההשלמה או הביטול, ולכן אין רגע
    שבו הדרישה סגורה ותזכורת עדיין חיה. משלוח שכבר יצא נשאר
    כמות שהוא - היסטוריה אינה נמחקת.
    """
    cur.execute(
        """update reminder_rules set is_paused = true
            where requirement_id = %s and firm_id = %s
              and stop_on_complete and not is_paused""",
        (requirement_id, firm_id),
    )
    paused = cur.rowcount
    cur.execute(
        """update message_deliveries
              set status = 'skipped', error = 'הדרישה נסגרה'
            where requirement_id = %s and firm_id = %s and status = 'queued'""",
        (requirement_id, firm_id),
    )
    return paused, cur.rowcount


@router.post("/api/office/requirements/{requirement_id}/complete")
def complete_requirement(requirement_id: str, request: Request,
                         identity=Depends(require_staff)):
    """סוגר דרישה בלי למחוק אותה, ועוצר את התזכורות באותו רגע."""
    require_csrf(request)
    with cursor(commit=True) as cur:
        req = _owned_requirement(cur, requirement_id, identity)
        if req["status"] == "completed":
            raise HTTPException(status_code=400, detail="הדרישה כבר הושלמה.")
        if req["status"] == "cancelled":
            raise HTTPException(status_code=400,
                                detail="לא ניתן להשלים דרישה שבוטלה.")
        cur.execute(
            """update case_requirements
                  set status = 'completed', completed_at = now(),
                      completed_by_user_id = %s,
                      updated_by_user_id = %s, updated_at = now()
                where id = %s and firm_id = %s""",
            (identity.subject_id, identity.subject_id,
             requirement_id, identity.firm_id),
        )
        _stop_reminders(cur, requirement_id, identity.firm_id)

    audit.record(identity, "office.requirement_completed",
                 entity_type="requirement", entity_id=requirement_id,
                 case_id=str(req["case_id"]), request=request,
                 metadata={"requirement_id": requirement_id,
                           "from_status": req["status"],
                           "to_status": "completed"})
    return {"ok": True, "status": "completed"}


@router.post("/api/office/requirements/{requirement_id}/cancel")
def cancel_requirement(requirement_id: str, request: Request,
                       identity=Depends(require_staff)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        req = _owned_requirement(cur, requirement_id, identity)
        if req["status"] in ("completed", "cancelled"):
            raise HTTPException(status_code=400, detail="הדרישה כבר סגורה.")
        cur.execute(
            """update case_requirements
                  set status = 'cancelled',
                      updated_by_user_id = %s, updated_at = now()
                where id = %s and firm_id = %s""",
            (identity.subject_id, requirement_id, identity.firm_id),
        )
        _stop_reminders(cur, requirement_id, identity.firm_id)

    audit.record(identity, "office.requirement_cancelled",
                 entity_type="requirement", entity_id=requirement_id,
                 case_id=str(req["case_id"]), request=request,
                 metadata={"requirement_id": requirement_id,
                           "from_status": req["status"],
                           "to_status": "cancelled"})
    return {"ok": True, "status": "cancelled"}


# ---- משלוח ----

class SendRequest(BaseModel):
    channel: str = Field(pattern="^(sms|whatsapp|email|portal)$")


@router.post("/api/office/requirements/{requirement_id}/send")
def send_requirement(requirement_id: str, body: SendRequest, request: Request,
                     identity=Depends(require_staff)):
    """
    שליחה יזומה. רושמת ניסיון משלוח ומנסה למסור אותו דרך הספק.

    אין ספק מוגדר, ולכן בפיתוח זה נכתב ללוג ובייצור נכשל ב-503.
    הרשומה נשמרת בכל מקרה - גם כישלון הוא היסטוריית משלוח.
    """
    require_csrf(request)

    with cursor(commit=True) as cur:
        req = _owned_requirement(cur, requirement_id, identity)
        if req["status"] in ("completed", "cancelled"):
            raise HTTPException(status_code=400,
                                detail="לא ניתן לשלוח תזכורת על דרישה שנסגרה.")

        cur.execute(
            """select cl.id, cl.phone, cl.email
                 from cases c join clients cl on cl.id = c.client_id
                where c.id = %s and c.firm_id = %s""",
            (req["case_id"], identity.firm_id),
        )
        client = cur.fetchone()

        to = client["phone"] if body.channel in ("sms", "whatsapp") else client["email"]
        if body.channel != "portal" and not to:
            raise HTTPException(
                status_code=400,
                detail="ללקוח אין פרטי קשר בערוץ הזה.")

        cur.execute(
            """insert into message_deliveries
                 (firm_id, requirement_id, subject_type, subject_id, channel,
                  to_address, template_key, status, attempts, scheduled_for,
                  created_by_user_id)
               values (%s, %s, 'client', %s, %s, %s, 'requirement.reminder',
                       'queued', 1, now(), %s)
               returning id""",
            (identity.firm_id, requirement_id, client["id"], body.channel,
             to, identity.subject_id),
        )
        delivery_id = cur.fetchone()["id"]

        status, error, provider_id = "sent", None, None
        try:
            provider_id = notify.get_sender().send(
                body.channel, to, "requirement.reminder",
                {"requirement_id": str(requirement_id)})
        except notify.SmsNotConfigured as exc:
            status, error = "failed", str(exc)
        except Exception as exc:                     # noqa: BLE001
            status, error = "failed", str(exc)[:500]

        cur.execute(
            """update message_deliveries
                  set status = %s, error = %s, provider_message_id = %s,
                      sent_at = case when %s = 'sent' then now() else null end
                where id = %s and firm_id = %s""",
            (status, error, provider_id, status, delivery_id, identity.firm_id),
        )

        if status == "sent" and req["status"] == "open":
            cur.execute(
                """update case_requirements set status = 'sent'
                    where id = %s and firm_id = %s""",
                (requirement_id, identity.firm_id),
            )

    audit.record(identity, "office.requirement_sent",
                 entity_type="requirement", entity_id=requirement_id,
                 case_id=str(req["case_id"]), request=request,
                 metadata={"requirement_id": requirement_id,
                           "delivery_id": str(delivery_id),
                           "channel": body.channel})

    if status == "failed":
        # 200 ולא שגיאה: הרשומה נוצרה והמשרד צריך לראות למה זה נכשל.
        return {"ok": False, "deliveryId": str(delivery_id),
                "status": status, "reason": error}
    return {"ok": True, "deliveryId": str(delivery_id), "status": status}


# ---- תזכורות ----

class NewReminder(BaseModel):
    every_days: int = Field(ge=1, le=90)
    channel: str = Field(pattern="^(sms|whatsapp|email|portal)$")
    end_at: str | None = None
    hours_from: int = Field(default=9, ge=0, le=23)
    hours_to: int = Field(default=20, ge=0, le=23)
    weekdays: str = Field(default="0,1,2,3,4", max_length=20)


@router.post("/api/office/requirements/{requirement_id}/reminders")
def create_reminder(requirement_id: str, body: NewReminder, request: Request,
                    identity=Depends(require_staff)):
    """
    שומר כלל תזכורת. הוא אינו רץ: אין scheduler בפרויקט, והשליחה
    יזומה. הכלל נשמר כדי שהפעלת worker בעתיד לא תדרוש הזנה מחדש.
    """
    require_csrf(request)
    if body.hours_from >= body.hours_to:
        raise HTTPException(status_code=400,
                            detail="שעת ההתחלה חייבת להקדים את שעת הסיום.")

    with cursor(commit=True) as cur:
        req = _owned_requirement(cur, requirement_id, identity)
        if req["status"] in ("completed", "cancelled"):
            raise HTTPException(status_code=400,
                                detail="לא ניתן להגדיר תזכורת לדרישה שנסגרה.")

        # שני כללים זהים על אותה דרישה הם הצפה, לא כפילות מקרית.
        cur.execute(
            """select id from reminder_rules
                where requirement_id = %s and firm_id = %s
                  and channel = %s and not is_paused""",
            (requirement_id, identity.firm_id, body.channel),
        )
        if cur.fetchone():
            raise HTTPException(
                status_code=400,
                detail="כבר קיימת תזכורת פעילה בערוץ הזה לדרישה הזו.")

        cur.execute(
            """insert into reminder_rules
                 (firm_id, requirement_id, every_days, channel, end_at,
                  hours_from, hours_to, weekdays, created_by_user_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, requirement_id, body.every_days, body.channel,
             _parse_due(body.end_at), body.hours_from, body.hours_to,
             body.weekdays, identity.subject_id),
        )
        reminder_id = cur.fetchone()["id"]

    audit.record(identity, "office.reminder_created",
                 entity_type="requirement", entity_id=requirement_id,
                 case_id=str(req["case_id"]), request=request,
                 metadata={"requirement_id": requirement_id,
                           "reminder_id": str(reminder_id),
                           "channel": body.channel,
                           "every_days": body.every_days})
    return {"ok": True, "reminderId": str(reminder_id)}


class ReminderState(BaseModel):
    is_paused: bool


@router.post("/api/office/reminders/{reminder_id}/state")
def set_reminder_state(reminder_id: str, body: ReminderState, request: Request,
                       identity=Depends(require_staff)):
    """השהיה או חידוש. אין מחיקה - כלל שהוגדר נשאר בהיסטוריה."""
    require_csrf(request)
    with cursor(commit=True) as cur:
        cur.execute(
            """select r.id, r.requirement_id, q.case_id, q.status
                 from reminder_rules r
                 join case_requirements q on q.id = r.requirement_id
                where r.id = %s and r.firm_id = %s""",
            (reminder_id, identity.firm_id),
        )
        rule = cur.fetchone()
        if rule is None:
            raise HTTPException(status_code=404, detail="התזכורת לא נמצאה.")
        if not body.is_paused and rule["status"] not in LIVE_STATUSES:
            raise HTTPException(status_code=400,
                                detail="לא ניתן לחדש תזכורת לדרישה שנסגרה.")

        cur.execute(
            "update reminder_rules set is_paused = %s where id = %s and firm_id = %s",
            (body.is_paused, reminder_id, identity.firm_id),
        )

    audit.record(identity,
                 "office.reminder_paused" if body.is_paused
                 else "office.reminder_resumed",
                 entity_type="requirement",
                 entity_id=str(rule["requirement_id"]),
                 case_id=str(rule["case_id"]), request=request,
                 metadata={"reminder_id": reminder_id,
                           "requirement_id": str(rule["requirement_id"])})
    return {"ok": True, "isPaused": body.is_paused}
