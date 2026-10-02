"""
make_holdout.py - קבוצת ביקורת (holdout) לסיווג. נכתבה *אחרי* שהכללים
הוקפאו בגרסה 2026.10.3, ואסור לכוונן את הכללים לפיה.

    python spikes/classification/make_holdout.py
    set CLASSIFY_CORPUS=%LOCALAPPDATA%\\nahmani-classify-holdout
    python spikes/classification/measure.py

למה: הקורפוס הראשי שימש גם לכתיבת הכללים וגם למדידה - ולכן המספרים
עליו הם גבול עליון. כאן אותם סוגים בניסוח אחר: כותרות אחרות, סדר
אחר, פחות מילות מפתח "של ספר לימוד". זה הקירוב הכי הוגן שאפשר בלי
מסמכים אמיתיים.

הכול סינתטי ומסומן כך.
"""

import json
import os
import pathlib
import random
import sys

import importlib.util

HERE = pathlib.Path(__file__).resolve().parent
# שני מודולים בשם make_corpus (ocr ו-classification) - טוענים לפי נתיב.
_spec = importlib.util.spec_from_file_location("classify_corpus", HERE / "make_corpus.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)
doc = cc.doc

OUT = pathlib.Path(os.environ["LOCALAPPDATA"]) / "nahmani-classify-holdout"
cc.OUT = OUT
cc.rng = random.Random(777)
P, D, ID = cc.P, cc.D, cc.ID

HOLDOUT = [
    ("hospital_discharge", doc("מכתב שחרור מהמחלקה", "בית חולים צפון סינתטי - כירורגיה",
        "המטופל אושפז בין התאריכים %s ל-%s." % (D(), D()),
        "אבחנות בשחרור: שבר בעצם הבריח. הומלץ על מנוחה.")),
    ("er_record", doc("רפואה דחופה - סיכום", "מטופל: %s" % P(),
        "הגיע למיון לאחר תאונת דרכים. לא נמצאו ממצאים חריגים בצילום.")),
    ("physician_visit", doc("סיכום מפגש עם רופא", "מרפאת עיניים סינתטית",
        "תלונות: ראייה מטושטשת. המלצות: בדיקה חוזרת בעוד שלושה חודשים.")),
    ("sick_leave", doc("תעודת מחלה", "המבוטח %s אינו כשיר לעבודה." % P(),
        "תקופת אי הכושר: %s עד %s." % (D(), D()))),
    ("lab_results", doc("בדיקות דם - תוצאות", "מטופל: %s" % P(),
        "ספירת דם מלאה: ערכים בטווח התקין. CRP מוגבר.")),
    ("imaging", doc("בדיקת אולטרסאונד של הכתף", "מכון דימות דרום סינתטי",
        "ממצאים: קרע חלקי בגיד הסופרספינטוס.")),
    ("nerve_conduction", doc("נוירופיזיולוגיה קלינית - EMG", "נבדק: %s" % P(),
        "הולכה מוטורית של העצב האולנרי תקינה. לא נמצאה דנרבציה.")),
    ("psychiatric", doc("בדיקה פסיכיאטרית", "נבדק: %s" % P(),
        "מצב נפשי: מתאר סיוטים ודריכות יתר מאז התאונה. אבחנה: PTSD.")),
    ("psychological", doc("הערכה פסיכולוגית", "הנבדקת: %s" % P(),
        "תפקוד קוגניטיבי בטווח הנמוך. מומלץ טיפול רגשי.")),
    ("functional_report", doc("הערכת תפקוד ביתית", "שם: %s" % P(),
        "זקוק לעזרה חלקית בפעולות יומיום. מגבלה תפקודית קשה בהליכה.")),
    ("treating_opinion", doc("מכתב רופא מטפל", "הנדון: %s" % P(),
        "המטופלת נמצאת במעקב אצלי בשל פריצת דיסק.")),
    ("expert_opinion", doc("חוות דעת מומחה בתחום הנוירולוגיה", "נתבקשתי לחוות דעתי.",
        "דיון ומסקנות: נכות צמיתה בשיעור 15%.")),
    ("nii_committee", doc("פרוטוקול ועדה", "המוסד לביטוח לאומי - סינתטי",
        "הוועדה קבעה כי אין נכות מעל 9%. ניתן להגיש ערר.")),
    ("nii_decision", doc("החלטה בתביעתך לדמי פגיעה", "המוסד לביטוח לאומי - סינתטי",
        "לאחר בדיקה, תביעתך לא אושרה. ניתן לערער לבית הדין לעבודה.")),
    ("nii_form", doc("טופס ביטוח לאומי", "תביעה לתשלום דמי פגיעה - העתק סינתטי",
        "פרטי התובע: %s" % P(), "חתימת התובע: ____")),
    ("payslip", doc("תלוש משכורת לחודש 09/2026", "שכר ברוטו 11,000",
        "ניכויים: מס הכנסה 900. שכר נטו 9,400.")),
    ("employment_confirmation", doc("אישור על העסקה", "%s עובד אצלנו בתפקיד נהג." % P(),
        "היקף משרה: חלקית, החל מתאריך %s." % D())),
    ("pension_fund", doc("קרן פנסיה סינתטית - פנסיית נכות", "עמית: %s" % P(),
        "הוגשה תביעה לקצבת נכות מהקרן.")),
    ("insurance_policy", doc("פוליסת ביטוח אובדן כושר עבודה", "המבוטח: %s" % P(),
        "תקופת הביטוח: 5 שנים. פרמיה חודשית: 220.")),
    ("power_of_attorney", doc("יפוי כח", "אני החתום מטה ממנה את עו\"ד %s לייצגני." % P(),
        "חתימת המייפה: ____")),
    ("affidavit", doc("תצהיר עדות", "אני הח\"מ %s, לאחר שהוזהרתי כדין," % P(),
        "מצהיר כי הייתי עד לתאונה.")),
    ("appeal_filing", doc("הודעת ערעור", "המערער: %s" % P(),
        "נימוקי הערר: ההחלטה ניתנה ללא בדיקה. מבוקש לבטל.")),
    ("court_decision", doc("פסק דין", "בית הדין האזורי לעבודה בחיפה - סינתטי",
        "התביעה נדחית. ניתן היום בהעדר הצדדים.")),
    ("id_card", doc("ספח תעודת זהות", "שם: %s" % P(), "כתובת: רחוב סינתטי 1, חיפה")),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for i, (kind, lines) in enumerate(HOLDOUT, 1):
        name = "holdout-%02d-%s" % (i, kind)
        cc.write_pdf(name, [lines], {"group": "kind", "expect": "clear", "kind": kind})
        cc.write_scan(name)
    print("%d holdout documents in %s" % (len(HOLDOUT), OUT))


if __name__ == "__main__":
    main()
