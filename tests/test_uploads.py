"""
העלאת קבצים והורדתם.

לפני 13.09 לא הייתה העלאה בכלל: dashboard.js שמר את file.name
כמחרוזת ב-localStorage והבייטים נזרקו. הבדיקות כאן מכסות את
שרשרת האבטחה שנבנתה במקום.
"""

import io

import pytest
from starlette.testclient import TestClient

from server import scan, storage
from server.app import app
from server.db.pool import cursor

LOCAL = ("127.0.0.1", 45123)

PDF = b"%PDF-1.4\n% minimal\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
NOT_A_PDF = b"MZ\x90\x00this is a windows executable"


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
def logged_in_client(api):
    """לקוח מחובר + מזהה מסמך פתוח שלו."""
    from server import auth

    with cursor() as cur:
        cur.execute(
            """select c.client_id, c.firm_id, d.id as document_id
                 from cases c
                 join case_documents d on d.case_id = c.id
                order by d.position limit 1"""
        )
        row = cur.fetchone()

    class _Req:
        client = type("c", (), {"host": "127.0.0.1"})()
        headers = {}

    class _Resp:
        def __init__(self): self.jar = {}
        def set_cookie(self, name, value, **kw): self.jar[name] = value

    resp = _Resp()
    auth.create_session(resp, firm_id=row["firm_id"], subject_type="client",
                        subject_id=row["client_id"], request=_Req(),
                        hours=auth.CLIENT_SESSION_HOURS)
    for name, value in resp.jar.items():
        api.cookies.set(name, value)
    return {"api": api, "csrf": resp.jar["portal_csrf"],
            "document_id": str(row["document_id"])}


def _upload(ctx, content, filename="doc.pdf", csrf=True):
    headers = {"X-CSRF-Token": ctx["csrf"]} if csrf else {}
    return ctx["api"].post(
        "/api/client/documents/%s/files" % ctx["document_id"],
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
        headers=headers,
    )


# ================================================================
#  זיהוי סוג לפי תוכן
# ================================================================

def test_sniff_recognises_real_types():
    assert storage.sniff_mime(PDF) == "application/pdf"
    assert storage.sniff_mime(PNG) == "image/png"
    assert storage.sniff_mime(JPEG) == "image/jpeg"


def test_executable_renamed_to_pdf_is_rejected(logged_in_client):
    """
    הבדיקה המרכזית: סיומת וכותרת Content-Type אומרות PDF,
    התוכן אומר קובץ הרצה. התוכן מנצח.
    """
    r = _upload(logged_in_client, NOT_A_PDF, filename="innocent.pdf")
    assert r.status_code == 400
    assert "סוג הקובץ" in r.json()["detail"]


def test_empty_file_is_rejected(logged_in_client):
    assert _upload(logged_in_client, b"").status_code == 400


def test_oversized_file_is_rejected(logged_in_client, monkeypatch):
    monkeypatch.setattr(storage, "MAX_BYTES", 100)
    r = _upload(logged_in_client, PDF + b"\x00" * 500)
    assert r.status_code == 400


# ================================================================
#  שם הקובץ
# ================================================================

@pytest.mark.parametrize("raw,expected_not_in", [
    ("../../etc/passwd", "/"),
    ("..\\..\\windows\\system32\\cmd.exe", "\\"),
    ("bad\x00name.pdf", "\x00"),
])
def test_filename_is_sanitised(raw, expected_not_in):
    cleaned = storage.safe_filename(raw)
    assert expected_not_in not in cleaned


def test_hidden_filename_loses_leading_dot():
    assert not storage.safe_filename(".htaccess").startswith(".")


def test_storage_key_does_not_contain_original_name():
    key = storage.build_key("firm-1", "case-1")
    assert "doc" not in key
    assert key.startswith("firms/firm-1/cases/case-1/")


# ================================================================
#  סריקה - הכלל שאסור לשבור
# ================================================================

def test_upload_starts_as_pending_not_clean(logged_in_client):
    r = _upload(logged_in_client, PDF)
    assert r.status_code == 200, r.text
    assert r.json()["scanStatus"] == "pending", (
        "קובץ סומן כנקי בלי סריקה אמיתית")


def test_no_scanner_never_returns_clean():
    result = scan.NoScanner().scan(PDF)
    assert result.status == "pending"
    assert result.status != "clean"


def test_pending_file_cannot_be_downloaded(logged_in_client):
    up = _upload(logged_in_client, PDF)
    file_id = up.json()["fileId"]
    r = logged_in_client["api"].get(
        "/api/client/documents/%s/files/%s"
        % (logged_in_client["document_id"], file_id))
    assert r.status_code == 409, "קובץ שלא נסרק היה ניתן להורדה"


def test_production_without_scanner_refuses_upload(logged_in_client, monkeypatch):
    """fail closed: לא מקבלים מסמך שלעולם לא יהיה נגיש."""
    monkeypatch.setenv("PORTAL_MODE", "production")
    r = _upload(logged_in_client, PDF)
    assert r.status_code == 503


def test_downloadable_only_for_clean():
    assert scan.downloadable("clean")
    for status in ("pending", "infected", "failed"):
        assert not scan.downloadable(status)


# ================================================================
#  הרשאה
# ================================================================

def test_upload_requires_csrf(logged_in_client):
    assert _upload(logged_in_client, PDF, csrf=False).status_code == 403


def test_upload_to_another_clients_document_is_refused(logged_in_client):
    with cursor() as cur:
        cur.execute(
            """select d.id from case_documents d
                 join cases c on c.id = d.case_id
                where c.client_id <> (select client_id from cases c2
                                       join case_documents d2 on d2.case_id = c2.id
                                      order by d2.position limit 1)
                limit 1"""
        )
        other = cur.fetchone()
    if other is None:
        pytest.skip("אין מסמך של לקוח אחר בנתוני הזרע")

    r = logged_in_client["api"].post(
        "/api/client/documents/%s/files" % other["id"],
        files={"file": ("x.pdf", io.BytesIO(PDF), "application/pdf")},
        headers={"X-CSRF-Token": logged_in_client["csrf"]},
    )
    assert r.status_code == 404


def test_anonymous_cannot_upload(api):
    with cursor() as cur:
        cur.execute("select id from case_documents limit 1")
        doc = cur.fetchone()
    r = api.post("/api/client/documents/%s/files" % doc["id"],
                 files={"file": ("x.pdf", io.BytesIO(PDF), "application/pdf")})
    assert r.status_code == 401


# ================================================================
#  כותרות אבטחה
# ================================================================

def test_security_headers_present(api):
    r = api.get("/index.html")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert "object-src 'none'" in r.headers["Content-Security-Policy"]


def test_api_responses_are_not_cached(api):
    r = api.get("/api/health")
    assert r.headers.get("Cache-Control") == "no-store"
