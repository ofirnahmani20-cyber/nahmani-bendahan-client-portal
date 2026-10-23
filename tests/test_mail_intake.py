"""
קליטת מסמכים ממייל - mail_intake.py ו-api_mailbox.py (docs/10, v1 סעיף 4).

השולח והתיק הם הצעה. שום קובץ אינו נכנס לתיק בלי שיוך של אדם.

דורש מסד נתונים חי. IMAP מוחלף ב-Fake - אין רשת.
"""

import uuid
from email.message import EmailMessage

import pytest
from starlette.testclient import TestClient

from server import mail_intake, maintenance, storage
from server.app import app
from server.db.connect import id_encrypt, id_lookup
from server.db.pool import cursor

from .samples import text_pdf
from .test_processing import LOCAL, STAFF, login

OTHER = ("bendahan@nahmani-bendahan.co.il", "office2026")
PDF = text_pdf(["SYNTHETIC intake page - long enough to be a real page of text"])


def message(sender, subject="מסמכים", attachments=(("scan.pdf", PDF, "application", "pdf"),),
            message_id=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "docs@nahmani-bendahan.co.il"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or "<%s@test>" % uuid.uuid4()
    msg.set_content("מצורף")
    for name, data, maintype, subtype in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg.as_bytes()


@pytest.fixture
def client_case():
    """לקוח עם אימייל ייחודי ותיק פעיל אחד (בלי אחראי - גלוי למנהל בלבד)."""
    marker = uuid.uuid4().hex[:8]
    email_addr = "mail-%s@example.com" % marker
    with cursor(commit=True) as cur:
        cur.execute("select id, firm_id, claim_type_id from cases limit 1")
        base = cur.fetchone()
        nid = "9%08d" % int(marker[:6], 16)
        cur.execute("""insert into clients (firm_id, full_name, national_id_lookup,
                                            national_id_enc, phone, email)
                       values (%s, 'לקוח מייל', %s, %s, '050-1234567', %s) returning id""",
                    (base["firm_id"], id_lookup(nid), id_encrypt(nid), email_addr))
        client_id = cur.fetchone()["id"]
        cur.execute("""insert into cases (firm_id, client_id, claim_type_id, case_number, opened_at)
                       values (%s, %s, %s, %s, current_date) returning id""",
                    (base["firm_id"], client_id, base["claim_type_id"], "ML-2031-%s" % marker[:4]))
        case_id = cur.fetchone()["id"]
        cur.execute("""insert into case_documents (firm_id, case_id, name, guidance, position)
                       values (%s, %s, 'מסמך מהמייל', 'הנחיה', 1) returning id""",
                    (base["firm_id"], case_id))
        document_id = cur.fetchone()["id"]
    yield {"firm_id": base["firm_id"], "client_id": client_id, "case_id": case_id,
           "document_id": document_id, "email": email_addr,
           "claim_type_id": base["claim_type_id"]}
    with cursor(commit=True) as cur:
        cur.execute("select storage_key from intake_items where firm_id = %s and"
                    " (matched_client_id = %s or from_address like 'stranger-%%')",
                    (base["firm_id"], client_id))
        keys = [r["storage_key"] for r in cur.fetchall()]
        cur.execute("delete from intake_items where matched_client_id = %s"
                    " or from_address like 'stranger-%%'", (client_id,))
        cur.execute("delete from document_processing where file_id in (select f.id from document_files f"
                    " join case_documents d on d.id = f.document_id where d.case_id = %s)", (case_id,))
        cur.execute("delete from document_files where document_id in"
                    " (select id from case_documents where case_id = %s)", (case_id,))
        cur.execute("delete from case_documents where case_id = %s", (case_id,))
        cur.execute("delete from audit_log where case_id = %s", (case_id,))
        cur.execute("delete from cases where client_id = %s", (client_id,))
        cur.execute("delete from clients where id = %s", (client_id,))
    for k in keys:
        storage.delete(k)


def _items(client_id=None, sender=None):
    with cursor() as cur:
        cur.execute("""select * from intake_items
                        where matched_client_id is not distinct from %s
                          and (%s::text is null or from_address = %s::text)""",
                    (client_id, sender, sender))
        return cur.fetchall()


def _api(who=STAFF):
    api = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    login(api, who)
    api.headers["X-CSRF-Token"] = api.cookies.get("portal_csrf")
    return api


# ================================================================
#  פענוח
# ================================================================

def test_parse_returns_sender_case_numbers_and_files_but_not_the_subject():
    raw = message("Client <Someone@Example.com>", subject="בדיקת MRI תיק bl-2031-0007")
    sender, mid, cases, files = mail_intake.parse(raw)
    assert sender == "someone@example.com"
    assert cases == ["BL-2031-0007"]
    assert [n for n, _ in files] == ["scan.pdf"]
    assert mid


# ================================================================
#  קליטה
# ================================================================

def test_ingest_stores_allowed_files_encrypted_and_suggests_the_case(client_case):
    raw = message(client_case["email"], attachments=[
        ("scan.pdf", PDF, "application", "pdf"),
        ("letter.docx", b"PK\x03\x04 not allowed", "application", "octet-stream")])
    counts = mail_intake.ingest(raw, client_case["firm_id"])
    assert counts["stored"] == 1 and counts["rejected"] == 1
    item = _items(client_case["client_id"])[0]
    assert str(item["matched_case_id"]) == str(client_case["case_id"])
    assert item["status"] == "new"
    raw_bytes = storage._path_for(item["storage_key"]).read_bytes()
    assert not raw_bytes.startswith(b"%PDF"), "קובץ ממייל נשמר לא מוצפן"
    assert storage.read(item["storage_key"]).startswith(b"%PDF")


def test_same_message_twice_is_stored_once(client_case):
    raw = message(client_case["email"], message_id="<dup-%s@test>" % uuid.uuid4())
    mail_intake.ingest(raw, client_case["firm_id"])
    assert mail_intake.ingest(raw, client_case["firm_id"])["duplicate"] == 1
    assert len(_items(client_case["client_id"])) == 1


def test_unknown_sender_gets_no_suggestion(client_case):
    sender = "stranger-%s@example.com" % uuid.uuid4().hex[:6]
    mail_intake.ingest(message(sender), client_case["firm_id"])
    item = _items(None, sender)[0]
    assert item["matched_case_id"] is None and item["matched_client_id"] is None


def test_subject_case_number_picks_among_several_cases(client_case):
    with cursor(commit=True) as cur:
        cur.execute("""insert into cases (firm_id, client_id, claim_type_id, case_number, opened_at)
                       values (%s, %s, %s, %s, current_date) returning id, case_number""",
                    (client_case["firm_id"], client_case["client_id"],
                     client_case["claim_type_id"], "ML-2031-%04d" % (uuid.uuid4().int % 10000)))
        second = cur.fetchone()
    mail_intake.ingest(message(client_case["email"]), client_case["firm_id"])
    mail_intake.ingest(message(client_case["email"], subject="תיק " + second["case_number"]),
                       client_case["firm_id"])
    matched = sorted(str(i["matched_case_id"]) for i in _items(client_case["client_id"]))
    assert matched == sorted(["None", str(second["id"])])


def test_intake_files_are_not_swept_as_orphans(client_case):
    mail_intake.ingest(message(client_case["email"]), client_case["firm_id"])
    assert _items(client_case["client_id"])[0]["storage_key"] in maintenance._known_keys()


# ================================================================
#  ה-API
# ================================================================

def _clean_item(client_case):
    mail_intake.ingest(message(client_case["email"]), client_case["firm_id"])
    item = _items(client_case["client_id"])[0]
    with cursor(commit=True) as cur:
        cur.execute("update intake_items set scan_status = 'clean' where id = %s", (item["id"],))
    return str(item["id"])


def test_mailbox_list_respects_case_scope(client_case):
    item_id = _clean_item(client_case)
    assert item_id in [i["id"] for i in _api().get("/api/office/mailbox").json()["items"]]
    # התיק בלי אחראי - bendahan אינו רואה אותו, ולכן גם לא את הפריט
    assert item_id not in [i["id"] for i in _api(OTHER).get("/api/office/mailbox").json()["items"]]


def test_assign_creates_an_email_file_under_the_requirement(client_case):
    item_id = _clean_item(client_case)
    api = _api()
    r = api.post("/api/office/mailbox/%s/assign" % item_id,
                 json={"document_id": str(client_case["document_id"])})
    assert r.status_code == 200, r.text
    with cursor() as cur:
        cur.execute("select source, is_current from document_files where id = %s",
                    (r.json()["fileId"],))
        f = cur.fetchone()
        cur.execute("select status from case_documents where id = %s", (client_case["document_id"],))
        doc = cur.fetchone()
        cur.execute("select count(*) as n from document_processing where file_id = %s",
                    (r.json()["fileId"],))
        queued = cur.fetchone()["n"]
    assert f["source"] == "email" and f["is_current"]
    assert doc["status"] == "pending_review"
    assert queued == 1
    again = api.post("/api/office/mailbox/%s/assign" % item_id,
                     json={"document_id": str(client_case["document_id"])})
    assert again.status_code == 409


def test_assign_to_a_requirement_outside_scope_is_refused(client_case):
    item_id = _clean_item(client_case)
    with cursor(commit=True) as cur:
        cur.execute("""update cases set assigned_user_id = (select id from users where email = %s)
                        where id = %s""", (OTHER[0], client_case["case_id"]))
        # תיק של המנהל שאינו של bendahan
        cur.execute("""select d.id from case_documents d join cases c on c.id = d.case_id
                        join users u on u.id = c.assigned_user_id
                       where u.email = %s limit 1""", (STAFF[0],))
        foreign_doc = cur.fetchone()["id"]
    r = _api(OTHER).post("/api/office/mailbox/%s/assign" % item_id,
                         json={"document_id": str(foreign_doc)})
    assert r.status_code == 404


def test_preview_requires_a_clean_scan(client_case):
    mail_intake.ingest(message(client_case["email"]), client_case["firm_id"])
    item = _items(client_case["client_id"])[0]
    api = _api()
    with cursor(commit=True) as cur:
        cur.execute("update intake_items set scan_status = 'pending' where id = %s", (item["id"],))
    assert api.get("/api/office/mailbox/%s/file" % item["id"]).status_code == 409
    with cursor(commit=True) as cur:
        cur.execute("update intake_items set scan_status = 'clean' where id = %s", (item["id"],))
    r = api.get("/api/office/mailbox/%s/file" % item["id"])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_discard_deletes_the_file_and_keeps_the_record(client_case):
    item_id = _clean_item(client_case)
    key = _items(client_case["client_id"])[0]["storage_key"]
    assert _api().post("/api/office/mailbox/%s/discard" % item_id).status_code == 200
    assert not storage._path_for(key).exists()
    assert _items(client_case["client_id"])[0]["status"] == "discarded"


# ================================================================
#  IMAP - Fake
# ================================================================

class FakeImap:
    instances = []

    def __init__(self, host):
        self.host, self.calls, self.messages = host, [], {}
        FakeImap.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def select(self, folder):
        self.calls.append(("select", folder))

    def create(self, folder):
        self.calls.append(("create", folder))

    def uid(self, command, *args):
        self.calls.append((command,) + args)
        if command == "search":
            return "OK", [b" ".join(self.messages)]
        if command == "fetch":
            return "OK", [(b"1 (RFC822)", self.messages[args[0]])]
        return "OK", [b""]

    def expunge(self):
        self.calls.append(("expunge",))

    def logout(self):
        self.calls.append(("logout",))


def test_poll_ingests_and_moves_messages(client_case, monkeypatch):
    raw = message(client_case["email"])
    FakeImap.instances.clear()

    def factory(host):
        imap = FakeImap(host)
        imap.messages = {b"7": raw}
        return imap

    monkeypatch.setattr(mail_intake.imaplib, "IMAP4_SSL", factory)
    monkeypatch.setenv("PORTAL_INTAKE_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("PORTAL_INTAKE_IMAP_USER", "docs")
    monkeypatch.setenv("PORTAL_INTAKE_IMAP_PASSWORD", "secret")
    totals = mail_intake.poll()
    assert totals["messages"] == 1 and totals["stored"] == 1
    calls = FakeImap.instances[0].calls
    assert ("copy", b"7", "Processed") in calls
    assert ("store", b"7", "+FLAGS", r"(\Deleted)") in calls
    assert len(_items(client_case["client_id"])) == 1


def test_poll_is_off_without_configuration(monkeypatch):
    monkeypatch.delenv("PORTAL_INTAKE_IMAP_HOST", raising=False)
    assert mail_intake.poll() is None
