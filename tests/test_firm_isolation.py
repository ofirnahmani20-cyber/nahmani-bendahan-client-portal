"""
בידוד בין משרדים.

נתוני הזרע מכילים משרד אחד, ולכן בדיקה על הזרע בלבד לא הייתה
מוכיחה דבר. הבדיקות כאן יוצרות משרד שני עם לקוח, תיק ומסמך
משלו, ומוודאות שאיש מהם אינו נראה לצד השני. בסיום הכול נמחק.
"""

import uuid

import pytest
from starlette.testclient import TestClient

from server import auth
from server.app import app
from server.db.pool import cursor

LOCAL = ("127.0.0.1", 45123)


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
def second_firm():
    """משרד ב' - נוצר לבדיקה ונמחק אחריה."""
    import bcrypt
    marker = uuid.uuid4().hex[:8]
    password_hash = bcrypt.hashpw(b"other-firm-pass", bcrypt.gensalt()).decode()

    with cursor(commit=True) as cur:
        cur.execute("insert into firms (name, slug) values (%s, %s) returning id",
                    ("משרד בדיקה %s" % marker, "test-%s" % marker))
        firm_id = cur.fetchone()["id"]

        cur.execute("""insert into users (firm_id, full_name, email, password_hash, role)
                       values (%s, %s, %s, %s, 'admin') returning id""",
                    (firm_id, "עו\"ד בדיקה", "staff-%s@test.local" % marker, password_hash))
        user_id = cur.fetchone()["id"]

        cur.execute("""insert into clients (firm_id, full_name, national_id_lookup,
                                            national_id_enc)
                       values (%s, %s, %s, %s) returning id""",
                    (firm_id, "לקוח משרד ב'", b"lookup-" + marker.encode(),
                     b"enc-" + marker.encode()))
        client_id = cur.fetchone()["id"]

        cur.execute("""insert into claim_types (firm_id, name, code, position)
                       values (%s, %s, %s, 1) returning id""",
                    (firm_id, "סוג בדיקה", "test-%s" % marker))
        claim_type_id = cur.fetchone()["id"]

        cur.execute("""insert into stage_templates (firm_id, claim_type_id, position,
                                                    title, is_terminal)
                       values (%s, %s, 1, 'שלב בדיקה', false) returning id""",
                    (firm_id, claim_type_id))
        stage_id = cur.fetchone()["id"]

        cur.execute("""insert into cases (firm_id, client_id, claim_type_id,
                                          case_number, opened_at)
                       values (%s, %s, %s, %s, current_date) returning id""",
                    (firm_id, client_id, claim_type_id, "TEST-%s" % marker))
        case_id = cur.fetchone()["id"]

        cur.execute("""insert into case_documents (firm_id, case_id, name, guidance,
                                                   position)
                       values (%s, %s, 'מסמך בדיקה', 'הנחיה', 1) returning id""",
                    (firm_id, case_id))
        document_id = cur.fetchone()["id"]

    yield {
        "firm_id": firm_id, "user_id": user_id, "client_id": client_id,
        "case_id": case_id, "document_id": document_id, "stage_id": stage_id,
        "email": "staff-%s@test.local" % marker, "password": "other-firm-pass",
    }

    with cursor(commit=True) as cur:
        cur.execute("delete from sessions where firm_id = %s", (firm_id,))
        cur.execute("delete from audit_log where firm_id = %s", (firm_id,))
        cur.execute("delete from case_documents where firm_id = %s", (firm_id,))
        cur.execute("delete from case_stage_events where firm_id = %s", (firm_id,))
        cur.execute("delete from cases where firm_id = %s", (firm_id,))
        cur.execute("delete from stage_templates where firm_id = %s", (firm_id,))
        cur.execute("delete from claim_types where firm_id = %s", (firm_id,))
        cur.execute("delete from clients where firm_id = %s", (firm_id,))
        cur.execute("delete from users where firm_id = %s", (firm_id,))
        cur.execute("delete from firms where id = %s", (firm_id,))


def _login_seed_staff(api):
    r = api.post("/api/office/auth/login",
                 json={"email": "nahmani@nahmani-bendahan.co.il",
                       "password": "office2026"})
    assert r.status_code == 200
    return api.cookies.get("portal_csrf")


def test_firm_a_cannot_list_firm_b_cases(api, second_firm):
    _login_seed_staff(api)
    listed = api.get("/api/office/cases").json()["cases"]
    ids = {c["id"] for c in listed}
    assert str(second_firm["case_id"]) not in ids, "תיק של משרד אחר הופיע ברשימה"


def test_firm_a_cannot_read_firm_b_case_by_id(api, second_firm):
    _login_seed_staff(api)
    r = api.get("/api/office/cases/%s" % second_firm["case_id"])
    assert r.status_code == 404, "משרד קיבל תיק של משרד אחר"


def test_firm_a_cannot_review_firm_b_document(api, second_firm):
    csrf = _login_seed_staff(api)
    r = api.post("/api/office/documents/%s/review" % second_firm["document_id"],
                 json={"decision": "approve"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404, "משרד אישר מסמך של משרד אחר"


def test_firm_a_cannot_write_stage_event_on_firm_b_case(api, second_firm):
    csrf = _login_seed_staff(api)
    r = api.post("/api/office/cases/%s/stage-events" % second_firm["case_id"],
                 json={"stage_template_id": str(second_firm["stage_id"])},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404


def test_firm_a_cannot_message_firm_b_case(api, second_firm):
    csrf = _login_seed_staff(api)
    r = api.post("/api/office/cases/%s/messages" % second_firm["case_id"],
                 json={"title": "בדיקה", "body": "גוף"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404


def test_firm_b_staff_sees_only_its_own_case(api, second_firm):
    r = api.post("/api/office/auth/login",
                 json={"email": second_firm["email"], "password": second_firm["password"]})
    assert r.status_code == 200
    listed = api.get("/api/office/cases").json()["cases"]
    assert {c["id"] for c in listed} == {str(second_firm["case_id"])}


def test_client_of_firm_b_cannot_read_firm_a_case(api, second_firm):
    """בידוד לקוח חוצה-משרד, לא רק חוצה-לקוח."""
    class _Req:
        client = type("c", (), {"host": "127.0.0.1"})()
        headers = {}

    class _Resp:
        def __init__(self): self.jar = {}
        def set_cookie(self, name, value, **kw): self.jar[name] = value

    resp = _Resp()
    auth.create_session(resp, firm_id=second_firm["firm_id"], subject_type="client",
                        subject_id=second_firm["client_id"], request=_Req(),
                        hours=auth.CLIENT_SESSION_HOURS)
    for name, value in resp.jar.items():
        api.cookies.set(name, value)

    with cursor() as cur:
        cur.execute("""select c.id from cases c
                        where c.firm_id <> %s limit 1""", (second_firm["firm_id"],))
        other_case = cur.fetchone()["id"]

    assert api.get("/api/client/cases/%s" % other_case).status_code == 404
