"""
textfix.py - תיקוני טקסט עברי שחולץ ממסמך. פונקציות טהורות, בלי I/O.

רץ בתוך ה-sandbox, ולכן אינו מייבא דבר מהשרת.

שלוש בעיות שה-spike מצא (spikes/ocr/RESULTS.md):

1. סדר מילים ויזואלי בשכבת הטקסט של PDF. גם PDF מודרני מחזיר
   ":הנבדק שם" במקום "שם הנבדק:" - אותיות בסדר לוגי, מילים בסדר
   ויזואלי. CER נאיבי: 68%. מתוקן: 0.5%.
2. סדר תווים הפוך לגמרי (מערכות ישנות): אותיות סופיות בתחילת מילה.
3. Tesseract קורא גרשיים כ-"יי": "דייר" במקום ד"ר.

מה *לא* נעשה כאן: תיקון ספרות. OCR מחליף 2/7 (25% -> 75%), ושום
"תיקון" אוטומטי לא יידע איזו ספרה נכונה. זה נפתר בשלב 5 באישור
אדם מול המקור - לא בניחוש כאן.
"""

import re
import unicodedata

_HEB = re.compile(r"[א-ת]")
_HEB_RUN = re.compile(r"[א-ת]+")
_HEB_WORD = re.compile(r"[א-ת]{2,}")
_FINALS = "ךםןףץ"            # ך ם ן ף ץ
_LEAD_PUNCT = re.compile(r"^[.,:;!?]+(?=[א-ת])")
_TRAIL_PUNCT = re.compile(r"(?<=[א-ת])[.,:;!?]+$")
# LRM, RLM, LRE..RLO, LRI..PDI, BOM - סימני כיוון בלתי נראים
_BIDI_MARKS = re.compile(r"[‎‏‪-‮⁦-⁩﻿]")
_NIKUD = re.compile(r"[֑-ׇ]")


def normalize(text: str) -> str:
    """NFC, בלי סימני כיוון ובלי ניקוד, רווחים מכווצים. שורות נשמרות."""
    text = unicodedata.normalize("NFC", text.replace("\r", ""))
    text = _BIDI_MARKS.sub("", text)
    text = _NIKUD.sub("", text)
    text = text.replace("״", '"').replace("׳", "'")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def chars_reversed(text: str) -> bool:
    words = _HEB_WORD.findall(text)
    starts = sum(1 for w in words if w[0] in _FINALS)
    ends = sum(1 for w in words if w[-1] in _FINALS)
    return starts > ends and starts >= 3


def fix_chars_reversed(text: str) -> str:
    return "\n".join(line[::-1] for line in text.split("\n"))


def words_reversed(text: str) -> bool:
    tokens = text.split()
    lead = sum(1 for t in tokens if _LEAD_PUNCT.match(t))
    trail = sum(1 for t in tokens if _TRAIL_PUNCT.search(t))
    return lead > trail and lead >= 2


def fix_words_reversed(text: str) -> str:
    """
    אלגוריתם ה-bidi של יוניקוד (python-bidi, LGPL-3.0) מסדר מילים,
    פיסוק וסוגריים - והופך גם אותיות. אחריו כל רצף אותיות עבריות
    מוחזר לסדרו. כללים ידניים נכשלו ב-spike על "ר"ד" ו-"ית/נוירולוג".
    """
    from bidi import get_display

    out = []
    for line in text.split("\n"):
        if not _HEB.search(line):
            out.append(line)
            continue
        display = get_display(line, base_dir="R")
        out.append(_HEB_RUN.sub(lambda m: m.group(0)[::-1], display))
    return "\n".join(out)


def fix_layer(text: str):
    """(טקסט מתוקן, אילו תיקונים הוחלו) - לשכבת טקסט של PDF."""
    applied = []
    if chars_reversed(text):
        text = fix_chars_reversed(text)
        applied.append("chars")
    if words_reversed(text):
        text = fix_words_reversed(text)
        applied.append("words")
    return normalize(text), applied


# קיצורים עם גרשיים, רק במקומות שהם עומדים לבד. החלפה כללית של "יי"
# הייתה הורסת מילים אמיתיות.
_ABBREV = [
    (re.compile(r"(?<![א-ת])ד(?:יי|''|\"\"|')ר(?![א-ת])"),
     'ד"ר'),
    (re.compile(r"(?<![א-ת])יו(?:יי|''|\"\"|')ר(?![א-ת])"),
     'יו"ר'),
]


def fix_ocr(text: str) -> str:
    for pattern, repl in _ABBREV:
        text = pattern.sub(repl, text)
    return text
