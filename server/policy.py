"""
policy.py - מה מותר לשלוח לניתוח החיצוני.

זהו הקובץ היחיד שקובע אילו נתונים יוצאים מהמשרד. הוא נכתב כשכבה
נפרדת בכוונה: אפשר לקרוא אותו במלואו בכמה דקות ולהחליט אם הוא בטוח,
בלי לקרוא את שאר המערכת.

עקרונות:
  1. פרטים מזהים של הלקוח לעולם אינם נשלחים - אין להם ערך לניתוח
     המקצועי, והשמטתם מקטינה את החשיפה בלי לפגוע בתועלת.
  2. תוכן רפואי אינו נשלח, אלא אם הופעל במפורש WITH_DOCUMENT.
  3. ברירת המחדל היא המצב המצומצם.
"""

# ============================================================
#  מתג המדיניות
# ------------------------------------------------------------
#  METADATA_ONLY  - מטא-דאטה של התיק בלבד. אין תוכן רפואי.
#  WITH_DOCUMENT  - בנוסף, תוכן של מסמך יחיד שעורך הדין בחר.
#
#  🔴 WITH_DOCUMENT כבוי עד להשלמת הבדיקה המשפטית.
#     הדלקתו מחייבת החלטה מודעת ולא רק שינוי שורה - ראה
#     "החלטות פתוחות" במסמך 05 ב-Vault.
# ============================================================
POLICY_MODE = "METADATA_ONLY"

# שדות שלעולם אינם עוזבים את המשרד, בכל מצב מדיניות.
BLOCKED_FIELDS = frozenset({
    "name", "fullName", "idNumber", "national_id",
    "phone", "email", "address",
})

# חריגים מפורשים: מקומות שבהם שם מפתח חסום הוא דווקא לגיטימי.
# "name" בתוך מסמך הוא שם המסמך ("סיכומי אשפוז"), לא שם אדם.
# הרשימה מכוונת להיות קצרה - כל תוספת כאן היא החלטה, לא נוחות.
ALLOWED_PATHS = frozenset({
    "context.documents[].name",
})


def _clean_document(doc):
    """מסמך מדווח לפי שמו וסטטוסו בלבד - אף פעם לא לפי תוכנו."""
    return {
        "name":     doc.get("name"),
        "required": bool(doc.get("required")),
        "status":   doc.get("status"),
        "note":     doc.get("note"),
        # סיבת דחייה נשלחת: היא נכתבה על ידי המשרד, לא מידע רפואי,
        # והיא מסבירה למודל למה המסמך עדיין פתוח.
        "rejectReason": doc.get("rejectReason") or None,
        "hasFile":  bool(doc.get("file")),
    }


def build_context(case):
    """
    בונה את הקשר התיק שיישלח לניתוח.

    מקבל את תיק הלקוח כפי שהפרונט שלח אותו, ומחזיר מבנה מצומצם.
    כל שדה שאינו ברשימה כאן פשוט לא נכלל - זו רשימת היתר, לא
    רשימת חסימה, כדי ששדה חדש שיתווסף בעתיד לא ידלוף בטעות.
    """
    documents = case.get("documents") or []

    return {
        "claimType":    case.get("claimType"),
        "branch":       case.get("branch"),
        "currentStage": case.get("currentStage"),
        "totalStages":  case.get("totalStages"),
        "stageTitle":   case.get("stageTitle"),
        "stageSince":   case.get("stageEnteredAt"),
        "openedAt":     case.get("openedAt"),
        "nextHearing":  case.get("nextHearing"),
        "documents":    [_clean_document(d) for d in documents],
        "gap":          case.get("gap"),
    }


def assert_clean(payload):
    """
    בדיקת רשת ביטחון אחרונה לפני היציאה החוצה.

    עוברת על המבנה כולו ומוודאת שאף שדה מזהה לא נכנס בטעות.
    מוטב להיכשל בקול מאשר לשלוח פרט מזהה בשקט.
    """
    def walk(node, path="context"):
        if isinstance(node, dict):
            for key, value in node.items():
                child = path + "." + key
                if key in BLOCKED_FIELDS and child not in ALLOWED_PATHS:
                    raise ValueError(
                        "שדה מזהה '%s' נמצא ב-%s ואינו מורשה לשליחה" % (key, child)
                    )
                walk(value, child)
        elif isinstance(node, list):
            # מיקום ברשימה אינו חלק מהזהות של השדה - כל האיברים
            # נבדקים מול אותו נתיב, אחרת חריג יתפוס רק את האיבר הראשון.
            for item in node:
                walk(item, path + "[]")

    walk(payload)
    return payload
