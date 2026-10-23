"""
ocr.py - Tesseract בתוך ה-sandbox.

רץ רק בתהליך המבודד (sandbox.py), תחת המגבלות שהוא כבר הטיל.
Tesseract מופעל כתהליך-בן: הוא יורש את ה-Job Object (Windows) או
את ה-rlimits (POSIX), ולכן גם הוא תחת תקרת הזיכרון ואינו יכול
לכתוב קבצים. התמונה עוברת אליו ב-stdin כ-PNG בזיכרון, והפלט חוזר
ב-stdout. אין קובץ זמני.

מדיניות המודלים
----------------
ההחלטה מ-2026-10-02: fast כברירת מחדל, best לעמודים עם ביטחון נמוך -
*בתנאי* שהמעבר אינו מוסיף שגיאות. המדידה (spikes/ocr/STAGE3-QUALITY.md)
הראתה שהוא כן מוסיף: best קורא 2026 כ-2076, ובכל צורת מעבר שנבדקה
(עמוד, שורה, החלפה או קריאה חלופית) אבדו עובדות נכונות או נוספו
שגויות. לכן ברירת המחדל היא **fast בלבד**.

התצורות האחרות נשארות זמינות (SANDBOX_OCR_MODE=hybrid, PAGE_SWITCH,
LINE_MODE) כדי למדוד שוב - לא כדי להפעיל בלי מדידה. ביטחון
(confidence) מודד כמה Tesseract בטוח, לא האם הוא צודק.

כל שורה נשמרת עם התיבה שלה בעמוד (bbox) ועם הביטחון שלה - כך כל
קטע טקסט מקושר למקום שממנו נקרא, ובשלב 5 אפשר להציג למאשר את
חיתוך התמונה ליד המספר.
"""

import csv
import io
import os
import re
import subprocess

from PIL import Image, ImageFilter, ImageMath, ImageStat

from . import textfix

TESSERACT = os.environ.get("SANDBOX_TESSERACT", "tesseract")
TESSDATA = os.environ.get("SANDBOX_TESSDATA", "")
MODE = os.environ.get("SANDBOX_OCR_MODE", "fast")            # fast | hybrid | best
LOW_CONFIDENCE = float(os.environ.get("SANDBOX_LOW_CONFIDENCE", "85"))
PAGE_TIMEOUT = int(os.environ.get("SANDBOX_PAGE_TIMEOUT", "60"))
FLATTEN_STD = 15.0
MAX_ICD_CROPS = 8


class OcrFailed(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def engine_version():
    try:
        out = subprocess.run([TESSERACT, "--version"], capture_output=True, timeout=20)
        first = out.stdout.decode("utf-8", "replace").splitlines()[:1]
        return first[0].strip() if first else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ----------------------------------------------------------------
#  הכנת תמונה
# ----------------------------------------------------------------

def prepare(img: Image.Image):
    """
    גווני אפור, ויישור תאורה רק כשהרקע אינו אחיד. (תמונה, האם יושר)

    בלי היישור צילום טלפון נכשל לגמרי (0/18 ב-spike). עם יישור
    תמידי סריקה נקייה נפגעה מעט - ולכן הוא מותנה. Pillow בלבד.
    """
    img = img.convert("L")
    small = img.resize((max(1, img.width // 4), max(1, img.height // 4)))
    background = small.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(8))
    if ImageStat.Stat(background).stddev[0] < FLATTEN_STD:
        return img, False
    background = background.resize(img.size, Image.Resampling.BILINEAR)
    flat = ImageMath.lambda_eval(
        lambda a: a["convert"](a["float"](a["x"]) * 255.0 / (a["float"](a["y"]) + 1.0), "L"),
        x=img, y=background)
    return flat, True


# ----------------------------------------------------------------
#  Tesseract
# ----------------------------------------------------------------

def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _run(png: bytes, model: str, lang: str, psm: int, tsv: bool) -> str:
    args = [TESSERACT, "stdin", "stdout", "-l", lang, "--psm", str(psm),
            "--tessdata-dir", os.path.join(TESSDATA, model)]
    if tsv:
        args += ["-c", "tessedit_create_tsv=1"]
    try:
        out = subprocess.run(args, input=png, capture_output=True, timeout=PAGE_TIMEOUT,
                             env={**_child_env(), "OMP_THREAD_LIMIT": "1"})
    except subprocess.TimeoutExpired:
        raise OcrFailed("ocr_timeout")
    except OSError:
        raise OcrFailed("ocr_unavailable")
    if out.returncode != 0:
        raise OcrFailed("ocr_failed")
    return out.stdout.decode("utf-8", "replace")


def _child_env():
    return {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR") if k in os.environ}


def _parse_tsv(tsv: str):
    """
    שורות: [{text, bbox, conf}] לפי סדר הקריאה של Tesseract.
    level 5 = מילה; block/par/line מזהים שורה.
    """
    lines, order = {}, []
    for row in csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(row) < 12 or row[0] != "5":
            continue
        word = row[11].strip()
        if not word:
            continue
        key = (int(row[2]), int(row[3]), int(row[4]))
        left, top, w, h, conf = (int(row[6]), int(row[7]), int(row[8]), int(row[9]),
                                 float(row[10]))
        if key not in lines:
            lines[key] = {"words": [], "boxes": [], "confs": []}
            order.append(key)
        lines[key]["words"].append(word)
        lines[key]["boxes"].append((left, top, left + w, top + h))
        if conf >= 0:
            lines[key]["confs"].append(conf)
    out = []
    for key in order:
        ln = lines[key]
        boxes = ln["boxes"]
        out.append({
            "text": " ".join(ln["words"]),
            "bbox": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                     max(b[2] for b in boxes), max(b[3] for b in boxes)],
            "conf": round(sum(ln["confs"]) / len(ln["confs"]), 1) if ln["confs"] else 0.0,
            "words": len(ln["words"]),
        })
    return out


LINE_LOW_CONFIDENCE = float(os.environ.get("SANDBOX_LINE_LOW_CONFIDENCE", "80"))
MAX_LINE_REFINES = 12


# מה עושים בקריאה השנייה (best) של שורה חלשה:
#   replace     - מחליפים אם בטוחה יותר (נמדד: מוסיף שגיאות מספריות)
#   alternative - שומרים לצד הקריאה הראשונה, לא מחליפים
#   off         - לא קוראים שוב
LINE_MODE = os.environ.get("SANDBOX_LINE_MODE", "off")
PAGE_SWITCH = os.environ.get("SANDBOX_PAGE_SWITCH", "1") == "1"


def _second_reading(img, line):
    x0, y0, x1, y1 = line["bbox"]
    pad = 10
    crop = img.crop((max(0, x0 - pad), max(0, y0 - pad),
                     min(img.width, x1 + pad), min(img.height, y1 + pad)))
    again = _parse_tsv(_run(_png(crop), "best", "heb+eng", 7, tsv=True))
    if not again:
        return None
    words = sum(l["words"] for l in again)
    conf = sum(l["conf"] * l["words"] for l in again) / words if words else 0
    return {"text": " ".join(l["text"] for l in again), "conf": round(conf, 1),
            "words": words}


def _refine_lines(img, lines, mode="replace"):
    """
    (מספר שורות שהוחלפו, קריאות חלופיות). ב-alternative הטקסט אינו
    משתנה: הקריאה השנייה נשמרת כדי ששלב 5 יוכל לסמן מספר ששתי
    הקריאות חלוקות עליו - לא כדי לבחור ביניהן בשקט.
    """
    replaced, alternatives = 0, []
    weak = sorted((i for i, l in enumerate(lines) if l["conf"] < LINE_LOW_CONFIDENCE),
                  key=lambda i: lines[i]["conf"])[:MAX_LINE_REFINES]
    for i in weak:
        again = _second_reading(img, lines[i])
        if again is None:
            continue
        if mode == "replace":
            if again["conf"] > lines[i]["conf"]:
                lines[i] = dict(lines[i], text=again["text"], conf=again["conf"],
                                words=again["words"], refined=True)
                replaced += 1
        else:
            alternatives.append({"line": i, "text": again["text"], "conf": again["conf"]})
    return replaced, alternatives


def _page_confidence(lines):
    total = sum(l["words"] for l in lines)
    if not total:
        return 0.0
    return round(sum(l["conf"] * l["words"] for l in lines) / total, 1)


def _read(png, model):
    lines = _parse_tsv(_run(png, model, "heb+eng", 3, tsv=True))
    return lines, _page_confidence(lines)


# קוד ICD בתוך שורה עברית: במעבר המשולב G56.0 נקרא כ-"156.0)".
# מעבר באנגלית בלבד, רק על שורות שיש בהן ספרה-נקודה-ספרה.
_ICD_HINT = re.compile(r"\d\.\d")
_ICD = re.compile(r"(?<![A-Za-z0-9])([A-TV-Z]\d{2}\.\d{1,2})(?![0-9])")


def _icd_pass(img, lines):
    found, crops = [], 0
    for idx, line in enumerate(lines):
        if crops >= MAX_ICD_CROPS:
            break
        if not _ICD_HINT.search(line["text"]):
            continue
        x0, y0, x1, y1 = line["bbox"]
        pad = 8
        crop = img.crop((max(0, x0 - pad), max(0, y0 - pad),
                         min(img.width, x1 + pad), min(img.height, y1 + pad)))
        crops += 1
        # best תמיד: חיתוך קטן, ודיוק בקוד אבחנה חשוב יותר ממהירות.
        text = _run(_png(crop), "best", "eng", 7, tsv=False)
        for m in _ICD.finditer(text):
            found.append({"code": m.group(1), "line": idx})
    return found


def ocr_page(img: Image.Image):
    """
    עמוד אחד. מחזיר text, model, confidence, segments, icd, flattened.
    segments: לכל שורה - היסט בטקסט, תיבה בעמוד, וביטחון.
    """
    img, flattened = prepare(img)
    png = _png(img)
    alternatives = []

    if MODE == "best":
        lines, conf = _read(png, "best")
        model = "best"
    else:
        lines, conf = _read(png, "fast")
        model = "fast"
        if MODE == "hybrid" and PAGE_SWITCH and conf < LOW_CONFIDENCE:
            best_lines, best_conf = _read(png, "best")
            # best רק אם הוא באמת בטוח יותר - לא כי הוא "best".
            if best_conf > conf:
                lines, conf, model = best_lines, best_conf, "best"
        if MODE == "hybrid" and model == "fast":
            # עמוד בטוח בממוצע יכול להסתיר שורה גרועה: fast החליף
            # מילים עבריות בזבל לטיני ("PANN") בשורה עם ביטחון 75,
            # בעמוד עם ממוצע 89. שורה כזו נקראת שוב ב-best, מהחיתוך
            # שלה בלבד, ומוחלפת רק אם הקריאה החדשה בטוחה יותר.
            if LINE_MODE != "off":
                replaced, alternatives = _refine_lines(img, lines, LINE_MODE)
                if replaced:
                    conf = _page_confidence(lines)
                    model = "fast+best"

    texts, segments, offset, index = [], [], 0, {}
    for i, line in enumerate(lines):
        text = textfix.normalize(textfix.fix_ocr(line["text"]))
        if not text:
            continue
        index[i] = len(segments)
        segments.append({"start": offset, "end": offset + len(text),
                         "bbox": line["bbox"], "conf": line["conf"]})
        texts.append(text)
        offset += len(text) + 1                      # "\n"
    # קריאה חלופית מקושרת לאותו segment - לאותה תיבה בעמוד.
    alternatives = [{"segment": index[a["line"]],
                     "text": textfix.normalize(textfix.fix_ocr(a["text"])),
                     "conf": a["conf"]}
                    for a in alternatives if a["line"] in index]

    return {"text": "\n".join(texts), "model": model, "confidence": conf,
            "segments": segments, "alternatives": alternatives,
            "icd": _icd_pass(img, lines),
            "flattened": flattened, "size": [img.width, img.height]}
