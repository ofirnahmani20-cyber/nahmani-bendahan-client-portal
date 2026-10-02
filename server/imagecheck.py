"""
imagecheck.py - בדיקת מבנה של תמונה *בלי לפענח אותה*.

למה
----
חתימת קובץ (magic) אומרת רק "זה מתחיל כמו PNG". היא לא אומרת
שהקובץ שלם, ולא כמה פיקסלים הוא מכריז עליהם. PNG של 0.1KB שמכריז
על 3.6 גיגה-פיקסל עבר את הבדיקה עד היום, והיה מגיע לשלב העיבוד
כפצצה (ראה spikes/ocr/RESULTS.md, ממצא 4).

כאן נקראות רק הכותרות - ממדים, ושלמות המבנה ב-WebP - בקוד Python
פשוט. שום ספריית פענוח תמונה (libwebp, libpng, libjpeg) אינה רצה
בתהליך השרת. הפענוח עצמו, אם יידרש, קורה ב-worker המבודד.

מה נדחה
--------
- ממדים אפס, או מעל MAX_PIXELS (ברירת מחדל 40 מגה-פיקסל - A3 ב-300dpi)
- כותרת קטועה או חסרה
- WebP: גודל RIFF שאינו תואם לגודל הקובץ, chunk לא מוכר, או אנימציה
  (WebP מונפש אינו צילום מסמך, והוא דרך להבריח פריימים רבים)
"""

import os
import struct

MAX_PIXELS = int(os.environ.get("PORTAL_MAX_IMAGE_PIXELS", 40_000_000))


class BadImage(ValueError):
    """המבנה אינו תקין. ההודעה מיועדת להצגה ללקוח."""


def _check(width, height):
    if width <= 0 or height <= 0:
        raise BadImage("ממדי התמונה אינם תקינים.")
    if width * height > MAX_PIXELS:
        raise BadImage("התמונה גדולה מדי (%d×%d). צלמו שוב ברזולוציה רגילה."
                       % (width, height))
    return width, height


def png_size(data: bytes):
    # חתימה(8) | אורך(4) | "IHDR" | רוחב(4) | גובה(4)
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise BadImage("קובץ PNG פגום.")
    width, height = struct.unpack(">II", data[16:24])
    return _check(width, height)


# SOF0..SOF15 פרט ל-DHT(C4), JPG(C8), DAC(CC)
_SOF = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def jpeg_size(data: bytes):
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            raise BadImage("קובץ JPEG פגום.")
        marker = data[i + 1]
        if marker == 0xFF:                 # ריפוד
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xD9:                 # EOI לפני SOF
            break
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        if length < 2:
            raise BadImage("קובץ JPEG פגום.")
        if marker in _SOF:
            if i + 9 > n:
                break
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return _check(width, height)
        i += 2 + length
    raise BadImage("קובץ JPEG פגום.")


def webp_size(data: bytes):
    """
    RIFF <size> WEBP <chunk>. שלושה סוגי chunk ראשון:
      VP8   - lossy:    start code 9d 01 2a, ואז רוחב/גובה 14 ביט
      VP8L  - lossless: 0x2f, ואז 14+14 ביט (פחות אחד)
      VP8X  - extended: דגלים, ואז canvas 24+24 ביט (פחות אחד)
    """
    if len(data) < 30:
        raise BadImage("קובץ WebP פגום.")
    (riff_size,) = struct.unpack("<I", data[4:8])
    # הגודל המוצהר חייב לכסות את הקובץ בדיוק (עם בית ריפוד אפשרי).
    # קובץ ארוך ממה שהוא מצהיר הוא מקום להחביא בו תוכן נוסף.
    if riff_size + 8 not in (len(data), len(data) - 1) or riff_size % 2:
        raise BadImage("קובץ WebP פגום (גודל לא תואם).")

    fourcc = data[12:16]
    if fourcc == b"VP8 ":
        if data[23:26] != b"\x9d\x01\x2a":
            raise BadImage("קובץ WebP פגום.")
        width, height = struct.unpack("<HH", data[26:30])
        return _check(width & 0x3FFF, height & 0x3FFF)

    if fourcc == b"VP8L":
        if data[20] != 0x2F:
            raise BadImage("קובץ WebP פגום.")
        b0, b1, b2, b3 = data[21:25]
        width = 1 + (b0 | ((b1 & 0x3F) << 8))
        height = 1 + ((b1 >> 6) | (b2 << 2) | ((b3 & 0x0F) << 10))
        return _check(width, height)

    if fourcc == b"VP8X":
        flags = data[20]
        if flags & 0x02:
            raise BadImage("תמונה מונפשת אינה נתמכת. העלו צילום רגיל של המסמך.")
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return _check(width, height)

    raise BadImage("קובץ WebP מסוג לא נתמך.")


CHECKS = {"image/png": png_size, "image/jpeg": jpeg_size, "image/webp": webp_size}


def check(data: bytes, mime: str):
    """(width, height) לתמונה תקינה; None לסוג שאינו תמונה."""
    fn = CHECKS.get(mime)
    return fn(data) if fn else None
