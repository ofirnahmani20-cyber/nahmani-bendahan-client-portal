"""
שירות ההתראות - server/notifier.py (docs/10, v1 סעיף 3).

השעון מוזרק: כל סבב רץ על "עכשיו" ב-2031, רחוק מכל נתון בזרע, כדי
שרק מה שהבדיקה יצרה ייחשב. כללי התזכורת של הזרע מושהים לזמן הבדיקה,
וכל המשלוחים שנוצרו (scheduled_for ב-2031) נמחקים בסופה.

דורש מסד נתונים חי עם נתוני הזרע.
"""

import re
from datetime import datetime, timedelta

import pytest

from server import notifier, notify
from server.db.pool import cursor

IL = notifier.TZ
# יום ראשון, 10:00 שעון ישראל - בתוך חלון השליחה ללקוח
SUNDAY_10 = datetime(2031, 3, 2, 10, 0, tzinfo=IL)
FAR = datetime(2031, 1, 1, tzinfo=IL)


@pytest.fixture(autouse=True)
def isolated():
    with cursor(commit=True) as cur:
        cur.execute("update reminder_rules set is_paused = true where not is_paused returning id")
        paused = [r["id"] for r in cur.fetchall()]
    yield
    with cursor(commit=True) as cur:
        cur.execute("delete from message_deliveries where scheduled_for >= %s", (FAR,))
        if paused:
            cur.execute("update reminder_rules set is_paused = false where id = any(%s)", (paused,))


@pytest.fixture
def case(temp_case):
    with cursor(commit=True) as cur:
        cur.execute("select client_id from cases where id = %s", (temp_case["case_id"],))
        temp_case["client_id"] = cur.fetchone()["client_id"]
        cur.execute("update cases set status = 'active' where id = %s", (temp_case["case_id"],))
    yield temp_case
    with cursor(commit=True) as cur:
        cur.execute("delete from notification_preferences where client_id = %s",
                    (temp_case["client_id"],))


def _deliveries(subject_id, template):
    with cursor() as cur:
        cur.execute("""select status, channel, case_id from message_deliveries
                        where subject_id = %s and template_key = %s
                          and scheduled_for >= %s""", (subject_id, template, FAR))
        return cur.fetchall()


def _stage_event(case, at):
    with cursor(commit=True) as cur:
        cur.execute("""select id from stage_templates where claim_type_id = %s
                        order by position limit 1""", (case["claim_type_id"],))
        stage = cur.fetchone()["id"]
        cur.execute("""insert into case_stage_events (firm_id, case_id, stage_template_id,
                                                      claim_type_id, occurred_at, created_at)
                       values (%s, %s, %s, %s, %s, %s)""",
                    (case["firm_id"], case["case_id"], stage, case["claim_type_id"], at, at))


# ================================================================
#  חלון שליחה ונוסחים
# ================================================================

@pytest.mark.parametrize("when, ok", [
    (SUNDAY_10, True),
    (SUNDAY_10.replace(hour=8, minute=59), False),
    (SUNDAY_10.replace(hour=20), False),
    (SUNDAY_10 + timedelta(days=5), False),   # שישי
    (SUNDAY_10 + timedelta(days=6), False),   # שבת
    (SUNDAY_10 + timedelta(days=4), True),    # חמישי
])
def test_client_window_is_israel_time_sunday_to_thursday(when, ok):
    assert notifier.in_window(when, *notifier.CLIENT_HOURS, notifier.CLIENT_DAYS) is ok


def test_templates_carry_no_case_content():
    """נוסח שמכיל מידע על התיק הוא נוסח שדולף ממסך נעול."""
    forbidden = re.compile(r"\{(name|client|document|doc|kind|diagnos|percent|outcome|"
                           r"medical|stage|title|body)\w*\}")
    for key, text in notify.TEMPLATES.items():
        assert not forbidden.search(text), key
        placeholders = set(re.findall(r"\{(\w+)\}", text)) - {"portal_url"}
        assert placeholders <= notify.TEMPLATE_PARAMS[key], key


def test_render_refuses_unlisted_params():
    with pytest.raises(ValueError):
        notify.render("case.update", {"document": "פסיכיאטרי"})
    assert "12.03" in notify.render("case.hearing_reminder", {"date": "12.03", "time": "09:00"})


# ================================================================
#  1. תזכורות על דרישות
# ================================================================

@pytest.fixture
def rule(case):
    with cursor(commit=True) as cur:
        cur.execute("""insert into case_requirements (firm_id, case_id, kind, title)
                       values (%s, %s, 'info', 'בדיקת תזכורת') returning id""",
                    (case["firm_id"], case["case_id"]))
        req = cur.fetchone()["id"]
        cur.execute("""insert into reminder_rules (firm_id, requirement_id, every_days,
                                                   start_at, channel)
                       values (%s, %s, 3, %s, 'sms') returning id""",
                    (case["firm_id"], req, SUNDAY_10 - timedelta(hours=1)))
        rid = cur.fetchone()["id"]
    return {"requirement_id": req, "rule_id": rid, **case}


def test_due_reminder_is_sent_once_and_marks_requirement_sent(rule):
    assert notifier.run_reminders(SUNDAY_10) == 1
    assert notifier.run_reminders(SUNDAY_10 + timedelta(minutes=5)) == 0
    with cursor() as cur:
        cur.execute("select status from case_requirements where id = %s",
                    (rule["requirement_id"],))
        assert cur.fetchone()["status"] == "sent"


def test_next_reminder_waits_every_days(rule):
    notifier.run_reminders(SUNDAY_10)
    assert notifier.run_reminders(SUNDAY_10 + timedelta(days=2)) == 0
    assert notifier.run_reminders(SUNDAY_10 + timedelta(days=3)) == 1


def test_long_outage_sends_one_reminder_not_a_burst(rule):
    late = SUNDAY_10 + timedelta(days=28)          # ראשון, ארבעה שבועות אחרי
    assert notifier.run_reminders(late) == 1
    assert notifier.run_reminders(late + timedelta(minutes=5)) == 0


def test_paused_or_completed_requirement_is_not_reminded(rule):
    with cursor(commit=True) as cur:
        cur.execute("update reminder_rules set is_paused = true where id = %s", (rule["rule_id"],))
    assert notifier.run_reminders(SUNDAY_10) == 0
    with cursor(commit=True) as cur:
        cur.execute("update reminder_rules set is_paused = false where id = %s", (rule["rule_id"],))
        cur.execute("update case_requirements set status = 'cancelled' where id = %s",
                    (rule["requirement_id"],))
    assert notifier.run_reminders(SUNDAY_10) == 0


def test_reminder_outside_window_waits(rule):
    assert notifier.run_reminders(SUNDAY_10.replace(hour=22)) == 0


def test_client_can_turn_reminders_off(rule):
    with cursor(commit=True) as cur:
        cur.execute("""insert into notification_preferences (firm_id, client_id, event_type, is_enabled)
                       values (%s, %s, 'requirement.reminder', false)""",
                    (rule["firm_id"], rule["client_id"]))
    assert notifier.run_reminders(SUNDAY_10) == 0


# ================================================================
#  2. "יש עדכון בתיק"
# ================================================================

def test_update_is_sent_for_new_event_then_throttled(case):
    _stage_event(case, SUNDAY_10 - timedelta(hours=1))
    notifier.run_client_updates(SUNDAY_10)
    assert len(_deliveries(case["client_id"], "case.update")) == 1

    # אירוע נוסף אחרי שעתיים - עדיין בתוך שש השעות
    _stage_event(case, SUNDAY_10 + timedelta(hours=2))
    notifier.run_client_updates(SUNDAY_10 + timedelta(hours=2, minutes=5))
    assert len(_deliveries(case["client_id"], "case.update")) == 1

    # אחרי שש שעות - הודעה אחת שמרכזת את מה שהצטבר
    notifier.run_client_updates(SUNDAY_10 + timedelta(hours=7))
    assert len(_deliveries(case["client_id"], "case.update")) == 2


def test_no_event_no_message(case):
    notifier.run_client_updates(SUNDAY_10)
    assert _deliveries(case["client_id"], "case.update") == []


def test_old_events_before_lookback_are_ignored(case):
    _stage_event(case, SUNDAY_10 - timedelta(days=3))
    notifier.run_client_updates(SUNDAY_10)
    assert _deliveries(case["client_id"], "case.update") == []


def test_update_waits_for_the_window(case):
    _stage_event(case, SUNDAY_10 - timedelta(hours=1))
    notifier.run_client_updates(SUNDAY_10 + timedelta(days=5))   # שישי
    assert _deliveries(case["client_id"], "case.update") == []


# ================================================================
#  3. ועדה
# ================================================================

def test_hearing_reminders_three_days_and_one_day_before(case):
    hearing = SUNDAY_10.replace(hour=11) + timedelta(days=3)          # רביעי 11:00
    with cursor(commit=True) as cur:
        cur.execute("update cases set next_hearing_at = %s where id = %s",
                    (hearing, case["case_id"]))
    assert notifier.run_hearing_reminders(SUNDAY_10) >= 1
    assert notifier.run_hearing_reminders(SUNDAY_10 + timedelta(hours=1)) == 0
    assert notifier.run_hearing_reminders(SUNDAY_10 + timedelta(days=1)) == 0   # יומיים לפני
    assert notifier.run_hearing_reminders(SUNDAY_10 + timedelta(days=2)) >= 1   # יום לפני
    rows = _deliveries(case["client_id"], "case.hearing_reminder")
    assert len(rows) == 2 and all(str(r["case_id"]) == str(case["case_id"]) for r in rows)


def test_postponed_hearing_gets_its_own_reminder(case):
    with cursor(commit=True) as cur:
        cur.execute("update cases set next_hearing_at = %s where id = %s",
                    (SUNDAY_10 + timedelta(days=3), case["case_id"]))
    notifier.run_hearing_reminders(SUNDAY_10)
    with cursor(commit=True) as cur:
        cur.execute("update cases set next_hearing_at = %s where id = %s",
                    (SUNDAY_10 + timedelta(days=3, hours=2), case["case_id"]))
    notifier.run_hearing_reminders(SUNDAY_10 + timedelta(minutes=5))
    assert len(_deliveries(case["client_id"], "case.hearing_reminder")) == 2


# ================================================================
#  4. סיכום בוקר
# ================================================================

def test_digest_waits_for_morning_and_goes_once_a_day():
    early = SUNDAY_10.replace(hour=7, minute=0)
    assert notifier.run_staff_digest(early) == 0
    first = notifier.run_staff_digest(early.replace(minute=45))
    assert notifier.run_staff_digest(early.replace(hour=9)) == 0
    with cursor() as cur:
        cur.execute("""select count(*) as n from message_deliveries
                        where template_key = 'staff.morning_digest' and scheduled_for >= %s""",
                    (FAR,))
        assert cur.fetchone()["n"] == first


def test_digest_not_sent_on_saturday():
    assert notifier.run_staff_digest(SUNDAY_10 + timedelta(days=6)) == 0


def test_digest_counts_follow_case_scope():
    """איש צוות סופר רק את התיקים שלו - כמו בממשק."""
    with cursor() as cur:
        cur.execute("select id, firm_id, role from users order by role")
        users = {u["role"]: u for u in cur.fetchall()}
        admin = notifier.staff_digest_counts(cur, users["admin"])
        staff = notifier.staff_digest_counts(cur, users["staff"])
    for k in admin:
        assert staff[k] <= admin[k], k


# ================================================================
#  ייצור בלי ספק
# ================================================================

def test_production_without_provider_records_failure_and_does_not_raise(case, monkeypatch):
    monkeypatch.setenv("PORTAL_MODE", "production")
    _stage_event(case, SUNDAY_10 - timedelta(hours=1))
    notifier.run_client_updates(SUNDAY_10)
    rows = _deliveries(case["client_id"], "case.update")
    assert len(rows) == 1 and rows[0]["status"] == "failed"
