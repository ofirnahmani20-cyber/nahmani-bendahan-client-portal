"""
פתיחת תיק ועריכת לקוח ותיק - server/api_intake.py (docs/10, v1 סעיף 2).

דורש מסד נתונים חי עם נתוני הזרע. כל תיק ולקוח שנוצרים כאן נמחקים
בסוף הבדיקה.
"""

import random

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from server import api_intake
from server.app import app
from server.db.pool import cursor

LOCAL = ("127.0.0.1", 45123)
ADMIN = ("nahmani@nahmani-bendahan.co.il", "office2026")
STAFF = ("bendahan@nahmani-bendahan.co.il", "office2026")


def valid_id():
    """ת"ז אקראית עם ספרת ביקורת תקינה, שאינה בנתוני הזרע."""
    body = "9" + "".join(random.choice("0123456789") for _ in range(7))
    for check in "0123456789":
        try:
            return api_intake.normalize_national_id(body + check)
        except HTTPException:
            continue


@pytest.fixture
def made():
    """אוסף את מה שנוצר, ומוחק בסוף - תיקים לפני לקוחות."""
    created = {"cases": [], "clients": []}
    yield created
    with cursor(commit=True) as cur:
        for case_id in created["cases"]:
            cur.execute("delete from case_stage_events where case_id = %s", (case_id,))
            cur.execute("delete from case_documents where case_id = %s", (case_id,))
            cur.execute("delete from audit_log where case_id = %s", (case_id,))
            cur.execute("delete from cases where id = %s", (case_id,))
        for client_id in created["clients"]:
            cur.execute("delete from sessions where subject_type = 'client' and subject_id = %s",
                        (client_id,))
            cur.execute("delete from clients where id = %s", (client_id,))


def _client(who):
    c = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    r = c.post("/api/office/auth/login", json={"email": who[0], "password": who[1]})
    assert r.status_code == 200 and "mfa" not in r.json(), r.text
    c.headers["X-CSRF-Token"] = c.cookies.get("portal_csrf")
    return c


@pytest.fixture
def admin():
    return _client(ADMIN)


@pytest.fixture
def staff():
    return _client(STAFF)


@pytest.fixture
def claim_type():
    with cursor() as cur:
        cur.execute("""select ct.id, count(t.id) as docs from claim_types ct
                         join required_document_templates t on t.claim_type_id = ct.id
                        where ct.is_active group by ct.id order by count(t.id) desc limit 1""")
        return cur.fetchone()


def _open(api, made, claim_type, **extra):
    body = {"national_id": valid_id(), "full_name": "לקוח בדיקה",
            "phone": "050-1234567", "claim_type_id": str(claim_type["id"])}
    body.update(extra)
    r = api.post("/api/office/cases", json=body)
    if r.status_code == 200:
        made["cases"].append(r.json()["caseId"])
        if r.json()["clientNew"]:
            made["clients"].append(r.json()["clientId"])
    return r, body


# ================================================================
#  אימות קלט
# ================================================================

@pytest.mark.parametrize("raw", ["123456782", "12345678-2", " 123456782 ", "000000018"])
def test_valid_israeli_ids_are_accepted(raw):
    assert len(api_intake.normalize_national_id(raw)) == 9


@pytest.mark.parametrize("raw", ["123456789", "abcdefghi", "1234", "1234567890", ""])
def test_invalid_israeli_ids_are_refused(raw):
    with pytest.raises(HTTPException):
        api_intake.normalize_national_id(raw)


@pytest.mark.parametrize("raw, out", [("0501234567", "050-1234567"),
                                      ("+972 50 123 4567", "050-1234567"),
                                      ("052-987-6543", "052-9876543")])
def test_mobile_numbers_are_normalized(raw, out):
    assert api_intake.normalize_phone(raw) == out


@pytest.mark.parametrize("raw", ["03-5551234", "12345", "", "05012345"])
def test_non_mobile_numbers_are_refused(raw):
    with pytest.raises(HTTPException):
        api_intake.normalize_phone(raw)


# ================================================================
#  פתיחת תיק
# ================================================================

def test_open_case_creates_client_case_first_stage_and_documents(admin, made, claim_type):
    r, body = _open(admin, made, claim_type)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["clientNew"] is True
    assert out["caseNumber"].startswith("BL-")
    assert out["documents"] == claim_type["docs"]

    case = admin.get("/api/office/cases/%s" % out["caseId"]).json()
    assert case["clientName"] == "לקוח בדיקה"
    assert case["currentStage"] == 1
    assert len(case["documents"]) == claim_type["docs"]
    assert all(d["status"] == "missing" for d in case["documents"])

    # הת"ז נשמרת מוצפנת ובחיפוש HMAC בלבד - לא כטקסט
    with cursor() as cur:
        cur.execute("select national_id_enc, national_id_lookup from clients where id = %s",
                    (out["clientId"],))
        row = cur.fetchone()
    assert body["national_id"].encode() not in bytes(row["national_id_enc"])
    assert body["national_id"].encode() not in bytes(row["national_id_lookup"])


def test_new_client_can_request_a_login_code(admin, made, claim_type):
    """הלקוח שנפתח הוא לקוח שמסוגל להתחבר - זו כל המטרה."""
    r, body = _open(admin, made, claim_type)
    portal = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    otp = portal.post("/api/client/auth/request-otp", json={"national_id": body["national_id"]})
    assert otp.status_code == 200
    with cursor() as cur:
        cur.execute("select count(*) as n from otp_challenges where client_id = %s",
                    (r.json()["clientId"],))
        assert cur.fetchone()["n"] == 1
        cur.execute("delete from otp_challenges where client_id = %s", (r.json()["clientId"],))


def test_existing_id_reuses_the_client_and_keeps_its_details(admin, made, claim_type):
    first, body = _open(admin, made, claim_type)
    second, _ = _open(admin, made, claim_type, national_id=body["national_id"],
                      full_name="שם אחר לגמרי", phone="052-0000000")
    assert second.status_code == 200
    assert second.json()["clientNew"] is False
    assert second.json()["clientId"] == first.json()["clientId"]
    with cursor() as cur:
        cur.execute("select full_name, phone from clients where id = %s",
                    (first.json()["clientId"],))
        row = cur.fetchone()
    assert row["full_name"] == "לקוח בדיקה" and row["phone"] == "050-1234567"


def test_case_numbers_are_sequential_and_duplicates_refused(admin, made, claim_type):
    a, _ = _open(admin, made, claim_type, opened_at="2031-03-01")
    b, _ = _open(admin, made, claim_type, opened_at="2031-04-01")
    assert a.json()["caseNumber"] == "BL-2031-0001"
    assert b.json()["caseNumber"] == "BL-2031-0002"
    dup, _ = _open(admin, made, claim_type, case_number="BL-2031-0001")
    assert dup.status_code == 409


def test_new_client_requires_name_and_mobile(admin, made, claim_type):
    r, _ = _open(admin, made, claim_type, full_name="")
    assert r.status_code == 400
    r, _ = _open(admin, made, claim_type, phone="03-5551234")
    assert r.status_code == 400


def test_invalid_id_and_unknown_claim_type_are_refused(admin, made, claim_type):
    r, _ = _open(admin, made, claim_type, national_id="123456789")
    assert r.status_code == 400
    r, _ = _open(admin, made, claim_type, claim_type_id="00000000-0000-0000-0000-000000000000")
    assert r.status_code == 400


def test_open_case_requires_csrf(made, claim_type):
    api = _client(ADMIN)
    del api.headers["X-CSRF-Token"]
    r, _ = _open(api, made, claim_type)
    assert r.status_code == 403


# ================================================================
#  היקף - מי פותח לאיזה אחראי
# ================================================================

def test_staff_opens_cases_for_themselves_only(staff, made, claim_type):
    with cursor() as cur:
        cur.execute("select id from users where email = %s", (ADMIN[0],))
        admin_id = cur.fetchone()["id"]
    r, _ = _open(staff, made, claim_type, assigned_user_id=str(admin_id))
    assert r.status_code == 403

    r, _ = _open(staff, made, claim_type)
    assert r.status_code == 200
    # התיק שלו, ולכן הוא רואה אותו
    assert staff.get("/api/office/cases/%s" % r.json()["caseId"]).status_code == 200


def test_admin_may_open_unassigned_or_for_others(admin, made, claim_type):
    with cursor() as cur:
        cur.execute("select id from users where email = %s", (STAFF[0],))
        staff_id = cur.fetchone()["id"]
    r, _ = _open(admin, made, claim_type, assigned_user_id=str(staff_id))
    assert r.status_code == 200
    with cursor() as cur:
        cur.execute("select assigned_user_id from cases where id = %s", (r.json()["caseId"],))
        assert str(cur.fetchone()["assigned_user_id"]) == str(staff_id)


def test_lookup_hides_name_of_client_outside_staff_scope(admin, staff, made, claim_type):
    _, body = _open(admin, made, claim_type)   # תיק של המנהל - בלי אחראי
    seen = staff.post("/api/office/intake/lookup", json={"national_id": body["national_id"]})
    assert seen.status_code == 200
    assert seen.json() == {"exists": True, "name": None, "status": "active", "cases": None}

    mine = admin.post("/api/office/intake/lookup", json={"national_id": body["national_id"]})
    assert mine.json()["name"] == "לקוח בדיקה"

    none = admin.post("/api/office/intake/lookup", json={"national_id": valid_id()})
    assert none.json() == {"exists": False}


# ================================================================
#  עדכון לקוח ותיק
# ================================================================

def test_update_client_contact_details(admin, made, claim_type):
    r, _ = _open(admin, made, claim_type)
    cid = r.json()["clientId"]
    u = admin.post("/api/office/clients/%s/update" % cid,
                   json={"phone": "+972521112222", "email": "x@example.com"})
    assert u.status_code == 200, u.text
    with cursor() as cur:
        cur.execute("select phone, email from clients where id = %s", (cid,))
        row = cur.fetchone()
        cur.execute("""select metadata from audit_log where action = 'office.client_updated'
                        and entity_id = %s order by id desc limit 1""", (cid,))
        meta = cur.fetchone()["metadata"]
    assert row["phone"] == "052-1112222" and row["email"] == "x@example.com"
    # היומן אומר אילו שדות, לא מה נכתב בהם
    assert sorted(meta["fields"]) == ["email", "phone"]
    assert "052" not in str(meta) and "example" not in str(meta)


def test_disabling_client_blocks_login_and_closes_sessions(admin, made, claim_type):
    from .test_authz import client_login
    r, body = _open(admin, made, claim_type)
    cid = r.json()["clientId"]
    portal = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    client_login(portal, cid)
    assert portal.get("/api/me").status_code == 200

    assert admin.post("/api/office/clients/%s/update" % cid,
                      json={"status": "disabled"}).status_code == 200
    assert portal.get("/api/me").status_code == 401
    otp = portal.post("/api/client/auth/request-otp", json={"national_id": body["national_id"]})
    with cursor() as cur:
        cur.execute("select count(*) as n from otp_challenges where client_id = %s", (cid,))
        assert cur.fetchone()["n"] == 0
    assert otp.status_code == 200   # אותה תשובה כמו ת"ז לא קיימת


def test_staff_cannot_edit_client_outside_their_cases(admin, staff, made, claim_type):
    r, _ = _open(admin, made, claim_type)
    u = staff.post("/api/office/clients/%s/update" % r.json()["clientId"],
                   json={"full_name": "ניסיון"})
    assert u.status_code == 404


def test_close_and_reopen_case_sets_and_clears_closed_at(admin, made, claim_type):
    r, _ = _open(admin, made, claim_type)
    cid = r.json()["caseId"]
    assert admin.post("/api/office/cases/%s/update" % cid,
                      json={"status": "closed_accepted"}).status_code == 200
    with cursor() as cur:
        cur.execute("select status, closed_at from cases where id = %s", (cid,))
        row = cur.fetchone()
    assert row["status"] == "closed_accepted" and row["closed_at"] is not None

    admin.post("/api/office/cases/%s/update" % cid, json={"status": "active"})
    with cursor() as cur:
        cur.execute("select closed_at from cases where id = %s", (cid,))
        assert cur.fetchone()["closed_at"] is None


def test_hearing_date_set_and_cleared(admin, made, claim_type):
    r, _ = _open(admin, made, claim_type)
    cid = r.json()["caseId"]
    admin.post("/api/office/cases/%s/update" % cid,
               json={"next_hearing_at": "2031-05-01T10:30:00+03:00"})
    assert admin.get("/api/office/cases/%s" % cid).json()["nextHearing"].startswith("2031-05-01")
    admin.post("/api/office/cases/%s/update" % cid, json={"clear_next_hearing": True})
    assert admin.get("/api/office/cases/%s" % cid).json()["nextHearing"] is None


def test_only_admin_reassigns_and_reassignment_moves_visibility(admin, staff, made, claim_type):
    r, _ = _open(staff, made, claim_type)
    cid = r.json()["caseId"]
    with cursor() as cur:
        cur.execute("select id from users where email = %s", (ADMIN[0],))
        admin_id = cur.fetchone()["id"]

    assert staff.post("/api/office/cases/%s/update" % cid,
                      json={"assigned_user_id": str(admin_id)}).status_code == 403
    assert admin.post("/api/office/cases/%s/update" % cid,
                      json={"assigned_user_id": str(admin_id)}).status_code == 200
    assert staff.get("/api/office/cases/%s" % cid).status_code == 404


def test_unknown_status_refused(admin, made, claim_type):
    r, _ = _open(admin, made, claim_type)
    u = admin.post("/api/office/cases/%s/update" % r.json()["caseId"], json={"status": "deleted"})
    assert u.status_code == 400
