"""
scan.py - סריקת קבצים שהועלו.

⚠️ אין כאן מנוע אנטי-וירוס. לא הותקן ולא נבחר.

הכלל היחיד שאסור לשבור
-----------------------
`clean` נכתב אך ורק על ידי סורק אמיתי שהחזיר תוצאה. אין בקובץ
הזה - ובשום מקום אחר - נתיב שמסמן קובץ כנקי בלי סריקה.

מה קורה בפועל
--------------
demo       - NoScanner. הקובץ נשאר pending לנצח, ואינו ניתן
             להורדה. זה מצב גלוי ולא תקלה.
production - בלי סורק מוגדר, ההעלאה עצמה נדחית ב-503. מוטב
             לסרב לקבל מסמך מאשר לקבל אותו ולהשאיר אותו תקוע.

מימוש אמיתי יתחבר כאן (ClamAV דרך clamd, או שירות ענן), יחזיר
Result, והקוד שסביב לא ישתנה.
"""

import os


class ScannerNotConfigured(RuntimeError):
    """אין סורק. נזרק בייצור במקום להעמיד פנים."""


class Result:
    """תוצאת סריקה. status חייב להיות אחד מערכי scan_status בסכמה."""

    def __init__(self, status, detail=None):
        assert status in ("pending", "clean", "infected", "failed")
        self.status = status
        self.detail = detail


class Scanner:
    """הממשק שמנוע אמיתי יצטרך לממש."""

    #: האם מותר להסתמך על הסורק הזה בייצור
    production_safe = False

    def scan(self, data: bytes) -> Result:
        raise NotImplementedError


class NoScanner(Scanner):
    """
    אין מנוע. הקובץ נשאר pending.

    במכוון אינו מחזיר clean ואינו מחזיר failed: הקובץ לא נסרק,
    וזה בדיוק מה ש-pending אומר.
    """

    production_safe = False

    def scan(self, data: bytes) -> Result:
        return Result("pending", "לא הוגדר מנוע סריקה; הקובץ לא נסרק.")


def get_scanner() -> Scanner:
    return NoScanner()


def is_production() -> bool:
    return os.environ.get("PORTAL_MODE", "demo").strip().lower() == "production"


def assert_upload_allowed() -> None:
    """
    נקרא לפני קבלת קובץ.

    בייצור בלי סורק בטוח - סירוב. אין fallback ל-NoScanner.
    """
    if is_production() and not get_scanner().production_safe:
        raise ScannerNotConfigured(
            "לא הוגדר מנוע סריקת קבצים. העלאת מסמכים מושבתת במצב ייצור."
        )


def downloadable(scan_status: str) -> bool:
    """רק קובץ שנסרק ונמצא נקי ניתן להורדה."""
    return scan_status == "clean"
