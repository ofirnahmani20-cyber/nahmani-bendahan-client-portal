"""
עמודים נקיים, PDF נקי וקלסר - cleanpage.py, pdfbuild.py, api_binder.py
(docs/10, v1 סעיף 4).

דורש מסד נתונים חי ו-Tesseract (לבדיקות מקצה לקצה דרך ה-worker).
"""

import io

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw
from starlette.testclient import TestClient

from server import cleanpage, maintenance, pdfbuild, storage, worker
from server.app import app
from server.db.pool import cursor

from .test_extraction import read
from .test_processing import LOCAL, STAFF, enqueue, login, make_file

OTHER = ("bendahan@nahmani-bendahan.co.il", "office2026")


def _jpeg(size=(1240, 1754), mode="L"):
    buf = io.BytesIO()
    Image.new(mode, size, 200).save(buf, "JPEG")
    return buf.getvalue()


def _text_page(angle=0.0):
    """עמוד סינתטי: שורות "טקסט" כהות על רקע בהיר, מוטה ב-angle מעלות."""
    img = Image.new("L", (1200, 1600), 245)
    d = ImageDraw.Draw(img)
    for y in range(150, 1450, 48):
        d.rectangle((150, y, 1050, y + 14), fill=30)
    return img.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=245)


# ================================================================
#  ניקוי עמוד
# ================================================================

@pytest.mark.parametrize("tilt", [3.0, -2.0, 4.5])
def test_skew_is_detected_and_reversed(tilt):
    found = cleanpage.skew_angle(_text_page(tilt))
    assert abs(found + tilt) <= 0.5, (tilt, found)


def test_straight_page_is_not_rotated():
    assert cleanpage.skew_angle(_text_page(0.0)) == 0.0


def test_clean_output_is_grayscale_jpeg_at_most_a4_width():
    jpeg, w, h, angle = cleanpage.clean(_text_page(3.0).convert("RGB").resize((2400, 3200)))
    iw, ih, comps = pdfbuild.jpeg_info(jpeg)
    assert (iw, ih) == (w, h) and comps == 1
    assert w <= cleanpage.MAX_WIDTH
    assert angle != 0


# ================================================================
#  בניית PDF - בלי לפענח את התמונות
# ================================================================

def test_jpeg_info_reads_the_header_only():
    assert pdfbuild.jpeg_info(_jpeg((300, 400), "L")) == (300, 400, 1)
    assert pdfbuild.jpeg_info(_jpeg((640, 480), "RGB")) == (640, 480, 3)
    png = io.BytesIO()
    Image.new("L", (10, 10)).save(png, "PNG")
    with pytest.raises(pdfbuild.NotJpeg):
        pdfbuild.jpeg_info(png.getvalue())


def test_built_pdf_opens_with_numbered_pages_and_right_orientation():
    data = pdfbuild.build([_jpeg(), _jpeg((1754, 1240), "RGB"), _jpeg()])
    pdf = pdfium.PdfDocument(data)
    assert len(pdf) == 3
    w, h = pdf[1].get_size()
    assert w > h                                   # עמוד לרוחב נשאר לרוחב
    assert pdf[2].get_textpage().get_text_range().strip() == "3 / 3"
    pdf.close()


def test_cover_renders_hebrew_without_error():
    cover = pdfbuild.binder_cover(["קלסר מסמכים - תיק BL-2031-0001", "ישראל ישראלי"],
                                  [("סיכום אשפוז", "2-4"), ("בדיקת EMG", "לא נכלל")])
    assert pdfbuild.jpeg_info(cover)[:2] == (1240, 1754)


# ================================================================
#  מקצה לקצה
# ================================================================

@pytest.fixture
def processed(temp_case):
    """emg-1.pdf עובר דרך ה-worker האמיתי - כולל משימת הניקוי ב-sandbox."""
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    with cursor() as cur:
        cur.execute("select page_no, storage_key from document_clean_pages"
                    " where file_id = %s order by page_no", (fid,))
        pages = cur.fetchall()
    yield dict(temp_case, file_id=fid, pages=pages)
    for p in pages:
        storage.delete(p["storage_key"])


def _api(who=STAFF):
    api = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    login(api, who)
    return api


def test_worker_stores_encrypted_clean_pages(processed):
    assert processed["pages"], "לא נוצרו עמודים נקיים"
    key = processed["pages"][0]["storage_key"]
    raw = storage._path_for(key).read_bytes()
    assert not raw.startswith(b"\xff\xd8"), "עמוד נקי נשמר לא מוצפן"
    assert storage.read(key).startswith(b"\xff\xd8")


def test_clean_pages_are_not_swept_as_orphans(processed):
    assert processed["pages"][0]["storage_key"] in maintenance._known_keys()


def test_clean_pdf_download(processed):
    api = _api()
    r = api.get("/api/office/documents/%s/files/%s/clean.pdf"
                % (processed["document_id"], processed["file_id"]))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    assert len(pdfium.PdfDocument(r.content)) == len(processed["pages"])


def test_clean_pdf_respects_case_scope(processed):
    """תיק זמני בלי אחראי - גלוי למנהל בלבד."""
    r = _api(OTHER).get("/api/office/documents/%s/files/%s/clean.pdf"
                        % (processed["document_id"], processed["file_id"]))
    assert r.status_code == 404


def test_clean_pdf_before_processing_is_409(temp_case):
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    r = _api().get("/api/office/documents/%s/files/%s/clean.pdf"
                   % (temp_case["document_id"], fid))
    assert r.status_code == 409


def test_binder_holds_cover_plus_approved_documents(processed):
    with cursor(commit=True) as cur:
        cur.execute("update case_documents set status = 'approved' where id = %s",
                    (processed["document_id"],))
    r = _api().get("/api/office/cases/%s/binder.pdf" % processed["case_id"])
    assert r.status_code == 200, r.text
    pdf = pdfium.PdfDocument(r.content)
    assert len(pdf) == 1 + len(processed["pages"])
    assert pdf[1].get_textpage().get_text_range().strip() == "2 / %d" % len(pdf)


def test_binder_lists_an_approved_document_without_clean_copy(temp_case):
    with cursor(commit=True) as cur:
        cur.execute("update case_documents set status = 'approved' where id = %s",
                    (temp_case["document_id"],))
    r = _api().get("/api/office/cases/%s/binder.pdf" % temp_case["case_id"])
    assert r.status_code == 200
    assert len(pdfium.PdfDocument(r.content)) == 1          # שער בלבד
    with cursor() as cur:
        cur.execute("""select metadata from audit_log where action = 'office.binder_downloaded'
                        and case_id = %s order by id desc limit 1""", (temp_case["case_id"],))
        assert cur.fetchone()["metadata"]["missing_clean_copy"] == 1


def test_binder_without_approved_documents_is_409(temp_case):
    r = _api().get("/api/office/cases/%s/binder.pdf" % temp_case["case_id"])
    assert r.status_code == 409


def test_binder_respects_case_scope(temp_case):
    r = _api(OTHER).get("/api/office/cases/%s/binder.pdf" % temp_case["case_id"])
    assert r.status_code == 404
