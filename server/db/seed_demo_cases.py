"""
seed_demo_cases.py - תיקי הדגמה שמכסים את כל יכולות המערכת.

    python -m server.db.seed_demo_cases          # יצירה
    python -m server.db.seed_demo_cases --reset  # מחיקה ויצירה מחדש

למה הקובץ הזה קיים
-------------------
שני תיקי הזרע שב-seed.py מספיקים כדי שהמערכת תעלה, ואינם
מספיקים כדי לראות אותה. מצבים שלמים לא הופיעו בשום מסך:
מסמך שנדחה, מועד משפטי שחלף, תזכורת מושהית, משלוח שנכשל,
תיק מוקפא, תיק בלי אחראי, החלטה מכל ארבעת הסוגים.

כל תיק כאן נבנה סביב מצב אחד שצריך להיראות, ויחד הם מכסים
את כל ערכי ה-CHECK שבסכמה. מי שרוצה לבדוק פיצ'ר יודע לאיזה
תיק להיכנס.

בטיחות
-------
כל מה שנוצר כאן נושא את התחילית DEMO- במספר התיק, ואת
המחרוזת "(הדגמה)" בשם הלקוח. --reset מוחק לפי הסימון הזה
בלבד, ולכן הוא אינו יכול לגעת בתיק אמיתי או בשני תיקי הזרע.

ההרצה חוזרת על עצמה: תיק שכבר קיים אינו נוצר פעמיים.

⚠️ מסד הדגמה בלבד. השמות, המספרים והמצבים הרפואיים מומצאים
ואינם נתונים של אדם אמיתי.
"""

import argparse
import datetime as dt
import sys

from .connect import MissingKey, id_encrypt, id_lookup
from .pool import cursor

MARK = "DEMO-"
NAME_SUFFIX = " (הדגמה)"

TODAY = dt.date.today()


def days(n):
    """תאריך יחסי להיום, כדי שהדמו לא יתיישן."""
    return TODAY + dt.timedelta(days=n)


def hours(n):
    return dt.datetime.now().astimezone() + dt.timedelta(hours=n)


def il_id(base8):
    """
    משלים ספרת ביקורת לתעודת זהות ישראלית תקפה.

    המסך דורש ת"ז בת תשע ספרות, ומספר שאינו עובר את הבדיקה
    היה הופך את הלקוח לבלתי-נגיש בדיוק במסך שנבנה להדגים.
    """
    total = 0
    for i, ch in enumerate(base8):
        d = int(ch) * (1 if i % 2 == 0 else 2)
        total += d if d < 10 else d - 9
    return base8 + str((10 - total % 10) % 10)


# ==================================================================
#  התיקים
# ------------------------------------------------------------------
#  purpose = מה התיק הזה נועד להדגים. הוא נכתב גם כהערה בתיק
#  עצמו (case_next_steps), כדי שמי שפותח אותו במסך יבין מיד.
#
#  lawyer = אינדקס ברשימה הממוינת לפי אימייל:
#    0 = bendahan@   1 = nahmani@   None = בלי אחראי
# ==================================================================

CASES = [
    # ---------------------------------------------------------------
    {
        "key": "hearing",
        "purpose": "ועדה רפואית בעוד שלושה ימים - מועד קרוב שמופיע גם בפורטל",
        "client": "מרים אלקיים", "id8": "31847265", "phone": "052-7418520",
        "email": "miriam.a@example.com",
        "claim": "general-disability", "branch": "ירושלים",
        "opened": days(-214), "stage": 4, "lawyer": 0,
        "hearing": days(3),
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("ייפוי כוח חתום", "approved", None),
            ("חוות דעת רפואית עדכנית", "approved", None),
            # שמות עם לטינית: בלעדיהם אין במסד ולו תו לטיני
            # אחד בשם מסמך, וחיפוש "EMG" מחזיר אפס גם כשהוא
            # עובד נכון לחלוטין.
            ("בדיקת EMG יד ימין", "approved", None),
            ("MRI עמוד שדרה מותני", "approved", None),
            ("תוצאות בדיקות הדמיה", "pending_review", None),
        ],
        "tasks": [
            ("הכנת מרים לוועדה", "committee-prep", days(1), "critical",
             "open", False, "לעבור על התיק הרפואי ולתרגל את השאלות הצפויות."),
            ("איסוף תיעוד פיזיותרפיה", "medical-record", days(-2), "high",
             "in_progress", False, None),
        ],
        "steps": [("הוועדה הרפואית", "התייצבות עם כל התיעוד המקורי", "בעוד שלושה ימים")],
        "messages": [("זימון לוועדה התקבל",
                      "התקבל זימון לוועדה רפואית. נתאם שיחת הכנה בימים הקרובים.",
                      True, -9)],
    },
    # ---------------------------------------------------------------
    {
        "key": "overdue",
        "purpose": "מועד משפטי שחלף - הבאנר האדום, והדחיפות הגבוהה ביותר במערכת",
        "client": "רפאל בוזגלו", "id8": "20491736", "phone": "054-3692580",
        "email": "rafi.b@example.com",
        "claim": "work-injury", "branch": "באר שבע",
        "opened": days(-402), "stage": 7, "lawyer": 1,
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("ייפוי כוח חתום", "approved", None),
            ("פרוטוקול ועדה מדרג ראשון", "approved", None),
            ("EMG + NCS גפיים תחתונות", "approved", None),
            ("CT כתף שמאל", "pending_review", None),
            ("חוות דעת מומחה מטעמנו", "missing", None),
        ],
        "tasks": [
            ("הגשת ערר לוועדת העררים", "appeal", days(-4), "critical",
             "open", True,
             "סעיף 121 לחוק הביטוח הלאומי - 60 יום מיום קבלת ההחלטה"),
            ("תיאום עם המומחה הרפואי", "medical-record", days(-1), "high",
             "in_progress", False, None),
        ],
        "decision": ("below-threshold", 8, False, days(-52),
                     "נקבעו 8% - מתחת לסף הזכאות לקצבה. הוגש ערר."),
        "steps": [("הגשת הערר", "כתב ערר מנומק בצירוף חוות דעת", "דחוף")],
    },
    # ---------------------------------------------------------------
    {
        "key": "rejected-doc",
        "purpose": "מסמך שנדחה עם סיבה - הלקוח רואה למה, ומתבקש להעלות מחדש",
        "client": "ויקטוריה חדד", "id8": "15738294", "phone": "050-8529630",
        "email": "viki.h@example.com",
        "claim": "general-disability", "branch": "חיפה",
        "opened": days(-88), "stage": 2, "lawyer": 0,
        "docs": [
            ("צילום תעודת זהות + ספח", "rejected",
             "הצילום מטושטש והספח חתוך. נדרש צילום חוזר של שני הצדדים."),
            ("ייפוי כוח חתום", "approved", None),
            ("תלושי שכר - 12 חודשים אחרונים", "rejected",
             "התקבלו שישה תלושים בלבד. נדרשים שנים-עשר החודשים המלאים."),
            ("טופס ויתור על סודיות רפואית", "pending_review", None),
            ("סיכומי אשפוז", "missing", None),
        ],
        "tasks": [
            ("מעקב אחר העלאה חוזרת", "client-docs", days(2), "normal",
             "waiting_client", False,
             "הלקוחה התבקשה לצלם מחדש. לוודא שקיבלה את ההודעה."),
        ],
        "replies": [("dont-have", "אין לי את כל התלושים, חלק מהמעסיקים לא שלחו.")],
    },
    # ---------------------------------------------------------------
    {
        "key": "requirements",
        "purpose": "שבעת סוגי הדרישות ביחד, בכל ארבעת מצבי הדרישה",
        "client": "יוסף אבוטבול", "id8": "40628173", "phone": "053-1472583",
        "email": "yossi.a@example.com",
        "claim": "work-injury", "branch": "תל אביב",
        "opened": days(-45), "stage": 1, "lawyer": 1,
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("ייפוי כוח חתום", "pending_review", None),
            ("הודעה על פגיעה בעבודה (ב.ל 250)", "missing", None),
        ],
        "requirements": [
            # (kind, title, guidance, status, due, doc_index)
            ("document", "העלאת טופס ב.ל 250", "חתום על ידי המעסיק",
             "sent", days(5), 2),
            ("info", "פרטי המעסיק במועד הפגיעה",
             "שם החברה, ח.פ, וטלפון של מחלקת כוח אדם", "sent", days(3), None),
            ("signature", "חתימה על כתב ויתור סודיות",
             "נשלח לחתימה דיגיטלית", "open", days(7), None),
            ("form", "מילוי שאלון תפקוד יומיומי",
             "השאלון נשלח למייל", "sent", days(10), None),
            ("contact", "שיחה עם עורך הדין",
             "לתיאום מול המזכירות", "completed", days(-3), None),
            ("action", "פנייה לקופת החולים לקבלת תיק רפואי",
             "הלקוח מבקש בעצמו - אנחנו לא יכולים במקומו",
             "sent", days(14), None),
            ("other", "אישור על תקופת אי-כושר",
             "מכל מקור שיש", "cancelled", None, None),
        ],
        "reminders": [
            # (requirement_index, every_days, channel, paused)
            (0, 3, "sms", False),
            (1, 5, "whatsapp", False),
            (3, 7, "email", True),
        ],
    },
    # ---------------------------------------------------------------
    {
        "key": "conversation",
        "purpose": "שיחה דו-כיוונית פעילה - הודעות חדשות שממתינות לתשובת המשרד",
        "client": "נעמה שטרית", "id8": "27391648", "phone": "058-9637410",
        "email": "naama.sh@example.com",
        "claim": "general-disability", "branch": "נתניה",
        "opened": days(-131), "stage": 3, "lawyer": 0,
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("ייפוי כוח חתום", "approved", None),
            ("בדיקת EMG - חוזרת", "pending_review", None),
            ("אישורי מחלה (טופס 100)", "pending_review", None),
        ],
        "conversation": [
            # (direction, channel, body, hours_ago, read)
            ("outbound", "portal",
             "שלום נעמה, התביעה הוגשה היום לסניף נתניה. נעדכן ברגע שתתקבל תשובה.",
             -168, True),
            ("inbound", "portal",
             "תודה רבה. כמה זמן בערך לוקח עד שמקבלים תשובה?", -160, True),
            ("outbound", "portal",
             "בדרך כלל בין שישה לשמונה שבועות. אם יבקשו השלמות נעדכן מיד.",
             -158, True),
            ("outbound", "sms",
             "נעמה, תזכורת: חסר אישור מחלה לחודש מרץ.", -80, True),
            ("inbound", "whatsapp",
             "שלחתי עכשיו את האישור בוואטסאפ.", -78, True),
            ("outbound", "email",
             "התקבל, תודה. העברנו לתיק.", -76, True),
            ("inbound", "portal",
             "קיבלתי היום מכתב מביטוח לאומי שמבקש אישור נוסף מהרופא. מה עושים?",
             -5, False),
            ("inbound", "portal",
             "אני מצרפת גם צילום של המכתב.", -5, False),
        ],
        "tasks": [
            ("מענה ללקוחה בנוגע למכתב ההשלמות", "client-contact",
             days(0), "high", "open", False, None),
        ],
    },
    # ---------------------------------------------------------------
    {
        "key": "deliveries",
        "purpose": "היסטוריית משלוח בכל חמשת המצבים - כולל משלוח שנכשל",
        "client": "אברהם מזרחי", "id8": "36174928", "phone": "050-2583691",
        "email": "avi.m@example.com",
        "claim": "work-injury", "branch": "אשדוד",
        "opened": days(-67), "stage": 2, "lawyer": 1,
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("תיעוד חדר מיון", "missing", None),
        ],
        "requirements": [
            ("document", "העלאת תיעוד חדר מיון",
             "מהפנייה הראשונה לאחר הפגיעה", "sent", days(4), 1),
        ],
        "deliveries": [
            # (channel, status, hours_ago, error)
            ("sms", "delivered", -96, None),
            ("sms", "sent", -48, None),
            ("whatsapp", "failed", -24, "המספר אינו רשום בוואטסאפ"),
            ("email", "queued", -1, None),
            ("portal", "skipped", -12, "הלקוח כבר קרא את הדרישה בפורטל"),
        ],
    },
    # ---------------------------------------------------------------
    {
        "key": "accepted",
        "purpose": "תיק שהסתיים בקבלה - קצבה חודשית, מופיע באזור 'תיקים שהושלמו'",
        "client": "סימה פרץ", "id8": "18246937", "phone": "052-3691470",
        "email": "sima.p@example.com",
        "claim": "general-disability", "branch": "ירושלים",
        "opened": days(-486), "stage": 8, "lawyer": 0,
        "status": "closed_accepted", "closed": days(-31),
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("ייפוי כוח חתום", "approved", None),
            ("פרוטוקול ועדה רפואית", "approved", None),
        ],
        "decision": ("pension", 74, True, days(-38),
                     "נקבעו 74% נכות יציבה. אושרה קצבה חודשית עם רטרו מיום ההגשה."),
        "messages": [("התביעה אושרה",
                      "הוועדה קבעה 74% נכות יציבה. הקצבה תשולם רטרואקטיבית מיום ההגשה.",
                      True, -37)],
    },
    # ---------------------------------------------------------------
    {
        "key": "grant",
        "purpose": "תיק שהסתיים במענק חד-פעמי - התוצאה השנייה מתוך ארבע",
        "client": "חיים דהן", "id8": "45291836", "phone": "054-7418529",
        "email": "haim.d@example.com",
        "claim": "work-injury", "branch": "פתח תקווה",
        "opened": days(-329), "stage": 8, "lawyer": 1,
        "status": "closed_accepted", "closed": days(-14),
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("הודעה על פגיעה בעבודה (ב.ל 250)", "approved", None),
        ],
        "decision": ("grant", 17, False, days(-21),
                     "נקבעו 17% זמניים. שולם מענק חד-פעמי."),
    },
    # ---------------------------------------------------------------
    {
        "key": "case-rejected",
        "purpose": "תיק שנדחה, ומועד הערר עוד פתוח - הפריט הדחוף בפורטל הלקוח",
        "client": "לובה גולדשטיין", "id8": "29385174", "phone": "053-8527419",
        "email": "luba.g@example.com",
        "claim": "general-disability", "branch": "אשקלון",
        "opened": days(-254), "stage": 6, "lawyer": 0,
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("ייפוי כוח חתום", "approved", None),
            ("חוות דעת רפואית עדכנית", "approved", None),
        ],
        "decision": ("rejected", 0, False, days(-18),
                     "התביעה נדחתה. לדעתנו יש עילה טובה לערר.",
                     days(42)),
        "tasks": [
            ("החלטה על הגשת ערר", "decision-followup", days(9), "high",
             "open", True,
             "60 יום מקבלת ההחלטה - נותרו שישה שבועות"),
        ],
        "messages": [("התקבלה החלטת הוועדה",
                      "התביעה נדחתה. יש באפשרותנו להגיש ערר, ולדעתנו יש לכך עילה טובה. "
                      "נקבע שיחה השבוע.", True, -17)],
    },
    # ---------------------------------------------------------------
    {
        "key": "closed-rejected",
        "purpose": "תיק שנסגר בדחייה אחרי שחלון הערר נסגר - המצב הרביעי של תיק",
        "client": "בוריס לוין", "id8": "42917583", "phone": "053-7419638",
        "email": "boris.l@example.com",
        "claim": "general-disability", "branch": "אשדוד",
        "opened": days(-611), "stage": 8, "lawyer": 1,
        "status": "closed_rejected", "closed": days(-73),
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("ייפוי כוח חתום", "approved", None),
            ("פרוטוקול ועדה רפואית", "approved", None),
        ],
        "decision": ("rejected", 0, False, days(-148),
                     "התביעה נדחתה גם בוועדת העררים. הוסבר ללקוח שהמיצוי הושלם.",
                     days(-88)),
        "messages": [("סיום הטיפול בתיק",
                      "ועדת העררים דחתה את הערר. מיצינו את ההליך מול המוסד. "
                      "נשמח לעמוד לרשותך בכל שאלה.", True, -72)],
    },
    # ---------------------------------------------------------------
    {
        "key": "frozen",
        "purpose": "תיק מוקפא - המצב הרביעי, ואינו מופיע ברשימת התיקים הפעילים",
        "client": "סאלח אבו-ראס", "id8": "34827159", "phone": "050-9638527",
        "email": "salah.a@example.com",
        "claim": "work-injury", "branch": "נצרת",
        "opened": days(-176), "stage": 3, "lawyer": 1,
        "status": "frozen",
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("אישור על תאונת עבודה מהמעסיק", "missing", None),
        ],
        "messages": [("התיק הוקפא זמנית",
                      "לבקשתך התיק מוקפא עד לסיום הטיפול הרפואי. "
                      "אפשר לחדש אותו בכל עת בשיחה למשרד.", False, -22)],
    },
    # ---------------------------------------------------------------
    {
        "key": "fresh",
        "purpose": "תיק שנפתח היום וטרם נרשם לו שלב - מצב חוקי שהפיל את הפורטל בעבר",
        "client": "תמר בן-חמו", "id8": "21673948", "phone": "058-1479632",
        "email": "tamar.bh@example.com",
        "claim": "general-disability", "branch": "רחובות",
        "opened": TODAY, "stage": None, "lawyer": 0,
        "docs": [
            ("צילום תעודת זהות + ספח", "missing", None),
            ("ייפוי כוח חתום", "missing", None),
            ("טופס ויתור על סודיות רפואית", "missing", None),
        ],
        "tasks": [
            ("פתיחת תיק ואיסוף מסמכים ראשוני", "client-docs", days(7),
             "normal", "open", False, None),
        ],
    },
    # ---------------------------------------------------------------
    {
        "key": "unassigned",
        "purpose": "תיק בלי עורך דין אחראי - נופל בין הכיסאות אם אין מי שרואה אותו",
        "client": "ג'ורג' חנא", "id8": "38519274", "phone": "052-8529637",
        "email": "george.h@example.com",
        "claim": "work-injury", "branch": "חיפה",
        "opened": days(-19), "stage": 1, "lawyer": None,
        "docs": [
            ("צילום תעודת זהות + ספח", "pending_review", None),
            ("ייפוי כוח חתום", "missing", None),
        ],
        "tasks": [
            ("שיוך התיק לעורך דין", "other", days(-1), "high",
             "open", False, "התיק נפתח לפני שבועיים ואיש לא שויך אליו."),
        ],
    },
    # ---------------------------------------------------------------
    {
        "key": "done-tasks",
        "purpose": "משימות שהושלמו ובוטלו - המצבים שמעטים רואים, ליד משימה פתוחה",
        "client": "אילנה קרמר", "id8": "47182936", "phone": "054-2583697",
        "email": "ilana.k@example.com",
        "claim": "general-disability", "branch": "תל אביב",
        "opened": days(-95), "stage": 5, "lawyer": 0,
        "docs": [
            ("צילום תעודת זהות + ספח", "approved", None),
            ("ייפוי כוח חתום", "approved", None),
            ("סיכומי אשפוז", "approved", None),
            ("אישור על קצבאות אחרות", "cancelled", None),
        ],
        "tasks": [
            ("הגשת התביעה לסניף", "nii-inquiry", days(-40), "normal",
             "done", False, None),
            ("איסוף סיכומי אשפוז", "medical-record", days(-55), "high",
             "done", False, None),
            ("בקשה לחוות דעת נוספת", "medical-record", days(-20), "low",
             "cancelled", False, "התייתר - הוועדה קיבלה את חוות הדעת הקיימת."),
            ("מעקב אחר החלטת הוועדה", "decision-followup", days(11),
             "normal", "open", False, None),
        ],
    },
]


# ==================================================================
#  בנייה
# ==================================================================

def _lookup(cur, firm_id):
    """מזהי העזר שכל התיקים נשענים עליהם."""
    # order by email ולא created_at: שני אנשי הצוות נוצרים
    # באותה טרנזקציה ולכן created_at שלהם זהה עד לרמת
    # המיקרו-שנייה. המיון היה שרירותי, ושיוך עורכי הדין
    # בתיקי ההדגמה יכול היה להתהפך בין הרצה להרצה.
    cur.execute("select id, email from users where firm_id = %s order by email",
                (firm_id,))
    users = [(r["id"], r["email"]) for r in cur.fetchall()]

    cur.execute("select id, code from claim_types where firm_id = %s", (firm_id,))
    claims = {r["code"]: r["id"] for r in cur.fetchall()}

    cur.execute("select id, code from task_types where firm_id = %s", (firm_id,))
    tasks = {r["code"]: r["id"] for r in cur.fetchall()}

    cur.execute(
        """select id, claim_type_id, position from stage_templates
            where firm_id = %s order by claim_type_id, position""",
        (firm_id,))
    stages = {}
    for r in cur.fetchall():
        stages[(r["claim_type_id"], r["position"])] = r["id"]

    return users, claims, tasks, stages


def build(reset=False):
    with cursor(commit=True) as cur:
        cur.execute("select id from firms order by created_at limit 1")
        row = cur.fetchone()
        if row is None:
            print("אין משרד במסד. יש להריץ קודם python -m server.db.seed")
            return 1
        firm_id = row["id"]

        if reset:
            n = _wipe(cur, firm_id)
            print("נמחקו %d תיקי הדגמה קודמים." % n)

        users, claims, task_types, stages = _lookup(cur, firm_id)
        if not users or not claims:
            print("חסרים משתמשים או סוגי תביעה. יש להריץ קודם python -m server.db.seed")
            return 1

        made = skipped = 0
        for spec in CASES:
            number = MARK + spec["key"].upper()
            cur.execute("select id from cases where firm_id = %s and case_number = %s",
                        (firm_id, number))
            if cur.fetchone():
                skipped += 1
                continue
            _one(cur, firm_id, number, spec, users, claims, task_types, stages)
            made += 1

    print("נוצרו %d תיקי הדגמה. %d כבר היו קיימים." % (made, skipped))
    if made:
        print("כל אחד נושא את התחילית %s ואת הסימון \"%s\" בשם הלקוח."
              % (MARK, NAME_SUFFIX.strip()))
    return 0


def _one(cur, firm_id, number, spec, users, claims, task_types, stages):
    lawyer = users[spec["lawyer"]][0] if spec.get("lawyer") is not None else None
    author = users[0][0]
    ct_id = claims[spec["claim"]]

    cur.execute(
        """insert into clients (firm_id, full_name, national_id_lookup,
                                national_id_enc, phone, email, privacy_accepted_at)
           values (%s,%s,%s,%s,%s,%s, now()) returning id""",
        (firm_id, spec["client"] + NAME_SUFFIX,
         id_lookup(il_id(spec["id8"])), id_encrypt(il_id(spec["id8"])),
         spec["phone"], spec["email"]))
    client_id = cur.fetchone()["id"]

    cur.execute(
        """insert into cases (firm_id, client_id, claim_type_id, case_number,
                              branch, opened_at, closed_at, status,
                              assigned_user_id, next_hearing_at)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (firm_id, client_id, ct_id, number, spec.get("branch"),
         spec["opened"], spec.get("closed"), spec.get("status", "active"),
         lawyer, spec.get("hearing")))
    case_id = cur.fetchone()["id"]

    # ---- שלבים ----
    # כל השלבים עד הנוכחי נרשמים כאירועים, אחרת "שלב 5" אינו
    # אומר דבר על מה שכבר קרה בתיק.
    if spec.get("stage"):
        span = (TODAY - spec["opened"]).days or 1
        for pos in range(1, spec["stage"] + 1):
            stage_id = stages.get((ct_id, pos))
            if not stage_id:
                continue
            when = spec["opened"] + dt.timedelta(
                days=int(span * (pos - 1) / max(spec["stage"], 1)))
            cur.execute(
                """insert into case_stage_events
                     (firm_id, case_id, stage_template_id, claim_type_id,
                      occurred_at, created_by_user_id)
                   values (%s,%s,%s,%s,%s,%s)""",
                (firm_id, case_id, stage_id, ct_id, when, author))

    # ---- מסמכים ----
    doc_ids = []
    for i, (name, status, reason) in enumerate(spec.get("docs", []), start=1):
        reviewed = author if status in ("approved", "rejected") else None
        cur.execute(
            """insert into case_documents
                 (firm_id, case_id, name, guidance, is_required, status,
                  reject_reason, reviewed_by_user_id, reviewed_at, position)
               values (%s,%s,%s,%s,true,%s,%s,%s,%s,%s) returning id""",
            (firm_id, case_id, name, None, status, reason, reviewed,
             hours(-72) if reviewed else None, i))
        doc_ids.append(cur.fetchone()["id"])

    # ---- דרישות ----
    req_ids = []
    for kind, title, guidance, status, due, doc_i in spec.get("requirements", []):
        # kind='document' מחייב document_id לפי אילוץ בסכמה.
        doc_id = doc_ids[doc_i] if doc_i is not None else None
        # שעה אמיתית ולא חצות. הטופס הוא datetime-local, כלומר
        # המשתמש בוחר שעה בפועל, ותאריך שנשמר כחצות מציג במסך
        # "בשעה 00:00" - נתון שאיש לא הזין. 17:00 הוא סוף יום
        # העבודה, וזה מה ש"עד תאריך X" אומר למעשה.
        due_at = (dt.datetime.combine(due, dt.time(17, 0)).astimezone()
                  if due else None)
        done = status == "completed"
        cur.execute(
            """insert into case_requirements
                 (firm_id, case_id, kind, title, guidance, due_at, status,
                  document_id, completed_at, completed_by_user_id,
                  created_by_user_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (firm_id, case_id, kind, title, guidance, due_at, status, doc_id,
             hours(-48) if done else None, author if done else None, author))
        req_ids.append(cur.fetchone()["id"])

    # ---- תזכורות ----
    for req_i, every, channel, paused in spec.get("reminders", []):
        cur.execute(
            """insert into reminder_rules
                 (firm_id, requirement_id, every_days, channel, is_paused,
                  created_by_user_id)
               values (%s,%s,%s,%s,%s,%s)""",
            (firm_id, req_ids[req_i], every, channel, paused, author))

    # ---- משלוחים ----
    for channel, status, ago, error in spec.get("deliveries", []):
        sent = hours(ago) if status in ("sent", "delivered") else None
        cur.execute(
            """insert into message_deliveries
                 (firm_id, requirement_id, subject_type, subject_id, channel,
                  to_address, template_key, status, error, attempts,
                  sent_at, delivered_at, created_by_user_id)
               values (%s,%s,'client',%s,%s,%s,'requirement.reminder',
                       %s,%s,%s,%s,%s,%s)""",
            (firm_id, req_ids[0] if req_ids else None, client_id, channel,
             spec["phone"] if channel in ("sms", "whatsapp") else spec["email"],
             status, error, 0 if status == "queued" else 1,
             sent, hours(ago) if status == "delivered" else None, author))

    # ---- משימות ----
    for title, code, due, priority, status, legal, desc in spec.get("tasks", []):
        done = status == "done"
        cur.execute(
            """insert into case_tasks
                 (firm_id, case_id, task_type_id, title, description,
                  assignee_user_id, due_at, status, priority,
                  is_legal_deadline, deadline_source,
                  confirmed_by_user_id, confirmed_at,
                  completed_at, completed_by_user_id, created_by_user_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (firm_id, case_id, task_types.get(code, task_types["other"]),
             title, desc if not legal else None, lawyer or author,
             dt.datetime.combine(due, dt.time(12, 0)).astimezone(),
             status, priority,
             # מועד משפטי מחייב מקור ואישור אנושי - אילוץ בסכמה,
             # ולא מוסכמה שאפשר לדלג עליה בזריעה.
             legal, desc if legal else None,
             author if legal else None, hours(-120) if legal else None,
             hours(-24) if done else None, author if done else None, author))

    # ---- החלטה ----
    if spec.get("decision"):
        d = spec["decision"]
        outcome, percent, permanent, decided, note = d[0], d[1], d[2], d[3], d[4]
        appeal = d[5] if len(d) > 5 else None
        cur.execute(
            """insert into case_decisions
                 (firm_id, case_id, decided_at, outcome, percent, is_permanent,
                  appeal_deadline, office_note, recorded_by_user_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (firm_id, case_id, decided, outcome, percent, permanent,
             appeal, note, author))

    # ---- הודעות ----
    for title, body, important, ago in spec.get("messages", []):
        cur.execute(
            """insert into messages
                 (firm_id, case_id, title, body, is_important,
                  sent_by_user_id, sent_at)
               values (%s,%s,%s,%s,%s,%s,%s)""",
            (firm_id, case_id, title, body, important, lawyer or author,
             hours(ago * 24)))

    # ---- שיחה ----
    for direction, channel, body, ago, read in spec.get("conversation", []):
        cur.execute(
            """insert into case_conversation
                 (firm_id, case_id, direction, channel, body,
                  sent_by_user_id, read_at, created_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (firm_id, case_id, direction, channel, body,
             (lawyer or author) if direction == "outbound" else None,
             hours(ago + 1) if read else None, hours(ago)))

    # ---- תגובות הלקוח על מסמך ----
    for kind, text in spec.get("replies", []):
        if doc_ids:
            cur.execute(
                """insert into document_replies
                     (firm_id, document_id, kind, text, is_read)
                   values (%s,%s,%s,%s,false)""",
                (firm_id, doc_ids[0], kind, text))

    # ---- מה הלאה, ומטרת התיק ----
    # השורה הראשונה אומרת מה התיק נועד להדגים. היא נראית
    # בפורטל, ולכן היא מנוסחת כמשפט ולא כתגית פנימית.
    steps = [("תיק הדגמה", spec["purpose"], "לצורכי בדיקה")]
    steps += [(t, d, e) for t, d, e in spec.get("steps", [])]
    for i, (title, desc, eta) in enumerate(steps, start=1):
        cur.execute(
            """insert into case_next_steps
                 (firm_id, case_id, title, description, eta_text, position)
               values (%s,%s,%s,%s,%s,%s)""",
            (firm_id, case_id, title, desc, eta, i))


def _wipe(cur, firm_id):
    """
    מוחק תיקי הדגמה בלבד.

    הסינון הוא על התחילית DEMO- במספר התיק, ולכן הוא אינו
    יכול לגעת בתיק אמיתי או בשני תיקי הזרע. המחיקה מדורגת:
    הילדים קודם, כי חלק מהמפתחות הזרים הם RESTRICT ולא CASCADE.
    """
    cur.execute("""select id, client_id from cases
                    where firm_id = %s and case_number like %s""",
                (firm_id, MARK + "%"))
    rows = cur.fetchall()
    if not rows:
        return 0

    case_ids = [r["id"] for r in rows]
    client_ids = [r["client_id"] for r in rows]

    cur.execute("""delete from message_deliveries
                    where firm_id = %s and requirement_id in (
                      select id from case_requirements where case_id = any(%s))""",
                (firm_id, case_ids))
    cur.execute("""delete from reminder_rules
                    where firm_id = %s and requirement_id in (
                      select id from case_requirements where case_id = any(%s))""",
                (firm_id, case_ids))
    cur.execute("delete from case_conversation where case_id = any(%s)", (case_ids,))
    cur.execute("delete from case_requirements where case_id = any(%s)", (case_ids,))
    cur.execute("""delete from document_replies where document_id in (
                     select id from case_documents where case_id = any(%s))""",
                (case_ids,))
    cur.execute("""delete from document_files where document_id in (
                     select id from case_documents where case_id = any(%s))""",
                (case_ids,))
    cur.execute("delete from case_documents where case_id = any(%s)", (case_ids,))
    cur.execute("delete from case_stage_events where case_id = any(%s)", (case_ids,))
    cur.execute("delete from case_next_steps where case_id = any(%s)", (case_ids,))
    cur.execute("delete from case_decisions where case_id = any(%s)", (case_ids,))
    cur.execute("delete from case_tasks where case_id = any(%s)", (case_ids,))
    cur.execute("delete from messages where case_id = any(%s)", (case_ids,))
    cur.execute("delete from notifications where case_id = any(%s)", (case_ids,))
    cur.execute("delete from audit_log where case_id = any(%s)", (case_ids,))
    cur.execute("delete from cases where id = any(%s)", (case_ids,))
    cur.execute("delete from otp_challenges where client_id = any(%s)", (client_ids,))
    cur.execute("delete from sessions where subject_id = any(%s)", (client_ids,))
    cur.execute("delete from clients where id = any(%s)", (client_ids,))
    return len(case_ids)


def main():
    ap = argparse.ArgumentParser(description="תיקי הדגמה שמכסים את כל היכולות")
    ap.add_argument("--reset", action="store_true",
                    help="מוחק תיקי DEMO- קיימים ויוצר מחדש")
    args = ap.parse_args()
    try:
        return build(reset=args.reset)
    except MissingKey as exc:
        print(exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
