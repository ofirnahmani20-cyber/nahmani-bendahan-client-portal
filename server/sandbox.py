"""
sandbox.py - התהליך המבודד שמפענח קבצים.

    python -E -s -m server.sandbox      (מופעל רק על ידי worker.py)

הוא היחיד במערכת שמריץ parser על קובץ שלקוח העלה, ולכן הוא
מניח שהקובץ עוין:

  1. מגבלות קשיחות *לפני* קריאת קלט (limits.py). בלעדיהן - סירוב.
  2. הקלט מגיע ב-stdin, הפלט שורת JSON אחת ב-stdout. אין קובץ
     זמני, ואין נתיב לקובץ המוצפן.
  3. אין לו מפתחות, אין לו מסד, ואין לו .env: הוא אינו מייבא את
     server.db או את server.crypto, וה-worker מריץ אותו עם סביבה
     ריקה. קוד זדוני שירוץ כאן לא ימצא סוד לגנוב.
  4. הודעות שגיאה הן קודים קבועים. חריגה של parser עלולה לצטט
     תוכן מהקובץ, ולכן הטקסט שלה אינו יוצא מכאן.

inspect (שלב 2) - סוג, מספר עמודים, ממדים, ואילו עמודים יש להם
                  שכבת טקסט ואילו יזדקקו ל-OCR.
extract (שלב 3) - הטקסט של אצוות עמודים (SANDBOX_PAGES): שכבת
                  טקסט מתוקנת (textfix.py), או OCR (ocr.py).

ניסיון הפריצה מתבטא כ-rejected, לא כקריסה של המערכת.
"""

import json
import os
import sys
import time

from . import limits

MEMORY_MB = int(os.environ.get("SANDBOX_MEMORY_MB", "768"))
CPU_SECONDS = int(os.environ.get("SANDBOX_CPU_SECONDS", "120"))
MAX_INPUT = int(os.environ.get("SANDBOX_MAX_INPUT", str(16 * 1024 * 1024)))
MAX_PAGES = int(os.environ.get("SANDBOX_MAX_PAGES", "50"))
MAX_PAGE_PIXELS = int(os.environ.get("SANDBOX_MAX_PAGE_PIXELS", "40000000"))
RENDER_DPI = 300
MIN_LAYER_CHARS = 40

EXIT_OK, EXIT_REJECTED, EXIT_NO_LIMITS, EXIT_MEMORY, EXIT_ERROR = 0, 3, 4, 5, 6


class Rejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _out(payload, code):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
    sys.exit(code)


# ----------------------------------------------------------------
#  inspect
# ----------------------------------------------------------------

def inspect_pdf(data):
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        raise Rejected("corrupt_pdf")
    try:
        n = len(pdf)
        if n == 0:
            raise Rejected("empty_pdf")
        if n > MAX_PAGES:
            raise Rejected("too_many_pages")
        pages = []
        for i in range(n):
            page = pdf[i]
            w, h = page.get_size()
            if (w / 72 * RENDER_DPI) * (h / 72 * RENDER_DPI) > MAX_PAGE_PIXELS:
                raise Rejected("page_too_large")
            # count_chars מפענח את ה-content stream - כאן פצצת
            # דחיסה מתנפחת, ולכן זה רץ תחת תקרת הזיכרון.
            chars = page.get_textpage().count_chars()
            pages.append({"page": i + 1, "layer_chars": chars,
                          "needs_ocr": chars < MIN_LAYER_CHARS})
        return {"kind": "pdf", "pages": pages, "engine": {"pdfium": str(pdfium.PDFIUM_INFO)}}
    finally:
        pdf.close()


def inspect_image(data):
    import io

    import PIL
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PAGE_PIXELS
    try:
        img = Image.open(io.BytesIO(data))
        if getattr(img, "n_frames", 1) > 1:
            raise Rejected("animated_image")
        img.load()                       # פענוח מלא, תחת תקרת הזיכרון
    except Image.DecompressionBombError:
        raise Rejected("image_too_large")
    except Rejected:
        raise
    except Exception:
        raise Rejected("corrupt_image")
    return {"kind": "image", "width": img.width, "height": img.height,
            "pages": [{"page": 1, "layer_chars": 0, "needs_ocr": True}],
            "engine": {"pillow": PIL.__version__}}


# ----------------------------------------------------------------
#  extract (שלב 3)
# ----------------------------------------------------------------
#  העבודה מחולקת לאצוות עמודים, וכל אצווה היא תהליך sandbox נפרד
#  תחת אותן מגבלות של שלב 2 (זיכרון, CPU, timeout). מסמך של 50
#  עמודים סרוקים אינו מחייב להגדיל אף מגבלה.
# ----------------------------------------------------------------

def _wanted_pages():
    raw = os.environ.get("SANDBOX_PAGES", "")
    return [int(p) for p in raw.split(",") if p.strip().isdigit()]


def extract_pdf(data, wanted):
    import pypdfium2 as pdfium

    from . import ocr, textfix

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        raise Rejected("corrupt_pdf")
    try:
        n = len(pdf)
        if n > MAX_PAGES:
            raise Rejected("too_many_pages")
        out = []
        for page_no in wanted:
            if not 1 <= page_no <= n:
                raise Rejected("bad_page_request")
            page = pdf[page_no - 1]
            layer = page.get_textpage().get_text_range()
            if len(layer.strip()) >= MIN_LAYER_CHARS:
                text, fixes = textfix.fix_layer(layer)
                # שכבת טקסט: הקישור הוא לעמוד ולשורה. תיבות לשורה
                # יתווספו כשיידרשו (שלב 5); הטקסט עצמו אינו ניחוש.
                segments, offset = [], 0
                for line in text.split("\n"):
                    segments.append({"start": offset, "end": offset + len(line),
                                     "bbox": None, "conf": None})
                    offset += len(line) + 1
                out.append({"page": page_no, "source": "layer", "model": "layer",
                            "confidence": None, "text": text, "segments": segments,
                            "icd": [], "fixes": fixes})
                continue
            w, h = page.get_size()
            if (w / 72 * RENDER_DPI) * (h / 72 * RENDER_DPI) > MAX_PAGE_PIXELS:
                raise Rejected("page_too_large")
            img = page.render(scale=RENDER_DPI / 72).to_pil()
            result = ocr.ocr_page(img)
            del img
            out.append(dict(result, page=page_no, source="ocr"))
        return out
    finally:
        pdf.close()


def extract_image(data):
    import io

    from PIL import Image

    from . import ocr

    Image.MAX_IMAGE_PIXELS = MAX_PAGE_PIXELS
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Image.DecompressionBombError:
        raise Rejected("image_too_large")
    except Exception:
        raise Rejected("corrupt_image")
    return [dict(ocr.ocr_page(img), page=1, source="ocr")]


def extract(data):
    from . import ocr

    try:
        if data.startswith(b"%PDF-"):
            pages = extract_pdf(data, _wanted_pages())
        elif data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or \
                (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
            pages = extract_image(data)
        else:
            raise Rejected("unsupported_type")
    except ocr.OcrFailed as exc:
        raise Rejected(exc.code)
    return {"pages": pages, "engine": {"tesseract": ocr.engine_version(),
                                       "ocr_mode": ocr.MODE}}


def inspect(data):
    if data.startswith(b"%PDF-"):
        return inspect_pdf(data)
    if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" or \
            (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return inspect_image(data)
    raise Rejected("unsupported_type")


# ----------------------------------------------------------------
#  בדיקות עצמיות - רק כשהבדיקות מבקשות במפורש. ה-worker לעולם
#  אינו מעביר SANDBOX_SELFTEST.
# ----------------------------------------------------------------

def selftest(task, arg):
    if task == "env":
        return {"env": sorted(os.environ),
                "modules": sorted(m for m in sys.modules if m.startswith("server"))}
    if task == "alloc":
        blob = bytearray(int(arg) * 1024 * 1024)
        return {"allocated_mb": len(blob) // (1024 * 1024)}
    if task == "sleep":
        time.sleep(float(arg))
        return {"slept": arg}
    if task == "spawn":
        import subprocess
        subprocess.run([sys.executable, "-c", "pass"], check=True, timeout=10)
        return {"spawned": True}
    raise Rejected("unknown_selftest")


def main():
    task = os.environ.get("SANDBOX_TASK", "inspect")
    try:
        # extract מפעיל את Tesseract: תהליך-בן אחד, ולא יותר.
        applied = limits.apply(MEMORY_MB, CPU_SECONDS,
                               max_processes=2 if task == "extract" else 1)
    except Exception:
        _out({"ok": False, "code": "limits_unavailable"}, EXIT_NO_LIMITS)

    try:
        if task in ("inspect", "extract"):
            data = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(data) > MAX_INPUT:
                raise Rejected("input_too_large")
            result = inspect(data) if task == "inspect" else extract(data)
            del data
        else:
            if os.environ.get("SANDBOX_SELFTEST") != "1":
                raise Rejected("unknown_task")
            result = selftest(task, os.environ.get("SANDBOX_ARG", ""))
        _out({"ok": True, "result": result, "limits": applied}, EXIT_OK)
    except Rejected as exc:
        _out({"ok": False, "code": exc.code}, EXIT_REJECTED)
    except MemoryError:
        _out({"ok": False, "code": "memory_limit"}, EXIT_MEMORY)
    except OSError:
        # קוד נייטיבי (pdfium) שנכשל תחת התקרה - למשל הקצאה שנדחתה
        # בתוך פצצת דחיסה - עולה כ-OSError דרך ctypes. הקובץ הוא
        # שהפיל את ה-parser, ולכן זו דחייה ולא תקלה זמנית.
        _out({"ok": False, "code": "native_fault"}, EXIT_REJECTED)
    except SystemExit:
        raise
    except Exception:
        # לא str(exc): ההודעה עלולה לצטט תוכן מהקובץ.
        _out({"ok": False, "code": "internal_error"}, EXIT_ERROR)


if __name__ == "__main__":
    main()
