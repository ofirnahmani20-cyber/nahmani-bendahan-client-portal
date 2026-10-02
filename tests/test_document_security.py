"""
שלב 0 של Layer 2: חסמי האבטחה לפני עיבוד מסמכים.

1. הצפנה במנוחה - קובץ לעולם אינו נכתב גלוי, גם לא זמנית.
2. סריקה אמיתית (clamd) - נגוע אינו נשמר, תקלה אינה clean.
3. שער אחד - קובץ שלא נסרק נקי אינו נגיש לשום שימוש.
4. יומן - אין טקסט ואין שאילתות.

הבדיקות משתמשות ב-FakeClamd שמדבר את הפרוטוקול האמיתי, ולכן
הלקוח ב-scan.py נבדק מקצה לקצה גם בלי ClamAV מותקן.
"""

import base64
import io
import os
import pathlib
import tempfile

import pytest
from starlette.testclient import TestClient

from server import audit, crypto, scan, storage
from server.app import app
from server.db.pool import cursor

from .fake_clamd import EICAR, FakeClamd
from .test_office_documents import staff_login
from .test_uploads import LOCAL, logged_in_client  # noqa: F401  (fixture)

PDF = b"%PDF-1.4\n% synthetic test document - not real data\n"
SIGNATURES = (b"%PDF", b"\xff\xd8\xff", b"\x89PNG")


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
def clamd(monkeypatch):
    server = FakeClamd()
    monkeypatch.setenv("PORTAL_CLAMD_ADDR", server.addr)
    yield server
    server.close()


def _upload(ctx, content, filename="doc.pdf"):
    return ctx["api"].post(
        "/api/client/documents/%s/files" % ctx["document_id"],
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
        headers={"X-CSRF-Token": ctx["csrf"]},
    )


def _row(file_id):
    with cursor() as cur:
        cur.execute("select storage_key, scan_status, firm_id from document_files"
                    " where id = %s", (file_id,))
        return cur.fetchone()


def _storage_root():
    return pathlib.Path(os.environ["PORTAL_STORAGE_DIR"])


# ================================================================
#  1. הצפנה
# ================================================================

def test_stored_file_is_ciphertext_and_reads_back(logged_in_client):
    r = _upload(logged_in_client, PDF)
    assert r.status_code == 200, r.text
    key = _row(r.json()["fileId"])["storage_key"]

    on_disk = (_storage_root() / key).read_bytes()
    assert crypto.is_encrypted(on_disk)
    assert PDF not in on_disk, "הבתים המקוריים נמצאו בדיסק"
    assert storage.read(key) == PDF


def test_no_plaintext_signature_anywhere_in_storage(logged_in_client):
    """אחרי העלאות - אין בכל תיקיית האחסון קובץ שמתחיל כ-PDF/JPEG/PNG."""
    _upload(logged_in_client, PDF)
    _upload(logged_in_client, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "a.png")
    for path in _storage_root().rglob("*"):
        if path.is_file():
            head = path.read_bytes()[:8]
            assert not head.startswith(SIGNATURES), "קובץ גלוי באחסון"


def test_tampered_ciphertext_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path))
    meta = storage.validate_and_store(PDF, firm_id="f1", case_id="c1",
                                      original_name="x.pdf")
    path = tmp_path / meta["storage_key"]
    blob = bytearray(path.read_bytes())
    blob[-5] ^= 0x01
    path.write_bytes(bytes(blob))
    with pytest.raises(crypto.DecryptionFailed):
        storage.read(meta["storage_key"])


def test_ciphertext_moved_to_another_record_fails(tmp_path, monkeypatch):
    """AAD מצמיד צופן לנתיב שלו. העתקה לתיק או למשרד אחר נכשלת."""
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path))
    meta = storage.validate_and_store(PDF, firm_id="f1", case_id="c1",
                                      original_name="x.pdf")
    blob = (tmp_path / meta["storage_key"]).read_bytes()
    for other in ("firms/f1/cases/c2/x", "firms/f2/cases/c1/x"):
        dest = tmp_path / other
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        with pytest.raises(crypto.DecryptionFailed):
            storage.read(other)


def test_without_keys_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(tmp_path / "no-such-dir"))
    with pytest.raises(crypto.KeysUnavailable):
        storage.validate_and_store(PDF, firm_id="f1", case_id="c1",
                                   original_name="x.pdf")
    assert not (tmp_path / "up").exists() or not any((tmp_path / "up").rglob("*.*"))


def test_upload_without_keys_is_refused_before_reading(logged_in_client, monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(tmp_path / "missing"))
    r = _upload(logged_in_client, PDF)
    assert r.status_code == 503


def test_rotation_new_writes_use_new_version_old_still_read(tmp_path, monkeypatch):
    secrets_dir = tmp_path / "secrets"
    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(secrets_dir))
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path / "up"))
    crypto.generate_keys(secrets_dir)

    old = storage.validate_and_store(PDF, firm_id="f", case_id="c", original_name="a")
    crypto.write_key_file(secrets_dir, "files", 2, os.urandom(32))
    new = storage.validate_and_store(PDF, firm_id="f", case_id="c", original_name="b")

    def version(key):
        return crypto.parse_header((tmp_path / "up" / key).read_bytes())[1]

    assert version(old["storage_key"]) == 1
    assert version(new["storage_key"]) == 2
    assert storage.read(old["storage_key"]) == PDF
    assert storage.read(new["storage_key"]) == PDF


def test_wrong_key_with_same_version_is_caught_by_kcv(tmp_path, monkeypatch):
    secrets_dir = tmp_path / "secrets"
    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(secrets_dir))
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path / "up"))
    crypto.generate_keys(secrets_dir)
    meta = storage.validate_and_store(PDF, firm_id="f", case_id="c", original_name="a")

    (secrets_dir / "files_kek_v1").write_text(
        base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(crypto.KeysUnavailable, match="KCV"):
        storage.read(meta["storage_key"])


def test_key_file_is_never_overwritten(tmp_path):
    crypto.write_key_file(tmp_path, "files", 1, b"a" * 32)
    with pytest.raises(FileExistsError):
        crypto.write_key_file(tmp_path, "files", 1, b"b" * 32)


# ================================================================
#  2. אין קבצים זמניים גלויים
# ================================================================

def test_large_upload_creates_no_temp_file(logged_in_client, monkeypatch, tmp_path):
    """
    5MB - מעל סף ה-spool של Starlette (1MB). כל דרך ליצור קובץ
    זמני נחסמת; אם ההעלאה בכל זאת הייתה נוגעת בדיסק, היא הייתה
    נכשלת.
    """
    import starlette.formparsers as fp

    def forbidden(*a, **kw):
        raise AssertionError("נוצר קובץ זמני בזמן העלאה")

    for name in ("SpooledTemporaryFile", "TemporaryFile",
                 "NamedTemporaryFile", "mkstemp"):
        monkeypatch.setattr(tempfile, name, forbidden)
    monkeypatch.setattr(fp, "SpooledTemporaryFile", forbidden)
    spy_dir = tmp_path / "tmp"
    spy_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(spy_dir))

    big = PDF + b"\x00" * (5 * 1024 * 1024)
    r = _upload(logged_in_client, big)
    assert r.status_code == 200, r.text
    assert list(spy_dir.iterdir()) == []


def test_oversized_upload_stops_early(logged_in_client, monkeypatch):
    monkeypatch.setattr(storage, "MAX_BYTES", 1024)
    r = _upload(logged_in_client, PDF + b"\x00" * 200_000)
    assert r.status_code == 400
    assert "גדול" in r.json()["detail"]


def test_db_failure_after_write_leaves_no_file(logged_in_client, monkeypatch):
    """הכתיבה הצליחה, ה-insert נכשל: הפעולה המפצה מוחקת את הצופן."""
    from server import api_client

    real_cursor = api_client.cursor
    before = {p for p in _storage_root().rglob("*") if p.is_file()}

    def failing_cursor(commit=False):
        if commit:
            raise RuntimeError("simulated database failure")
        return real_cursor()

    monkeypatch.setattr(api_client, "cursor", failing_cursor)
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1",
                    raise_server_exceptions=False) as c:
        c.cookies = logged_in_client["api"].cookies
        r = c.post("/api/client/documents/%s/files" % logged_in_client["document_id"],
                   files={"file": ("x.pdf", io.BytesIO(PDF), "application/pdf")},
                   headers={"X-CSRF-Token": logged_in_client["csrf"]})
    assert r.status_code == 500
    after = {p for p in _storage_root().rglob("*") if p.is_file()}
    assert after == before, "נשאר קובץ יתום אחרי כשל במסד"


def test_crash_mid_write_leaves_only_ciphertext_and_sweep_removes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path))

    def crash(*a, **kw):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(storage.os, "replace", crash)
    with pytest.raises(OSError):
        storage.validate_and_store(PDF, firm_id="f", case_id="c", original_name="a")
    monkeypatch.undo()
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path))
    # הכשל מנקה את ה-.part בעצמו; וגם אם לא - sweep אוסף.
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file()]
    for p in leftovers:
        assert crypto.is_encrypted(p.read_bytes())
    assert storage.sweep(None, min_age=0)["parts"] == len(
        [p for p in leftovers if p.name.endswith(".part")])


def test_sweep_removes_orphans_but_keeps_known(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path))
    keep = storage.validate_and_store(PDF, firm_id="f", case_id="c", original_name="a")
    orphan = storage.validate_and_store(PDF, firm_id="f", case_id="c", original_name="b")
    dry = storage.sweep({keep["storage_key"]}, min_age=0, dry_run=True)
    assert dry["orphans"] == 1 and (tmp_path / orphan["storage_key"]).exists()
    storage.sweep({keep["storage_key"]}, min_age=0)
    assert not (tmp_path / orphan["storage_key"]).exists()
    assert (tmp_path / keep["storage_key"]).exists()


# ================================================================
#  3. סריקה
# ================================================================

def test_clean_file_is_marked_clean_by_real_protocol(logged_in_client, clamd):
    r = _upload(logged_in_client, PDF)
    assert r.status_code == 200, r.text
    assert r.json()["scanStatus"] == "clean"
    assert clamd.scanned == 1


def test_infected_file_is_rejected_and_not_stored(logged_in_client, clamd):
    before = {p for p in _storage_root().rglob("*") if p.is_file()}
    r = _upload(logged_in_client, PDF + EICAR)
    assert r.status_code == 400
    assert "נחסם" in r.json()["detail"]
    after = {p for p in _storage_root().rglob("*") if p.is_file()}
    assert after == before, "קובץ נגוע נשמר"


def test_scanner_down_means_failed_never_clean(logged_in_client, monkeypatch):
    monkeypatch.setenv("PORTAL_CLAMD_ADDR", "tcp://127.0.0.1:1")
    r = _upload(logged_in_client, PDF)
    assert r.status_code == 200
    assert r.json()["scanStatus"] == "failed"
    row = _row(r.json()["fileId"])
    assert row["scan_status"] == "failed"
    assert crypto.is_encrypted((_storage_root() / row["storage_key"]).read_bytes())


def test_garbled_scanner_reply_is_failed(clamd):
    clamd.broken = True
    assert scan.ClamdScanner(clamd.addr).scan(PDF).status == "failed"


def test_rescan_promotes_failed_to_clean(logged_in_client, monkeypatch, clamd):
    from server import maintenance

    monkeypatch.setenv("PORTAL_CLAMD_ADDR", "tcp://127.0.0.1:1")
    file_id = _upload(logged_in_client, PDF).json()["fileId"]
    assert _row(file_id)["scan_status"] == "failed"

    monkeypatch.setenv("PORTAL_CLAMD_ADDR", clamd.addr)
    maintenance.rescan(limit=1000)
    assert _row(file_id)["scan_status"] == "clean"


def test_rescan_deletes_infected_ciphertext(logged_in_client, monkeypatch, clamd):
    from server import maintenance

    monkeypatch.setenv("PORTAL_CLAMD_ADDR", "tcp://127.0.0.1:1")
    file_id = _upload(logged_in_client, PDF + EICAR).json()["fileId"]
    key = _row(file_id)["storage_key"]

    monkeypatch.setenv("PORTAL_CLAMD_ADDR", clamd.addr)
    maintenance.rescan(limit=1000)
    assert _row(file_id)["scan_status"] == "infected"
    assert not (_storage_root() / key).exists()


def test_production_refuses_to_start_without_keys_or_scanner(monkeypatch, tmp_path):
    from server import app as module

    monkeypatch.setenv("PORTAL_SECRETS_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("PORTAL_CLAMD_ADDR", "")
    with pytest.raises(RuntimeError) as exc:
        module._assert_production_ready()
    assert "הצפנה במנוחה" in str(exc.value)
    assert "סורק" in str(exc.value)


def test_production_gate_requires_scanner_and_fresh_signatures(monkeypatch, clamd):
    monkeypatch.setenv("PORTAL_CLAMD_ADDR", "")
    assert scan.production_problems()

    monkeypatch.setenv("PORTAL_CLAMD_ADDR", clamd.addr)
    clamd.version = "ClamAV 1.4.1/27000/Mon Jan  1 00:00:00 2024"
    assert any("48" in p for p in scan.production_problems())

    import datetime
    now = datetime.datetime.now().strftime("%a %b %d %H:%M:%S %Y")
    clamd.version = "ClamAV 1.4.1/27412/" + now
    assert scan.production_problems() == []


#  מול ClamAV אמיתי - רק כשהוגדר במפורש (scripts/clamav-local.ps1).
#  הסמן מזוהה בחתימת הבדיקה nahmani-test.ndb שהסקריפט מתקין בפיתוח.
REAL_CLAMD = os.environ.get("PORTAL_TEST_CLAMD_ADDR", "")
MARKER = b"NB-SYNTHETIC-MALWARE-MARKER-FOR-TESTS-ONLY"


@pytest.mark.skipif(not REAL_CLAMD, reason="PORTAL_TEST_CLAMD_ADDR לא הוגדר")
def test_real_clamd_end_to_end(logged_in_client, monkeypatch):
    monkeypatch.setenv("PORTAL_CLAMD_ADDR", REAL_CLAMD)
    assert scan.get_scanner().ping()

    ok = _upload(logged_in_client, PDF)
    assert ok.status_code == 200 and ok.json()["scanStatus"] == "clean"
    got = logged_in_client["api"].get("/api/client/documents/%s/files/%s"
                                      % (logged_in_client["document_id"],
                                         ok.json()["fileId"]))
    assert got.status_code == 200 and got.content == PDF

    bad = _upload(logged_in_client, PDF + MARKER)
    assert bad.status_code == 400 and "נחסם" in bad.json()["detail"]


# ================================================================
#  4. השער האחד: רק clean נגיש
# ================================================================

@pytest.mark.parametrize("status", ["pending", "failed", "infected"])
@pytest.mark.parametrize("side", ["client", "office"])
def test_not_clean_is_not_downloadable_anywhere(logged_in_client, status, side):
    r = _upload(logged_in_client, PDF)
    file_id = r.json()["fileId"]
    with cursor(commit=True) as cur:
        cur.execute("update document_files set scan_status = %s where id = %s",
                    (status, file_id))

    doc = logged_in_client["document_id"]
    if side == "client":
        resp = logged_in_client["api"].get(
            "/api/client/documents/%s/files/%s" % (doc, file_id))
    else:
        with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
            staff_login(c)
            resp = c.get("/api/office/documents/%s/files/%s" % (doc, file_id))
    assert resp.status_code == 409, (side, status, resp.text)


def test_usable_is_the_only_gate():
    assert scan.usable("clean")
    for status in ("pending", "failed", "infected", None, ""):
        assert not scan.usable(status)
    assert scan.downloadable is scan.usable


# ================================================================
#  5. כותרת הורדה עם שם בעברית
# ================================================================

def test_hebrew_filename_download_does_not_break(logged_in_client, clamd):
    r = _upload(logged_in_client, PDF, filename='סיכום "אשפוז".pdf')
    assert r.status_code == 200, r.text
    resp = logged_in_client["api"].get(
        "/api/client/documents/%s/files/%s"
        % (logged_in_client["document_id"], r.json()["fileId"]))
    assert resp.status_code == 200, resp.text
    assert resp.content == PDF
    disp = resp.headers["content-disposition"]
    assert disp.startswith("attachment;")
    assert "filename*=UTF-8''" in disp
    assert disp.count('"') == 2, "מירכאות בשם שברו את הכותרת"


# ================================================================
#  6. היומן
# ================================================================

def test_audit_never_keeps_text_or_queries():
    kept = audit._safe({"q": "EMG ישראל", "query": "x", "text": "y",
                        "value": "z", "snippet": "w", "body": "v",
                        "kinds": ["document"], "result_count": 3})
    assert kept == {"kinds": ["document"], "result_count": 3}


def test_infected_rejection_is_audited_without_content(logged_in_client, clamd):
    _upload(logged_in_client, PDF + EICAR)
    with cursor() as cur:
        cur.execute("""select metadata from audit_log
                        where action = 'client.file_rejected'
                        order by id desc limit 1""")
        meta = cur.fetchone()["metadata"]
    assert meta["scan_status"] == "infected"
    assert set(meta) <= audit.SAFE_METADATA_KEYS


def test_policy_mode_is_untouched():
    from server import policy
    assert policy.POLICY_MODE == "METADATA_ONLY"
