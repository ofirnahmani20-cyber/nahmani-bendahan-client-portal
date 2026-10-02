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

שלב 2 (עכשיו): inspect - סוג, מספר עמודים, ממדים, ואילו עמודים
יש להם שכבת טקסט ואילו יזדקקו ל-OCR. שלב 3 יוסיף כאן את החילוץ.

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
    try:
        applied = limits.apply(MEMORY_MB, CPU_SECONDS)
    except Exception:
        _out({"ok": False, "code": "limits_unavailable"}, EXIT_NO_LIMITS)

    try:
        task = os.environ.get("SANDBOX_TASK", "inspect")
        if task != "inspect":
            if os.environ.get("SANDBOX_SELFTEST") != "1":
                raise Rejected("unknown_task")
            result = selftest(task, os.environ.get("SANDBOX_ARG", ""))
        else:
            data = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(data) > MAX_INPUT:
                raise Rejected("input_too_large")
            result = inspect(data)
            del data
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
