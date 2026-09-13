"""
redact.py - חיטוי טקסט חופשי לפני יציאה לספק AI חיצוני.

למה זה קיים
------------
policy.py בונה allowlist לפי *שמות שדות*. זה נכון, אבל אינו מספיק:
שדה מותר כמו note או rejectReason הוא טקסט חופשי שפרליגל כותב, ואין
מה שימנע ממנו לכתוב שם "לתאם מול ישראל ישראלי, ת\"ז 123456789".
בדיקה לפי שם שדה תעביר את זה בשלום.

לכן שתי שכבות משלימות:
  1. מזעור  - policy.py לא שולח טקסט חופשי שאינו הכרחי.
  2. חיטוי  - מה שכן נשלח עובר כאן, והבדיקה היא על *הערך*.

מה מזוהה
---------
ת"ז ישראלית, טלפון ישראלי, אימייל, IBAN, ומספר כרטיס אשראי.

מה *לא* מזוהה
--------------
שמות פרטיים וכתובות. אין דרך אמינה לזהות אותם בטקסט עברי חופשי בלי
מודל ייעודי, ורשימת שמות הייתה נותנת ביטחון כוזב. ההגנה עליהם היא
מזעור: שדות שאינם נחוצים ל-AI פשוט אינם נשלחים.
"""

import re

MASK = "[הוסר]"

# ----------------------------------------------------------------
#  ת"ז ישראלית
# ----------------------------------------------------------------
#  תשע ספרות עם ספרת ביקורת. הבדיקה על הביקורת מכוונת: בלעדיה כל
#  מספר תיק בן תשע ספרות היה נמחק, והטקסט שמגיע למודל היה נפגע.
# ----------------------------------------------------------------

_ID_CANDIDATE = re.compile(r"(?<!\d)(\d{9})(?!\d)")


def is_israeli_id(digits: str) -> bool:
    """אלגוריתם ספרת הביקורת של משרד הפנים."""
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(digits):
        step = int(ch) * (1 if i % 2 == 0 else 2)
        total += step if step < 10 else step - 9
    return total % 10 == 0


def _mask_ids(text: str) -> str:
    return _ID_CANDIDATE.sub(
        lambda m: MASK if is_israeli_id(m.group(1)) else m.group(1), text
    )


# ----------------------------------------------------------------
#  שאר הדפוסים
# ----------------------------------------------------------------

PATTERNS = [
    # אימייל
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), MASK),
    # IBAN
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), MASK),
    # כרטיס אשראי - 13 עד 19 ספרות עם מפרידים אפשריים
    (re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), MASK),
    # טלפון ישראלי: 05X-0000000, 0X-0000000, +9725X…
    (re.compile(r"(?<!\d)(?:\+?972[-\s]?|0)\d{1,2}[-\s]?\d{3}[-\s]?\d{4}(?!\d)"), MASK),
]


def redact(text):
    """מחזיר את הטקסט בלי מזהים. קלט שאינו מחרוזת מוחזר כמות שהוא."""
    if not isinstance(text, str) or not text:
        return text
    out = _mask_ids(text)
    for pattern, mask in PATTERNS:
        out = pattern.sub(mask, out)
    return out


def find_pii(text):
    """מחזיר את סוגי המזהים שנמצאו. משמש את שכבת הבדיקה האחרונה."""
    if not isinstance(text, str) or not text:
        return []
    found = []
    if any(is_israeli_id(m.group(1)) for m in _ID_CANDIDATE.finditer(text)):
        found.append("israeli_id")
    names = ["email", "iban", "credit_card", "phone"]
    for name, (pattern, _) in zip(names, PATTERNS):
        if pattern.search(text):
            found.append(name)
    return found
