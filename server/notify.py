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


class Sender:
    """הממשק שספק אמיתי יצטרך לממש."""

    def send_otp(self, phone, code):
        raise NotImplementedError


class ConsoleSender(Sender):
    """פיתוח בלבד."""

    production_safe = False

    def send_otp(self, phone, code):
        print("[OTP][demo] קוד עבור %s: %s" % (phone, code))


class NullSender(Sender):
    """פרודקשן בלי ספק. נכשל סגור."""

    production_safe = True

    def send_otp(self, phone, code):
        raise SmsNotConfigured(
            "לא הוגדר ספק SMS. שליחת קוד אינה אפשרית במצב ייצור."
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
