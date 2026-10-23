"""
doc_decision.py - החלטה אוטומטית על מסמך שהלקוח העלה (docs/10, 23.10).

החלטת המוצר: "המערכת מאשרת כשהיא בטוחה; ספק - למתמחה; השותף רק
מקבל עדכון". עד היום (נעול מ-2.10) סיווג רק הציע, ואדם אישר כל
מסמך. כאן נוסף שלב *נפרד* שמחליט, ורק בשני מקרים חד-משמעיים:

1. לא קריא - אף עמוד לא נקרא (classify: "unreadable"). המסמך נדחה
   עם הנחיה לצלם שוב, ושירות ההתראות מודיע ללקוח. זה המקרה שבו
   המתנה לאדם עולה הכי הרבה: הלקוח מחכה ימים כדי לשמוע "צלם שוב".

2. אושר - כל אלה יחד:
     * הסיווג "ברור" (לא דו-משמעי, לא מעורב, לא לא-ידוע)
     * הסוג תואם בדיוק לדרישה ("match" - לא "undetermined")
     * אין עמוד לא קריא
     * כל עמוד שנקרא ב-OCR - בביטחון של AUTO_MIN_OCR_CONFIDENCE ומעלה

כל השאר - נשאר "ממתין לבדיקה", בתור של מי שאחראי על התיק.

מה זה *לא* עושה
----------------
* אינו קורא את תוכן המסמך מעבר לסוג. "זה סיכום אשפוז, קריא" - כן.
  "סיכום האשפוז תומך בתביעה" - לעולם לא.
* אינו נוגע במסמך שאדם כבר החליט עליו (status אינו pending_review).
* אינו הופך את הסיווג ל"מאושר": document_classifications נשאר
  "suggested", כי שום אדם לא אישר אותו. ההחלטה רשומה בנפרד
  (case_documents.decided_by_system) - וכל החלטת אדם מאוחרת דורסת.
* המשרד יכול לכבות (firms.auto_document_decisions).
"""

from . import audit, processing
from .db.pool import cursor

# גבוה בהרבה מסף הקריאוּת של הסיווג (50): לאשר צריך יותר ביטחון
# מאשר להציע.
AUTO_MIN_OCR_CONFIDENCE = 75.0

UNREADABLE_REASON = ("לא הצלחנו לקרוא את הקובץ. נא להעלות שוב: דף שטוח, אור טוב "
                     "בלי צל, כל הדף בתמונה, ובלי טשטוש.")


def eligible_for_approval(result, accepted, pages):
    """הכללים לאישור, כפונקציה טהורה - נבדקת בלי מסד."""
    if result["decision"] != "clear" or result.get("unreadable_pages"):
        return False
    if processing.requirement_match(accepted, result["kind"]) != "match":
        return False
    for p in pages:
        if p.get("source") == "ocr":
            conf = p.get("confidence")
            if conf is None or conf < AUTO_MIN_OCR_CONFIDENCE:
                return False
    return True


def decide(file_id, firm_id, result, pages):
    """
    נקרא מה-worker אחרי הסיווג. מחזיר 'approved' / 'unreadable' / None.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            """select d.id, d.case_id, d.status, d.accepted_kinds, f.is_current,
                      fm.auto_document_decisions as enabled
                 from document_files f
                 join case_documents d on d.id = f.document_id and d.firm_id = f.firm_id
                 join firms fm on fm.id = f.firm_id
                where f.id = %s and f.firm_id = %s
                for update of d""",
            (file_id, firm_id))
        row = cur.fetchone()
        # קובץ שהוחלף בינתיים, או מסמך שאדם כבר החליט עליו - לא נוגעים.
        if (row is None or not row["enabled"] or not row["is_current"]
                or row["status"] != "pending_review"):
            return None

        if result["decision"] == "unreadable":
            outcome = "unreadable"
            cur.execute(
                """update case_documents
                      set status = 'rejected', reject_reason = %s,
                          reviewed_by_user_id = null, reviewed_at = now(),
                          decided_by_system = 'unreadable'
                    where id = %s""",
                (UNREADABLE_REASON, row["id"]))
        elif eligible_for_approval(result, list(row["accepted_kinds"]), pages):
            outcome = "approved"
            cur.execute(
                """update case_documents
                      set status = 'approved', reject_reason = null,
                          reviewed_by_user_id = null, reviewed_at = now(),
                          decided_by_system = 'approved'
                    where id = %s""",
                (row["id"],))
        else:
            return None

    # הסוג אינו ביומן - אותו כלל של שלב 4 (היומן פתוח לכל הצוות).
    audit.record_actor(firm_id, "system", None, "system.document_auto_decided",
                       metadata={"file_id": str(file_id), "document_id": str(row["id"]),
                                 "case_id": str(row["case_id"]), "outcome": outcome})
    return outcome
