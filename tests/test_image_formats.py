"""
WebP, ובדיקת מבנה תמונה לפני קבלה.

WebP נוסף כי טלפונים רבים (ודפדפנים שמכווצים צילום לפני העלאה)
מייצרים אותו, ו-quality.js כבר קיבל אותו - כלומר הלקוח עבר את
בדיקת האיכות בדפדפן ואז נדחה בשרת.

הוספת סוג היא גם הוספת משטח תקיפה, ולכן כל תמונה - לא רק WebP -
נבדקת עכשיו מהכותרות: מבנה שלם, ממדים, ותקרת פיקסלים. בלי פענוח.
"""

import io
import struct
import zlib

import pytest
from PIL import Image

from server import imagecheck, storage

from .fake_clamd import FakeClamd
from .test_uploads import api, logged_in_client  # noqa: F401  (fixtures)


def _webp(**kw):
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (240, 240, 240)).save(buf, "WEBP", **kw)
    return buf.getvalue()


LOSSY = _webp(quality=80)
LOSSLESS = _webp(lossless=True)
# exif מכריח את Pillow לכתוב VP8X (extended)
EXTENDED = _webp(quality=80, exif=Image.Exif().tobytes())


def _png(w, h):
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b""))


def _jpeg_header(w, h):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof


def _vp8x(w, h, flags=0):
    payload = bytes([flags, 0, 0, 0]) + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ================================================================
#  WebP תקין - שלושת סוגי ה-chunk
# ================================================================

@pytest.mark.parametrize("data", [LOSSY, LOSSLESS, EXTENDED],
                         ids=["VP8-lossy", "VP8L-lossless", "VP8X-extended"])
def test_valid_webp_is_accepted_with_correct_size(data):
    assert storage.validate(data) == "image/webp"
    assert imagecheck.check(data, "image/webp") == (640, 480)


def test_extended_variant_really_is_vp8x():
    assert EXTENDED[12:16] == b"VP8X", "הבדיקה לא מכסה את מה שהיא מתיימרת לכסות"


# ================================================================
#  WebP עוין או פגום
# ================================================================

def test_animated_webp_is_rejected():
    buf = io.BytesIO()
    frames = [Image.new("RGB", (64, 64), c) for c in ("red", "blue")]
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:])
    with pytest.raises(storage.RejectedFile, match="מונפשת"):
        storage.validate(buf.getvalue())


def test_webp_declaring_gigapixels_is_rejected():
    with pytest.raises(storage.RejectedFile, match="גדולה מדי"):
        storage.validate(_vp8x(16384, 16384))


def test_webp_with_trailing_payload_is_rejected():
    """RIFF מצהיר על גודל אחד והקובץ ארוך ממנו - מקום להחביא תוכן."""
    with pytest.raises(storage.RejectedFile, match="גודל"):
        storage.validate(LOSSY + b"\x00" * 64 + b"<script>")


def test_truncated_webp_is_rejected():
    with pytest.raises(storage.RejectedFile):
        storage.validate(LOSSY[:len(LOSSY) // 2])


def test_webp_with_unknown_chunk_is_rejected():
    forged = LOSSY[:12] + b"EVIL" + LOSSY[16:]
    with pytest.raises(storage.RejectedFile):
        storage.validate(forged)


def test_riff_that_is_not_webp_is_rejected():
    wav = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + b"\x00" * 28
    with pytest.raises(storage.RejectedFile, match="אינו נתמך"):
        storage.validate(wav)


# ================================================================
#  אותה תקרה ל-PNG ול-JPEG - הפער שהבדיקה של WebP חשפה
# ================================================================

def test_png_bomb_header_is_rejected():
    with pytest.raises(storage.RejectedFile, match="גדולה מדי"):
        storage.validate(_png(60000, 60000))


def test_jpeg_bomb_header_is_rejected():
    with pytest.raises(storage.RejectedFile, match="גדולה מדי"):
        storage.validate(_jpeg_header(65000, 65000))


def test_png_without_ihdr_is_rejected():
    with pytest.raises(storage.RejectedFile):
        storage.validate(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)


def test_zero_dimension_is_rejected():
    with pytest.raises(storage.RejectedFile):
        storage.validate(_png(0, 100))


def test_real_png_and_jpeg_still_pass():
    for fmt in ("PNG", "JPEG"):
        buf = io.BytesIO()
        Image.new("RGB", (800, 600), "white").save(buf, fmt)
        assert storage.validate(buf.getvalue()) in ("image/png", "image/jpeg")
        assert imagecheck.check(buf.getvalue(),
                                "image/png" if fmt == "PNG" else "image/jpeg") == (800, 600)


# ================================================================
#  מקצה לקצה: העלאה, סריקה, הצפנה, הורדה
# ================================================================

def test_webp_upload_round_trip(logged_in_client, monkeypatch):
    server = FakeClamd()
    monkeypatch.setenv("PORTAL_CLAMD_ADDR", server.addr)
    try:
        ctx = logged_in_client
        r = ctx["api"].post(
            "/api/client/documents/%s/files" % ctx["document_id"],
            files={"file": ("photo.webp", io.BytesIO(LOSSY), "image/webp")},
            headers={"X-CSRF-Token": ctx["csrf"]})
        assert r.status_code == 200, r.text
        assert r.json()["scanStatus"] == "clean"
        assert server.scanned == 1, "WebP עקף את הסורק"

        got = ctx["api"].get("/api/client/documents/%s/files/%s"
                             % (ctx["document_id"], r.json()["fileId"]))
        assert got.status_code == 200
        assert got.content == LOSSY
        assert got.headers["content-type"] == "image/webp"
        assert got.headers["x-content-type-options"] == "nosniff"
    finally:
        server.close()


def test_hostile_webp_upload_is_refused_before_storage(logged_in_client):
    ctx = logged_in_client
    r = ctx["api"].post(
        "/api/client/documents/%s/files" % ctx["document_id"],
        files={"file": ("x.webp", io.BytesIO(_vp8x(20000, 20000)), "image/webp")},
        headers={"X-CSRF-Token": ctx["csrf"]})
    assert r.status_code == 400
