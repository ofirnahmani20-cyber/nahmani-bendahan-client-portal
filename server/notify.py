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
