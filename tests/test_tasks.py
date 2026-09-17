"""
משימות, מועדי גג ודחיפויות.

דורש מסד נתונים חי עם נתוני הזרע.

בידוד בין משרדים נבדק ב-test_firm_isolation.py, שם יושב ה-fixture
שבונה משרד שני אמיתי: משימה של משרד אחר אינה ברשימה, אינה נגישה
דרך התיק, ואינה ניתנת להשלמה או לשינוי אחראי.
"""

import datetime

import psycopg
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


def client_login(api, client_id):
    """מדלג על ה-SMS: יוצר session ישירות, כמו אחרי OTP תקין."""
    from server import auth
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


@pytest.fixture
def task_type(temp_case):
    """סוג משימה של משרד הזרע, מהקטלוג שבמסד."""
    with cursor() as cur:
        cur.execute("""select id from task_types
                        where firm_id = %s and is_active
                        order by position limit 1""",
                    (temp_case["firm_id"],))
        row = cur.fetchone()
    if row is None:
        pytest.skip("קטלוג סוגי המשימות לא נזרע - python -m server.db.seed_task_types")
    return str(row["id"])


def due_in(days, clock="09:00"):
    """מועד יעד יחסי, בפורמט שהדפדפן שולח (datetime-local)."""
    day = datetime.date.today() + datetime.timedelta(days=days)
    return "%sT%s" % (day.isoformat(), clock)


def make_task(api, csrf, case_id, task_type, **over):
    body = {"task_type_id": task_type, "title": "משימת בדיקה",
            "due_at": due_in(5), "priority": "normal"}
    body.update(over)
    r = api.post("/api/office/cases/%s/tasks" % case_id, json=body,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    return r.json()["taskId"]


def case_tasks(api, case_id):
    r = api.get("/api/office/cases/%s/tasks" % case_id)
    assert r.status_code == 200, r.text
    return r.json()["tasks"]


def one(tasks, task_id):
    for t in tasks:
        if t["id"] == task_id:
            return t
    raise AssertionError("המשימה %s אינה ברשימה" % task_id)


# ================================================================
#  1. מיון לפי דחיפות
# ================================================================

def test_overdue_task_sorts_above_a_normal_one(api, temp_case, task_type):
    """הדרישה: משימה באיחור עולה לפני משימה רגילה."""
    csrf = staff_login(api)
    later = make_task(api, csrf, temp_case["case_id"], task_type,
                      title="מועד רחוק", due_at=due_in(12))
    overdue = make_task(api, csrf, temp_case["case_id"], task_type,
                        title="מועד שחלף", due_at=due_in(-2))

    order = [t["id"] for t in case_tasks(api, temp_case["case_id"])]
    assert order.index(overdue) < order.index(later), \
        "משימה באיחור לא עלתה לפני משימה רגילה"


def test_priority_beats_a_nearer_due_date(api, temp_case, task_type):
    """
    המיון שאושר הוא היברידי: העדיפות שהצוות קבע גוברת, ורק בתוך
    אותה עדיפות המועד הקרוב עולה ראשון.
    """
    csrf = staff_login(api)
    soon_normal = make_task(api, csrf, temp_case["case_id"], task_type,
                            title="רגיל ומיד", due_at=due_in(2),
                            priority="normal")
    far_critical = make_task(api, csrf, temp_case["case_id"], task_type,
                             title="קריטי ורחוק", due_at=due_in(11),
                             priority="critical")

    order = [t["id"] for t in case_tasks(api, temp_case["case_id"])]
    assert order.index(far_critical) < order.index(soon_normal), \
        "עדיפות קריטית לא גברה על מועד קרוב יותר"


def test_due_date_orders_within_the_same_priority(api, temp_case, task_type):
    csrf = staff_login(api)
    late = make_task(api, csrf, temp_case["case_id"], task_type,
                     title="מאוחר", due_at=due_in(10), priority="high")
    early = make_task(api, csrf, temp_case["case_id"], task_type,
                      title="מוקדם", due_at=due_in(6), priority="high")

    order = [t["id"] for t in case_tasks(api, temp_case["case_id"])]
    assert order.index(early) < order.index(late), \
        "בתוך אותה עדיפות המועד הקרוב לא עלה ראשון"


def test_firm_wide_list_keeps_the_same_order(api, temp_case, task_type):
    """
    מסך "דורש טיפול" ורשימת התיק חייבים למיין זהה - שניהם
    נשענים על אותה תצוגה ועל אותו order by.
    """
    csrf = staff_login(api)
    later = make_task(api, csrf, temp_case["case_id"], task_type,
                      due_at=due_in(13))
    overdue = make_task(api, csrf, temp_case["case_id"], task_type,
                        due_at=due_in(-1))

    order = [t["id"] for t in api.get("/api/office/tasks").json()["tasks"]]
    assert order.index(overdue) < order.index(later)


# ================================================================
#  2. זיהוי משימה קריטית
# ================================================================

def test_buckets_match_the_required_thresholds(api, temp_case, task_type):
    """
    המדרגות מדרישה 6: עבר · 3-1 ימים · 7-4 · 14-8 · מעבר לכך.
    נבדקים הגבולות עצמם, לא רק אמצע כל טווח.
    """
    csrf = staff_login(api)
    expected = {
        make_task(api, csrf, temp_case["case_id"], task_type,
                  due_at=due_in(-1)): "overdue",
        make_task(api, csrf, temp_case["case_id"], task_type,
                  due_at=due_in(1)): "critical",
        make_task(api, csrf, temp_case["case_id"], task_type,
                  due_at=due_in(3)): "critical",
        make_task(api, csrf, temp_case["case_id"], task_type,
                  due_at=due_in(4)): "warning",
        make_task(api, csrf, temp_case["case_id"], task_type,
                  due_at=due_in(7)): "warning",
        make_task(api, csrf, temp_case["case_id"], task_type,
                  due_at=due_in(8)): "normal",
        make_task(api, csrf, temp_case["case_id"], task_type,
                  due_at=due_in(14)): "normal",
        make_task(api, csrf, temp_case["case_id"], task_type,
                  due_at=due_in(20)): "later",
    }
    tasks = case_tasks(api, temp_case["case_id"])
    for task_id, bucket in expected.items():
        assert one(tasks, task_id)["bucket"] == bucket, \
            "מדרגת המועד שגויה למשימה %s" % task_id


def test_a_deadline_earlier_today_is_already_overdue(api, temp_case, task_type):
    """
    המדרגה נקבעת לפי השעה ולא לפי היום. ועדה שהייתה הבוקר ב-09:00
    היא מועד שחלף, גם אם התאריך הוא היום.
    """
    csrf = staff_login(api)
    past = make_task(api, csrf, temp_case["case_id"], task_type,
                     due_at=due_in(0, "00:01"))
    ahead = make_task(api, csrf, temp_case["case_id"], task_type,
                      due_at=due_in(0, "23:59"))

    tasks = case_tasks(api, temp_case["case_id"])
    assert one(tasks, past)["bucket"] == "overdue"
    assert one(tasks, past)["isOverdue"] is True
    assert one(tasks, ahead)["bucket"] == "critical"
    assert one(tasks, ahead)["isOverdue"] is False


def test_critical_task_reaches_the_banner(api, temp_case, task_type):
    """
    דרישה 7: משימה קריטית קשה לפספוס. היא בבאנר, והבאנר אינו
    מושפע מהסינון של התצוגה.
    """
    csrf = staff_login(api)
    near = make_task(api, csrf, temp_case["case_id"], task_type,
                     due_at=due_in(2), priority="normal")
    far_critical = make_task(api, csrf, temp_case["case_id"], task_type,
                             due_at=due_in(30), priority="critical")
    quiet = make_task(api, csrf, temp_case["case_id"], task_type,
                      due_at=due_in(30), priority="normal")

    banner = {t["id"] for t in api.get("/api/office/tasks").json()["banner"]}
    assert near in banner, "משימה במועד של יומיים לא הגיעה לבאנר"
    assert far_critical in banner, "עדיפות קריטית לא הגיעה לבאנר"
    assert quiet not in banner, "משימה שקטה הופיעה בבאנר"

    # גם כשהתצוגה מסוננת לסטטוס אחר, הבאנר נשאר מלא.
    # הסינון נמדד מול שלוש המשימות של הבדיקה ולא מול כל המסד:
    # למשרד יכולות להיות משימות "ממתין ללקוח" משלו.
    filtered = api.get("/api/office/tasks?status=waiting_client").json()
    listed = {t["id"] for t in filtered["tasks"]}
    for task_id in [near, far_critical, quiet]:
        assert task_id not in listed, "הסינון לפי סטטוס לא סינן"
    assert near in {t["id"] for t in filtered["banner"]}, \
        "סינון התצוגה רוקן את הבאנר"


def test_completed_task_leaves_the_banner(api, temp_case, task_type):
    """דרישה 7: מוצגת עד שהמשימה הושלמה - ואז יורדת."""
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type,
                        due_at=due_in(1), priority="critical")
    assert task_id in {t["id"] for t
                       in api.get("/api/office/tasks").json()["banner"]}

    api.post("/api/office/tasks/%s/complete" % task_id,
             headers={"X-CSRF-Token": csrf})
    after = api.get("/api/office/tasks").json()
    assert task_id not in {t["id"] for t in after["banner"]}, \
        "משימה שהושלמה נשארה בבאנר"


def test_counts_feed_the_summary_chips(api, temp_case, task_type):
    """
    המונים נמדדים בדלתא ולא בערך מוחלט: הם על כל המשרד, ובמסד
    יש גם נתוני זרע.
    """
    csrf = staff_login(api)
    before = api.get("/api/office/tasks").json()["counts"]

    make_task(api, csrf, temp_case["case_id"], task_type, due_at=due_in(-1))
    # סוף היום ולא 09:00: משימה שמועדה היום בבוקר והשעה כבר אחרי
    # הצהריים היא באיחור לכל דבר, והבדיקה הזו מודדת את "להיום".
    make_task(api, csrf, temp_case["case_id"], task_type,
              due_at=due_in(0, "23:59"))
    make_task(api, csrf, temp_case["case_id"], task_type, due_at=due_in(6))
    make_task(api, csrf, temp_case["case_id"], task_type, due_at=due_in(5),
              status="waiting_client")

    after = api.get("/api/office/tasks").json()["counts"]
    assert after["overdue"] - before["overdue"] == 1
    assert after["today"] - before["today"] == 1
    assert after["waitingClient"] - before["waitingClient"] == 1
    # השבוע: היום, 6 ימים ו-5 ימים. זו שחלפה אינה בטווח.
    assert after["week"] - before["week"] == 3


# ================================================================
#  3. השלמה אינה מוחקת היסטוריה
# ================================================================

def test_completing_a_task_keeps_the_row_and_signs_it(api, temp_case, task_type):
    """דרישה 9: אל תמחק משימות שהושלמו. שמור היסטוריה מלאה."""
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type)

    with cursor() as cur:
        cur.execute("select count(*) as n from case_tasks where case_id = %s",
                    (temp_case["case_id"],))
        before = cur.fetchone()["n"]

    r = api.post("/api/office/tasks/%s/complete" % task_id,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    with cursor() as cur:
        cur.execute("""select status, completed_at, completed_by_user_id
                         from case_tasks where id = %s""", (task_id,))
        row = cur.fetchone()
        cur.execute("select count(*) as n from case_tasks where case_id = %s",
                    (temp_case["case_id"],))
        after = cur.fetchone()["n"]

    assert row is not None, "המשימה נמחקה במקום להיסגר"
    assert after == before, "מספר המשימות בתיק ירד"
    assert row["status"] == "done"
    assert row["completed_at"] is not None, "אין חתימת זמן להשלמה"
    assert row["completed_by_user_id"] is not None, "אין חתימת מי השלים"


def test_completed_task_is_hidden_by_default_but_still_listed(api, temp_case,
                                                              task_type):
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type)
    api.post("/api/office/tasks/%s/complete" % task_id,
             headers={"X-CSRF-Token": csrf})

    default = {t["id"] for t in api.get("/api/office/tasks").json()["tasks"]}
    assert task_id not in default, "משימה שהושלמה עומסת את מסך דורש הטיפול"

    everything = {t["id"] for t
                  in api.get("/api/office/tasks?include_done=true").json()["tasks"]}
    assert task_id in everything, "משימה שהושלמה נעלמה גם מהתצוגה המלאה"

    # במסך התיק ההיסטוריה היא המידע, ולכן היא שם בלי בקשה מיוחדת.
    assert task_id in {t["id"] for t in case_tasks(api, temp_case["case_id"])}


def test_reopen_clears_the_completion_signature(api, temp_case, task_type):
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type)
    api.post("/api/office/tasks/%s/complete" % task_id,
             headers={"X-CSRF-Token": csrf})

    r = api.post("/api/office/tasks/%s/reopen" % task_id,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text

    with cursor() as cur:
        cur.execute("""select status, completed_at, completed_by_user_id
                         from case_tasks where id = %s""", (task_id,))
        row = cur.fetchone()
    assert row["status"] == "open"
    assert row["completed_at"] is None
    assert row["completed_by_user_id"] is None


def test_double_completion_and_empty_reopen_are_refused(api, temp_case,
                                                        task_type):
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type)

    r = api.post("/api/office/tasks/%s/reopen" % task_id,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "נפתחה מחדש משימה שכלל לא נסגרה"

    api.post("/api/office/tasks/%s/complete" % task_id,
             headers={"X-CSRF-Token": csrf})
    r = api.post("/api/office/tasks/%s/complete" % task_id,
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "משימה הושלמה פעמיים"


def test_database_refuses_done_without_a_signature(temp_case, task_type):
    """
    הגב של דרישה 9 במסד: אי אפשר לסמן "הושלמה" בלי מי ומתי, גם
    ב-update שמדלג על ה-API.
    """
    with cursor(commit=True) as cur:
        cur.execute("""insert into case_tasks (firm_id, case_id, task_type_id,
                                               title, due_at)
                       values (%s, %s, %s, %s, now()) returning id""",
                    (temp_case["firm_id"], temp_case["case_id"], task_type,
                     "משימה לבדיקת אילוץ"))
        task_id = cur.fetchone()["id"]

    with pytest.raises(psycopg.errors.CheckViolation) as err:
        with cursor(commit=True) as cur:
            cur.execute("update case_tasks set status = 'done' where id = %s",
                        (task_id,))
    assert "done_needs_completion" in str(err.value)


# ================================================================
#  4. מועד משפטי - שער אנושי
# ================================================================

def test_legal_deadline_requires_a_source(api, temp_case, task_type):
    csrf = staff_login(api)
    r = api.post("/api/office/cases/%s/tasks" % temp_case["case_id"],
                 json={"task_type_id": task_type, "title": "הגשת ערר",
                       "due_at": due_in(20), "is_legal_deadline": True,
                       "confirm_legal_deadline": True},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "נשמר מועד משפטי בלי מקור"
    assert "מקור" in r.json()["detail"]


def test_legal_deadline_requires_explicit_confirmation(api, temp_case,
                                                       task_type):
    """
    דרישה 10: המערכת אינה רשאית להמציא או להסיק מועד משפטי.
    מקור לבד אינו מספיק - נדרש אישור מפורש של איש הצוות.
    """
    csrf = staff_login(api)
    r = api.post("/api/office/cases/%s/tasks" % temp_case["case_id"],
                 json={"task_type_id": task_type, "title": "הגשת ערר",
                       "due_at": due_in(20), "is_legal_deadline": True,
                       "deadline_source": "מכתב דחייה מ-19.08.2026",
                       "confirm_legal_deadline": False},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "נשמר מועד משפטי בלי אישור אנושי"
    assert "אישור" in r.json()["detail"]


def test_confirmed_legal_deadline_records_who_approved_it(api, temp_case,
                                                          task_type):
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type,
                        title="הגשת ערר", due_at=due_in(20),
                        is_legal_deadline=True,
                        deadline_source="מכתב דחייה מ-19.08.2026",
                        confirm_legal_deadline=True)

    task = one(case_tasks(api, temp_case["case_id"]), task_id)
    assert task["isLegalDeadline"] is True
    assert task["deadlineSource"] == "מכתב דחייה מ-19.08.2026"
    assert task["confirmedBy"], "לא נשמר מי אישר את המועד"
    assert task["confirmedAt"], "לא נשמר מתי אושר המועד"


def test_database_refuses_a_legal_deadline_nobody_approved(temp_case, task_type):
    """
    השער אינו בקוד בלבד. insert ישיר, מחוץ ל-API, נדחה על
    האילוץ legal_deadline_needs_human.
    """
    with pytest.raises(psycopg.errors.CheckViolation) as err:
        with cursor(commit=True) as cur:
            cur.execute("""insert into case_tasks
                             (firm_id, case_id, task_type_id, title, due_at,
                              is_legal_deadline)
                           values (%s, %s, %s, %s, now(), true)""",
                        (temp_case["firm_id"], temp_case["case_id"],
                         task_type, "מועד שהמערכת המציאה"))
    assert "legal_deadline_needs_human" in str(err.value)


def test_moving_a_deadline_requires_re_confirmation(api, temp_case, task_type):
    """
    מועד משפטי שהוזז הוא מועד חדש. אישור שניתן לתאריך הקודם
    אינו מאשר את הבא אחריו.
    """
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type,
                        due_at=due_in(20), is_legal_deadline=True,
                        deadline_source="מכתב דחייה מ-19.08.2026",
                        confirm_legal_deadline=True)

    r = api.post("/api/office/tasks/%s/update" % task_id,
                 json={"task_type_id": task_type, "title": "הגשת ערר",
                       "due_at": due_in(25), "is_legal_deadline": True,
                       "deadline_source": "מכתב דחייה מ-19.08.2026",
                       "confirm_legal_deadline": False},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400, "מועד משפטי הוזז בלי אישור מחדש"


# ================================================================
#  5. שינוי מועד מתועד
# ================================================================

def test_changing_the_due_date_is_audited(api, temp_case, task_type):
    """
    הזזת מועד נרשמת כפעולה נפרדת, ועם המועד החדש ב-metadata.
    זו גם הבדיקה שמוכיחה שהמפתחות החדשים עברו את רשימת ההיתר
    ב-audit.py: מפתח שאינו ברשימה נזרק בשקט, בלי שגיאה.
    """
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type,
                        due_at=due_in(10))

    new_due = due_in(4)
    r = api.post("/api/office/tasks/%s/update" % task_id,
                 json={"task_type_id": task_type, "title": "משימת בדיקה",
                       "due_at": new_due, "priority": "high",
                       "status": "open"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["dueChanged"] is True

    with cursor() as cur:
        cur.execute("""select metadata from audit_log
                        where case_id = %s
                          and action = 'office.task_deadline_changed'
                        order by created_at desc limit 1""",
                    (temp_case["case_id"],))
        row = cur.fetchone()

    assert row is not None, "הזזת המועד לא נרשמה ביומן"
    assert row["metadata"].get("task_id") == task_id
    assert row["metadata"].get("due_at"), \
        "המועד החדש לא נשמר ב-metadata - בדוק את SAFE_METADATA_KEYS"
    assert new_due[:10] in row["metadata"]["due_at"]


def test_editing_without_moving_the_date_is_not_a_deadline_change(api, temp_case,
                                                                  task_type):
    csrf = staff_login(api)
    same_due = due_in(9)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type,
                        due_at=same_due)

    r = api.post("/api/office/tasks/%s/update" % task_id,
                 json={"task_type_id": task_type, "title": "כותרת אחרת",
                       "due_at": same_due, "priority": "low",
                       "status": "in_progress"},
                 headers={"X-CSRF-Token": csrf})
    assert r.json()["dueChanged"] is False

    with cursor() as cur:
        cur.execute("""select count(*) as n from audit_log
                        where case_id = %s
                          and action = 'office.task_deadline_changed'""",
                    (temp_case["case_id"],))
        assert cur.fetchone()["n"] == 0, "עריכה בלי הזזת מועד נרשמה כהזזה"


def test_task_lifecycle_is_audited(api, temp_case, task_type):
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type)
    api.post("/api/office/tasks/%s/assignee" % task_id,
             json={"assignee_user_id": None},
             headers={"X-CSRF-Token": csrf})
    api.post("/api/office/tasks/%s/complete" % task_id,
             headers={"X-CSRF-Token": csrf})
    api.post("/api/office/tasks/%s/reopen" % task_id,
             headers={"X-CSRF-Token": csrf})

    with cursor() as cur:
        cur.execute("""select action from audit_log where case_id = %s""",
                    (temp_case["case_id"],))
        actions = {r["action"] for r in cur.fetchall()}

    for expected in ["office.task_created", "office.task_reassigned",
                     "office.task_completed", "office.task_reopened"]:
        assert expected in actions, "חסר ביומן: %s" % expected


def test_audit_metadata_never_carries_the_task_text(api, temp_case, task_type):
    """
    הכלל של audit.py: היומן עונה מי נגע במה, לא מה היה כתוב.
    כותרת משימה היא טקסט חופשי שהצוות מקליד.
    """
    csrf = staff_login(api)
    secret = "כותרת שאסור שתגיע ליומן"
    make_task(api, csrf, temp_case["case_id"], task_type, title=secret)

    with cursor() as cur:
        cur.execute("""select metadata from audit_log where case_id = %s""",
                    (temp_case["case_id"],))
        blob = " ".join(str(r["metadata"]) for r in cur.fetchall())
    assert secret not in blob, "כותרת המשימה נכתבה ליומן הביקורת"


# ================================================================
#  6. הרשאות, CSRF, ואי-דליפה ללקוח
# ================================================================

def test_task_routes_require_a_session(api, temp_case):
    for path in ["/api/office/tasks", "/api/office/task-types",
                 "/api/office/staff",
                 "/api/office/cases/%s/tasks" % temp_case["case_id"]]:
        assert api.get(path).status_code == 401, path


def test_task_writes_require_csrf(api, temp_case, task_type):
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type)

    assert api.post("/api/office/cases/%s/tasks" % temp_case["case_id"],
                    json={"task_type_id": task_type, "title": "בלי טוקן",
                          "due_at": due_in(3)}).status_code == 403
    assert api.post("/api/office/tasks/%s/complete" % task_id
                    ).status_code == 403
    assert api.post("/api/office/tasks/%s/reopen" % task_id
                    ).status_code == 403
    assert api.post("/api/office/tasks/%s/assignee" % task_id,
                    json={"assignee_user_id": None}).status_code == 403


def test_client_cannot_touch_tasks(api, temp_case, task_type):
    csrf = staff_login(api)
    task_id = make_task(api, csrf, temp_case["case_id"], task_type)

    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]

    api.cookies.clear()
    client_csrf = client_login(api, client_id)

    assert api.get("/api/office/tasks").status_code == 403
    assert api.post("/api/office/tasks/%s/complete" % task_id,
                    headers={"X-CSRF-Token": client_csrf}).status_code == 403


def test_tasks_never_leak_into_the_client_view(api, temp_case, task_type):
    """
    מועד יעד אינו מוצג ללקוח. ראה ההערה על case_next_steps.eta_text
    בסכמה: תאריך מדויק שמוצג ללקוח נקרא כהתחייבות.
    """
    csrf = staff_login(api)
    marker = "משימה פנימית שאסור שתגיע ללקוח"
    make_task(api, csrf, temp_case["case_id"], task_type, title=marker,
              due_at=due_in(2), priority="critical")

    with cursor() as cur:
        cur.execute("select client_id from cases where id = %s",
                    (temp_case["case_id"],))
        client_id = cur.fetchone()["client_id"]

    api.cookies.clear()
    client_login(api, client_id)

    r = api.get("/api/client/cases/%s" % temp_case["case_id"])
    assert r.status_code == 200, r.text
    body = r.text
    assert marker not in body, "כותרת משימה הודלפה ללקוח"
    for key in ["dueAt", "bucket", "isLegalDeadline", "tasks"]:
        assert key not in body, "שדה משימה (%s) הופיע בתשובה ללקוח" % key


def test_ai_context_has_no_task_data(temp_case, task_type):
    """
    הקשר ה-AI נבנה מ-allowlist של שדות. משימות אינן בו, וטקסט
    חופשי שהצוות מקליד לא היה עובר את assert_clean בכל מקרה.
    """
    from server import policy
    context = policy.build_context({
        "clientName": "ישראל ישראלי", "caseNumber": "BL-2026-0417",
        "claimType": "נכות כללית", "currentStage": 7,
        "tasks": [{"title": "הגשת ערר", "dueAt": "2026-10-01"}],
    })
    assert "tasks" not in context
    policy.assert_clean(context)


# ================================================================
#  7. ולידציה
# ================================================================

def test_unknown_task_type_is_refused(api, temp_case):
    csrf = staff_login(api)
    r = api.post("/api/office/cases/%s/tasks" % temp_case["case_id"],
                 json={"task_type_id": "00000000-0000-0000-0000-000000000000",
                       "title": "סוג שאינו קיים", "due_at": due_in(3)},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
    assert "סוג" in r.json()["detail"]


def test_bad_due_date_gets_a_hebrew_error(api, temp_case, task_type):
    csrf = staff_login(api)
    r = api.post("/api/office/cases/%s/tasks" % temp_case["case_id"],
                 json={"task_type_id": task_type, "title": "מועד שבור",
                       "due_at": "מחר בבוקר"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
    assert "תקין" in r.json()["detail"]


def test_status_done_cannot_be_set_through_create_or_update(api, temp_case,
                                                            task_type):
    """
    done מחייב חתימה, ולכן הוא עובר דרך /complete בלבד. אחרת
    היה אפשר לעקוף את done_needs_completion דרך הטופס.
    """
    csrf = staff_login(api)
    r = api.post("/api/office/cases/%s/tasks" % temp_case["case_id"],
                 json={"task_type_id": task_type, "title": "קפיצה ל-done",
                       "due_at": due_in(3), "status": "done"},
                 headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422, "אפשר היה ליצור משימה 'הושלמה' בלי חתימה"


def test_missing_task_returns_404(api):
    csrf = staff_login(api)
    ghost = "00000000-0000-0000-0000-000000000000"
    assert api.post("/api/office/tasks/%s/complete" % ghost,
                    headers={"X-CSRF-Token": csrf}).status_code == 404
