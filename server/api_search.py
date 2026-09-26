"""
api_search.py - חיפוש רוחבי במשרד.

עד כה אפשר היה למצוא תיק לפי שם לקוח, ורק מתוך הרשימה שכבר
נטענה לדפדפן. מסמך, משימה או דרישה היו נגישים דרך התיק בלבד,
ולכן השאלה "איפה ה-EMG של ישראל" חייבה לפתוח תיקים אחד אחד.

הקובץ הזה נותן נקודת קצה אחת שמחפשת בחמישה מקורות בבת אחת.

--------------------------------------------------------------
למה זה בנוי כרישום מקורות ולא כשאילתה אחת גדולה

זהו שלב א' של החיפוש. שלב ב' יהיה חיפוש בשפה טבעית שנשען על
שכבת Document Intelligence - חילוץ טקסט, metadata רפואי
ומשפטי מובנה, ואינדוקס. כדי שלא נזרוק את מה שנבנה כאן:

1. SOURCES הוא רישום. כל מקור הוא ערך אחד עם שאילתה וממפה
   שורות. הוספת מקור "תוכן מסמך" בעתיד היא ערך נוסף ברישום,
   ולא כתיבה מחדש של הקובץ.

2. כל תוצאה חוזרת במעטפת אחידה - kind, title, subtitle,
   הקשר התיק, חותמת זמן ומזהה. שכבת השפה הטבעית תחזיר בדיוק
   את אותה מעטפת, ולכן הממשק לא ישתנה כשהיא תגיע.

3. הפרמטר kinds קיים כבר עכשיו. מפרש שאילתות עתידי ייצר
   בדיוק אותו סינון - "מסמכים של ישראל" הוא
   kinds=document + q=ישראל - ולא יצטרך נתיב משלו.

4. every_hit נושא caseId ו-tab, ולכן כל תוצאה יודעת לאן
   לקפוץ. תשובה עתידית עם "המקור שעליו היא נשענת" תשתמש
   באותם שדות בדיוק.

--------------------------------------------------------------
מה הקובץ הזה אינו עושה, במכוון

אין כאן AI, אין OCR, ואין קריאה לתוכן קבצים. החיפוש הוא על
שמות וכותרות שכבר שמורים במסד. מדיניות METADATA_ONLY אינה
נוגעת לכאן כלל, כי שום דבר אינו נשלח לשום מודל.

חיפוש אינו נרשם ל-audit_log. הוא פעולת קריאה בתדירות גבוהה,
ורישום שלו היה מציף את היומן ומאבד לו את הערך - בדיוק
כפי ש-office.case_viewed מציף אותו היום.
"""

from fastapi import APIRouter, Depends, Request

from .auth import require_staff
from .db.pool import cursor

router = APIRouter()

# אורך מינימלי. תו אחד מחזיר כמעט הכול ואינו חיפוש.
MIN_Q = 2

# תקרה לכל מקור ולתשובה כולה, כדי ששאילתה רחבה לא תגרור
# אלפי שורות אל הדפדפן.
PER_KIND = 8
MAX_TOTAL = 30


def _like(q: str) -> str:
    """
    הופך קלט חופשי לתבנית ILIKE בטוחה.

    בלי הבריחה הזאת תו % בקלט היה הופך לתו כללי, וחיפוש "%"
    היה מחזיר את כל המשרד.
    """
    safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + safe + "%"


# ================================================================
#  רישום המקורות
# ----------------------------------------------------------------
#  כל מקור: שאילתה שמחזירה עמודות בשמות אחידים, וממפה לשורת
#  תוצאה. השאילתה חייבת לסנן על firm_id - הבידוד אינו נשען על
#  ההצטרפות אלא נכתב במפורש בכל אחת מהן.
# ================================================================

_CLIENT_SQL = """
    select cl.id, cl.full_name as title, cl.status,
           cl.created_at as at,
           c.id as case_id, c.case_number, cl.full_name as client_name
      from clients cl
      left join cases c on c.client_id = cl.id and c.firm_id = cl.firm_id
     where cl.firm_id = %(firm)s and cl.full_name ilike %(q)s escape '\\'
     order by cl.full_name
     limit %(lim)s
"""

_CASE_SQL = """
    select c.id, c.case_number as title, c.status,
           c.opened_at as at,
           c.id as case_id, c.case_number, cl.full_name as client_name,
           ct.name as claim_type
      from cases c
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
      left join claim_types ct on ct.id = c.claim_type_id
     where c.firm_id = %(firm)s
       and (c.case_number ilike %(q)s escape '\\'
            or ct.name ilike %(q)s escape '\\')
     order by c.opened_at desc
     limit %(lim)s
"""

_DOCUMENT_SQL = """
    select d.id, d.name as title, d.status,
           coalesce(d.reviewed_at, d.created_at) as at,
           d.case_id, c.case_number, cl.full_name as client_name
      from case_documents d
      join cases c on c.id = d.case_id and c.firm_id = d.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where d.firm_id = %(firm)s and d.name ilike %(q)s escape '\\'
     order by coalesce(d.reviewed_at, d.created_at) desc
     limit %(lim)s
"""

_TASK_SQL = """
    select t.id, t.title, t.status,
           coalesce(t.due_at, t.created_at) as at,
           t.case_id, c.case_number, cl.full_name as client_name,
           t.is_legal_deadline
      from case_tasks t
      join cases c on c.id = t.case_id and c.firm_id = t.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where t.firm_id = %(firm)s and t.title ilike %(q)s escape '\\'
     order by t.status in ('done', 'cancelled'),
              coalesce(t.due_at, t.created_at) desc
     limit %(lim)s
"""

_REQUIREMENT_SQL = """
    select r.id, r.title, r.status,
           coalesce(r.due_at, r.created_at) as at,
           r.case_id, c.case_number, cl.full_name as client_name,
           r.kind
      from case_requirements r
      join cases c on c.id = r.case_id and c.firm_id = r.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where r.firm_id = %(firm)s and r.title ilike %(q)s escape '\\'
     order by r.status in ('completed', 'cancelled'),
              coalesce(r.due_at, r.created_at) desc
     limit %(lim)s
"""

# תוויות סטטוס בעברית. ערך גולמי לעולם אינו מוצג למשתמש -
# אותו כלל שנאכף בכל שאר הממשק.
DOC_STATUS = {
    "missing": "צריך להעלות", "uploaded": "הועלה",
    "pending_review": "ממתין לבדיקה", "approved": "אושר",
    "rejected": "נדחה", "cancelled": "בוטל",
}
TASK_STATUS = {
    "open": "פתוחה", "in_progress": "בטיפול",
    "waiting_client": "ממתין ללקוח", "done": "הושלמה", "cancelled": "בוטלה",
}
REQ_STATUS = {
    "open": "פתוחה", "sent": "נשלחה ללקוח",
    "completed": "הושלמה", "cancelled": "בוטלה",
}
CASE_STATUS = {
    "active": "פעיל", "frozen": "מוקפא", "closed": "סגור",
}


def _client_row(r):
    return {"subtitle": "לקוח", "tab": "state"}


def _case_row(r):
    bits = [b for b in (r.get("claim_type"), CASE_STATUS.get(r["status"], "")) if b]
    return {"subtitle": " · ".join(bits), "tab": "state"}


def _document_row(r):
    return {"subtitle": DOC_STATUS.get(r["status"], "מסמך"), "tab": "docs"}


def _task_row(r):
    label = TASK_STATUS.get(r["status"], "משימה")
    if r.get("is_legal_deadline"):
        label = "מועד משפטי · " + label
    return {"subtitle": label, "tab": "tasks"}


def _requirement_row(r):
    return {"subtitle": REQ_STATUS.get(r["status"], "דרישה"), "tab": "comms"}


SOURCES = {
    "client":      {"sql": _CLIENT_SQL,      "map": _client_row,      "label": "לקוחות"},
    "case":        {"sql": _CASE_SQL,        "map": _case_row,        "label": "תיקים"},
    "document":    {"sql": _DOCUMENT_SQL,    "map": _document_row,    "label": "מסמכים"},
    "task":        {"sql": _TASK_SQL,        "map": _task_row,        "label": "משימות"},
    "requirement": {"sql": _REQUIREMENT_SQL, "map": _requirement_row, "label": "דרישות"},
}

# הסדר שבו הקבוצות מוצגות. תיק ולקוח קודמים, כי הם ההקשר.
ORDER = ["case", "client", "document", "task", "requirement"]


@router.get("/api/office/search")
def search(request: Request, q: str = "", kinds: str | None = None,
           limit: int = PER_KIND, identity=Depends(require_staff)):
    """
    חיפוש רוחבי בכל מקורות המשרד.

    q      מחרוזת חופשית. פחות משני תווים מחזיר תשובה ריקה.
    kinds  רשימה מופרדת בפסיקים לצמצום המקורות. ריק = הכול.
    limit  תקרה לכל מקור.
    """
    q = (q or "").strip()
    limit = max(1, min(limit, PER_KIND))

    wanted = [k for k in ORDER if k in SOURCES]
    if kinds:
        asked = {k.strip() for k in kinds.split(",") if k.strip()}
        wanted = [k for k in wanted if k in asked]

    if len(q) < MIN_Q or not wanted:
        return {"query": q, "results": [], "truncated": False,
                "minLength": MIN_Q}

    params = {"firm": identity.firm_id, "q": _like(q), "lim": limit}
    results = []

    with cursor() as cur:
        for kind in wanted:
            src = SOURCES[kind]
            cur.execute(src["sql"], params)
            for r in cur.fetchall():
                extra = src["map"](r)
                results.append({
                    "kind": kind,
                    "id": str(r["id"]),
                    "title": r["title"],
                    "subtitle": extra["subtitle"],
                    "status": r["status"],
                    "at": r["at"].isoformat() if r.get("at") else None,
                    "caseId": str(r["case_id"]) if r.get("case_id") else None,
                    "caseNumber": r.get("case_number"),
                    "clientName": r.get("client_name"),
                    "tab": extra["tab"],
                })

    truncated = len(results) > MAX_TOTAL
    return {
        "query": q,
        "results": results[:MAX_TOTAL],
        "truncated": truncated,
        "minLength": MIN_Q,
    }


@router.get("/api/office/search/kinds")
def search_kinds(request: Request, identity=Depends(require_staff)):
    """
    הקטלוג שהממשק מציג לפיו את הקבוצות.

    הוא מגיע מהשרת ולא מקודד בדפדפן, כדי שהוספת מקור חיפוש
    בעתיד - תוכן מסמך, למשל - תופיע בממשק בלי שינוי בצד הלקוח.
    """
    return {"kinds": [{"kind": k, "label": SOURCES[k]["label"]} for k in ORDER]}
