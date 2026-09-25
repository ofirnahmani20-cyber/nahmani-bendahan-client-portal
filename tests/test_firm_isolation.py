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

        cur.execute("""insert into task_types (firm_id, name, code, position)
                       values (%s, 'סוג משימה בדיקה', %s, 1) returning id""",
                    (firm_id, "tt-%s" % marker))
        task_type_id = cur.fetchone()["id"]

        cur.execute("""insert into case_tasks (firm_id, case_id, task_type_id,
                                               title, due_at, priority)
                       values (%s, %s, %s, %s,
                               now() + interval '2 days', 'critical')
                       returning id""",
                    (firm_id, case_id, task_type_id, "משימה של משרד אחר"))
        task_id = cur.fetchone()["id"]

    yield {
        "firm_id": firm_id, "user_id": user_id, "client_id": client_id,
        "case_id": case_id, "document_id": document_id, "stage_id": stage_id,
        "task_id": task_id, "task_type_id": task_type_id,
        "email": "staff-%s@test.local" % marker, "password": "other-firm-pass",
    }

    with cursor(commit=True) as cur:
        cur.execute("delete from sessions where firm_id = %s", (firm_id,))
        cur.execute("delete from audit_log where firm_id = %s", (firm_id,))
        cur.execute("delete from case_conversation where firm_id = %s", (firm_id,))
        cur.execute("delete from message_deliveries where firm_id = %s", (firm_id,))
        cur.execute("delete from reminder_rules where firm_id = %s", (firm_id,))
        cur.execute("update case_documents set requirement_id = null where firm_id = %s", (firm_id,))
        cur.execute("delete from case_requirements where firm_id = %s", (firm_id,))
        cur.execute("delete from case_tasks where firm_id = %s", (firm_id,))
        cur.execute("delete from case_documents where firm_id = %s", (firm_id,))
        cur.execute("delete from case_stage_events where firm_id = %s", (firm_id,))
        cur.execute("delete from cases where firm_id = %s", (firm_id,))
        cur.execute("delete from task_types where firm_id = %s", (firm_id,))
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


def test_firm_a_cannot_list_firm_b_tasks(api, second_firm):
    _login_seed_staff(api)
    listed = api.get("/api/office/tasks?include_done=true").json()["tasks"]
    ids = {t["id"] for t in listed}
    assert str(second_firm["task_id"]) not in ids, "משימה של משרד אחר הופיעה ברשימה"


def test_firm_a_cannot_read_firm_b_tasks_by_case(api, second_firm):
    _login_seed_staff(api)
    r = api.get("/api/office/cases/%s/tasks" % second_firm["case_id"])
    assert r.status_code == 404, "משרד קיבל משימות של תיק של משרד אחר"


def test_firm_a_cannot_complete_firm_b_task(api, second_firm):
    csrf = _login_seed_staff(api)
    r = api.post("/api/office/tasks/%s/complete" % second_firm["task_id"],
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404, "משרד השלים משימה של משרד אחר"

    r = api.post("/api/office/tasks/%s/assignee" % second_firm["task_id"],
                 json={"assignee_user_id": None},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404, "משרד שינה אחראי במשימה של משרד אחר"


def test_firm_a_cannot_assign_firm_b_user_to_its_own_task(api, second_firm,
                                                          temp_case):
    """
    הכיוון ההפוך: לא לשאוב משימה, אלא לדחוף לתוכה איש צוות זר.
    400 ולא 404 - התיק שלי, אבל הערך בבקשה אינו שמיש.
    """
    csrf = _login_seed_staff(api)
    with cursor() as cur:
        cur.execute("""select id from task_types where firm_id = %s limit 1""",
                    (temp_case["firm_id"],))
        task_type_id = cur.fetchone()["id"]

    r = api.post("/api/office/cases/%s/tasks" % temp_case["case_id"],
                 json={"task_type_id": str(task_type_id),
                       "title": "משימה עם אחראי זר",
                       "due_at": "2026-12-01T09:00",
                       "assignee_user_id": str(second_firm["user_id"])},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "איש צוות של משרד אחר הוגדר כאחראי"


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


# ================================================================
#  היכולות שהוחזרו - אותו בידוד, גם עליהן
# ================================================================

def test_firm_a_cannot_record_a_decision_on_firm_b_case(api, second_firm):
    csrf = _login_seed_staff(api)
    r = api.post("/api/office/cases/%s/decisions" % second_firm["case_id"],
                 json={"decided_at": "2026-09-01", "outcome": "grant",
                       "percent": 30, "is_permanent": False},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404, "משרד רשם החלטה בתיק של משרד אחר"


def test_firm_a_cannot_cancel_firm_b_document(api, second_firm):
    csrf = _login_seed_staff(api)
    r = api.post("/api/office/documents/%s/cancel" % second_firm["document_id"],
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404


def test_firm_a_cannot_read_firm_b_catalog(api, second_firm):
    _login_seed_staff(api)
    r = api.get("/api/office/cases/%s/document-templates" % second_firm["case_id"])
    assert r.status_code == 404


def test_audit_log_does_not_leak_across_firms(api, second_firm):
    """
    היומן הוא הנקודה שהכי קל לשכוח בה סינון: הוא נשאל בלי
    מזהה תיק, ולכן שאילתה בלי firm_id הייתה מחזירה את הכול.
    """
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into audit_log (firm_id, actor_type, actor_id, action,
                                      entity_type, case_id)
               values (%s, 'user', %s, 'office.case_viewed', 'case', %s)""",
            (second_firm["firm_id"], second_firm["user_id"], second_firm["case_id"]),
        )

    _login_seed_staff(api)
    entries = api.get("/api/office/audit-log?limit=200").json()["entries"]

    with cursor() as cur:
        cur.execute("""select count(*) as n from audit_log
                        where firm_id = %s""", (second_firm["firm_id"],))
        assert cur.fetchone()["n"] >= 1, "שורת הבדיקה לא נוצרה"

    # השורה של משרד ב' קיימת במסד אך אינה בתשובה למשרד א'.
    assert all(e["entity"] != "case" or e["actor"] != "עו\"ד בדיקה"
               for e in entries), "יומן של משרד אחד הכיל שורה של משרד אחר"


def test_client_of_firm_b_cannot_reply_to_firm_a_document(api, second_firm):
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
        cur.execute("""select id from case_documents
                        where firm_id <> %s limit 1""", (second_firm["firm_id"],))
        other_doc = cur.fetchone()["id"]

    r = api.post("/api/client/documents/%s/replies" % other_doc,
                 json={"kind": "dont-have"},
                 headers={"X-CSRF-Token": resp.jar["portal_csrf"]})
    assert r.status_code == 404


def test_firm_a_cannot_read_firm_b_requirements(api, second_firm):
    _login_seed_staff(api)
    r = api.get("/api/office/cases/%s/requirements" % second_firm["case_id"])
    assert r.status_code == 404, "משרד קיבל דרישות של תיק של משרד אחר"


def test_firm_a_cannot_read_firm_b_conversation(api, second_firm):
    _login_seed_staff(api)
    r = api.get("/api/office/cases/%s/conversation" % second_firm["case_id"])
    assert r.status_code == 404, "משרד קרא שיחה של תיק של משרד אחר"


def test_firm_a_cannot_write_into_firm_b_conversation(api, second_firm):
    csrf = _login_seed_staff(api)
    r = api.post("/api/office/cases/%s/conversation" % second_firm["case_id"],
                 json={"body": "חדירה"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404, "משרד כתב לשיחה של משרד אחר"


def test_firm_a_cannot_touch_firm_b_requirement(api, second_firm):
    """דרישה של משרד אחר - 404 בכל פעולה, ולא 403."""
    csrf = _login_seed_staff(api)
    with cursor(commit=True) as cur:
        cur.execute("""insert into case_requirements
                         (firm_id, case_id, kind, title)
                       values (%s, %s, 'info', 'דרישה של משרד ב') returning id""",
                    (second_firm["firm_id"], second_firm["case_id"]))
        rid = cur.fetchone()["id"]

    for path, payload in [("complete", None), ("cancel", None),
                          ("send", {"channel": "sms"}),
                          ("reminders", {"every_days": 3, "channel": "sms"})]:
        r = api.post("/api/office/requirements/%s/%s" % (rid, path),
                     json=payload, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 404, "משרד ביצע %s על דרישה של משרד אחר" % path

    assert api.get("/api/office/requirements/%s/deliveries" % rid
                   ).status_code == 404
