"""
שלב 2 של Layer 2: תשתית העיבוד.

מה ננעל כאן
------------
1. המסד עצמו: אין תור לקובץ שאינו clean, אין טקסט גלוי, אין
   אישור עובדה בלי מאשר, וקובץ שהפך ללא-clean מאבד את נגזרותיו.
2. התור: SKIP LOCKED, ניסיונות חוזרים, עבודות יתומות.
3. ה-worker וה-sandbox: מגבלות קשיחות, סביבה בלי סודות, קבצים
   עוינים נדחים ולא מפילים, אין קובץ זמני.
4. הרשאות: can_view_medical, בידוד משרדים, הלקוח לא רואה כלום,
   ויומן בלי תוכן.

כל המסמכים כאן סינתטיים (tests/samples.py).
"""

import datetime
import json
import tempfile
import time

import psycopg
import pytest
from starlette.testclient import TestClient

from server import audit, policy, processing, storage, worker
from server.app import app
from server.db.connect import connect
from server.db.pool import cursor

from . import samples
from .test_firm_isolation import second_firm  # noqa: F401  (fixture)
from .test_uploads import LOCAL

STAFF = ("nahmani@nahmani-bendahan.co.il", "office2026")
OTHER_STAFF = ("bendahan@nahmani-bendahan.co.il", "office2026")
TEXT_PDF = samples.text_pdf(["SYNTHETIC page one - text layer for the processing test",
                             "SYNTHETIC page two - also long enough to count as text"])


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


def login(api, who=STAFF):
    r = api.post("/api/office/auth/login", json={"email": who[0], "password": who[1]})
    assert r.status_code == 200, r.text


def make_file(case, data=TEXT_PDF, scan_status="clean", firm_id=None, document_id=None):
    firm_id = firm_id or case["firm_id"]
    meta = storage.validate_and_store(data, firm_id=str(firm_id),
                                      case_id=str(case["case_id"]), original_name="s.pdf")
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into document_files (firm_id, document_id, storage_key,
                   original_filename, mime_type, size_bytes, scan_status)
               values (%s, %s, %s, 's.pdf', %s, %s, %s) returning id""",
            (firm_id, document_id or case["document_id"], meta["storage_key"],
             meta["mime_type"], meta["size_bytes"], scan_status))
        return str(cur.fetchone()["id"])


def job_of(file_id):
    with cursor() as cur:
        cur.execute("select * from document_processing where file_id = %s", (file_id,))
        return cur.fetchone()


def enqueue(file_id, firm_id):
    with cursor(commit=True) as cur:
        return processing.enqueue(cur, file_id, firm_id)


# ================================================================
#  1. אכיפה במסד
# ================================================================

@pytest.mark.parametrize("status", ["pending", "failed", "infected"])
def test_db_refuses_to_queue_a_file_that_is_not_clean(temp_case, status):
    fid = make_file(temp_case, scan_status=status)
    with pytest.raises(psycopg.errors.CheckViolation):
        enqueue(fid, temp_case["firm_id"])


def test_db_refuses_plaintext_page_text(temp_case):
    fid = make_file(temp_case)
    with pytest.raises(psycopg.errors.CheckViolation):
        with cursor(commit=True) as cur:
            cur.execute("""insert into document_pages (firm_id, file_id, page_no, source,
                               char_count, text_enc)
                           values (%s, %s, 1, 'layer', 5, %s)""",
                        (temp_case["firm_id"], fid, b"hello plaintext"))


def test_db_refuses_confirmed_fact_without_reviewer(temp_case):
    fid = make_file(temp_case)
    from server import crypto
    blob = crypto.encrypt(b"25", purpose="text", firm_id=str(temp_case["firm_id"]),
                          record_id="fact:test")
    with pytest.raises(psycopg.errors.CheckViolation):
        with cursor(commit=True) as cur:
            cur.execute("""insert into document_facts (firm_id, file_id, page_no, kind,
                               value_enc, offset_start, offset_end, source, status, engine)
                           values (%s, %s, 1, 'percent', %s, 0, 2, 'ocr', 'confirmed', 't')""",
                        (temp_case["firm_id"], fid, blob))


def test_file_turning_unclean_purges_everything_derived(temp_case):
    fid = make_file(temp_case)
    enqueue(fid, temp_case["firm_id"])
    with cursor(commit=True) as cur:
        processing.store_page_text(cur, firm_id=temp_case["firm_id"], file_id=fid,
                                   page_no=1, text="synthetic", source="layer")
        cur.execute("update document_files set scan_status = 'infected' where id = %s", (fid,))
    with cursor() as cur:
        cur.execute("select count(*) as n from document_pages where file_id = %s", (fid,))
        assert cur.fetchone()["n"] == 0
    job = job_of(fid)
    assert job["status"] == "rejected" and job["error_code"] == "not_clean"


def test_deleting_the_file_deletes_derived_data(temp_case):
    """מדיניות השמירה של המקור חלה על הנגזרות - CASCADE."""
    fid = make_file(temp_case)
    enqueue(fid, temp_case["firm_id"])
    with cursor(commit=True) as cur:
        processing.store_page_text(cur, firm_id=temp_case["firm_id"], file_id=fid,
                                   page_no=1, text="synthetic", source="layer")
        cur.execute("delete from document_files where id = %s", (fid,))
        cur.execute("select (select count(*) from document_pages where file_id = %s) +"
                    " (select count(*) from document_processing where file_id = %s) as n",
                    (fid, fid))
        assert cur.fetchone()["n"] == 0


def test_job_cannot_point_to_a_file_of_another_firm(temp_case, second_firm):
    """שתי שכבות מסרבות: הטריגר (הקובץ אינו clean *באותו משרד*)
    רץ ראשון, ואחריו המפתח הזר המורכב. כל אחת מהן מספיקה."""
    fid = make_file(temp_case)
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation)):
        enqueue(fid, second_firm["firm_id"])
    # המפתח הזר לבדו, בלי הטריגר. ה-DDL טרנזקציוני: הכשל מגלגל
    # אחורה גם את ה-disable, והטריגר חוזר לפעול בלי צעד נוסף.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with cursor(commit=True) as cur:
            cur.execute("alter table document_processing disable trigger"
                        " trg_processing_requires_clean")
            processing.enqueue(cur, fid, second_firm["firm_id"])
    with cursor() as cur:
        cur.execute("select tgenabled from pg_trigger"
                    " where tgname = 'trg_processing_requires_clean'")
        assert cur.fetchone()["tgenabled"] == "O", "הטריגר נשאר כבוי"


# ================================================================
#  2. התור
# ================================================================

def test_clean_upload_is_queued_and_pending_is_not(temp_case):
    clean = make_file(temp_case)
    pending = make_file(temp_case, scan_status="pending")
    assert enqueue(clean, temp_case["firm_id"]) is True
    assert enqueue(clean, temp_case["firm_id"]) is False, "עבודה כפולה לאותו קובץ"
    assert job_of(clean)["status"] == "queued"
    assert job_of(pending) is None


def test_skip_locked_never_hands_the_same_job_twice(temp_case):
    a, b = make_file(temp_case), make_file(temp_case)
    enqueue(a, temp_case["firm_id"])
    enqueue(b, temp_case["firm_id"])
    with connect() as other:                       # worker אחר מחזיק את a
        with other.cursor() as cur:
            cur.execute("select id from document_processing where file_id = %s"
                        " for update skip locked", (a,))
            got = processing.claim("w2", [a, b])
            assert str(got["file_id"]) == b
        other.rollback()


def test_transient_failure_retries_then_fails(temp_case):
    fid = make_file(temp_case)
    enqueue(fid, temp_case["firm_id"])
    for attempt in (1, 2, 3):
        with cursor(commit=True) as cur:
            cur.execute("update document_processing set next_attempt_at = now()"
                        " where file_id = %s", (fid,))
        job = processing.claim("w", [fid])
        assert job["attempts"] == attempt
        status = processing.fail(job, "timeout", 10)
        assert status == ("queued" if attempt < 3 else "failed")
    assert job_of(fid)["finished_at"] is not None


def test_hostile_file_is_rejected_without_retry(temp_case):
    fid = make_file(temp_case)
    enqueue(fid, temp_case["firm_id"])
    job = processing.claim("w", [fid])
    assert processing.fail(job, "too_many_pages", 5) == "rejected"


def test_stale_running_job_is_recovered(temp_case):
    fid = make_file(temp_case)
    enqueue(fid, temp_case["firm_id"])
    processing.claim("dead-worker", [fid])
    with cursor(commit=True) as cur:
        cur.execute("update document_processing set locked_at = now() - interval '1 hour'"
                    " where file_id = %s", (fid,))
    assert processing.recover_stale() >= 1
    job = job_of(fid)
    assert job["status"] == "queued" and job["error_code"] == "worker_lost"


# ================================================================
#  3. worker + sandbox
# ================================================================

def test_worker_processes_a_synthetic_pdf_end_to_end(temp_case):
    fid = make_file(temp_case)
    enqueue(fid, temp_case["firm_id"])
    assert worker.process_one("test-worker", [fid]) is True
    job = job_of(fid)
    assert job["status"] == "done", job
    assert (job["page_count"], job["text_layer_pages"], job["ocr_needed_pages"]) == (2, 2, 0)
    assert "pdfium" in job["engine"]

    with cursor() as cur:
        cur.execute("""select metadata from audit_log where action = 'system.document_processed'
                        and metadata->>'file_id' = %s""", (fid,))
        meta = cur.fetchone()["metadata"]
    assert set(meta) <= audit.SAFE_METADATA_KEYS
    assert meta["page_count"] == 2


def test_scanned_pages_are_marked_for_ocr(temp_case):
    fid = make_file(temp_case, data=samples.blank_pdf(3))
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    assert job_of(fid)["ocr_needed_pages"] == 3


@pytest.mark.parametrize("make,code", [
    (lambda: samples.many_pages(5000), "too_many_pages"),
    (samples.huge_page, "page_too_large"),
])
def test_hostile_pdf_is_rejected_by_the_worker(temp_case, make, code):
    fid = make_file(temp_case, data=make())
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    job = job_of(fid)
    assert (job["status"], job["error_code"]) == ("rejected", code)


def test_worker_rechecks_scan_status_at_run_time(temp_case):
    """
    התור והמסד כבר שומרים - אבל ה-worker בודק שוב. כאן עוקפים את
    הטריגר בכוונה (session_replication_role) כדי לוודא שהשער השני
    עומד גם לבדו.
    """
    fid = make_file(temp_case)
    enqueue(fid, temp_case["firm_id"])
    with cursor(commit=True) as cur:
        cur.execute("set local session_replication_role = replica")
        cur.execute("update document_files set scan_status = 'pending' where id = %s", (fid,))
    worker.process_one("t", [fid])
    job = job_of(fid)
    assert (job["status"], job["error_code"]) == ("rejected", "not_clean")


def test_one_unclean_job_does_not_jam_the_queue(temp_case):
    """
    באג שנתפס בכתיבת הבדיקות: עבודה שהקובץ שלה אינו clean נדחתה
    בטריגר בכל claim, נשארה בראש התור, ותקעה את כל מה שאחריה.
    """
    bad, good = make_file(temp_case), make_file(temp_case)
    enqueue(bad, temp_case["firm_id"])
    enqueue(good, temp_case["firm_id"])
    with cursor(commit=True) as cur:
        cur.execute("update document_processing set next_attempt_at = now() - interval '1 hour'"
                    " where file_id = %s", (bad,))          # הרע ראשון בתור
        cur.execute("set local session_replication_role = replica")
        cur.execute("update document_files set scan_status = 'failed' where id = %s", (bad,))
    job = processing.claim("w", [bad, good])
    assert str(job["file_id"]) == good
    assert job_of(bad)["status"] == "rejected"


def test_inflate_bomb_is_stopped_by_the_memory_cap_not_the_clock():
    """ב-spike אותה פצצה הגיעה ל-3.9GB ונהרגה רק אחרי 45 שניות."""
    started = time.monotonic()
    outcome, code = worker.run_sandbox(samples.inflate_bomb(600), timeout=60,
                                       extra_env={"SANDBOX_MEMORY_MB": "256"})
    assert outcome in ("rejected", "memory_limit"), (outcome, code)
    assert code in ("native_fault", "memory_limit")
    assert time.monotonic() - started < 30, "התקרה לא עצרה - רק השעון"


def _selftest(task, arg="", memory="200"):
    return worker.run_sandbox(b"", timeout=30, extra_env={
        "SANDBOX_SELFTEST": "1", "SANDBOX_TASK": task, "SANDBOX_ARG": arg,
        "SANDBOX_MEMORY_MB": memory})


def test_sandbox_memory_cap_is_hard():
    assert _selftest("alloc", "50")[0] == "ok"
    assert _selftest("alloc", "1000") == ("memory_limit", "memory_limit")


def test_sandbox_has_no_secrets_and_no_database():
    outcome, result = _selftest("env")
    assert outcome == "ok"
    for secret in ("PORTAL_SECRETS_DIR", "DATABASE_URL", "PORTAL_ID_ENC_KEY",
                   "PORTAL_ID_HMAC_KEY", "ANTHROPIC_API_KEY", "TEMP", "TMP"):
        assert secret not in result["env"], secret
    assert result["modules"] == ["server", "server.limits"], (
        "ה-sandbox טען מודול שמחזיק מסד או מפתחות")


def test_sandbox_cannot_spawn_processes():
    assert _selftest("spawn")[0] != "ok"


def test_sandbox_timeout_kills_the_child():
    started = time.monotonic()
    outcome, _ = worker.run_sandbox(b"", timeout=2, extra_env={
        "SANDBOX_SELFTEST": "1", "SANDBOX_TASK": "sleep", "SANDBOX_ARG": "30"})
    assert outcome == "timeout"
    assert time.monotonic() - started < 10


def test_selftests_are_off_unless_explicitly_enabled():
    outcome, code = worker.run_sandbox(b"", timeout=30, extra_env={"SANDBOX_TASK": "env"})
    assert (outcome, code) == ("rejected", "unknown_task")


def test_processing_writes_no_temp_file(temp_case, monkeypatch, tmp_path):
    for name in ("SpooledTemporaryFile", "TemporaryFile", "NamedTemporaryFile", "mkstemp"):
        monkeypatch.setattr(tempfile, name, lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("קובץ זמני בזמן עיבוד")))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    fid = make_file(temp_case)
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    assert job_of(fid)["status"] == "done"
    assert list(tmp_path.iterdir()) == []


# ================================================================
#  4. הרשאות, בידוד, דליפה
# ================================================================

@pytest.fixture
def file_with_text(temp_case):
    fid = make_file(temp_case)
    with cursor(commit=True) as cur:
        processing.store_page_text(cur, firm_id=temp_case["firm_id"], file_id=fid,
                                   page_no=1, text="SYNTHETIC medical text", source="layer")
    return {"file_id": fid, "document_id": str(temp_case["document_id"])}


def pages_url(f):
    return "/api/office/documents/%s/files/%s/pages" % (f["document_id"], f["file_id"])


def test_medical_permission_can_read_text_and_is_audited_without_it(api, file_with_text):
    login(api)
    r = api.get(pages_url(file_with_text))
    assert r.status_code == 200, r.text
    assert r.json()["pages"][0]["text"] == "SYNTHETIC medical text"
    with cursor() as cur:
        cur.execute("""select metadata from audit_log where action = 'office.document_text_viewed'
                        order by id desc limit 1""")
        meta = cur.fetchone()["metadata"]
    assert meta["file_id"] == file_with_text["file_id"]
    assert "SYNTHETIC" not in json.dumps(meta, ensure_ascii=False)


def test_staff_without_medical_permission_gets_403(api, file_with_text):
    with cursor(commit=True) as cur:
        cur.execute("update users set can_view_medical = false where email = %s",
                    (OTHER_STAFF[0],))
    try:
        login(api, OTHER_STAFF)
        assert api.get(pages_url(file_with_text)).status_code == 403
        # מצב העיבוד אינו תוכן - נגיש לכל הצוות
        status = api.get(pages_url(file_with_text).replace("/pages", "/processing"))
        assert status.status_code == 200
    finally:
        with cursor(commit=True) as cur:
            cur.execute("update users set can_view_medical = true where email = %s",
                        (OTHER_STAFF[0],))


def test_permission_revocation_applies_immediately(api, file_with_text):
    login(api, OTHER_STAFF)
    assert api.get(pages_url(file_with_text)).status_code == 200
    with cursor(commit=True) as cur:
        cur.execute("update users set can_view_medical = false where email = %s",
                    (OTHER_STAFF[0],))
    try:
        assert api.get(pages_url(file_with_text)).status_code == 403
    finally:
        with cursor(commit=True) as cur:
            cur.execute("update users set can_view_medical = true where email = %s",
                        (OTHER_STAFF[0],))


def test_other_firm_gets_404_even_with_medical_permission(api, file_with_text, second_firm):
    with cursor(commit=True) as cur:
        cur.execute("update users set can_view_medical = true where id = %s",
                    (second_firm["user_id"],))
    login(api, (second_firm["email"], second_firm["password"]))
    assert api.get(pages_url(file_with_text)).status_code == 404
    assert api.get(pages_url(file_with_text).replace("/pages", "/processing")).status_code == 404


def test_anonymous_and_client_cannot_reach_medical_endpoints(api, file_with_text):
    assert api.get(pages_url(file_with_text)).status_code == 401
    from .test_uploads import logged_in_client as _  # noqa: F401
    from server import auth
    with cursor() as cur:
        cur.execute("select client_id, firm_id from cases where id = "
                    "(select case_id from case_documents where id = %s)",
                    (file_with_text["document_id"],))
        row = cur.fetchone()

    class _Req:
        client = type("c", (), {"host": "127.0.0.1"})()
        headers = {}

    class _Resp:
        def __init__(self): self.jar = {}
        def set_cookie(self, name, value, **kw): self.jar[name] = value

    resp = _Resp()
    auth.create_session(resp, firm_id=row["firm_id"], subject_type="client",
                        subject_id=row["client_id"], request=_Req(), hours=1)
    for name, value in resp.jar.items():
        api.cookies.set(name, value)
    assert api.get(pages_url(file_with_text)).status_code == 403


def test_text_of_unclean_file_is_not_served(api, file_with_text):
    with cursor(commit=True) as cur:
        cur.execute("set local session_replication_role = replica")   # בלי ה-purge
        cur.execute("update document_files set scan_status = 'pending' where id = %s",
                    (file_with_text["file_id"],))
    login(api)
    assert api.get(pages_url(file_with_text)).status_code == 409


def test_page_text_is_ciphertext_bound_to_its_row(temp_case):
    from server import crypto
    fid = make_file(temp_case)
    with cursor(commit=True) as cur:
        a = processing.store_page_text(cur, firm_id=temp_case["firm_id"], file_id=fid,
                                       page_no=1, text="SYNTHETIC one", source="layer")
        processing.store_page_text(cur, firm_id=temp_case["firm_id"], file_id=fid,
                                   page_no=2, text="SYNTHETIC two", source="ocr")
        cur.execute("select text_enc from document_pages where id = %s", (a,))
        blob = bytes(cur.fetchone()["text_enc"])
        assert b"SYNTHETIC" not in blob
        # העתקת הצופן של עמוד 1 לעמוד 2: הפענוח חייב להיכשל
        cur.execute("update document_pages set text_enc = %s where file_id = %s and page_no = 2",
                    (blob, fid))
    with pytest.raises(crypto.DecryptionFailed):
        processing.read_pages(temp_case["firm_id"], fid)


def test_client_api_and_ai_context_never_carry_processing_data(temp_case):
    """METADATA_ONLY: רשימת ההיתר אינה מעבירה שדה שאינו בה."""
    case = {"claimType": "x", "documents": [{"name": "doc", "status": "approved",
                                             "pages": [{"text": "SYNTHETIC"}],
                                             "facts": [{"value": "25%"}],
                                             "processing": {"status": "done"}}],
            "extractedText": "SYNTHETIC", "facts": ["25%"]}
    ctx = json.dumps(policy.assert_clean(policy.build_context(case)), ensure_ascii=False)
    for leaked in ("SYNTHETIC", "25%", "processing", "facts", "pages", "extracted"):
        assert leaked not in ctx
    assert policy.POLICY_MODE == "METADATA_ONLY"

    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "server" / "api_client.py").read_text(
        encoding="utf-8")
    for table in ("document_pages", "document_facts", "document_classifications",
                  "document_processing"):
        assert table not in src, "api_client נוגע ב-%s" % table


# ================================================================
#  5. דוח היתומים - קריאה בלבד
# ================================================================

def test_orphan_report_is_read_only(tmp_path, monkeypatch):
    from server import maintenance
    monkeypatch.setenv("PORTAL_STORAGE_DIR", str(tmp_path))
    import uuid
    meta = storage.validate_and_store(b"%PDF-1.4\n% synthetic\n", firm_id=str(uuid.uuid4()),
                                      case_id=str(uuid.uuid4()), original_name="x")
    path = tmp_path / meta["storage_key"]
    before = path.read_bytes()
    rows = maintenance.orphan_report()
    assert len(rows) == 1 and rows[0]["content"] == "synthetic test stub"
    assert rows[0]["case_exists"] is False
    maintenance.write_orphan_report(rows, tmp_path / "r.md")
    assert path.read_bytes() == before, "הדוח שינה קובץ"
