"""
היקף התיקים של איש צוות - docs/10, החלטה מ-23.10.

איש צוות שאינו מנהל רואה רק את התיקים שהוא האחראי עליהם. המנהל
רואה הכול. עד 23.10 firm_id היה הגבול היחיד, ולכן כל בדיקה כאן
הייתה נכשלת לפני השינוי: עורך דין אחד ראה, חיפש והוריד קבצים
מתיקים של עורך דין אחר.

לכל מסלול שנוגע בתיק יש כאן בדיקה, כי הסינון נעשה בכל שאילתה
בנפרד - ושאילתה שתתווסף בלעדיו לא תיכשל בשום מקום אחר.

דורש מסד נתונים חי עם נתוני הזרע.
"""

import pytest
from starlette.testclient import TestClient

from server.app import app
from server.db.pool import cursor

LOCAL = ("127.0.0.1", 45123)
ADMIN = ("nahmani@nahmani-bendahan.co.il", "office2026")
STAFF = ("bendahan@nahmani-bendahan.co.il", "office2026")


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


def login(api, who):
    r = api.post("/api/office/auth/login", json={"email": who[0], "password": who[1]})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": api.cookies.get("portal_csrf")}


@pytest.fixture
def foreign():
    """
    תיק פעיל של המנהל - כלומר לא של STAFF - עם מסמך, קובץ, משימה,
    דרישה והודעה נכנסת. כל מה שמסלול משרדי יכול לגעת בו.
    """
    with cursor() as cur:
        cur.execute(
            """select c.id as case_id, c.case_number, c.client_id, cl.full_name,
                      (select d.id from case_documents d where d.case_id = c.id
                        order by d.position limit 1) as document_id,
                      (select f.id from document_files f
                         join case_documents d on d.id = f.document_id
                        where d.case_id = c.id limit 1) as file_id,
                      (select f.document_id from document_files f
                         join case_documents d on d.id = f.document_id
                        where d.case_id = c.id limit 1) as file_document_id,
                      (select t.id from case_tasks t where t.case_id = c.id limit 1)
                        as task_id,
                      -- דרישה מכל תיק של המנהל, לא דווקא מזה:
                      -- לא בכל תיק זרע יש דרישות.
                      (select q.id from case_requirements q
                         join cases c2 on c2.id = q.case_id
                        where c2.assigned_user_id = u.id limit 1) as requirement_id
                 from cases c
                 join clients cl on cl.id = c.client_id
                 join users u on u.id = c.assigned_user_id
                where u.email = %s and c.status = 'active'
                order by c.case_number limit 1""",
            (ADMIN[0],),
        )
        row = cur.fetchone()
    assert row is not None, "נתוני הזרע חייבים לכלול תיק פעיל של המנהל"
    return row


# ================================================================
#  רשימות - התיק של האחר אינו מופיע
# ================================================================

def test_staff_case_list_holds_only_their_cases(api):
    login(api, STAFF)
    cases = api.get("/api/office/cases").json()["cases"]
    assert cases, "ל-STAFF יש תיקים בנתוני הזרע"
    assert all(c["mine"] for c in cases)


def test_admin_still_sees_every_case(api):
    login(api, ADMIN)
    cases = api.get("/api/office/cases").json()["cases"]
    assert any(not c["mine"] for c in cases)
    with cursor() as cur:
        cur.execute("""select count(*) as n from cases c
                        join users u on u.firm_id = c.firm_id and u.email = %s
                       where c.status in ('active', 'frozen')""", (ADMIN[0],))
        assert len(cases) == cur.fetchone()["n"]


def test_unassigned_case_is_admin_only(api):
    with cursor() as cur:
        cur.execute("select id from cases where assigned_user_id is null limit 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("אין תיק בלי אחראי בנתוני הזרע")
    login(api, STAFF)
    assert api.get("/api/office/cases/%s" % row["id"]).status_code == 404
    admin = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    login(admin, ADMIN)
    assert admin.get("/api/office/cases/%s" % row["id"]).status_code == 200


def test_document_binder_counts_and_rows_exclude_foreign_cases(api, foreign):
    login(api, STAFF)
    body = api.get("/api/office/documents").json()
    assert all(d["caseId"] != str(foreign["case_id"]) for d in body["documents"])
    with cursor() as cur:
        cur.execute("""select count(*) as n from case_documents d
                         join cases c on c.id = d.case_id
                         join users u on u.id = c.assigned_user_id
                        where u.email = %s""", (STAFF[0],))
        assert body["total"] == cur.fetchone()["n"]


def test_task_list_and_banner_exclude_foreign_cases(api, foreign):
    login(api, STAFF)
    body = api.get("/api/office/tasks?include_done=true").json()
    seen = {t["caseNumber"] for t in body["tasks"] + body["banner"]}
    assert foreign["case_number"] not in seen


def test_clients_list_holds_only_clients_with_a_visible_case(api, foreign):
    login(api, STAFF)
    names = {c["name"] for c in api.get("/api/office/clients").json()["clients"]}
    with cursor() as cur:
        cur.execute("""select 1 from cases c join users u on u.id = c.assigned_user_id
                        where c.client_id = %s and u.email = %s""",
                    (foreign["client_id"], STAFF[0]))
        shared = cur.fetchone() is not None
    if not shared:
        assert foreign["full_name"] not in names


def test_closed_cases_exclude_foreign_cases(api):
    login(api, STAFF)
    with cursor() as cur:
        cur.execute("""select c.case_number from cases c
                         join users u on u.id = c.assigned_user_id
                        where u.email = %s
                          and c.status in ('closed_accepted', 'closed_rejected')""",
                    (ADMIN[0],))
        foreign_closed = {r["case_number"] for r in cur.fetchall()}
    got = {c["caseNumber"] for c in api.get("/api/office/closed-cases").json()["cases"]}
    assert not (got & foreign_closed)


def test_firm_wide_audit_log_holds_only_own_case_rows(api, foreign):
    # שורה בתיק של המנהל, כדי שיהיה מה להסתיר
    admin = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    login(admin, ADMIN)
    admin.get("/api/office/cases/%s" % foreign["case_id"])

    login(api, STAFF)
    entries = api.get("/api/office/audit-log?limit=200").json()["entries"]
    assert all(e["caseId"] for e in entries), "שורות בלי תיק שייכות למנהל"
    assert all(e["caseId"] != str(foreign["case_id"]) for e in entries)


def test_search_finds_nothing_in_foreign_cases(api, foreign):
    login(api, STAFF)
    for q in (foreign["case_number"], foreign["full_name"]):
        body = api.get("/api/office/search", params={"q": q}).json()
        assert all(r.get("caseId") != str(foreign["case_id"]) for r in body["results"]), q


# ================================================================
#  ישות בודדת - 404, כמו מזהה של משרד אחר
# ================================================================

def test_foreign_case_reads_are_404(api, foreign):
    login(api, STAFF)
    cid = foreign["case_id"]
    for url in ("/api/office/cases/%s",
                "/api/office/cases/%s/tasks",
                "/api/office/cases/%s/requirements",
                "/api/office/cases/%s/conversation",
                "/api/office/cases/%s/document-templates",
                "/api/office/audit-log?case_id=%s"):
        assert api.get(url % cid).status_code == 404, url


def test_foreign_case_writes_are_404_and_change_nothing(api, foreign):
    h = login(api, STAFF)
    cid = foreign["case_id"]
    with cursor() as cur:
        cur.execute("select count(*) as n from case_conversation where case_id = %s", (cid,))
        before = cur.fetchone()["n"]
        cur.execute("select s.id from stage_templates s join cases c on c.claim_type_id = s.claim_type_id"
                    " where c.id = %s limit 1", (cid,))
        stage = cur.fetchone()["id"]
        cur.execute("select id from task_types limit 1")
        task_type = cur.fetchone()["id"]

    assert api.post("/api/office/cases/%s/conversation" % cid,
                    json={"body": "x"}, headers=h).status_code == 404
    assert api.post("/api/office/cases/%s/stage-events" % cid,
                    json={"stage_template_id": str(stage)}, headers=h).status_code == 404
    assert api.post("/api/office/cases/%s/tasks" % cid, headers=h, json={
        "task_type_id": str(task_type), "title": "xx", "due_at": "2030-01-01T09:00:00",
    }).status_code == 404
    assert api.post("/api/office/cases/%s/messages" % cid,
                    json={"body": "x"}, headers=h).status_code in (404, 422)

    with cursor() as cur:
        cur.execute("select count(*) as n from case_conversation where case_id = %s", (cid,))
        assert cur.fetchone()["n"] == before


def test_foreign_document_file_task_requirement_message_are_404(api, foreign):
    h = login(api, STAFF)
    if foreign["document_id"]:
        assert api.post("/api/office/documents/%s/review" % foreign["document_id"],
                        json={"decision": "approve"}, headers=h).status_code == 404
    if foreign["file_id"]:
        url = "/api/office/documents/%s/files/%s" % (foreign["file_document_id"],
                                                    foreign["file_id"])
        assert api.get(url).status_code == 404
        assert api.get(url + "/processing").status_code == 404
    if foreign["task_id"]:
        assert api.post("/api/office/tasks/%s/complete" % foreign["task_id"],
                        headers=h).status_code == 404
    assert foreign["requirement_id"], "נתוני הזרע חייבים לכלול דרישה בתיק של המנהל"
    assert api.get("/api/office/requirements/%s/deliveries"
                   % foreign["requirement_id"]).status_code == 404
    # בנתוני הזרע אין הודעה נכנסת בתיק של המנהל - יוצרים אחת לבדיקה.
    with cursor(commit=True) as cur:
        cur.execute("""insert into case_conversation (firm_id, case_id, direction, body)
                       select firm_id, id, 'inbound', 'בדיקת היקף' from cases where id = %s
                       returning id""", (foreign["case_id"],))
        message_id = cur.fetchone()["id"]
    try:
        assert api.post("/api/office/conversation/%s/read" % message_id,
                        headers=h).status_code == 404
        with cursor() as cur:
            cur.execute("select read_at from case_conversation where id = %s", (message_id,))
            assert cur.fetchone()["read_at"] is None
    finally:
        with cursor(commit=True) as cur:
            cur.execute("delete from case_conversation where id = %s", (message_id,))


# ================================================================
#  משימה אינה דלת אחורית לתיק
# ================================================================

def test_task_cannot_be_assigned_to_staff_who_cannot_see_the_case(api, foreign):
    h = login(api, ADMIN)
    with cursor() as cur:
        cur.execute("select id from users where email = %s", (STAFF[0],))
        staff_id = cur.fetchone()["id"]
        cur.execute("select id from task_types limit 1")
        task_type = cur.fetchone()["id"]
    r = api.post("/api/office/cases/%s/tasks" % foreign["case_id"], headers=h, json={
        "task_type_id": str(task_type), "title": "בדיקת היקף",
        "due_at": "2030-01-01T09:00:00", "assignee_user_id": str(staff_id),
    })
    assert r.status_code == 400, r.text


def test_reassigning_the_case_moves_visibility_immediately(api, foreign):
    """ההרשאה נגזרת מהמסד בכל בקשה - בלי התחברות מחדש."""
    login(api, STAFF)
    url = "/api/office/cases/%s" % foreign["case_id"]
    assert api.get(url).status_code == 404
    with cursor(commit=True) as cur:
        cur.execute("""update cases set assigned_user_id =
                         (select id from users where email = %s) where id = %s
                       returning (select id from users where email = %s) as prev""",
                    (STAFF[0], foreign["case_id"], ADMIN[0]))
    try:
        assert api.get(url).status_code == 200
    finally:
        with cursor(commit=True) as cur:
            cur.execute("""update cases set assigned_user_id =
                             (select id from users where email = %s) where id = %s""",
                        (ADMIN[0], foreign["case_id"]))
    assert api.get(url).status_code == 404
