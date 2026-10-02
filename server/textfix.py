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


def visual_word_order(text: str, centers) -> bool | None:
    """
    האם סדר המילים בשכבת הטקסט ויזואלי, לפי *מיקום התווים בעמוד*.

    בשורה עברית בסדר לוגי, המילה הבאה נמצאת משמאל לקודמת. אם אחרי
    רווח האות העברית הבאה נמצאת *מימין* - הסדר ויזואלי. זו עובדה על
    העמוד, לא ניחוש: הזיהוי לפי פיסוק (words_reversed) נכשל במסמכים
    קצרים בלי נקודות ונקודתיים (נמצא בשלב 4: "בתביעה החלטה").

    centers: לכל אינדקס בטקסט (x, y) של מרכז התו, או None.
    None כשאין מספיק מעברים כדי להחליט.
    """
    visual = logical = 0
    prev, gap = None, False
    for i, ch in enumerate(text):
        if ch in " \t":
            gap = prev is not None
            continue
        if ch in "\r\n" or not _HEB.match(ch) or centers[i] is None:
            prev, gap = None, False
            continue
        if prev is not None and gap:
            (px, py), (cx, cy) = centers[prev], centers[i]
            if abs(cy - py) < 4:                       # אותה שורה
                if cx > px:
                    visual += 1
                else:
                    logical += 1
        prev, gap = i, False
    if visual + logical < 2:
        return None
    return visual > logical


def fix_layer(text: str, visual_words=None):
    """
    (טקסט מתוקן, אילו תיקונים הוחלו) - לשכבת טקסט של PDF.
    visual_words: התשובה של visual_word_order, אם חושבה. None -> זיהוי
    לפי פיסוק, כגיבוי.
    """
    applied = []
    if chars_reversed(text):
        text = fix_chars_reversed(text)
        applied.append("chars")
    if visual_words is None:
        visual_words = words_reversed(text)
    if visual_words:
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
