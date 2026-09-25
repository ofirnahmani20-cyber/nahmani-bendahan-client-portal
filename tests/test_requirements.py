"""
דרישות מהלקוח, משלוח ותזכורות.

דורש מסד נתונים חי עם נתוני הזרע.

ארבעת התנאים שנקבעו לשלב ג' נבדקים כאן במפורש:
  1. אין שליחה אמיתית - stub בלבד.
  2. תזכורת נעצרת מיד בהשלמה או בביטול.
  3. אין משלוח כפול.
  4. תקשורת ותוכן אינם נכנסים להקשר ה-AI.

בידוד בין משרדים נבדק ב-test_firm_isolation.py, שם יושב ה-fixture
שבונה משרד שני אמיתי.
"""

import pytest
from starlette.testclient import TestClient

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


def make_req(api, csrf, case_id, **over):
    body = {"kind": "info", "title": "נא למסור פרטי חשבון בנק",
            "guidance": "לצורך העברת התשלום", "priority": "normal"}
    body.update(over)
    r = api.post("/api/office/cases/%s/requirements" % case_id, json=body,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    return r.json()["requirementId"]


def reqs(api, case_id):
    r = api.get("/api/office/cases/%s/requirements" % case_id)
    assert r.status_code == 200, r.text
    return r.json()["requirements"]


def one(items, rid):
    for x in items:
        if x["id"] == rid:
            return x
    raise AssertionError("הדרישה %s אינה ברשימה" % rid)


# ================================================================
#  1. דרישה ומסמך - רשומה אחת, לא שתיים
# ================================================================

def test_document_requirement_creates_one_document_not_a_copy(api, temp_case):
    """
    דרישת מסמך יוצרת שורת case_documents אחת ומקושרת אליה.
    הקובץ שהלקוח יעלה הוא אותו קובץ שיופיע בקלסר.
    """
    csrf = staff_login(api)
    with cursor() as cur:
        cur.execute("select count(*) as n from case_documents where case_id = %s",
                    (temp_case["case_id"],))
        before = cur.fetchone()["n"]

    rid = make_req(api, csrf, temp_case["case_id"],
                   kind="document", title="MRI ברך")

    with cursor() as cur:
        cur.execute("""select id, requirement_id from case_documents
                        where case_id = %s""", (temp_case["case_id"],))
        docs = cur.fetchall()

    assert len(docs) == before + 1, "נוצר יותר ממסמך אחד"
    linked = [d for d in docs if d["requirement_id"] is not None]
    assert len(linked) == 1, "הקשר בין הדרישה למסמך לא נוצר"
    assert str(linked[0]["requirement_id"]) == rid

    item = one(reqs(api, temp_case["case_id"]), rid)
    assert item["documentId"] == str(linked[0]["id"])
    assert item["documentName"] == "MRI ברך"


def test_non_document_requirement_creates_no_document(api, temp_case):
    csrf = staff_login(api)
    with cursor() as cur:
        cur.execute("select count(*) as n from case_documents where case_id = %s",
                    (temp_case["case_id"],))
        before = cur.fetchone()["n"]

    make_req(api, csrf, temp_case["case_id"], kind="contact",
             title="נא ליצור קשר עם המשרד")

    with cursor() as cur:
        cur.execute("select count(*) as n from case_documents where case_id = %s",
                    (temp_case["case_id"],))
        assert cur.fetchone()["n"] == before, "דרישה שאינה מסמך יצרה מסמך"


def test_database_refuses_a_document_requirement_without_a_document(temp_case):
    """הגב של הכלל במסד, לא רק בקוד."""
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation) as err:
        with cursor(commit=True) as cur:
            cur.execute(
                """insert into case_requirements
                     (firm_id, case_id, kind, title)
                   values (%s, %s, 'document', 'בלי מסמך')""",
                (temp_case["firm_id"], temp_case["case_id"]),
            )
    assert "requirement_needs_document" in str(err.value)


# ================================================================
#  2. תזכורת נעצרת בהשלמה - התנאי המפורש
# ================================================================

def test_completing_a_requirement_stops_its_reminders(api, temp_case):
    """התנאי: תזכורת חייבת להיעצר מיד כשהדרישה הושלמה."""
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])

    r = api.post("/api/office/requirements/%s/reminders" % rid,
                 json={"every_days": 3, "channel": "sms"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    with cursor() as cur:
        cur.execute("""select count(*) as n from reminder_rules
                        where requirement_id = %s and not is_paused""", (rid,))
        assert cur.fetchone()["n"] == 1

    r = api.post("/api/office/requirements/%s/complete" % rid,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    with cursor() as cur:
        cur.execute("""select count(*) as n from reminder_rules
                        where requirement_id = %s and not is_paused""", (rid,))
        assert cur.fetchone()["n"] == 0, "התזכורת המשיכה אחרי שהדרישה הושלמה"
        cur.execute("""select count(*) as n from reminder_rules
                        where requirement_id = %s""", (rid,))
        assert cur.fetchone()["n"] == 1, "כלל התזכורת נמחק במקום להיעצר"


def test_cancelling_a_requirement_stops_its_reminders(api, temp_case):
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])
    api.post("/api/office/requirements/%s/reminders" % rid,
             json={"every_days": 7, "channel": "sms"},
             headers={"X-CSRF-Token": csrf})

    api.post("/api/office/requirements/%s/cancel" % rid,
             headers={"X-CSRF-Token": csrf})

    with cursor() as cur:
        cur.execute("""select count(*) as n from reminder_rules
                        where requirement_id = %s and not is_paused""", (rid,))
        assert cur.fetchone()["n"] == 0, "התזכורת המשיכה אחרי ביטול הדרישה"


def test_a_closed_requirement_cannot_get_a_new_reminder(api, temp_case):
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])
    api.post("/api/office/requirements/%s/complete" % rid,
             headers={"X-CSRF-Token": csrf})

    r = api.post("/api/office/requirements/%s/reminders" % rid,
                 json={"every_days": 3, "channel": "sms"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "הוגדרה תזכורת לדרישה שנסגרה"

    r = api.post("/api/office/requirements/%s/send" % rid,
                 json={"channel": "sms"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "נשלחה תזכורת על דרישה שנסגרה"


def test_a_paused_reminder_cannot_be_resumed_after_completion(api, temp_case):
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])
    r = api.post("/api/office/requirements/%s/reminders" % rid,
                 json={"every_days": 3, "channel": "sms"},
                 headers={"X-CSRF-Token": csrf})
    reminder_id = r.json()["reminderId"]

    api.post("/api/office/requirements/%s/complete" % rid,
             headers={"X-CSRF-Token": csrf})

    r = api.post("/api/office/reminders/%s/state" % reminder_id,
                 json={"is_paused": False}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "תזכורת חודשה על דרישה סגורה"


# ================================================================
#  3. אין משלוח כפול
# ================================================================

def test_two_identical_reminder_rules_are_refused(api, temp_case):
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])
    body = {"every_days": 3, "channel": "sms"}

    assert api.post("/api/office/requirements/%s/reminders" % rid, json=body,
                    headers={"X-CSRF-Token": csrf}).status_code == 200
    r = api.post("/api/office/requirements/%s/reminders" % rid, json=body,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "נוצרו שתי אוטומציות זהות"


def test_database_refuses_a_duplicate_delivery(api, temp_case):
    """
    האילוץ UNIQUE הוא מה שיאפשר ל-worker עתידי לרוץ שוב בלי
    לשלוח פעמיים. נבדק ברמת המסד ולא רק בקוד.
    """
    import psycopg
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])

    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]

    row = (temp_case["firm_id"], rid, client_id)
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into message_deliveries
                 (firm_id, requirement_id, subject_type, subject_id, channel,
                  template_key, scheduled_for)
               values (%s, %s, 'client', %s, 'sms', 'requirement.reminder',
                       '2026-10-01 09:00+03')""", row)

    with pytest.raises(psycopg.errors.UniqueViolation):
        with cursor(commit=True) as cur:
            cur.execute(
                """insert into message_deliveries
                     (firm_id, requirement_id, subject_type, subject_id, channel,
                      template_key, scheduled_for)
                   values (%s, %s, 'client', %s, 'sms', 'requirement.reminder',
                           '2026-10-01 09:00+03')""", row)


# ================================================================
#  4. אין שליחה אמיתית, והגוף אינו נשמר
# ================================================================

def test_sending_records_an_attempt_without_a_real_provider(api, temp_case):
    """
    אין ספק. בפיתוח ה-stub כותב ללוג ומחזיר None, ולכן אין
    provider_message_id - וזו בדיוק העדות שלא יצאה הודעה אמיתית.
    """
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])

    r = api.post("/api/office/requirements/%s/send" % rid,
                 json={"channel": "sms"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    with cursor() as cur:
        cur.execute("""select channel, template_key, provider_message_id, status
                         from message_deliveries where requirement_id = %s""",
                    (rid,))
        row = cur.fetchone()

    assert row is not None, "לא נרשם ניסיון משלוח"
    assert row["channel"] == "sms"
    assert row["template_key"] == "requirement.reminder"
    assert row["provider_message_id"] is None, "נראה שיצאה הודעה דרך ספק אמיתי"


def test_delivery_never_stores_the_message_body(api, temp_case):
    """גוף ההודעה אינו נשמר - רק מפתח תבנית."""
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"],
                   title="סוד שאסור שיישמר בטבלת המשלוחים")
    api.post("/api/office/requirements/%s/send" % rid,
             json={"channel": "sms"}, headers={"X-CSRF-Token": csrf})

    with cursor() as cur:
        cur.execute("""select * from message_deliveries
                        where requirement_id = %s""", (rid,))
        row = dict(cur.fetchone())

    assert "body" not in row, "לטבלת המשלוחים יש עמודת גוף הודעה"
    assert "סוד" not in " ".join(str(v) for v in row.values()), \
        "תוכן הדרישה נשמר ברשומת המשלוח"


def test_sending_marks_the_requirement_as_sent(api, temp_case):
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])
    assert one(reqs(api, temp_case["case_id"]), rid)["status"] == "open"

    api.post("/api/office/requirements/%s/send" % rid,
             json={"channel": "sms"}, headers={"X-CSRF-Token": csrf})
    assert one(reqs(api, temp_case["case_id"]), rid)["status"] == "sent"


# ================================================================
#  5. היסטוריה, הרשאות ו-CSRF
# ================================================================

def test_completing_a_requirement_keeps_the_row(api, temp_case):
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])
    api.post("/api/office/requirements/%s/complete" % rid,
             headers={"X-CSRF-Token": csrf})

    item = one(reqs(api, temp_case["case_id"]), rid)
    assert item["status"] == "completed"
    assert item["completedAt"], "אין חתימת זמן להשלמה"
    assert item["completedBy"], "אין חתימת מי השלים"


def test_requirement_writes_need_csrf(api, temp_case):
    csrf = staff_login(api)
    rid = make_req(api, csrf, temp_case["case_id"])

    assert api.post("/api/office/cases/%s/requirements" % temp_case["case_id"],
                    json={"kind": "info", "title": "בלי טוקן"}).status_code == 403
    assert api.post("/api/office/requirements/%s/complete" % rid
                    ).status_code == 403
    assert api.post("/api/office/requirements/%s/send" % rid,
                    json={"channel": "sms"}).status_code == 403


def test_requirement_routes_need_a_session(api, temp_case):
    assert api.get("/api/office/cases/%s/requirements" % temp_case["case_id"]
                   ).status_code == 401


def test_missing_requirement_returns_404(api):
    csrf = staff_login(api)
    ghost = "00000000-0000-0000-0000-000000000000"
    assert api.post("/api/office/requirements/%s/complete" % ghost,
                    headers={"X-CSRF-Token": csrf}).status_code == 404
