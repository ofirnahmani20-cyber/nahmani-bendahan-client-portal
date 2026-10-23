"""
notify.py - שליחת קוד חד-פעמי.

⚠️ אין כאן ספק SMS אמיתי. לא נבחר ספק ולא נחתם הסכם עיבוד נתונים
   (ראה 12.2 ב-"05 - אפיון מוצר").

הקובץ מגדיר interface ושתי מימושים:
  ConsoleSender - פיתוח בלבד. כותב את הקוד ללוג התהליך.
  NullSender    - פרודקשן בלי ספק. מסרב, ולא כותב את הקוד לשום מקום.

הכלל שאסור לשבור
-----------------
ב-PORTAL_MODE=production אין נפילה ל-ConsoleSender. קוד OTP שנכתב
ללוג בייצור הוא קוד שדלף - לוגים נאספים, נשלחים ונקראים. לכן בלי
ספק מוגדר הבקשה נכשלת ב-503 ואינה מתחזה להצלחה.
"""

import os


class SmsNotConfigured(RuntimeError):
    """אין ספק SMS. נזרק במקום לזייף שליחה."""


#: הערוצים שהמודל מכיר. sms בלבד נתמך בפועל; השאר מוגדרים כדי
#: שהוספת ספק בעתיד לא תדרוש שינוי סכמה או חוזה.
CHANNELS = ("sms", "whatsapp", "email", "portal")


#: נוסחי ההודעות. ספק אמיתי ירנדר מהם; עד אז הם החוזה.
#:
#: הכלל: אין כאן שום מידע על מצב רפואי, סוג מסמך או תוצאה. SMS נשאר
#: בטלפון ומוצג על מסך נעול. הפרטים - רק אחרי כניסה מאומתת לאתר.
#: tests/test_notifier.py אוכף את הרשימה הסגורה של הפרמטרים.
TEMPLATES = {
    "requirement.reminder": "תזכורת ממשרד נחמני-בן דהן: מחכה לך פריט להשלמה בתיק. "
                            "לפרטים: {portal_url}",
    "case.update": "יש עדכון חדש בתיק שלך במשרד נחמני-בן דהן. לצפייה: {portal_url}",
    "case.hearing_reminder": "תזכורת: נקבע לך מועד ב-{date} בשעה {time}. "
                             "פרטים והכנה: {portal_url}",
    "staff.morning_digest": "סיכום הבוקר: {overdue} משימות באיחור, {critical} דחופות, "
                            "{today} להיום, {toReview} מסמכים לבדיקה, {unread} הודעות "
                            "שלא נקראו, {hearings} ועדות השבוע, {autoDecided} מסמכים "
                            "שהמערכת אישרה או דחתה לבד, {mailbox} קבצים מהמייל "
                            "שממתינים לשיוך.",
}

#: הפרמטרים שמותר להעביר לכל נוסח. כל דבר אחר - נדחה.
TEMPLATE_PARAMS = {
    "requirement.reminder": {"requirement_id"},
    "case.update": set(),
    "case.hearing_reminder": {"date", "time"},
    "staff.morning_digest": {"overdue", "critical", "today", "toReview", "unread", "hearings",
                             "autoDecided", "mailbox"},
}


def render(template_key, params=None):
    """הנוסח הסופי. portal_url מגיע מהסביבה, לא מהקורא."""
    allowed = TEMPLATE_PARAMS[template_key]
    extra = set(params or {}) - allowed
    if extra:
        raise ValueError("פרמטרים אסורים לנוסח %s: %s" % (template_key, sorted(extra)))
    values = {"portal_url": os.environ.get("PORTAL_PUBLIC_URL", "")}
    values.update(params or {})
    return TEMPLATES[template_key].format(**values)


class Sender:
    """
    הממשק שספק אמיתי יצטרך לממש.

    שתי מתודות בכוונה: send_otp נשאר נפרד כי הוא רגיש אחרת -
    קוד חד-פעמי שנכתב ללוג הוא קוד שדלף - ואילו send משמש כל
    הודעה אחרת. שתיהן חייבות להיכשל סגור בלי ספק.
    """

    #: האם מותר להסתמך על המימוש הזה בייצור
    production_safe = False

    def send_otp(self, phone, code):
        raise NotImplementedError

    def send(self, channel, to, template_key, params=None):
        """
        שולח הודעה לפי תבנית ומחזיר מזהה אצל הספק, או None.

        גוף ההודעה אינו מועבר כאן אלא מפתח תבנית, כדי שהגוף לא
        ייכתב ללוג, למסד או לטבלת המשלוחים.
        """
        raise NotImplementedError


class ConsoleSender(Sender):
    """פיתוח בלבד."""

    production_safe = False

    def send_otp(self, phone, code):
        print("[OTP][demo] קוד עבור %s: %s" % (phone, code))

    def send(self, channel, to, template_key, params=None):
        print("[%s][demo] אל %s · תבנית %s" % (channel, to, template_key))
        return None


class NullSender(Sender):
    """פרודקשן בלי ספק. נכשל סגור."""

    production_safe = True

    def send_otp(self, phone, code):
        raise SmsNotConfigured(
            "לא הוגדר ספק SMS. שליחת קוד אינה אפשרית במצב ייצור."
        )

    def send(self, channel, to, template_key, params=None):
        raise SmsNotConfigured(
            "לא הוגדר ספק ל-%s. שליחת הודעות אינה אפשרית במצב ייצור." % channel
        )


def get_sender():
    """
    בוחר מימוש לפי מצב ההרצה.

    אין כאן קריאה ל-ConsoleSender בייצור בשום נתיב - זו בדיוק
    הנפילה השקטה שהתבקשנו למנוע.
    """
    is_production = os.environ.get("PORTAL_MODE", "demo").strip().lower() == "production"
    if is_production:
        # כאן ייכנס ספק אמיתי כשייבחר, לפי משתנה סביבה.
        return NullSender()
    return ConsoleSender()
