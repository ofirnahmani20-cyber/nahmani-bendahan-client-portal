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
