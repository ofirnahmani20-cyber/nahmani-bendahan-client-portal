"""
audit.py - רישום פעולות רגישות.

ההפרדה שנשמרת כאן
------------------
case_stage_events = ההיסטוריה העסקית של התיק. מה קרה בתביעה.
audit_log         = מי עשה מה ומתי. מעקב אחר גישה ופעולה.

שניהם נכתבים, אף אחד לא מוחק את השני, ושינוי שלב אינו מוחק
היסטוריה קודמת - הוא מוסיף אירוע.

מה לא נכנס ללוג
----------------
תוכן מסמכים, תוכן הודעות, ת"ז, סיסמאות, טוקנים, וגוף הבקשה ל-AI.
ה-metadata נועד לענות "מי נגע במה", לא לשכפל את הנתונים עצמם.
"""

import json

from .db.pool import cursor

# רשימת ההיתר של מפתחות שמותר לכתוב ל-metadata. כל השאר מושמט.
# רשימת היתר ולא רשימת חסימה, כדי ששדה חדש לא ידלוף בטעות.
SAFE_METADATA_KEYS = frozenset({
    "case_id", "document_id", "stage_template_id", "decision",
    "preset", "outcome", "reason_given", "from_stage", "to_stage",
    "message_id", "scan_status", "mime_type", "size_bytes",
    # משימות. שים לב למה שאין כאן: כותרת ותיאור המשימה. היומן
    # עונה מי נגע במה ומתי, ולא מה היה כתוב - וכותרת משימה היא
    # טקסט חופשי שהצוות מקליד ועלול להכיל פרט מזהה.
    "task_id", "task_type_id", "priority", "from_status", "to_status",
    "due_at", "due_changed", "assignee_changed", "is_legal_deadline",
    # דרישות, משלוח ותקשורת. שוב, שים לב למה שאין כאן: כותרת
    # הדרישה, ההנחיה, וגוף ההודעה. היומן עונה מי שלח מה ולמי,
    # ולא מה נכתב - וטקסט חופשי עלול להכיל פרט מזהה.
    "requirement_id", "delivery_id", "reminder_id", "conversation_id",
    "channel", "kind", "direction", "every_days",
    # סריקה, אחסון מוצפן וגישה לתוכן רפואי (Layer 2). מזהים, ספירות
    # וסוגים בלבד. אין כאן - ולא יהיה - טקסט מסמך, ערך של עובדה
    # רפואית, או טקסט של שאילתת חיפוש: שאילתה כמו "EMG ישראל" היא
    # בעצמה מידע רפואי מזוהה. מחיפוש נרשמים kinds ו-result_count.
    "file_id", "scan_signature", "rescanned", "swept_parts",
    "swept_orphans", "result_count", "kinds", "page_count",
    "ocr_page_count", "duration_ms", "fact_id",
    # סיווג (שלב 4): קוד סוג מרשימה סגורה - לא טקסט. ואישור מפורש של
    # מסמך שאינו תואם לדרישה.
    "mismatch_acknowledged",
})


def _safe(metadata):
    if not metadata:
        return {}
    return {k: v for k, v in metadata.items() if k in SAFE_METADATA_KEYS}


def record(identity, action, *, entity_type=None, entity_id=None,
           case_id=None, request=None, metadata=None):
    """
    כותב שורת ביקורת. לעולם אינו מפיל את הבקשה הקוראת.

    כישלון ברישום אינו סיבה להכשיל פעולה עסקית שכבר הצליחה, אבל
    הוא גם לא נבלע בשקט מוחלט - הוא נרשם ללוג התהליך.
    """
    try:
        with cursor(commit=True) as cur:
            cur.execute(
                """insert into audit_log
                     (firm_id, actor_type, actor_id, action, entity_type,
                      entity_id, case_id, ip, user_agent, metadata)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    identity.firm_id if identity else None,
                    identity.subject_type if identity else "system",
                    identity.subject_id if identity else None,
                    action, entity_type, entity_id, case_id,
                    request.client.host if request and request.client else None,
                    (request.headers.get("user-agent", "")[:500]
                     if request else None),
                    json.dumps(_safe(metadata), ensure_ascii=False),
                ),
            )
    except Exception as exc:                       # pragma: no cover
        print("[audit] רישום נכשל: %s" % exc)


def record_actor(firm_id, subject_type, subject_id, action, *,
                 request=None, metadata=None):
    """
    רישום מיוחס כשעוד אין אובייקט Identity.

    הצורך היחיד נכון להיום: התחברות מוצלחת. ברגע שהסיסמה
    אומתה אנחנו כן יודעים מי נכנס, אבל ה-Identity נבנה משורת
    session רק בבקשה הבאה. עד 26.09 כל התחברות נרשמה דרך
    record_anonymous, ולכן 1963 שורות התחברות נשמרו עם
    actor_type='system' ו-actor_id ריק - ודוח "כניסות למערכת"
    לא יכול היה לומר מי נכנס.

    כישלון התחברות ממשיך להיכתב דרך record_anonymous, במכוון:
    שם אין זהות מאומתת, ולייחס שורה למשתמש על סמך אימייל
    שהוקלד היה רישום של טענה ולא של עובדה.
    """
    try:
        with cursor(commit=True) as cur:
            cur.execute(
                """insert into audit_log
                     (firm_id, actor_type, actor_id, action, ip,
                      user_agent, metadata)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    firm_id, subject_type, subject_id, action,
                    request.client.host if request and request.client else None,
                    (request.headers.get("user-agent", "")[:500]
                     if request else None),
                    json.dumps(_safe(metadata), ensure_ascii=False),
                ),
            )
    except Exception as exc:                       # pragma: no cover
        print("[audit] רישום נכשל: %s" % exc)


def record_anonymous(firm_id, action, *, request=None, metadata=None):
    """רישום כשאין עדיין זהות - למשל כשל התחברות."""
    try:
        with cursor(commit=True) as cur:
            cur.execute(
                """insert into audit_log
                     (firm_id, actor_type, actor_id, action, ip,
                      user_agent, metadata)
                   values (%s, 'system', null, %s, %s, %s, %s)""",
                (
                    firm_id, action,
                    request.client.host if request and request.client else None,
                    (request.headers.get("user-agent", "")[:500]
                     if request else None),
                    json.dumps(_safe(metadata), ensure_ascii=False),
                ),
            )
    except Exception as exc:                       # pragma: no cover
        print("[audit] רישום נכשל: %s" % exc)
