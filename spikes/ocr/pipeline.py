"""
pipeline.py - אב-טיפוס של צנרת החילוץ (חלופה A: מקומי, OCR + כללים).

זה קוד spike: הוא קיים כדי למדוד, לא כדי לרוץ בשרת. הוא מנסה
עם זאת לשקף את המבנה המתוכנן לשלב 3, כדי שהמדידה תהיה רלוונטית:

    guards -> שכבת טקסט (pypdfium2) -> OCR לעמודים ריקים (Tesseract)
           -> נרמול -> Classifier (כללים) -> Extractor (כללים)

אין קריאת רשת בשום מקום כאן.
"""

import os
import pathlib
import re
import subprocess
import tempfile
import unicodedata

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter, ImageOps

HOME = pathlib.Path(os.environ["LOCALAPPDATA"]) / "nahmani-ocr-spike"
TESSERACT = os.environ.get("TESSERACT",
                           str(pathlib.Path.home() / "scoop" / "shims" / "tesseract.exe"))

# ------------------------------------------------------------------
#  מגבלות משאבים - אלה בדיוק הערכים שיעברו ל-worker
# ------------------------------------------------------------------
MAX_PAGES = 50
MAX_PAGE_PIXELS = 40_000_000          # ~ A3 ב-300dpi, עם מרווח
OCR_DPI = 300
TESSERACT_TIMEOUT = 60                # לעמוד
MIN_LAYER_CHARS = 40                  # פחות מזה בעמוד = סריקה, צריך OCR
FIX_ORDER = os.environ.get("SPIKE_FIX_ORDER", "1") == "1"
ICD_PASS = os.environ.get("SPIKE_ICD_PASS", "1") == "1"
Image.MAX_IMAGE_PIXELS = MAX_PAGE_PIXELS


class Rejected(ValueError):
    """הקובץ נדחה על ידי מגבלת משאבים. לא קריסה - החלטה."""


# ------------------------------------------------------------------
#  OCR
# ------------------------------------------------------------------

def ocr(img: Image.Image, model="best", psm=3, lang="heb+eng") -> str:
    """Tesseract כתהליך נפרד עם timeout. התמונה עוברת דרך קובץ זמני
    *בתוך תיקיית ה-spike* - ב-worker האמיתי זה יהיה stdin/tmpfs."""
    tessdata = HOME / "tessdata" / model
    with tempfile.TemporaryDirectory(dir=HOME) as tmp:
        src = pathlib.Path(tmp) / "page.png"
        img.save(src)
        result = subprocess.run(
            [TESSERACT, str(src), "stdout", "-l", lang, "--psm", str(psm),
             "--tessdata-dir", str(tessdata)],
            capture_output=True, timeout=TESSERACT_TIMEOUT)
    return result.stdout.decode("utf-8", "replace")


PREPARE = os.environ.get("SPIKE_PREPARE", "flatten")


def prepare(img: Image.Image) -> Image.Image:
    """
    גווני אפור, ואז יישור תאורה: חלוקה ברקע מטושטש מאוד.

    v0 נתן לטסרקט את התמונה כמות שהיא - והבינאריזציה הגלובלית
    שלו (Otsu) קרסה על צילום טלפון עם תאורה לא אחידה: 0 מ-18.
    """
    img = img.convert("L")
    if PREPARE == "none":
        return img
    # רקע = "הנייר": MaxFilter מוחק את הדיו, הטשטוש מחליק. חלוקה
    # בו משטחת את מפל התאורה ומשאירה את הכתב.
    background = img.filter(ImageFilter.MaxFilter(15)).filter(ImageFilter.GaussianBlur(30))
    a = np.asarray(img, dtype=np.float32)
    b = np.maximum(np.asarray(background, dtype=np.float32), 1.0)
    flat = Image.fromarray(np.clip(a / b * 255.0, 0, 255).astype(np.uint8))
    return ImageOps.autocontrast(flat, cutoff=1)


# ------------------------------------------------------------------
#  סדר ויזואלי
# ------------------------------------------------------------------
#  PDF ממערכות ישנות שומר עברית בסדר הפוך. הסימן האמין: אותיות
#  סופיות (ך ם ן ף ץ) מופיעות בתחילת מילה במקום בסופה.
# ------------------------------------------------------------------

FINALS = "ךםןףץ"
_WORD = re.compile(r"[א-ת]{2,}")


def looks_visual(text: str) -> bool:
    words = _WORD.findall(text)
    starts = sum(1 for w in words if w[0] in FINALS)
    ends = sum(1 for w in words if w[-1] in FINALS)
    return starts > ends and starts >= 3


def fix_visual(text: str) -> str:
    return "\n".join(line[::-1] for line in text.splitlines())


# ------------------------------------------------------------------
#  סדר מילים ויזואלי
# ------------------------------------------------------------------
#  הממצא החשוב של ה-spike: גם PDF מודרני (Chromium/Skia, וכך גם
#  מערכות רבות) מחזיר משכבת הטקסט שורות שבהן *סדר המילים* הפוך,
#  אף שהאותיות בתוך כל מילה תקינות: ":הנבדק שם" במקום "שם הנבדק:".
#  הסימן: סימני פיסוק *לפני* מילה עברית במקום אחריה.
# ------------------------------------------------------------------

_LEAD_PUNCT = re.compile(r"^([.,:;!?]+)(?=[א-ת])")
_TRAIL_PUNCT = re.compile(r"(?<=[א-ת])[.,:;!?]+$")
_HEB_RUN = re.compile(r"[א-ת]+")


def word_order_reversed(text: str) -> bool:
    tokens = text.split()
    lead = sum(1 for t in tokens if _LEAD_PUNCT.match(t))
    trail = sum(1 for t in tokens if _TRAIL_PUNCT.search(t))
    return lead > trail and lead >= 2


def fix_word_order(text: str) -> str:
    """
    שכבת הטקסט היא "מעורבת": אותיות בסדר לוגי בתוך כל מילה, אבל
    מילים, פיסוק וסוגריים בסדר ויזואלי. אלגוריתם ה-bidi של יוניקוד
    (python-bidi) מחזיר את סדר המילים והפיסוק נכון - אבל הופך גם
    את האותיות - ולכן אחריו כל רצף אותיות עבריות מוחזר לסדרו.

    ניסיון ראשון היה כללים ידניים (היפוך טוקנים, הזזת פיסוק); הוא
    נכשל על "ר"ד", "ית/נוירולוג" ו-"ז.ת:." - בדיוק המקרים שהאלגוריתם
    התקני פותר.
    """
    from bidi import get_display

    out = []
    for line in text.splitlines():
        if not _HEB_RUN.search(line):
            out.append(line)
            continue
        display = get_display(line, base_dir="R")
        out.append(_HEB_RUN.sub(lambda m: m.group(0)[::-1], display))
    return "\n".join(out)


# ------------------------------------------------------------------
#  חילוץ טקסט
# ------------------------------------------------------------------

def extract_text(path: pathlib.Path, model="best"):
    """
    מחזיר (text, info). info: pages, ocr_pages, layer_pages, visual_fixed.
    """
    info = {"pages": 0, "ocr_pages": 0, "layer_pages": 0, "visual_fixed": False,
            "aux_text": ""}
    suffix = path.suffix.lower()

    def ocr_page(img):
        img = prepare(img)
        # מעבר שני באנגלית בלבד: קודים כמו G56.0 בתוך שורה עברית
        # נקראים במעבר המשולב כ-"156.0)". הטקסט הזה משמש רק לחילוץ
        # עובדות, לא לתצוגה ולא ל-CER.
        if ICD_PASS:
            info["aux_text"] += "\n" + ocr(img, model, lang="eng")
        return ocr(img, model)

    if suffix in (".png", ".jpg", ".jpeg"):
        try:
            img = Image.open(path)
            img.load()
        except Image.DecompressionBombError:
            raise Rejected("image exceeds pixel limit")
        info.update(pages=1, ocr_pages=1)
        return ocr_page(img), info

    if suffix != ".pdf":
        raise Rejected("unsupported type")

    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError:
        raise Rejected("corrupt pdf")
    try:
        n = len(pdf)
        if n > MAX_PAGES:
            raise Rejected("too many pages (%d)" % n)
        info["pages"] = n
        parts = []
        for i in range(n):
            page = pdf[i]
            layer = page.get_textpage().get_text_range()
            if len(layer.strip()) >= MIN_LAYER_CHARS:
                if FIX_ORDER and looks_visual(layer):
                    layer = fix_visual(layer)
                    info["visual_fixed"] = True
                if FIX_ORDER and word_order_reversed(layer):
                    layer = fix_word_order(layer)
                    info["visual_fixed"] = True
                parts.append(layer)
                info["layer_pages"] += 1
                continue
            w, h = page.get_size()
            pixels = (w / 72 * OCR_DPI) * (h / 72 * OCR_DPI)
            if pixels > MAX_PAGE_PIXELS:
                raise Rejected("page too large to render (%.0f MP)" % (pixels / 1e6))
            img = page.render(scale=OCR_DPI / 72).to_pil()
            parts.append(ocr_page(img))
            info["ocr_pages"] += 1
        return "\n".join(parts), info
    finally:
        pdf.close()


# ------------------------------------------------------------------
#  נרמול
# ------------------------------------------------------------------

_NIKUD = re.compile(r"[֑-ׇ]")
QUOTES = {"״": '"', "“": '"', "”": '"', "''": '"',
          "׳": "'", "’": "'", "‘": "'", "`": "'",
          "—": "-", "–": "-"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _NIKUD.sub("", text)
    for a, b in QUOTES.items():
        text = text.replace(a, b)
    # שכבת טקסט של PDF מוסיפה סימני כיוון בלתי נראים
    text = re.sub(r"[‎‏‪-‮﻿\r]", "", text)
    # טסרקט קורא גרשיים (״) כ-"יי" או כשני גרשים: "דייר", "יוייר".
    # רק בקיצורים המוכרים - החלפה כללית של "יי" הייתה הורסת מילים.
    text = re.sub(r"(?<![א-ת])ד(?:יי|''|'|\"\")ר(?![א-ת])", 'ד"ר', text)
    text = re.sub(r"(?<![א-ת])יו(?:יי|''|'|\"\")ר(?![א-ת])", 'יו"ר', text)
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(l.strip() for l in text.splitlines() if l.strip())


# ------------------------------------------------------------------
#  סיווג (כללים)
# ------------------------------------------------------------------

RULES = {
    "discharge": [("סיכום אשפוז", 3), ("תאריך שחרור", 2), ("תאריך קבלה", 1),
                  ("מהלך האשפוז", 2)],
    "emg":       [("EMG", 3), ("הולכה עצבית", 2), ("ההולכה", 1), ("נוירולוג", 1)],
    "mri":       [("MRI", 3), ("רדיולוג", 2), ("בלט דיסק", 1)],
    "opinion":   [("חוות דעת", 3), ("נכות צמיתה", 2), ("לתקנות", 1)],
    "form7801":  [("7801", 3), ("קצבת נכות", 2), ("תביעה", 1)],
    "committee": [("ועדה רפואית", 3), ("פרוטוקול", 2), ("נכות רפואית משוקללת", 2),
                  ("ערר", 1)],
}


def classify(text: str):
    """(type, score, margin). margin נמוך = לא בטוח -> אדם מחליט."""
    scores = {k: sum(w for kw, w in rules if kw in text) for k, rules in RULES.items()}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (best, s1), (_, s2) = ranked[0], ranked[1]
    if s1 == 0:
        return "unknown", 0, 0
    return best, s1, s1 - s2


# ------------------------------------------------------------------
#  חילוץ עובדות (כללים). כל עובדה עם עוגן: היסט בטקסט.
# ------------------------------------------------------------------

_DATE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})(?!\d)")
_ICD = re.compile(r"(?<![A-Za-z0-9])([A-TV-Z]\d{2}\.\d{1,2})(?![0-9])")
_PCT = re.compile(r"(?:(?<!\d)(\d{1,3})\s?%|%\s?(\d{1,3})(?!\d))")
_DR = re.compile(r'ד"ר\s+([א-ת]{2,})\s+([א-ת]{2,})')
_INST = re.compile(r"((?:מרכז רפואי|בית חולים|מכון)\s[א-ת ]{2,40}?סינתטי)")
TEST_TYPES = ["EMG", "MRI", "CT"]


def extract_facts(text: str):
    facts = []

    def add(kind, value, pos):
        facts.append({"kind": kind, "value": value, "offset": pos})

    for m in _DATE.finditer(text):
        dd, mm, yy = (int(x) for x in m.groups())
        if yy < 100:
            yy += 2000
        if 1 <= dd <= 31 and 1 <= mm <= 12:
            add("date", "%04d-%02d-%02d" % (yy, mm, dd), m.start())
    for m in _ICD.finditer(text):
        add("icd10", m.group(1), m.start())
    for m in _PCT.finditer(text):
        add("percent", m.group(1) or m.group(2), m.start())
    for m in _DR.finditer(text):
        add("physician", "%s %s" % m.groups(), m.start())
    for m in _INST.finditer(text):
        add("institution", " ".join(m.group(1).split()), m.start())
    for t in TEST_TYPES:
        for m in re.finditer(r"(?<![A-Za-z])%s(?![A-Za-z])" % t, text):
            add("test_type", t, m.start())
            break
    return facts
