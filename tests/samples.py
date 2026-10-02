"""
samples.py - קבצי PDF סינתטיים לבדיקות. נבנים כאן, בבתים, בלי ספרייה.

אין בהם קוד זדוני; העוינים (many_pages, huge_page, inflate_bomb)
בנויים רק כדי למצות משאבים - בדיוק מה שה-sandbox צריך לעצור.
"""

import zlib


def _pdf(objects):
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return bytes(out)


def _stream(content: bytes, compress=False):
    if compress:
        content = zlib.compress(content)
        return b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(content) \
            + content + b"\nendstream"
    return b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"


def text_pdf(pages_text):
    """PDF עם שכבת טקסט אמיתית (Helvetica), עמוד לכל מחרוזת. ASCII בלבד."""
    n = len(pages_text)
    kids = b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(n))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages_text):
        page_obj, content_obj = 4 + 2 * i, 5 + 2 * i
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
                       % content_obj)
        ops = b"BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text.encode("ascii")
        objects.append(_stream(ops))
        assert len(objects) == content_obj
        assert page_obj == content_obj - 1
    return _pdf(objects)


def blank_pdf(pages=1):
    """עמודים בלי טקסט - כמו סריקה. ה-sandbox צריך לסמן אותם ל-OCR."""
    kids = b" ".join(b"3 0 R" for _ in range(pages))
    return _pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                 b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, pages),
                 b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"])


def many_pages(n=5000):
    return blank_pdf(n)


def huge_page():
    return _pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                 b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 14400 14400] >>"])


def inflate_bomb(mb=600):
    """content stream שמתנפח ל-mb מגה-בייט של פקודות ציור."""
    comp = zlib.compressobj(9)
    chunk = b"0 0 m 1 1 l S\n" * 4096
    data, total = bytearray(), 0
    while total < mb * 1024 * 1024:
        data += comp.compress(chunk)
        total += len(chunk)
    data += comp.flush()
    stream = (b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data)
              + bytes(data) + b"\nendstream")
    return _pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                 b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
                 stream])
