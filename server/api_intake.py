"""
api_intake.py - פתיחת תיק, ועריכת לקוח ותיק מהממשק (docs/10, v1 סעיף 2).

עד 23.10 לא הייתה שום דרך ליצור לקוח או תיק: הם נוצרו רק בנתוני
הזרע. כלומר המערכת לא הייתה מסוגלת לקבל את הלקוח האמיתי הראשון.

    POST /api/office/intake/lookup        האם ת"ז כבר לקוח של המשרד
    POST /api/office/cases                פתיחת תיק (ולקוח, אם חדש)
    POST /api/office/clients/{id}/update  עדכון פרטי קשר וסטטוס
    POST /api/office/cases/{id}/update    סניף, ועדה, סטטוס, אחראי

החלטות
-------
* לקוח נוצר רק יחד עם תיק. לקוח בלי תיק לא היה גלוי לאיש מלבד
  המנהל (case_scope), ולא היה לו שום דבר לראות בפורטל.
* ת"ז קיימת - הלקוח הקיים משמש, ופרטיו אינם נדרסים מטופס הפתיחה.
  שינוי פרטים עובר דרך עדכון לקוח, שם הוא מתועד.
* ת"ז אינה ניתנת לשינוי מכאן. היא מפתח ההתחברות של הלקוח; שינוי
  שלה הוא החלפת זהות, לא עריכה.
* איש צוות שאינו מנהל פותח תיקים לעצמו בלבד. אחרת התיק היה נעלם
  ממנו ברגע שנוצר.
"""

import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit
from .auth import case_scope, require_csrf, require_staff, sees_all_cases
from .db.connect import id_encrypt, id_lookup
from .db.pool import cursor

router = APIRouter()

CASE_STATUSES = ("active", "frozen", "closed_accepted", "closed_rejected")


# ================================================================
#  אימות קלט
# ================================================================

def normalize_national_id(raw: str) -> str:
    """
    ת"ז ישראלית: עד 9 ספרות, משלימים באפסים משמאל, וספרת ביקורת
    לפי משקלים 1,2 מתחלפים (סכום ספרות המכפלה, מודולו 10).

    ת"ז שגויה נדחית כאן ולא בכניסה: מספר שגוי שנשמר היה יוצר לקוח
    שלעולם לא יוכל להתחבר, בלי שאיש יבין למה.
    """
    digits = re.sub(r"[\s-]", "", raw or "")
    if not digits.isdigit() or not 5 <= len(digits) <= 9:
        raise HTTPException(status_code=400, detail="תעודת הזהות צריכה להכיל 5 עד 9 ספרות.")
    digits = digits.zfill(9)
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (1 if i % 2 == 0 else 2)
        total += n - 9 if n > 9 else n
    if total % 10 != 0:
        raise HTTPException(status_code=400, detail="תעודת הזהות אינה תקינה (ספרת ביקורת).")
    return digits


def normalize_phone(raw: str) -> str:
    """
    טלפון נייד ישראלי, כי אליו נשלח קוד הכניסה. 05XXXXXXXX, או
    +9725XXXXXXXX שמומר לצורה המקומית.
    """
    digits = re.sub(r"[\s()-]", "", raw or "")
    if digits.startswith("+972"):
        digits = "0" + digits[4:]
    elif digits.startswith("972"):
        digits = "0" + digits[3:]
    if not re.fullmatch(r"05\d{8}", digits):
        raise HTTPException(status_code=400,
                            detail="נדרש מספר נייד ישראלי (05X-XXXXXXX). אליו נשלח קוד הכניסה.")
    return digits[:3] + "-" + digits[3:]


def normalize_email(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) or len(value) > 200:
        raise HTTPException(status_code=400, detail="כתובת האימייל אינה תקינה.")
    return value


def _parse_date(value, field):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail="%s אינו תאריך תקין." % field)


def _parse_dt(value, field):
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="%s אינו מועד תקין." % field)


def _assignee(cur, identity, requested):
    """
    מי אחראי על התיק החדש. מנהל בוחר כל איש צוות פעיל, או משאיר
    בלי אחראי. כל אחד אחר - רק את עצמו.
    """
    if not sees_all_cases(identity):
        if requested and str(requested) != str(identity.subject_id):
            raise HTTPException(status_code=403,
                                detail="רק מנהל יכול לשייך תיק לאיש צוות אחר.")
        return identity.subject_id
    if not requested:
        return None
    cur.execute("select id from users where id = %s and firm_id = %s and status = 'active'",
                (requested, identity.firm_id))
    if cur.fetchone() is None:
        raise HTTPException(status_code=400, detail="האחראי שנבחר אינו איש צוות פעיל במשרד.")
    return requested


# ================================================================
#  בדיקת ת"ז
# ================================================================

class Lookup(BaseModel):
    national_id: str = Field(min_length=5, max_length=20)


@router.post("/api/office/intake/lookup")
def intake_lookup(body: Lookup, request: Request, identity=Depends(require_staff)):
    """
    לפני פתיחת תיק: האם האדם כבר לקוח של המשרד.

    השם מוחזר רק למי שרואה תיק של הלקוח. אחר רק לומדים "קיים" -
    מספיק כדי לא לפתוח כפילות, בלי לחשוף שם של לקוח שאינו שלו.
    הבדיקה נרשמת ביומן, כי היא שאילתה על אדם לפי ת"ז.
    """
    require_csrf(request)
    nid = normalize_national_id(body.national_id)
    with cursor() as cur:
        cur.execute(
            """select cl.id, cl.full_name, cl.status,
                      bool_or(""" + case_scope(identity) + """) as visible,
                      count(c.id) as cases
                 from clients cl
                 left join cases c on c.client_id = cl.id and c.firm_id = cl.firm_id
                where cl.firm_id = %s and cl.national_id_lookup = %s
                group by cl.id""",
            (identity.firm_id, id_lookup(nid)),
        )
        row = cur.fetchone()
    audit.record(identity, "office.client_lookup", request=request,
                 metadata={"found": row is not None})
    if row is None:
        return {"exists": False}
    visible = sees_all_cases(identity) or bool(row["visible"])
    return {
        "exists": True,
        "name": row["full_name"] if visible else None,
        "status": row["status"],
        "cases": row["cases"] if visible else None,
    }


# ================================================================
#  פתיחת תיק
# ================================================================

class NewCase(BaseModel):
    national_id: str = Field(min_length=5, max_length=20)
    # פרטי לקוח - נדרשים רק כשהלקוח חדש
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    claim_type_id: str
    assigned_user_id: str | None = None
    case_number: str | None = Field(default=None, max_length=40)
    branch: str | None = Field(default=None, max_length=100)
    opened_at: str | None = None


def _next_case_number(cur, firm_id, year):
    """BL-<שנה>-<רץ בן 4 ספרות>, כמו בתיקי ההדגמה הראשונים."""
    prefix = "BL-%d-" % year
    cur.execute(
        """select max(substring(case_number from %s)::int) as n
             from cases where firm_id = %s and case_number ~ %s""",
        (len(prefix) + 1, firm_id, "^" + re.escape(prefix) + r"\d{4}$"),
    )
    n = (cur.fetchone()["n"] or 0) + 1
    return "%s%04d" % (prefix, n)


@router.post("/api/office/cases")
def create_case(body: NewCase, request: Request, identity=Depends(require_staff)):
    """
    לקוח (קיים או חדש) + תיק + השלב הראשון במסלול + רשימת
    המסמכים הנדרשים מקטלוג סוג התביעה. הכול בטרנזקציה אחת: תיק
    בלי שלב או בלי דרישות היה מוצג ללקוח ריק.
    """
    require_csrf(request)
    nid = normalize_national_id(body.national_id)
    opened = _parse_date(body.opened_at, "תאריך הפתיחה") or date.today()
    number = (body.case_number or "").strip() or None

    with cursor(commit=True) as cur:
        cur.execute("select id, code from claim_types where id = %s and firm_id = %s and is_active",
                    (body.claim_type_id, identity.firm_id))
        claim = cur.fetchone()
        if claim is None:
            raise HTTPException(status_code=400, detail="סוג התביעה אינו קיים או אינו פעיל.")

        cur.execute(
            """select id, position from stage_templates
                where claim_type_id = %s and firm_id = %s
                order by position limit 1""",
            (claim["id"], identity.firm_id))
        first_stage = cur.fetchone()
        if first_stage is None:
            raise HTTPException(status_code=400,
                                detail="לסוג התביעה אין עדיין מסלול שלבים. יש להגדיר אותו קודם.")

        assignee = _assignee(cur, identity, body.assigned_user_id)

        # ---- הלקוח ----
        cur.execute("""select id, full_name from clients
                        where firm_id = %s and national_id_lookup = %s for update""",
                    (identity.firm_id, id_lookup(nid)))
        existing = cur.fetchone()
        if existing:
            client_id, client_new = existing["id"], False
        else:
            name = (body.full_name or "").strip()
            if len(name) < 2:
                raise HTTPException(status_code=400, detail="לקוח חדש - נדרש שם מלא.")
            phone = normalize_phone(body.phone)
            email = normalize_email(body.email)
            cur.execute(
                """insert into clients (firm_id, full_name, national_id_lookup,
                                        national_id_enc, phone, email)
                   values (%s, %s, %s, %s, %s, %s) returning id""",
                (identity.firm_id, name, id_lookup(nid), id_encrypt(nid), phone, email))
            client_id, client_new = cur.fetchone()["id"], True

        # ---- התיק ----
        if number is None:
            number = _next_case_number(cur, identity.firm_id, opened.year)
        else:
            cur.execute("select 1 from cases where firm_id = %s and case_number = %s",
                        (identity.firm_id, number))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="מספר התיק כבר קיים במשרד.")

        cur.execute(
            """insert into cases (firm_id, client_id, claim_type_id, case_number,
                                  branch, opened_at, assigned_user_id)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, client_id, claim["id"], number,
             (body.branch or "").strip() or None, opened, assignee))
        case_id = cur.fetchone()["id"]

        cur.execute(
            """insert into case_stage_events
                 (firm_id, case_id, stage_template_id, claim_type_id, note,
                  created_by_user_id)
               values (%s, %s, %s, %s, 'פתיחת תיק', %s)""",
            (identity.firm_id, case_id, first_stage["id"], claim["id"], identity.subject_id))

        # המסמכים הנדרשים - מהקטלוג, כולל הסוגים שהסיווג מקבל
        cur.execute(
            """insert into case_documents (firm_id, case_id, name, guidance,
                                           is_required, position, accepted_kinds)
               select firm_id, %s, name, guidance, is_required, position, accepted_kinds
                 from required_document_templates
                where claim_type_id = %s and firm_id = %s""",
            (case_id, claim["id"], identity.firm_id))
        documents = cur.rowcount

    audit.record(identity, "office.case_created", entity_type="case",
                 entity_id=str(case_id), case_id=str(case_id), request=request,
                 metadata={"client_new": client_new, "documents": documents})
    return {"ok": True, "caseId": str(case_id), "caseNumber": number,
            "clientId": str(client_id), "clientNew": client_new, "documents": documents}


# ================================================================
#  עדכון לקוח
# ================================================================

class ClientUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")


@router.post("/api/office/clients/{client_id}/update")
def update_client(client_id: str, body: ClientUpdate, request: Request,
                  identity=Depends(require_staff)):
    """
    רק שדות שנשלחו משתנים. ת"ז אינה כאן בכוונה (ראה ראש הקובץ).

    השבתה (disabled) חוסמת כניסה לפורטל: request-otp בודק
    status = 'active'. ה-sessions הפתוחים של הלקוח נסגרים מיד.
    """
    require_csrf(request)
    changes, params = [], []
    if body.full_name is not None:
        name = body.full_name.strip()
        if len(name) < 2:
            raise HTTPException(status_code=400, detail="השם קצר מדי.")
        changes.append("full_name = %s"); params.append(name)
    if body.phone is not None:
        changes.append("phone = %s"); params.append(normalize_phone(body.phone))
    if body.email is not None:
        changes.append("email = %s"); params.append(normalize_email(body.email))
    if body.status is not None:
        changes.append("status = %s"); params.append(body.status)
    if not changes:
        raise HTTPException(status_code=400, detail="לא נשלח שום שינוי.")

    with cursor(commit=True) as cur:
        cur.execute(
            """select cl.id from clients cl
                where cl.id = %s and cl.firm_id = %s
                  and exists (select 1 from cases c
                               where c.client_id = cl.id and c.firm_id = cl.firm_id
                                 and """ + case_scope(identity) + """)""",
            (client_id, identity.firm_id))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="הלקוח לא נמצא.")
        cur.execute("update clients set " + ", ".join(changes) + " where id = %s and firm_id = %s",
                    (*params, client_id, identity.firm_id))
        if body.status == "disabled":
            cur.execute("""update sessions set revoked_at = now()
                            where subject_type = 'client' and subject_id = %s
                              and revoked_at is null""", (client_id,))

    # שמות השדות בלבד - לא הערכים. טלפון ואימייל הם מידע אישי.
    audit.record(identity, "office.client_updated", entity_type="client",
                 entity_id=client_id, request=request,
                 metadata={"fields": [c.split(" =")[0] for c in changes]})
    return {"ok": True}


# ================================================================
#  עדכון תיק
# ================================================================

class CaseUpdate(BaseModel):
    branch: str | None = Field(default=None, max_length=100)
    next_hearing_at: str | None = None
    clear_next_hearing: bool = False
    status: str | None = None
    assigned_user_id: str | None = None
    unassign: bool = False


@router.post("/api/office/cases/{case_id}/update")
def update_case(case_id: str, body: CaseUpdate, request: Request,
                identity=Depends(require_staff)):
    """
    סגירת תיק קובעת closed_at להיום; פתיחה מחדש מנקה אותו.
    העברת אחריות - מנהל בלבד. איש צוות שהיה מעביר תיק לאחר היה
    מאבד אליו גישה באותו רגע, וזו החלטה של המנהל.
    """
    require_csrf(request)
    if body.status is not None and body.status not in CASE_STATUSES:
        raise HTTPException(status_code=400, detail="סטטוס תיק לא מוכר.")
    if (body.assigned_user_id or body.unassign) and not sees_all_cases(identity):
        raise HTTPException(status_code=403, detail="רק מנהל יכול להעביר תיק לאחריות אחרת.")

    changes, params, fields = [], [], []
    if body.branch is not None:
        changes.append("branch = %s"); params.append(body.branch.strip() or None)
        fields.append("branch")
    if body.clear_next_hearing:
        changes.append("next_hearing_at = null"); fields.append("next_hearing_at")
    elif body.next_hearing_at is not None:
        changes.append("next_hearing_at = %s")
        params.append(_parse_dt(body.next_hearing_at, "מועד הוועדה"))
        fields.append("next_hearing_at")
    if body.status is not None:
        changes.append("status = %s"); params.append(body.status)
        changes.append("closed_at = " + ("current_date" if body.status.startswith("closed")
                                         else "null"))
        fields.append("status")

    with cursor(commit=True) as cur:
        cur.execute("select c.id, c.status from cases c where c.id = %s and c.firm_id = %s and "
                    + case_scope(identity), (case_id, identity.firm_id))
        case = cur.fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="התיק לא נמצא.")

        if body.unassign:
            changes.append("assigned_user_id = null"); fields.append("assigned_user_id")
        elif body.assigned_user_id:
            changes.append("assigned_user_id = %s")
            params.append(_assignee(cur, identity, body.assigned_user_id))
            fields.append("assigned_user_id")

        if not changes:
            raise HTTPException(status_code=400, detail="לא נשלח שום שינוי.")
        cur.execute("update cases set " + ", ".join(changes) + " where id = %s and firm_id = %s",
                    (*params, case_id, identity.firm_id))

    audit.record(identity, "office.case_updated", entity_type="case",
                 entity_id=case_id, case_id=case_id, request=request,
                 metadata={"fields": fields,
                           "from_status": case["status"], "to_status": body.status})
    return {"ok": True}
