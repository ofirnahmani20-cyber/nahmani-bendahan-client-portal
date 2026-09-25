"""
api_conversation.py - שיחה דו-כיוונית בין המשרד ללקוח.

הפער שהמודול הזה סוגר: עד 25.09 המשרד לא יכול היה לקרוא דבר
שהלקוח כתב. document_replies הייתה write-only - הלקוח כתב ואף
שורת קוד לא קראה - ו-messages הייתה חד-כיוונית: המשרד שלח ולא
יכול היה לראות את מה ששלח.

ההפרדה שנדרשה נאכפת במודל ולא במוסכמה:
  תגובה על דרישה או מסמך  ->  נושאת requirement_id / document_id
  פנייה כללית             ->  נושאת case_id בלבד

⚠️ הערוץ היחיד שעובד הוא portal. sms ו-whatsapp מוגדרים בסכמה
   כדי שהוספת ספק לא תדרוש migration, אך אין ספק ואין קליטה
   נכנסת מהם.

זהו היפוך של OD-3 ב-REQUIREMENTS.md, שהוציאה מענה לקוח מגרסה 1.
ההחלטה התקבלה במפורש ב-18.09.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit
from .auth import require_client, require_csrf, require_staff
from .db.pool import cursor

router = APIRouter()

CONVERSATION_SELECT = """
    select v.id, v.direction, v.channel, v.body, v.created_at, v.read_at,
           v.requirement_id, v.document_id,
           u.full_name as sender_name,
           r.title as requirement_title,
           d.name  as document_name
      from case_conversation v
      left join users u on u.id = v.sent_by_user_id
      left join case_requirements r on r.id = v.requirement_id
      left join case_documents d on d.id = v.document_id
"""


def _message_out(m):
    return {
        "id": str(m["id"]),
        "direction": m["direction"],
        "channel": m["channel"],
        "body": m["body"],
        "at": m["created_at"].isoformat(),
        "readAt": m["read_at"].isoformat() if m["read_at"] else None,
        "sender": m["sender_name"],
        "requirementId": (str(m["requirement_id"])
                          if m["requirement_id"] else None),
        "requirementTitle": m["requirement_title"],
        "documentId": str(m["document_id"]) if m["document_id"] else None,
        "documentName": m["document_name"],
    }


# ================================================================
#  צד המשרד
# ================================================================

@router.get("/api/office/cases/{case_id}/conversation")
def office_conversation(case_id: str, request: Request,
                        identity=Depends(require_staff)):
    with cursor() as cur:
        cur.execute(
            "select id from cases where id = %s and firm_id = %s",
            (case_id, identity.firm_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="התיק לא נמצא.")

        cur.execute(
            CONVERSATION_SELECT +
            " where v.case_id = %s and v.firm_id = %s order by v.created_at",
            (case_id, identity.firm_id),
        )
        rows = cur.fetchall()

    unread = sum(1 for m in rows
                 if m["direction"] == "inbound" and m["read_at"] is None)
    return {"messages": [_message_out(m) for m in rows], "unread": unread}


class OfficeReply(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    requirement_id: str | None = None


@router.post("/api/office/cases/{case_id}/conversation")
def office_send(case_id: str, body: OfficeReply, request: Request,
                identity=Depends(require_staff)):
    """תשובת עורך הדין מתוך התיק."""
    require_csrf(request)
    with cursor(commit=True) as cur:
        cur.execute(
            "select id from cases where id = %s and firm_id = %s",
            (case_id, identity.firm_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="התיק לא נמצא.")

        if body.requirement_id:
            cur.execute(
                """select id from case_requirements
                    where id = %s and case_id = %s and firm_id = %s""",
                (body.requirement_id, case_id, identity.firm_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=400,
                                    detail="הדרישה אינה שייכת לתיק הזה.")

        cur.execute(
            """insert into case_conversation
                 (firm_id, case_id, direction, channel, body,
                  requirement_id, sent_by_user_id)
               values (%s, %s, 'outbound', 'portal', %s, %s, %s)
               returning id""",
            (identity.firm_id, case_id, body.body.strip(),
             body.requirement_id, identity.subject_id),
        )
        message_id = cur.fetchone()["id"]

    # גוף ההודעה אינו נכנס ל-metadata. היומן עונה מי כתב למי ומתי.
    audit.record(identity, "office.conversation_sent",
                 entity_type="conversation", entity_id=str(message_id),
                 case_id=case_id, request=request,
                 metadata={"conversation_id": str(message_id),
                           "direction": "outbound", "channel": "portal"})
    return {"ok": True, "messageId": str(message_id)}


@router.post("/api/office/conversation/{message_id}/read")
def mark_read(message_id: str, request: Request,
              identity=Depends(require_staff)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        cur.execute(
            """select id, case_id from case_conversation
                where id = %s and firm_id = %s and direction = 'inbound'""",
            (message_id, identity.firm_id),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="ההודעה לא נמצאה.")
        cur.execute(
            """update case_conversation set read_at = now()
                where id = %s and firm_id = %s and read_at is null""",
            (message_id, identity.firm_id),
        )
    return {"ok": True}


# ================================================================
#  צד הלקוח
# ================================================================

@router.get("/api/client/conversation")
def client_conversation(request: Request, identity=Depends(require_client)):
    """
    השיחה של התיק של הלקוח המחובר.

    אין כאן case_id בבקשה: התיק נגזר מה-session, בדיוק כמו בשאר
    נתיבי הלקוח.
    """
    with cursor() as cur:
        cur.execute(
            """select id from cases
                where client_id = %s and firm_id = %s
                order by opened_at desc limit 1""",
            (identity.subject_id, identity.firm_id),
        )
        case = cur.fetchone()
        if case is None:
            return {"messages": []}

        cur.execute(
            CONVERSATION_SELECT +
            " where v.case_id = %s and v.firm_id = %s order by v.created_at",
            (case["id"], identity.firm_id),
        )
        rows = cur.fetchall()

    # הלקוח אינו רואה מי מהצוות טיפל, רק שהמשרד ענה.
    return {"messages": [{
        "id": str(m["id"]),
        "direction": m["direction"],
        "body": m["body"],
        "at": m["created_at"].isoformat(),
        "requirementTitle": m["requirement_title"],
    } for m in rows]}


class ClientMessage(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    requirement_id: str | None = None


@router.post("/api/client/conversation")
def client_send(body: ClientMessage, request: Request,
                identity=Depends(require_client)):
    """פנייה של הלקוח למשרד, מתוך הפורטל."""
    require_csrf(request)
    with cursor(commit=True) as cur:
        cur.execute(
            """select id from cases
                where client_id = %s and firm_id = %s
                order by opened_at desc limit 1""",
            (identity.subject_id, identity.firm_id),
        )
        case = cur.fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="לא נמצא תיק פעיל.")

        if body.requirement_id:
            cur.execute(
                """select id from case_requirements
                    where id = %s and case_id = %s and firm_id = %s""",
                (body.requirement_id, case["id"], identity.firm_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="הדרישה לא נמצאה.")

        cur.execute(
            """insert into case_conversation
                 (firm_id, case_id, direction, channel, body, requirement_id)
               values (%s, %s, 'inbound', 'portal', %s, %s) returning id""",
            (identity.firm_id, case["id"], body.body.strip(),
             body.requirement_id),
        )
        message_id = cur.fetchone()["id"]

    audit.record(identity, "client.conversation_sent",
                 entity_type="conversation", entity_id=str(message_id),
                 case_id=str(case["id"]), request=request,
                 metadata={"conversation_id": str(message_id),
                           "direction": "inbound", "channel": "portal"})
    return {"ok": True, "messageId": str(message_id)}
