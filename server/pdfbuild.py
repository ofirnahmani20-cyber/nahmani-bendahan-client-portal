"""
pdfbuild.py - PDF מעמודים נקיים, בלי לפענח אותם (docs/10, 23.10).

למה כותב PDF משלנו
-------------------
השרת אינו מפענח קבצים - זה תפקיד ה-sandbox בלבד (sandbox.py). העמודים
הנקיים הם JPEG, ו-PDF יודע להכיל JPEG כמו שהוא (DCTDecode): הבתים
עוברים לקובץ בלי שאיש פותח אותם. כל מה שנדרש הוא רוחב, גובה ומספר
ערוצים - והם נקראים מכותרת ה-JPEG, באותו אופן ש-imagecheck.py קורא
כותרות בהעלאה.

כך ה-PDF הנקי והקלסר נבנים בתהליך השרת בלי parser של תמונות, וגם בלי
ספריית PDF נוספת.

מספרי העמודים כתובים בגופן Helvetica המובנה בכל קורא PDF - ספרות
ותו "/" בלבד, ולכן אין צורך להטמיע גופן. דף השער (עברית) נוצר כתמונה
- ראו binder_cover.
"""

import io

A4 = (595.28, 841.89)          # נקודות
MARGIN = 24
FOOTER = 22


class NotJpeg(ValueError):
    pass


def jpeg_info(data: bytes):
    """(רוחב, גובה, ערוצים) מתוך סמן ה-SOF. בלי פענוח של התמונה."""
    if data[:2] != b"\xff\xd8":
        raise NotJpeg("not a jpeg")
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            raise NotJpeg("bad marker")
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 >= n:
                break
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            comps = data[i + 9]
            if not (w and h and comps in (1, 3)):
                raise NotJpeg("unsupported jpeg")
            return w, h, comps
        i += 2 + length
    raise NotJpeg("no frame")


class _Writer:
    def __init__(self):
        self.buf = io.BytesIO()
        self.offsets = []
        self.buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

    def reserve(self):
        self.offsets.append(None)
        return len(self.offsets)

    def put(self, num, body: bytes):
        self.offsets[num - 1] = self.buf.tell()
        self.buf.write(b"%d 0 obj\n" % num + body + b"\nendobj\n")

    def stream(self, num, dict_body: bytes, data: bytes):
        self.put(num, b"<< " + dict_body + b" /Length %d >>\nstream\n" % len(data)
                 + data + b"\nendstream")

    def finish(self, root):
        xref = self.buf.tell()
        self.buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(self.offsets) + 1))
        for off in self.offsets:
            self.buf.write(b"%010d 00000 n \n" % off)
        self.buf.write(b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                       % (len(self.offsets) + 1, root, xref))
        return self.buf.getvalue()


def build(pages, *, number_pages=True):
    """
    pages: רשימת JPEG (bytes), עמוד לכל אחד. כל עמוד מותאם ל-A4 לפי
    הכיוון שלו, ממורכז, עם "n / N" בתחתית כשמבקשים.
    """
    if not pages:
        raise ValueError("no pages")
    w = _Writer()
    catalog, tree, font = w.reserve(), w.reserve(), w.reserve()
    w.put(font, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    kids, total = [], len(pages)
    for idx, jpeg in enumerate(pages, start=1):
        iw, ih, comps = jpeg_info(jpeg)
        pw, ph = A4 if ih >= iw else (A4[1], A4[0])
        avail_w = pw - 2 * MARGIN
        avail_h = ph - 2 * MARGIN - (FOOTER if number_pages else 0)
        scale = min(avail_w / iw, avail_h / ih)
        dw, dh = iw * scale, ih * scale
        x = (pw - dw) / 2
        y = MARGIN + (FOOTER if number_pages else 0) + (avail_h - dh) / 2

        img, content, page = w.reserve(), w.reserve(), w.reserve()
        w.stream(img, b"/Type /XObject /Subtype /Image /Width %d /Height %d "
                      b"/ColorSpace /%s /BitsPerComponent 8 /Filter /DCTDecode"
                 % (iw, ih, b"DeviceGray" if comps == 1 else b"DeviceRGB"), jpeg)
        ops = b"q %.2f 0 0 %.2f %.2f %.2f cm /Im0 Do Q\n" % (dw, dh, x, y)
        if number_pages:
            label = b"%d / %d" % (idx, total)
            # Helvetica: ספרה ~0.556em. 9pt - מרכוז מקורב מספיק.
            tx = pw / 2 - len(label) * 9 * 0.556 / 2
            ops += b"BT /F1 9 Tf 0.35 g %.2f %.2f Td (%s) Tj ET\n" % (tx, MARGIN, label)
        w.stream(content, b"", ops)
        w.put(page, b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                    b"/Resources << /XObject << /Im0 %d 0 R >> /Font << /F1 %d 0 R >> >> "
                    b"/Contents %d 0 R >>" % (tree, pw, ph, img, font, content))
        kids.append(page)

    w.put(tree, b"<< /Type /Pages /Kids [%s] /Count %d >>"
          % (b" ".join(b"%d 0 R" % k for k in kids), len(kids)))
    w.put(catalog, b"<< /Type /Catalog /Pages %d 0 R >>" % tree)
    return w.finish(catalog)


# ================================================================
#  דף שער לקלסר
# ================================================================

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",     # Docker: fonts-dejavu-core
    "C:/Windows/Fonts/arial.ttf",
    "/Library/Fonts/Arial.ttf",
)


def _font(size):
    import os

    from PIL import ImageFont
    for path in (os.environ.get("PORTAL_FONT"),) + FONT_CANDIDATES:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise RuntimeError("לא נמצא גופן עם עברית. הגדר PORTAL_FONT.")


def binder_cover(title_lines, rows):
    """
    דף שער ותוכן עניינים, כתמונה (JPEG). הטקסט שלנו - שם התיק ושמות
    המסמכים מהמסד - לא תוכן של קובץ שהועלה, ולכן Pillow מצייר אותו
    כאן בתהליך השרת.

    rows: [(שם המסמך, "3-5" או הערה)]
    """
    from bidi.algorithm import get_display
    from PIL import Image, ImageDraw

    width, height = 1240, 1754                 # A4 ב-150 DPI
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    big, small = _font(44), _font(28)
    right = width - 110
    y = 130

    for i, line in enumerate(title_lines):
        f = big if i == 0 else small
        draw.text((right, y), get_display(line), font=f, fill=0, anchor="ra")
        y += 70 if i == 0 else 46
    y += 30
    draw.line((110, y, right, y), fill=0, width=2)
    y += 40

    for name, pages in rows:
        if y > height - 120:
            draw.text((right, y), get_display("... (המשך הרשימה בתוך הקלסר)"),
                      font=small, fill=0, anchor="ra")
            break
        draw.text((right, y), get_display(name), font=small, fill=0, anchor="ra")
        draw.text((110, y), get_display(pages), font=small, fill=0, anchor="la")
        y += 46

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()
