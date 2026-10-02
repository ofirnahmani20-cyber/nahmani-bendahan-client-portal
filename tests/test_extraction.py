"""
שלב 3 של Layer 2: חילוץ טקסט ו-OCR.

מה ננעל כאן
------------
1. איכות: ספי CER לכל סוג קלט, על מסמכים סינתטיים בלבד
   (tests/fixtures/synthetic). הספים מעט מעל מה שנמדד, כדי לתפוס
   נסיגה ולא רעש.
2. קישור: כל קטע טקסט נושא עמוד, היסט מדויק בטקסט, ובעמוד סרוק
   גם תיבה וביטחון.
3. אחסון: טקסט ופריסה מוצפנים, עיבוד חוזר מחליף, אצווה שנכשלה
   אינה משאירה חצי מסמך.
4. מגבלות שלב 2 נשמרות גם עם Tesseract: זיכרון, timeout, אין
   תהליך שנשאר חי, אין קובץ זמני.
5. בחירת המודל: best רק כשהוא בטוח יותר.

בדיקות ה-OCR דורשות Tesseract ומודלים (PORTAL_TESSERACT,
PORTAL_TESSDATA). בלעדיהם הן מדולגות - ובשער Linux (REQUIREMENTS
10.1) הן חייבות לרוץ.
"""

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from server import audit, crypto, ocr, policy, processing, redact, storage, textfix, worker
from server.db.pool import cursor

from .test_processing import enqueue, job_of, make_file

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "synthetic"

HAVE_OCR = bool(worker.TESSDATA) and (
    pathlib.Path(worker.TESSDATA, "fast", "heb.traineddata").exists()
    and pathlib.Path(worker.TESSDATA, "best", "heb.traineddata").exists())
needs_ocr = pytest.mark.skipif(not HAVE_OCR, reason="Tesseract/מודלים לא הוגדרו "
                               "(PORTAL_TESSERACT, PORTAL_TESSDATA) - חובה בשער Linux")


def read(name):
    return (FIX / name).read_bytes()


def truth(doc):
    return json.loads((FIX / ("%s.truth.json" % doc)).read_text(encoding="utf-8"))


def flat(text):
    return " ".join(textfix.normalize(text).split())


def cer(ref, hyp):
    """מרחק עריכה / אורך האמת. מימוש פשוט - הטקסטים כאן קצרים."""
    ref, hyp = flat(ref), flat(hyp)
    prev = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        cur = [i]
        for j, b in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = cur
    return prev[-1] / max(1, len(ref))


def extract(data, pages=(1,), timeout=120, **env):
    return worker.run_sandbox(data, timeout=timeout,
                              extra_env={**worker.extract_env(list(pages)), **env})


# ================================================================
#  0. הקבצים באמת סינתטיים
# ================================================================

def test_fixtures_are_synthetic():
    for path in FIX.glob("*.truth.json"):
        t = json.loads(path.read_text(encoding="utf-8"))
        assert t["synthetic"] is True
        assert "סינתטי" in t["text"]
        for candidate in __import__("re").findall(r"\b\d{9}\b", t["text"]):
            assert not redact.is_israeli_id(candidate), "ת\"ז תקפה בקובץ בדיקה"


# ================================================================
#  1. שכבת טקסט - בלי OCR
# ================================================================

def test_word_order_fix_on_a_chromium_pdf():
    outcome, result = extract(read("emg-1.pdf"))
    assert outcome == "ok", result
    page = result["pages"][0]
    assert (page["source"], page["model"]) == ("layer", "layer")
    assert "words" in page["fixes"]
    assert "שם הנבדק:" in page["text"], "סדר המילים לא תוקן"
    assert cer(truth("emg-1")["text"], page["text"]) < 0.02


def test_without_the_fix_the_text_layer_is_unusable():
    """הממצא שהצדיק את התיקון - נשמר כבדיקה, כדי שלא יוסר בטעות."""
    import pypdfium2 as pdfium
    raw = pdfium.PdfDocument(read("emg-1.pdf"))[0].get_textpage().get_text_range()
    assert cer(truth("emg-1")["text"], raw) > 0.4
    fixed, applied = textfix.fix_layer(raw)
    assert cer(truth("emg-1")["text"], fixed) < 0.02


def test_fully_reversed_legacy_pdf_is_detected_and_improved():
    import pypdfium2 as pdfium
    raw = pdfium.PdfDocument(read("discharge-1.visual.pdf"))[0].get_textpage().get_text_range()
    fixed, applied = textfix.fix_layer(raw)
    assert "chars" in applied
    ref = truth("discharge-1")["text"]
    assert cer(ref, fixed) < cer(ref, raw), "התיקון החמיר"


def test_layer_segments_point_exactly_into_the_text():
    _, result = extract(read("committee-2.pdf"))
    page = result["pages"][0]
    lines = page["text"].split("\n")
    assert len(page["segments"]) == len(lines)
    for seg, line in zip(page["segments"], lines):
        assert page["text"][seg["start"]:seg["end"]] == line


def test_textfix_units():
    assert textfix.normalize("‏שלום‎  עולם\r\n") == "שלום עולם"
    assert textfix.fix_ocr("דייר נועה, יוייר הוועדה") == 'ד"ר נועה, יו"ר הוועדה'
    assert textfix.fix_ocr("ביידי") == "ביידי", "החלפה כללית של יי הורסת מילים"
    assert not textfix.words_reversed("שם הנבדק: איתי. תאריך: היום.")


# ================================================================
#  2. OCR - איכות לפי סוג קלט
# ================================================================

# הספים: מה שנמדד ב-fast על הקורפוס (spikes/ocr/STAGE3-QUALITY.md), ועוד
# מרווח של כ-3-5 נקודות. הם שומרים מפני נסיגה - לא מעידים שהאיכות
# "מספיקה". emg-1.scan כאן הוא JPEG (קטן יותר מה-PNG שנמדד): 16.3%.
@needs_ocr
@pytest.mark.parametrize("name,doc,limit", [
    ("emg-1.scan.jpg", "emg-1", 0.20),
    ("mri-1.degraded.jpg", "mri-1", 0.30),
    ("committee-2.phone.jpg", "committee-2", 0.25),
])
def test_ocr_quality_thresholds(name, doc, limit):
    outcome, result = extract(read(name))
    assert outcome == "ok", result
    page = result["pages"][0]
    assert page["source"] == "ocr"
    assert page["model"] in ("fast", "best", "fast+best")
    assert cer(truth(doc)["text"], page["text"]) < limit


@needs_ocr
def test_ocr_segments_link_every_line_to_the_page():
    _, result = extract(read("emg-1.scan.jpg"))
    page = result["pages"][0]
    w, h = page["size"]
    assert page["segments"]
    for seg in page["segments"]:
        x0, y0, x1, y1 = seg["bbox"]
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h
        assert 0 <= seg["conf"] <= 100
        assert page["text"][seg["start"]:seg["end"]].strip()


@needs_ocr
def test_phone_photo_is_flattened_and_scan_is_not():
    _, phone = extract(read("committee-2.phone.jpg"))
    _, scan = extract(read("emg-1.scan.jpg"))
    assert phone["pages"][0]["flattened"] is True
    assert scan["pages"][0]["flattened"] is False


@needs_ocr
def test_icd_second_pass_recovers_codes_inside_hebrew_lines():
    _, result = extract(read("emg-1.scan.jpg"))
    assert "G56.0" in [c["code"] for c in result["pages"][0]["icd"]]


# ================================================================
#  3. בחירת המודל - best רק כשהוא בטוח יותר
# ================================================================

def _fake_reads(monkeypatch, fast_conf, best_conf):
    def fake_read(png, model):
        conf = fast_conf if model == "fast" else best_conf
        return [{"text": model, "bbox": [0, 0, 10, 10], "conf": conf, "words": 1}], conf
    monkeypatch.setattr(ocr, "_read", fake_read)
    monkeypatch.setattr(ocr, "_icd_pass", lambda img, lines: [])
    monkeypatch.setattr(ocr, "_refine_lines", lambda img, lines, mode="replace": (0, []))
    monkeypatch.setattr(ocr, "MODE", "hybrid")


def test_hybrid_keeps_fast_when_best_is_not_more_confident(monkeypatch):
    from PIL import Image
    _fake_reads(monkeypatch, fast_conf=60, best_conf=55)
    page = ocr.ocr_page(Image.new("L", (50, 50), 255))
    assert (page["model"], page["text"]) == ("fast", "fast")


def test_hybrid_switches_to_best_only_when_it_is_more_confident(monkeypatch):
    from PIL import Image
    _fake_reads(monkeypatch, fast_conf=60, best_conf=91)
    assert ocr.ocr_page(Image.new("L", (50, 50), 255))["model"] == "best"


def test_confident_page_never_pays_for_best(monkeypatch):
    from PIL import Image
    calls = []
    _fake_reads(monkeypatch, fast_conf=95, best_conf=99)
    original = ocr._read
    monkeypatch.setattr(ocr, "_read", lambda png, model: (calls.append(model), original(png, model))[1])
    ocr.ocr_page(Image.new("L", (50, 50), 255))
    assert calls == ["fast"]


def test_line_refinement_only_replaces_with_higher_confidence(monkeypatch):
    from PIL import Image
    lines = [{"text": "PANN", "bbox": [0, 0, 20, 10], "conf": 50.0, "words": 1},
             {"text": "טוב", "bbox": [0, 10, 20, 20], "conf": 70.0, "words": 1}]
    answers = iter([[{"text": "שם", "conf": 90.0, "words": 1}],       # שורה 0: משתפר
                    [{"text": "גרוע", "conf": 40.0, "words": 1}]])    # שורה 1: לא
    monkeypatch.setattr(ocr, "_run", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "_parse_tsv", lambda tsv: next(answers))
    replaced, _ = ocr._refine_lines(Image.new("L", (30, 30), 255), lines, "replace")
    assert replaced == 1
    assert lines[0]["text"] == "שם" and lines[1]["text"] == "טוב"


def test_alternative_mode_never_changes_the_text(monkeypatch):
    """
    ברירת המחדל אחרי המדידה: הקריאה השנייה נשמרת לצד הראשונה ואינה
    מחליפה אותה (best החליף 2026 ב-2076 - spikes/ocr/STAGE3-QUALITY.md).
    """
    from PIL import Image
    lines = [{"text": "17/12/2026", "bbox": [0, 0, 20, 10], "conf": 50.0, "words": 1}]
    monkeypatch.setattr(ocr, "_run", lambda *a, **k: "")
    monkeypatch.setattr(ocr, "_parse_tsv",
                        lambda tsv: [{"text": "17/12/2076", "conf": 95.0, "words": 1}])
    replaced, alternatives = ocr._refine_lines(Image.new("L", (30, 30), 255), lines,
                                               "alternative")
    assert replaced == 0
    assert lines[0]["text"] == "17/12/2026"
    assert alternatives == [{"line": 0, "text": "17/12/2076", "conf": 95.0}]



def test_default_is_fast_only_as_measured():
    """
    המדידה: כל צורת מעבר ל-best הוסיפה שגיאה מספרית או איבדה עובדה
    נכונה (spikes/ocr/STAGE3-QUALITY.md). ברירת המחדל - fast בלבד.
    """
    assert worker.OCR_MODE == "fast" or os.environ.get("PORTAL_OCR_MODE")
    assert worker.extract_env([1])["SANDBOX_OCR_MODE"] == worker.OCR_MODE
    assert ocr.LINE_MODE == "off"


# ================================================================
#  4. worker: אחסון מוצפן, קישור לעמוד, עיבוד חוזר, אטומיות
# ================================================================

def _three_page_pdf():
    """emg + committee + עמוד ריק (כמו סריקה) - שלושה עמודים, שלושה מקורות."""
    import io

    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument.new()
    for name in ("emg-1.pdf", "committee-2.pdf"):
        doc.import_pages(pdfium.PdfDocument(read(name)))
    doc.new_page(612, 792)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@needs_ocr
def test_worker_stores_every_page_encrypted_and_linked(temp_case):
    fid = make_file(temp_case, data=_three_page_pdf())
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    job = job_of(fid)
    assert job["status"] == "done", job
    assert "tesseract" in job["engine"] and "pdfium" in job["engine"]

    pages = processing.read_pages(temp_case["firm_id"], fid)
    assert [p["page"] for p in pages] == [1, 2, 3]
    assert "EMG" in pages[0]["text"] and "ועדה" in pages[1]["text"]
    assert pages[0]["source"] == "layer" and pages[2]["source"] == "ocr"
    assert pages[0]["requiresHumanVerification"] is False
    assert pages[2]["requiresHumanVerification"] is True

    with cursor() as cur:
        cur.execute("select text_enc, layout_enc from document_pages where file_id = %s", (fid,))
        for row in cur.fetchall():
            for blob in (bytes(row["text_enc"]), bytes(row["layout_enc"])):
                assert crypto.is_encrypted(blob)
                assert "EMG".encode() not in blob and "ועדה".encode() not in blob


def test_reprocessing_replaces_pages_instead_of_duplicating(temp_case):
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    for _ in range(2):
        enqueue(fid, temp_case["firm_id"])
        with cursor(commit=True) as cur:
            cur.execute("update document_processing set status = 'queued', finished_at = null,"
                        " next_attempt_at = now() where file_id = %s", (fid,))
        worker.process_one("t", [fid])
    with cursor() as cur:
        cur.execute("select count(*) as n from document_pages where file_id = %s", (fid,))
        assert cur.fetchone()["n"] == 1


def test_a_failed_batch_stores_nothing(temp_case, monkeypatch):
    """עמוד 1 שכבת טקסט, עמוד 3 צריך OCR - ו-OCR לא זמין. אין חצי מסמך."""
    monkeypatch.setattr(worker, "TESSERACT", str(pathlib.Path("no", "such", "tesseract")))
    fid = make_file(temp_case, data=_three_page_pdf())
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    job = job_of(fid)
    assert job["status"] == "queued" and job["error_code"] == "ocr_unavailable"
    with cursor() as cur:
        cur.execute("select count(*) as n from document_pages where file_id = %s", (fid,))
        assert cur.fetchone()["n"] == 0


def test_processing_audit_has_counts_not_text(temp_case):
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    with cursor() as cur:
        cur.execute("""select metadata from audit_log where action = 'system.document_processed'
                        and metadata->>'file_id' = %s""", (fid,))
        meta = cur.fetchone()["metadata"]
    assert set(meta) <= audit.SAFE_METADATA_KEYS
    assert "EMG" not in json.dumps(meta) and "נוירולוג" not in json.dumps(meta, ensure_ascii=False)


# ================================================================
#  5. מגבלות שלב 2 - גם עם Tesseract
# ================================================================

def _tesseract_count():
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq tesseract.exe", "/NH"],
                             capture_output=True, text=True).stdout
        return out.lower().count("tesseract.exe")
    return int(subprocess.run(["pgrep", "-c", "tesseract"], capture_output=True,
                              text=True).stdout.strip() or 0)


def _heavy_image():
    """ארבע סריקות ברשת 2x2 - כ-33 מגה-פיקסל, מתחת לתקרה, וכבד מספיק
    כדי ש-Tesseract עדיין ירוץ כשה-timeout פוקע."""
    import io

    from PIL import Image
    tile = Image.open(io.BytesIO(read("emg-1.scan.jpg"))).convert("L")
    big = Image.new("L", (tile.width * 2, tile.height * 2), 255)
    for x in (0, tile.width):
        for y in (0, tile.height):
            big.paste(tile, (x, y))
    buf = io.BytesIO()
    big.save(buf, "PNG")
    return buf.getvalue()


@needs_ocr
def test_memory_cap_applies_to_ocr_too():
    outcome, code = extract(read("emg-1.scan.jpg"), SANDBOX_MEMORY_MB="48")
    assert outcome != "ok", "OCR רץ מתחת לתקרה שאינה מספיקה לו - התקרה לא חלה"


@needs_ocr
def test_timeout_leaves_no_tesseract_behind():
    before = _tesseract_count()
    outcome, _ = extract(_heavy_image(), timeout=2)
    assert outcome == "timeout"
    deadline = time.monotonic() + 5
    while _tesseract_count() > before and time.monotonic() < deadline:
        time.sleep(0.2)
    assert _tesseract_count() <= before, "Tesseract נשאר חי אחרי שה-sandbox נהרג"


@needs_ocr
def test_ocr_writes_no_temp_files():
    temp = pathlib.Path(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp")
    before = {p.name for p in temp.iterdir()} if temp.exists() else set()
    outcome, _ = extract(read("committee-2.phone.jpg"))
    assert outcome == "ok"
    after = {p.name for p in temp.iterdir()} if temp.exists() else set()
    new = {n for n in after - before if "tess" in n.lower() or n.lower().endswith((".png", ".tif"))}
    assert not new, new


def test_extract_cannot_spawn_a_second_helper():
    """התקרה במצב extract היא תהליך-בן אחד (Tesseract), לא יותר."""
    if os.name != "nt":
        pytest.skip("ב-Linux מוגבל ב-pids_limit של הקונטיינר - שער Linux")
    code = ("import subprocess,sys\nfrom server import limits\nlimits.apply(256, 30, 2)\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])\n"
            "try:\n  subprocess.run([sys.executable, '-c', 'pass'], check=True)\n  print('second')\n"
            "except OSError:\n  print('blocked')\np.kill()")
    out = subprocess.run([sys.executable, "-E", "-s", "-c", code], cwd=worker.ROOT,
                         env=worker.sandbox_env(), capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "blocked", out.stdout + out.stderr


# ================================================================
#  6. METADATA_ONLY
# ================================================================

def test_policy_is_untouched_and_ocr_never_reaches_ai():
    assert policy.POLICY_MODE == "METADATA_ONLY"
    src = "\n".join((worker.ROOT / "server" / n).read_text(encoding="utf-8")
                    for n in ("app.py", "policy.py"))
    for name in ("read_pages", "document_pages", "ocr_page", "textfix"):
        assert name not in src, "%s מגיע לנתיב ה-AI" % name
