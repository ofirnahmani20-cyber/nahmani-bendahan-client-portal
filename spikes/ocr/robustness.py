"""
robustness.py - קבצים עוינים מול הצנרת. כל אחד חייב להסתיים
ב"נדחה" או ב"timeout" - לעולם לא בקריסה של התהליך הראשי ולא
בצריכת זיכרון בלתי מוגבלת.

    spikes/ocr/.venv/Scripts/python spikes/ocr/robustness.py

כל קובץ מעובד בתהליך ילד עם timeout קשיח ותקרת זמן - בדיוק כמו
ה-worker המתוכנן. הקבצים נוצרים כאן; אין בהם קוד זדוני, רק מבנה
שנועד למצות משאבים.
"""

import multiprocessing as mp
import pathlib
import struct
import time
import zlib

import psutil

import pipeline as P

WORK = P.HOME / "hostile"
TIMEOUT = 45          # לקובץ שלם


# ------------------------------------------------------------------
#  יצירת הקבצים
# ------------------------------------------------------------------

def _pdf(objects):
    """בונה PDF תקין מרשימת גופי אובייקטים (bytes), עם xref."""
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


def many_pages(n=5000):
    """5000 עמודים בקובץ זעיר: כולם מפנים לאותו אובייקט עמוד."""
    kids = b" ".join(b"3 0 R" for _ in range(n))
    return _pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                 b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, n),
                 b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"])


def huge_page():
    """עמוד בגודל המרבי של התקן (200 אינץ'): 60000x60000 פיקסלים ב-300dpi."""
    return _pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                 b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 14400 14400] >>"])


def inflate_bomb(mb=1500):
    """content stream שמתנפח מ-~1.5MB ל-1.5GB של פקודות ציור."""
    comp = zlib.compressobj(9)
    chunk = b"0 0 m 1 1 l S\n" * 4096
    data = bytearray()
    total = 0
    while total < mb * 1024 * 1024:
        data += comp.compress(chunk)
        total += len(chunk)
    data += comp.flush()
    stream = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + bytes(data) \
        + b"\nendstream"
    return _pdf([b"<< /Type /Catalog /Pages 2 0 R >>",
                 b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                 b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
                 stream])


def png_bomb(w=60000, h=60000):
    """כותרת PNG שמכריזה על 3.6 גיגה-פיקסל, עם מעט נתונים."""
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
    raw = zlib.compress(b"\x00" + b"\x00" * (w // 8 + 1), 9)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 0, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def corrupt():
    real = sorted((P.HOME / "corpus").glob("*-1.pdf"))[0].read_bytes()
    return real[: len(real) // 3]


CASES = {
    "5000-pages.pdf": many_pages,
    "huge-page.pdf": huge_page,
    "inflate-bomb.pdf": inflate_bomb,
    "png-bomb.png": png_bomb,
    "truncated.pdf": corrupt,
    "not-a-pdf.pdf": lambda: b"MZ\x90\x00 this is not a pdf",
}


# ------------------------------------------------------------------
#  הרצה בתהליך ילד
# ------------------------------------------------------------------

def _child(path, queue):
    try:
        text, info = P.extract_text(pathlib.Path(path), "fast")
        queue.put(("processed", "%d chars, %d ocr pages" % (len(text), info["ocr_pages"])))
    except P.Rejected as exc:
        queue.put(("rejected", str(exc)))
    except Exception as exc:                      # כשל לא צפוי - עדיין לא קריסה של האב
        queue.put(("error", "%s: %s" % (type(exc).__name__, str(exc)[:80])))


def run_case(path):
    queue = mp.Queue()
    proc = mp.Process(target=_child, args=(str(path), queue))
    t0 = time.perf_counter()
    proc.start()
    peak = 0
    ps = psutil.Process(proc.pid)
    while proc.is_alive() and time.perf_counter() - t0 < TIMEOUT:
        try:
            peak = max(peak, ps.memory_info().rss + sum(
                c.memory_info().rss for c in ps.children(recursive=True)))
        except psutil.Error:
            pass
        time.sleep(0.05)
    if proc.is_alive():
        for c in ps.children(recursive=True):
            c.kill()
        proc.kill()
        proc.join()
        return "timeout", "killed after %ds" % TIMEOUT, time.perf_counter() - t0, peak
    proc.join()
    outcome, detail = queue.get() if not queue.empty() else ("crashed", "exit %s" % proc.exitcode)
    return outcome, detail, time.perf_counter() - t0, peak


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    lines = ["| קובץ | גודל | תוצאה | פירוט | זמן | זיכרון שיא |", "|---|---|---|---|---|---|"]
    for name, make in CASES.items():
        path = WORK / name
        path.write_bytes(make())
        outcome, detail, secs, peak = run_case(path)
        lines.append("| %s | %.1f KB | **%s** | %s | %.1fs | %.0f MB |" % (
            name, path.stat().st_size / 1024, outcome, detail, secs, peak / 1e6))
        print(lines[-1], flush=True)
    out = P.HOME / "out" / "robustness.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
