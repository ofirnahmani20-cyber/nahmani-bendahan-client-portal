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


def _escape(q):
    """
    מנטרל את תווי ה-ILIKE בקלט חופשי.

    בלי הבריחה הזאת תו % בקלט היה הופך לתו כללי, וחיפוש "%"
    היה מחזיר את כל המשרד.
    """
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(q):
    """תבנית הכלה: מחפשת את המחרוזת בכל מקום בשדה."""
    return "%" + _escape(q) + "%"


# ==================================================================
#  רישום המקורות
# ------------------------------------------------------------------
#  כל מקור: שאילתה שמחזירה עמודות בשמות אחידים, וממפה לשורת
#  תוצאה. השאילתה חייבת לסנן על firm_id - הבידוד אינו נשען על
#  ההצטרפות אלא נכתב במפורש בכל אחת מהן.
#
#  ----------------------------------------------------------------
#  הדירוג
#
#  כל שאילתה מחזירה עמודת rank:
#    0  התאמה מדויקת בשדה הראשי
#    1  תחילית בשדה הראשי
#    2  הכלה בשדה הראשי
#    3  התאמה בשדה משני בלבד - טלפון, הנחיה, תיאור, סניף
#  ארבע רמות, והן חלק מהחוזה: הממשק ושכבת השפה הטבעית
#  העתידית שניהם מסתמכים על אותו מספר. עד 27.09 לא היה דירוג כלל - לקוח בשם "ישראל" ולקוח
#  ששמו רק מכיל "ישראל" חזרו באותה רמה, והסדר נקבע לפי
#  אלפבית או תאריך. בחיפוש שנועד למצוא פריט מסוים זה ההפרש
#  בין תוצאה ראשונה נכונה לבין גלילה.
#
#  הדירוג נשאר דטרמיניסטי לחלוטין: שלוש רמות, וכלל שובר-שוויון
#  אחד לכל מקור. אין ציון, אין למידה, ואין AI.
# ==================================================================

# מחושב על הביטוי שבאמת התאים. COALESCE ולא CASE מקונן, כדי
# שהוספת שדה חיפוש נוסף תהיה שורה ולא כתיבה מחדש.
def _rank(*fields):
    """בונה ביטוי דירוג על פני כמה שדות: המדויק שבהם קובע."""
    parts = []
    for f in fields:
        parts.append(
            "case when lower({f}) = lower(%(exact)s) then 0"
            " when lower({f}) like lower(%(prefix)s) escape '\\' then 1"
            " when {f} ilike %(q)s escape '\\' then 2"
            " else 3 end".format(f=f))
    return "least(" + ", ".join(parts) + ")"


def _any(*fields):
    """התאמה באחד מהשדות."""
    return " or ".join("{f} ilike %(q)s escape '\\'".format(f=f) for f in fields)


# ---- לקוח ----
# distinct on (cl.id): ההצטרפות לתיקים מחזירה שורה לכל תיק,
# ולכן לקוח עם שלושה תיקים היה מופיע שלוש פעמים ובולע את
# מכסת התוצאות. התיק האחרון שנפתח הוא ההקשר הרלוונטי.
_CLIENT_SQL = """
    select distinct on (cl.id)
           cl.id, cl.full_name as title, cl.status,
           cl.created_at as at,
           c.id as case_id, c.case_number, cl.full_name as client_name,
           {rank} as rank
      from clients cl
      left join cases c on c.client_id = cl.id and c.firm_id = cl.firm_id
     where cl.firm_id = %(firm)s and ({any})
     order by cl.id, c.opened_at desc nulls last
""".format(rank=_rank("cl.full_name"),
           any=_any("cl.full_name", "coalesce(cl.phone, '')"))

# ---- תיק ----
# שישה שדות ולא שניים. "ישראל" צריך למצוא את התיק שלו ולא
# רק אותו, ו"ועדה רפואית" צריך למצוא תיק שנמצא בשלב הזה.
_CASE_SQL = """
    select c.id, c.case_number as title, c.status,
           c.opened_at as at,
           c.id as case_id, c.case_number, cl.full_name as client_name,
           ct.name as claim_type, st.stage_title, u.full_name as assignee,
           {rank} as rank
      from cases c
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
      left join claim_types ct on ct.id = c.claim_type_id
      left join case_current_stage st on st.case_id = c.id
      left join users u on u.id = c.assigned_user_id and u.firm_id = c.firm_id
     where c.firm_id = %(firm)s and ({any})
     order by rank, c.opened_at desc
""".format(rank=_rank("c.case_number", "cl.full_name"),
           any=_any("c.case_number", "cl.full_name", "coalesce(ct.name, '')",
                    "coalesce(c.branch, '')", "coalesce(st.stage_title, '')",
                    "coalesce(u.full_name, '')"))

# ---- מסמך ----
# גם ההנחיה, לא רק השם. "צילום חוזר" יושב בהנחיה ולא בכותרת.
_DOCUMENT_SQL = """
    select d.id, d.name as title, d.status,
           coalesce(d.reviewed_at, d.created_at) as at,
           d.case_id, c.case_number, cl.full_name as client_name,
           {rank} as rank
      from case_documents d
      join cases c on c.id = d.case_id and c.firm_id = d.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where d.firm_id = %(firm)s and ({any})
     order by rank, coalesce(d.reviewed_at, d.created_at) desc
""".format(rank=_rank("d.name"),
           any=_any("d.name", "coalesce(d.guidance, '')"))

_TASK_SQL = """
    select t.id, t.title, t.status,
           coalesce(t.due_at, t.created_at) as at,
           t.case_id, c.case_number, cl.full_name as client_name,
           t.is_legal_deadline,
           {rank} as rank
      from case_tasks t
      join cases c on c.id = t.case_id and c.firm_id = t.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where t.firm_id = %(firm)s and ({any})
     order by rank, t.status in ('done', 'cancelled'),
              coalesce(t.due_at, t.created_at) desc
""".format(rank=_rank("t.title"),
           any=_any("t.title", "coalesce(t.description, '')"))

_REQUIREMENT_SQL = """
    select r.id, r.title, r.status,
           coalesce(r.due_at, r.created_at) as at,
           r.case_id, c.case_number, cl.full_name as client_name,
           r.kind,
           {rank} as rank
      from case_requirements r
      join cases c on c.id = r.case_id and c.firm_id = r.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where r.firm_id = %(firm)s and ({any})
     order by rank, r.status in ('completed', 'cancelled'),
              coalesce(r.due_at, r.created_at) desc
""".format(rank=_rank("r.title"),
           any=_any("r.title", "coalesce(r.guidance, '')"))

# ---- החלטה ----
# "אילו תיקים נדחו" היא שאלת התמצאות בסיסית, והנתון קיים.
# הכותרת נבנית מהתוצאה ומהאחוזים, כי outcome גולמי
# ("below-threshold") אינו מוצג למשתמש בשום מקום אחר.
_DECISION_SQL = """
    select d.id, d.outcome as title, d.outcome as status,
           d.decided_at as at,
           d.case_id, c.case_number, cl.full_name as client_name,
           d.percent, d.is_permanent, d.appeal_deadline, d.office_note,
           {rank} as rank
      from case_decisions d
      join cases c on c.id = d.case_id and c.firm_id = d.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where d.firm_id = %(firm)s and ({any})
     order by rank, d.decided_at desc
""".format(rank=_rank("coalesce(d.office_note, '')"),
           any=_any("coalesce(d.office_note, '')",
                    "coalesce(d.outcome, '')",
                    "coalesce(d.percent::text, '')"))

# ---- עדכון שנשלח ללקוח ----
_MESSAGE_SQL = """
    select m.id, m.title, 'sent' as status,
           m.sent_at as at,
           m.case_id, c.case_number, cl.full_name as client_name,
           m.is_important, m.body,
           {rank} as rank
      from messages m
      join cases c on c.id = m.case_id and c.firm_id = m.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where m.firm_id = %(firm)s and ({any})
     order by rank, m.sent_at desc
""".format(rank=_rank("m.title"),
           any=_any("m.title", "m.body"))

# ---- שיחה עם הלקוח ----
# הגוף הוא הכותרת כאן, כי להודעת צ'אט אין כותרת. הוא נחתך
# ב-Python ולא ב-SQL, כדי שהחיתוך לא ישבור תו עברי.
_CONVERSATION_SQL = """
    select v.id, v.body as title, v.direction as status,
           v.created_at as at,
           v.case_id, c.case_number, cl.full_name as client_name,
           v.direction, v.channel,
           {rank} as rank
      from case_conversation v
      join cases c on c.id = v.case_id and c.firm_id = v.firm_id
      join clients cl on cl.id = c.client_id and cl.firm_id = c.firm_id
     where v.firm_id = %(firm)s and ({any})
     order by rank, v.created_at desc
""".format(rank=_rank("v.body"), any=_any("v.body"))


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
    "active": "פעיל", "frozen": "מוקפא",
    "closed_accepted": "הסתיים בקבלה", "closed_rejected": "הסתיים בדחייה",
}
# אוצר המילים של ההחלטה זהה ל-OUTCOMES שב-admin.js ולאילוץ
# שבסכמה. מפה שנייה הייתה יכולה להיפרד מהם בשקט.
DECISION_OUTCOME = {
    "below-threshold": "מתחת לסף הזכאות", "grant": "מענק חד-פעמי",
    "pension": "קצבה חודשית", "rejected": "התביעה נדחתה",
}
REQ_KIND = {
    "document": "העלאת מסמך", "info": "מסירת מידע", "signature": "חתימה",
    "form": "השלמת טופס", "contact": "יצירת קשר", "action": "ביצוע פעולה",
    "other": "אחר",
}
CHANNEL = {"sms": "SMS", "whatsapp": "WhatsApp", "email": "אימייל",
           "portal": "פורטל"}

# אורך מרבי לגוף הודעה שמוצג ככותרת תוצאה. החיתוך נעשה
# ב-Python ולא ב-SQL, כדי שלא ישבור תו עברי באמצע.
SNIP = 70


def _snip(text):
    t = " ".join(str(text or "").split())
    return t if len(t) <= SNIP else t[:SNIP - 1] + "…"


def _client_row(r):
    return {"subtitle": "לקוח", "tab": "state"}


def _case_row(r):
    bits = [b for b in (r.get("claim_type"), r.get("stage_title"),
                        CASE_STATUS.get(r["status"], "")) if b]
    return {"subtitle": " · ".join(bits), "tab": "state"}


def _document_row(r):
    return {"subtitle": DOC_STATUS.get(r["status"], "מסמך"), "tab": "docs"}


def _task_row(r):
    label = TASK_STATUS.get(r["status"], "משימה")
    if r.get("is_legal_deadline"):
        label = "מועד משפטי · " + label
    return {"subtitle": label, "tab": "tasks"}


def _requirement_row(r):
    bits = [REQ_KIND.get(r.get("kind"), "דרישה"),
            REQ_STATUS.get(r["status"], "")]
    return {"subtitle": " · ".join(b for b in bits if b), "tab": "comms"}


def _decision_row(r):
    """
    הכותרת נבנית כאן ולא בשאילתה: outcome גולמי
    ("below-threshold") אינו מוצג למשתמש בשום מקום אחר
    במערכת, וחיפוש אינו המקום להתחיל.
    """
    title = DECISION_OUTCOME.get(r["status"], "החלטה")
    if r.get("percent") is not None and r["status"] != "rejected":
        title += " · " + str(r["percent"]) + "%"
    if r.get("is_permanent"):
        title += " · צמיתה"
    return {"title": title,
            "subtitle": _snip(r.get("office_note")) or "החלטת ועדה",
            "tab": "committees"}


def _message_row(r):
    bits = ["עדכון שנשלח ללקוח"]
    if r.get("is_important"):
        bits.insert(0, "חשוב")
    body = _snip(r.get("body"))
    if body:
        bits.append(body)
    # comms ולא updates: "updates" היא תצוגה בפורטל הלקוח,
    # ובאדמין אין לשונית בשם הזה. טופס "שליחת עדכון ללקוח"
    # יושב בלשונית התקשורת, וזה המקום שהתוצאה מובילה אליו.
    return {"subtitle": " · ".join(bits), "tab": "comms"}


def _conversation_row(r):
    """
    לשורת שיחה אין כותרת, ולכן הגוף עצמו הוא הכותרת - חתוך.
    זו אינה חשיפה חדשה: הצוות רואה את אותה הודעה בלשונית
    התקשורת, וחיפוש אינו עוקף שום הרשאה.
    """
    who = "מהלקוח" if r.get("direction") == "inbound" else "מהמשרד"
    return {"title": _snip(r.get("title")),
            "subtitle": who + " · " + CHANNEL.get(r.get("channel"), "פורטל"),
            "tab": "comms"}


SOURCES = {
    "client":       {"sql": _CLIENT_SQL,       "map": _client_row,       "label": "לקוחות"},
    "case":         {"sql": _CASE_SQL,         "map": _case_row,         "label": "תיקים"},
    "document":     {"sql": _DOCUMENT_SQL,     "map": _document_row,     "label": "מסמכים"},
    "task":         {"sql": _TASK_SQL,         "map": _task_row,         "label": "משימות"},
    "requirement":  {"sql": _REQUIREMENT_SQL,  "map": _requirement_row,  "label": "דרישות"},
    "decision":     {"sql": _DECISION_SQL,     "map": _decision_row,     "label": "החלטות"},
    "message":      {"sql": _MESSAGE_SQL,      "map": _message_row,      "label": "עדכונים ללקוח"},
    "conversation": {"sql": _CONVERSATION_SQL, "map": _conversation_row, "label": "שיחות"},
}

# הסדר שבו הקבוצות מוצגות. תיק ולקוח קודמים, כי הם ההקשר.
ORDER = ["case", "client", "document", "task", "requirement",
         "decision", "message", "conversation"]


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
        return {"query": q, "results": [], "groups": [],
                "total": 0, "truncated": False, "minLength": MIN_Q}

    # שלושת הדפוסים של הדירוג. כולם עוברים דרך _escape, ולכן
    # תו % בקלט אינו הופך לתו כללי גם בדפוס התחילית.
    params = {
        "firm": identity.firm_id,
        "q": _like(q),
        "exact": q,
        "prefix": _escape(q) + "%",
        "lim": limit,
    }

    groups = []
    flat = []

    with cursor() as cur:
        for kind in wanted:
            src = SOURCES[kind]
            cur.execute(src["sql"], params)
            # החיתוך כאן ולא ב-SQL: distinct on בלקוח מחייב
            # order by לפי המפתח, ולכן limit בשאילתה היה חותך
            # לפי מזהה ולא לפי דירוג.
            rows = sorted(cur.fetchall(), key=lambda r: r.get("rank", 3))
            items = []
            for r in rows[:limit]:
                extra = src["map"](r)
                items.append({
                    "kind": kind,
                    "id": str(r["id"]),
                    # החלטה ושיחה בונות כותרת משלהן: לשורה
                    # הגולמית אין כותרת שאפשר להציג כמות שהיא.
                    "title": extra.get("title", r["title"]),
                    "subtitle": extra["subtitle"],
                    "status": r["status"],
                    "rank": r.get("rank", 3),
                    "at": r["at"].isoformat() if r.get("at") else None,
                    "caseId": str(r["case_id"]) if r.get("case_id") else None,
                    "caseNumber": r.get("case_number"),
                    "clientName": r.get("client_name"),
                    "tab": extra["tab"],
                })
            if items:
                groups.append({"kind": kind, "label": src["label"],
                               "results": items})
                flat.extend(items)

    # הרשימה השטוחה נשמרת לצד המקובצת: הממשק הקיים צורך אותה,
    # ושכבת השפה הטבעית העתידית תרצה רשימה אחת מדורגת ולא
    # שמונה קבוצות.
    flat.sort(key=lambda x: (x["rank"], ORDER.index(x["kind"])))
    truncated = len(flat) > MAX_TOTAL
    return {
        "query": q,
        "groups": groups,
        "results": flat[:MAX_TOTAL],
        "total": len(flat),
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
