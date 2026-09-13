"""
seed_templates.py - קטלוג המסמכים לפי סוג תביעה.

הקטלוג היה קבוע ב-JavaScript (REQUIRED_DOC_CATALOG ב-data.js).
זה אומר ששינוי בדרישות המשרד חייב שינוי קוד ופריסה, ושהדפדפן
החזיק עותק שיכול לצאת מסנכרון מול המסד.

עכשיו הוא יושב ב-required_document_templates, והוא מקור האמת
היחיד. ההרצה חוזרת על עצמה: מסמך שכבר קיים אינו משוכפל.

    python -m server.db.seed_templates
"""

import sys

from .connect import MissingKey
from .pool import cursor

CATALOG = {
    "נכות כללית": [
        ("צילום תעודת זהות + ספח",       "קריא, כולל הספח המלא", True),
        ("ייפוי כוח חתום",                "חתום בפני עורך דין", True),
        ("טופס ויתור על סודיות רפואית",   "טופס 1811 של המוסד לביטוח לאומי", True),
        ("סיכומי אשפוז",                  "כל האשפוזים משנת 2023 ואילך", True),
        ("חוות דעת רפואית עדכנית",        "מרופא מומחה בתחום הרלוונטי, עד 6 חודשים אחורה", True),
        ("תלושי שכר - 12 חודשים אחרונים", "לחישוב בסיס הגמלה", True),
        ("אישורי מחלה (טופס 100)",        "מקופת החולים, לתקופת אי הכושר", True),
        ("תוצאות בדיקות הדמיה",           "MRI / CT / רנטגן - דיסק או קובץ סרוק", False),
        ("אישור על קצבאות אחרות",         "ככל שמתקבלות קצבאות ממקור אחר", False),
        ("פרוטוקול ועדה רפואית",          "מתקבל מהמוסד לביטוח לאומי לאחר הוועדה", False),
    ],
    "נכות מעבודה": [
        ("צילום תעודת זהות + ספח",        "קריא, כולל הספח המלא", True),
        ("ייפוי כוח חתום",                 "חתום בפני עורך דין", True),
        ("הודעה על פגיעה בעבודה (ב.ל 250)", "חתום על ידי המעסיק", True),
        ("טופס ויתור על סודיות רפואית",    "טופס 1811 של המוסד לביטוח לאומי", True),
        ("אישור על תאונת עבודה מהמעסיק",   "כולל תיאור נסיבות הפגיעה ומועדה", True),
        ("תיעוד חדר מיון",                 "מהפנייה הראשונה לאחר הפגיעה", True),
        ("פרוטוקול ועדה מדרג ראשון",       "התקבל מהמוסד לביטוח לאומי", True),
        ("חוות דעת מומחה מטעמנו",          "לצורך הדיון בוועדת העררים", True),
        ("תיעוד טיפולים פיזיותרפיים",      "מ-2026 ואילך", False),
        ("תצהירי עדים לפגיעה",             "ככל שהיו עדים לאירוע", False),
    ],
}


def run(cur) -> int:
    added = 0
    for claim_name, rows in CATALOG.items():
        cur.execute(
            "select id, firm_id from claim_types where name = %s", (claim_name,)
        )
        for claim in cur.fetchall():
            for position, (name, guidance, required) in enumerate(rows, start=1):
                cur.execute(
                    """select 1 from required_document_templates
                        where claim_type_id = %s and name = %s""",
                    (claim["id"], name),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    """insert into required_document_templates
                         (firm_id, claim_type_id, name, guidance, is_required, position)
                       values (%s, %s, %s, %s, %s, %s)
                       on conflict (claim_type_id, position) do nothing""",
                    (claim["firm_id"], claim["id"], name, guidance,
                     required, position),
                )
                added += cur.rowcount
    return added


def main() -> int:
    try:
        with cursor(commit=True) as cur:
            added = run(cur)
        print("תבניות מסמכים שנוספו: %d" % added)
        return 0
    except MissingKey as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
