"""
חמש היכולות שהוחזרו דרך ה-Backend.

כולן היו קיימות בממשק, נחתכו במעבר ל-API כי לא היה להן endpoint,
וחזרו עכשיו עם הרשאה, בידוד משרדים, CSRF ורישום ביקורת.
"""

import pytest
from starlette.testclient import TestClient

from server import auth
from server.app import app
from server.db.pool import cursor

LOCAL = ("127.0.0.1", 45123)
STAFF_EMAIL = "nahmani@nahmani-bendahan.co.il"
STAFF_PASSWORD = "office2026"


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


def staff_login(api):
    r = api.post("/api/office/auth/login",
                 json={"email": STAFF_EMAIL, "password": STAFF_PASSWORD})
    assert r.status_code == 200, r.text
    return api.cookies.get("portal_csrf")


def client_login(api, client_id):
    with cursor() as cur:
        cur.execute("select firm_id from clients where id = %s", (client_id,))
        firm_id = cur.fetchone()["firm_id"]

    class _Req:
        client = type("c", (), {"host": "127.0.0.1"})()
        headers = {}

    class _Resp:
        def __init__(self): self.jar = {}
        def set_cookie(self, name, value, **kw): self.jar[name] = value

    resp = _Resp()
    auth.create_session(resp, firm_id=firm_id, subject_type="client",
                        subject_id=client_id, request=_Req(),
                        hours=auth.CLIENT_SESSION_HOURS)
    for name, value in resp.jar.items():
        api.cookies.set(name, value)
    return resp.jar["portal_csrf"]


DECISION = {"decided_at": "2026-09-01", "outcome": "grant", "percent": 25,
            "is_permanent": False, "appeal_deadline": "2026-10-01",
            "office_note": "הערה"}


# ================================================================
#  1. החלטת ועדה
# ================================================================

def test_decision_is_recorded(api, temp_case):
    csrf = staff_login(api)
    r = api.post("/api/office/cases/%s/decisions" % temp_case["case_id"],
                 json=DECISION, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text


def test_second_decision_does_not_overwrite_the_first(api, temp_case):
    """הדרישה המפורשת: החלטה חדשה אינה דורסת היסטוריה."""
    csrf = staff_login(api)
    api.post("/api/office/cases/%s/decisions" % temp_case["case_id"],
             json=DECISION, headers={"X-CSRF-Token": csrf})
    second = dict(DECISION, decided_at="2026-09-10", outcome="pension", percent=40)
    api.post("/api/office/cases/%s/decisions" % temp_case["case_id"],
             json=second, headers={"X-CSRF-Token": csrf})

    with cursor() as cur:
        cur.execute("""select outcome, decided_at from case_decisions
                        where case_id = %s order by decided_at""",
                    (temp_case["case_id"],))
        rows = cur.fetchall()
    assert len(rows) == 2, "ההחלטה הראשונה נמחקה"
    assert [r["outcome"] for r in rows] == ["grant", "pension"]


def test_decision_requires_csrf(api, temp_case):
    staff_login(api)
    r = api.post("/api/office/cases/%s/decisions" % temp_case["case_id"],
                 json=DECISION)
    assert r.status_code == 403


def test_decision_is_written_to_audit_log(api, temp_case):
    csrf = staff_login(api)
    api.post("/api/office/cases/%s/decisions" % temp_case["case_id"],
             json=DECISION, headers={"X-CSRF-Token": csrf})
    with cursor() as cur:
        cur.execute("""select count(*) as n from audit_log
                        where case_id = %s and action = 'office.decision_recorded'""",
                    (temp_case["case_id"],))
        assert cur.fetchone()["n"] >= 1


def test_client_cannot_record_a_decision(api, temp_case):
    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]
    csrf = client_login(api, client_id)
    r = api.post("/api/office/cases/%s/decisions" % temp_case["case_id"],
                 json=DECISION, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 403


# ================================================================
#  2. תגובת לקוח למסמך
# ================================================================

def test_client_can_reply_to_own_document(api, temp_case):
    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]
    csrf = client_login(api, client_id)

    r = api.post("/api/client/documents/%s/replies" % temp_case["document_id"],
                 json={"kind": "dont-have", "text": "אין לי"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    with cursor() as cur:
        cur.execute("select kind, text from document_replies where document_id = %s",
                    (temp_case["document_id"],))
        row = cur.fetchone()
    assert row["kind"] == "dont-have"


def test_client_cannot_reply_to_another_clients_document(api, temp_case):
    """הדרישה המפורשת: תגובה רק למסמך של התיק שלו."""
    with cursor() as cur:
        cur.execute("""select c.client_id from cases c
                        where c.id <> %s and c.client_id <> (
                              select client_id from cases where id = %s)
                        limit 1""",
                    (temp_case["case_id"], temp_case["case_id"]))
        other = cur.fetchone()
    if other is None:
        pytest.skip("אין לקוח שני בנתוני הזרע")

    csrf = client_login(api, other["client_id"])
    r = api.post("/api/client/documents/%s/replies" % temp_case["document_id"],
                 json={"kind": "dont-have"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404, "לקוח הגיב על מסמך של לקוח אחר"


def test_reply_requires_csrf(api, temp_case):
    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]
    client_login(api, client_id)
    r = api.post("/api/client/documents/%s/replies" % temp_case["document_id"],
                 json={"kind": "dont-have"})
    assert r.status_code == 403


def test_unknown_reply_kind_is_refused(api, temp_case):
    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]
    csrf = client_login(api, client_id)
    r = api.post("/api/client/documents/%s/replies" % temp_case["document_id"],
                 json={"kind": "whatever"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


# ================================================================
#  3. סגירת דרישת מסמך
# ================================================================

def test_cancel_keeps_the_row_and_its_files(api, temp_case):
    """הדרישה המפורשת: ביטול לא מוחק היסטוריה."""
    csrf = staff_login(api)
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into document_files
                 (firm_id, document_id, storage_key, original_filename,
                  mime_type, size_bytes, checksum)
               values (%s, %s, %s, 'old.pdf', 'application/pdf', 10, 'abc')""",
            (temp_case["firm_id"], temp_case["document_id"],
             "test/key/%s" % temp_case["document_id"]),
        )

    r = api.post("/api/office/documents/%s/cancel" % temp_case["document_id"],
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    with cursor() as cur:
        cur.execute("select status from case_documents where id = %s",
                    (temp_case["document_id"],))
        assert cur.fetchone()["status"] == "cancelled", "הסטטוס לא השתנה"

        cur.execute("select count(*) as n from document_files where document_id = %s",
                    (temp_case["document_id"],))
        assert cur.fetchone()["n"] == 1, "הקובץ שהועלה נמחק בקסקייד"


def test_cancel_requires_csrf(api, temp_case):
    staff_login(api)
    r = api.post("/api/office/documents/%s/cancel" % temp_case["document_id"])
    assert r.status_code == 403


def test_cancel_is_audited(api, temp_case):
    csrf = staff_login(api)
    api.post("/api/office/documents/%s/cancel" % temp_case["document_id"],
             headers={"X-CSRF-Token": csrf})
    with cursor() as cur:
        cur.execute("""select count(*) as n from audit_log
                        where case_id = %s and action = 'office.document_cancelled'""",
                    (temp_case["case_id"],))
        assert cur.fetchone()["n"] >= 1


def test_approved_document_cannot_be_cancelled(api, temp_case):
    csrf = staff_login(api)
    api.post("/api/office/documents/%s/review" % temp_case["document_id"],
             json={"decision": "approve"}, headers={"X-CSRF-Token": csrf})
    r = api.post("/api/office/documents/%s/cancel" % temp_case["document_id"],
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_cancelled_document_is_hidden_from_the_client(api, temp_case):
    csrf = staff_login(api)
    api.post("/api/office/documents/%s/cancel" % temp_case["document_id"],
             headers={"X-CSRF-Token": csrf})
    api.post("/api/office/auth/logout", headers={"X-CSRF-Token": csrf})

    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]
    client_login(api, client_id)

    body = api.get("/api/client/cases/%s" % temp_case["case_id"]).json()
    names = [d["name"] for d in body["documents"]]
    assert "מסמך זמני" not in names


# ================================================================
#  4. קטלוג המסמכים - מהמסד
# ================================================================

def test_catalog_comes_from_the_database(api, temp_case):
    staff_login(api)
    r = api.get("/api/office/cases/%s/document-templates" % temp_case["case_id"])
    assert r.status_code == 200
    templates = r.json()["templates"]
    assert templates, "הקטלוג ריק - required_document_templates לא נזרע"

    with cursor() as cur:
        cur.execute("""select count(*) as n from required_document_templates
                        where claim_type_id = %s""",
                    (temp_case["claim_type_id"],))
        assert len(templates) == cur.fetchone()["n"]


def test_catalog_requires_staff(api, temp_case):
    r = api.get("/api/office/cases/%s/document-templates" % temp_case["case_id"])
    assert r.status_code == 401


def test_catalog_of_unknown_case_is_refused(api):
    staff_login(api)
    import uuid
    r = api.get("/api/office/cases/%s/document-templates" % uuid.uuid4())
    assert r.status_code == 404


# ================================================================
#  5. יומן הפעולות
# ================================================================

def test_audit_log_is_served_from_the_database(api, temp_case):
    csrf = staff_login(api)
    api.post("/api/office/documents/%s/review" % temp_case["document_id"],
             json={"decision": "approve"}, headers={"X-CSRF-Token": csrf})

    r = api.get("/api/office/audit-log?case_id=%s" % temp_case["case_id"])
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert any(e["action"] == "office.document_reviewed" for e in entries)
    assert any(e["actor"] for e in entries), "שם המבצע לא הוחזר"


def test_audit_log_requires_staff(api):
    assert api.get("/api/office/audit-log").status_code == 401


def test_client_cannot_read_the_audit_log(api, temp_case):
    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]
    client_login(api, client_id)
    assert api.get("/api/office/audit-log").status_code == 403


# ================================================================
#  6. חוזה הממשק מול השרת
# ================================================================

def test_client_reply_kinds_match_the_browser():
    """
    מזהי התגובה בדפדפן חייבים להיות זהים ל-REPLY_KINDS שבשרת.

    עד 18.09 שלושה מהם היו שונים, והשרת דחה אותם ב-400 - כלומר
    שלושה מארבעת כפתורי התגובה של הלקוח פשוט לא עבדו. הבדיקות
    לא תפסו זאת כי הן שלחו את המחרוזות של השרת ישירות.
    """
    import pathlib
    import re

    from server.api_client import REPLY_KINDS

    root = pathlib.Path(__file__).resolve().parent.parent
    source = (root / "assets" / "js" / "dashboard.js").read_text(encoding="utf-8")
    block = source.split("REPLY_OPTIONS = [")[1].split("]")[0]
    in_browser = set(re.findall(r"kind:\s*'([a-z-]+)'", block))

    assert in_browser == set(REPLY_KINDS), (
        "מזהי התגובה בדפדפן אינם תואמים לשרת: בדפדפן בלבד %s, בשרת בלבד %s"
        % (sorted(in_browser - set(REPLY_KINDS)), sorted(set(REPLY_KINDS) - in_browser))
    )


def test_every_reply_kind_is_accepted_by_the_server(api, temp_case):
    """כל אחד מארבעת המזהים מתקבל בפועל, ולא רק קיים ברשימה."""
    from server.api_client import REPLY_KINDS

    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]

    csrf = client_login(api, client_id)
    for kind in REPLY_KINDS:
        r = api.post("/api/client/documents/%s/replies" % temp_case["document_id"],
                     json={"kind": kind},
                     headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, "השרת דחה את סוג התגובה %s: %s" % (kind, r.text)
