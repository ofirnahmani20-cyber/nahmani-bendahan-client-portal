"""
notifier.py - שירות ההתראות (docs/10, v1 סעיף 3).

    python -m server.notifier            סבב אחד
    python -m server.notifier --loop     כל 5 דקות, עד עצירה

ארבעה דברים בכל סבב, וכולם בטוחים להרצה חוזרת:

1. תזכורות על דרישות - לפי reminder_rules שהמשרד הגדיר בתיק.
   עד היום הכללים נשמרו ואיש לא הריץ אותם ("אין scheduler").
2. "יש עדכון בתיק" ללקוח - שינוי שלב, מסמך שנדחה, דרישה חדשה,
   הודעה מהמשרד. *הודעה אחת* מרכזת את כל מה שקרה, ולכל היותר
   אחת לשש שעות: כל SMS עולה כסף, ולקוח שמקבל חמש הודעות ביום
   מפסיק לקרוא אותן.
3. תזכורת לוועדה - שלושה ימים ויום לפני.
4. סיכום בוקר לכל איש צוות - מספרים בלבד, רק על התיקים שלו.

מה אינו נשלח, לעולם
--------------------
תוכן. ה-SMS אומר "יש עדכון, היכנס לאתר", לא "המסמך הפסיכיאטרי
נדחה". הודעה נשארת בטלפון, מוצגת על מסך נעול ונקראת בידי מי
שמחזיק בו. הפרטים - רק אחרי כניסה מאומתת. גם בסיכום הבוקר: מספרים,
בלי שמות לקוחות - מייל עובר דרך שרתים שאינם שלנו.

מניעת כפילות
-------------
לכל משלוח dedupe_key (אינדקס ייחודי במסד). הרצה כפולה, או קריסה
אחרי שליחה ולפני סיום הסבב, מסתיימות בשורה אחת ולא בשתי הודעות.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import notify
from .db.pool import cursor

TZ = ZoneInfo("Asia/Jerusalem")

# לקוח: א'-ה', 09:00-20:00. שעון ישראל, לא UTC.
CLIENT_HOURS = (9, 20)
CLIENT_DAYS = "0,1,2,3,4"
CLIENT_UPDATE_MIN_HOURS = 6
# אירוע שקרה לפני שהשירות הכיר את הלקוח לא מצדיק הודעה.
FIRST_LOOKBACK_HOURS = 24

HEARING_DAYS_BEFORE = (3, 1)

# סיכום הבוקר: א'-ו' מ-07:30.
DIGEST_AT = (7, 30)
DIGEST_DAYS = "0,1,2,3,4,5"

LOOP_SECONDS = 300


# ================================================================
#  עזרים
# ================================================================

def _local(now):
    return now.astimezone(TZ)


def _weekday(local):
    """0 = ראשון, כמו בעמודה reminder_rules.weekdays. ב-Python שני = 0."""
    return (local.weekday() + 1) % 7


def in_window(now, hours_from, hours_to, weekdays):
    local = _local(now)
    days = {int(d) for d in str(weekdays).split(",") if d.strip().isdigit()}
    return _weekday(local) in days and hours_from <= local.hour < hours_to


def _client_enabled(cur, client_id, event_type):
    """ברירת המחדל: מופעל. הלקוח יכול לכבות סוג אירוע."""
    cur.execute("""select is_enabled from notification_preferences
                    where client_id = %s and event_type = %s""", (client_id, event_type))
    row = cur.fetchone()
    return row is None or row["is_enabled"]


def deliver(*, firm_id, subject_type, subject_id, channel, to, template_key,
            params=None, case_id=None, requirement_id=None, dedupe_key=None, now):
    """
    רושם משלוח ומוסר אותו. מחזיר את הסטטוס, או None אם האירוע
    כבר טופל (dedupe_key קיים).

    כל משלוח בטרנזקציה משלו: כישלון של אחד לא מבטל את האחרים.
    כישלון ספק נרשם כ-failed ואינו נשלח שוב אוטומטית - ניסיון
    חוזר הוא החלטה, לא לולאה שעלולה לשלוח לאדם עשר הודעות.
    """
    # נכשל לפני שנרשם דבר אם נוסח מקבל פרמטר שאינו ברשימה הסגורה.
    notify.render(template_key, params)

    with cursor(commit=True) as cur:
        cur.execute(
            """insert into message_deliveries
                 (firm_id, requirement_id, case_id, subject_type, subject_id,
                  channel, to_address, template_key, status, attempts,
                  scheduled_for, dedupe_key)
               values (%s, %s, %s, %s, %s, %s, %s, %s, 'queued', 1, %s, %s)
               on conflict do nothing
               returning id""",
            (firm_id, requirement_id, case_id, subject_type, subject_id,
             channel, to, template_key, now, dedupe_key))
        row = cur.fetchone()
        if row is None:
            return None

        status, error, provider_id = "sent", None, None
        if channel != "portal":
            try:
                provider_id = notify.get_sender().send(channel, to, template_key, params or {})
            except notify.SmsNotConfigured as exc:
                status, error = "failed", str(exc)
            except Exception as exc:                     # noqa: BLE001
                status, error = "failed", str(exc)[:500]

        cur.execute(
            """update message_deliveries
                  set status = %s, error = %s, provider_message_id = %s,
                      sent_at = case when %s = 'sent' then %s else null end
                where id = %s""",
            (status, error, provider_id, status, now, row["id"]))
        return status


# ================================================================
#  1. תזכורות על דרישות
# ================================================================

def run_reminders(now):
    with cursor() as cur:
        cur.execute(
            """select r.id, r.firm_id, r.requirement_id, r.every_days, r.start_at,
                      r.channel, r.hours_from, r.hours_to, r.weekdays,
                      q.case_id, q.status as req_status,
                      cl.id as client_id, cl.phone, cl.email,
                      (select max(d.scheduled_for) from message_deliveries d
                        where d.requirement_id = r.requirement_id
                          and d.channel = r.channel
                          and d.template_key = 'requirement.reminder') as last_at
                 from reminder_rules r
                 join case_requirements q on q.id = r.requirement_id and q.firm_id = r.firm_id
                 join cases c on c.id = q.case_id and c.firm_id = q.firm_id
                 join clients cl on cl.id = c.client_id
                where not r.is_paused
                  and q.status in ('open', 'sent')
                  and c.status = 'active' and cl.status = 'active'
                  and r.start_at <= %s
                  and (r.end_at is null or r.end_at > %s)""",
            (now, now))
        rules = cur.fetchall()

    sent = 0
    for r in rules:
        due = r["start_at"] if r["last_at"] is None else r["last_at"] + timedelta(days=r["every_days"])
        if due > now or not in_window(now, r["hours_from"], r["hours_to"], r["weekdays"]):
            continue
        to = r["phone"] if r["channel"] in ("sms", "whatsapp") else r["email"]
        if r["channel"] != "portal" and not to:
            continue
        with cursor() as cur:
            if not _client_enabled(cur, r["client_id"], "requirement.reminder"):
                continue
        # scheduled_for = עכשיו, לא המועד שהוחמץ: שירות שהיה למטה
        # שבוע ישלח תזכורת אחת, לא שבע ברצף. המפתח - לפי המועד.
        status = deliver(
            firm_id=r["firm_id"], subject_type="client", subject_id=r["client_id"],
            channel=r["channel"], to=to, template_key="requirement.reminder",
            params={"requirement_id": str(r["requirement_id"])},
            case_id=r["case_id"], requirement_id=r["requirement_id"],
            dedupe_key="rem:%s:%s" % (r["id"], due.isoformat()), now=now)
        if status == "sent":
            sent += 1
            if r["req_status"] == "open":
                with cursor(commit=True) as cur:
                    cur.execute("update case_requirements set status = 'sent' where id = %s",
                                (r["requirement_id"],))
    return sent


# ================================================================
#  2. "יש עדכון בתיק"
# ================================================================

_CLIENT_EVENTS = """
    select cl.id as client_id, cl.firm_id, cl.phone,
           max(ev.at) as latest, count(*) as events,
           -- התיק האחרון שבו קרה משהו - להקשר בהיסטוריית המשלוחים
           (array_agg(ev.case_id order by ev.at desc))[1] as case_id
      from clients cl
      join cases c on c.client_id = cl.id and c.firm_id = cl.firm_id
                  and c.status in ('active', 'frozen')
      join lateral (
            select e.case_id, e.created_at as at from case_stage_events e
             where e.case_id = c.id
            union all
            select d.case_id, d.reviewed_at from case_documents d
             where d.case_id = c.id and d.status = 'rejected'
            union all
            select q.case_id, q.created_at from case_requirements q
             where q.case_id = c.id and q.status in ('open', 'sent')
            union all
            select v.case_id, v.created_at from case_conversation v
             where v.case_id = c.id and v.direction = 'outbound'
            union all
            select m.case_id, m.sent_at from messages m
             where m.case_id = c.id
      ) ev on ev.at > coalesce(
            -- scheduled_for ולא created_at: זה "עכשיו" של הסבב ששלח,
            -- והוא מה שמודד את שש השעות.
            (select max(d.scheduled_for) from message_deliveries d
              where d.subject_type = 'client' and d.subject_id = cl.id
                and d.template_key = 'case.update'),
            cl.created_at,
            %(floor)s) and ev.at > %(floor)s
     where cl.status = 'active' and cl.phone is not null
     group by cl.id, cl.firm_id, cl.phone
"""


def run_client_updates(now):
    if not in_window(now, *CLIENT_HOURS, CLIENT_DAYS):
        return 0
    floor = now - timedelta(hours=FIRST_LOOKBACK_HOURS)
    with cursor() as cur:
        cur.execute(_CLIENT_EVENTS, {"floor": floor})
        rows = cur.fetchall()

    sent = 0
    for r in rows:
        with cursor() as cur:
            cur.execute("""select max(scheduled_for) as last from message_deliveries
                            where subject_type = 'client' and subject_id = %s
                              and template_key = 'case.update'""", (r["client_id"],))
            last = cur.fetchone()["last"]
            if last and now - last < timedelta(hours=CLIENT_UPDATE_MIN_HOURS):
                continue
            if not _client_enabled(cur, r["client_id"], "case.update"):
                continue
        status = deliver(
            firm_id=r["firm_id"], subject_type="client", subject_id=r["client_id"],
            channel="sms", to=r["phone"], template_key="case.update",
            case_id=r["case_id"],
            # לכל היותר אחת לשעה גם אם שני סבבים רצים במקביל
            dedupe_key="upd:%s:%s" % (r["client_id"], _local(now).strftime("%Y%m%d%H")),
            now=now)
        if status == "sent":
            sent += 1
    return sent


# ================================================================
#  3. תזכורת לוועדה
# ================================================================

def run_hearing_reminders(now):
    if not in_window(now, *CLIENT_HOURS, CLIENT_DAYS):
        return 0
    horizon = now + timedelta(days=max(HEARING_DAYS_BEFORE) + 1)
    with cursor() as cur:
        cur.execute(
            """select c.id as case_id, c.firm_id, c.next_hearing_at,
                      cl.id as client_id, cl.phone
                 from cases c join clients cl on cl.id = c.client_id
                where c.status = 'active' and cl.status = 'active'
                  and cl.phone is not null
                  and c.next_hearing_at > %s and c.next_hearing_at <= %s""",
            (now, horizon))
        rows = cur.fetchall()

    sent = 0
    today = _local(now).date()
    for r in rows:
        hearing = _local(r["next_hearing_at"])
        days = (hearing.date() - today).days
        if days not in HEARING_DAYS_BEFORE:
            continue
        with cursor() as cur:
            if not _client_enabled(cur, r["client_id"], "case.hearing_reminder"):
                continue
        status = deliver(
            firm_id=r["firm_id"], subject_type="client", subject_id=r["client_id"],
            channel="sms", to=r["phone"], template_key="case.hearing_reminder",
            # תאריך ושעה - לא סוג הוועדה ולא מה ייבדק בה
            params={"date": hearing.strftime("%d.%m"), "time": hearing.strftime("%H:%M")},
            case_id=r["case_id"],
            # המועד במפתח: ועדה שנדחתה מקבלת תזכורת משלה
            dedupe_key="hear:%s:%s:%d" % (r["case_id"], r["next_hearing_at"].isoformat(), days),
            now=now)
        if status == "sent":
            sent += 1
    return sent


# ================================================================
#  4. סיכום בוקר לצוות
# ================================================================

def staff_digest_counts(cur, user):
    """המספרים של איש צוות - בהיקף התיקים שלו, כמו בממשק."""
    scope = "true" if user["role"] == "admin" else "c.assigned_user_id = %(uid)s"
    params = {"uid": user["id"], "firm": user["firm_id"]}
    cur.execute(
        """select
             count(*) filter (where g.is_overdue)                          as overdue,
             count(*) filter (where not g.is_closed and g.days_left = 0)  as today,
             count(*) filter (where not g.is_closed
                                and (t.priority = 'critical' or g.days_left <= 3)) as critical
             from case_tasks t
             join case_task_urgency g on g.task_id = t.id
             join cases c on c.id = t.case_id
            where t.firm_id = %(firm)s and """ + scope, params)
    tasks = cur.fetchone()
    cur.execute(
        """select
             (select count(*) from case_documents d join cases c on c.id = d.case_id
               where d.firm_id = %(firm)s and d.status = 'pending_review' and """ + scope + """)
               as to_review,
             (select count(*) from case_conversation v join cases c on c.id = v.case_id
               where v.firm_id = %(firm)s and v.direction = 'inbound' and v.read_at is null
                 and """ + scope + """) as unread,
             (select count(*) from cases c
               where c.firm_id = %(firm)s and c.status = 'active'
                 and c.next_hearing_at between now() and now() + interval '7 days'
                 and """ + scope + """) as hearings""", params)
    other = cur.fetchone()
    return {"overdue": tasks["overdue"], "today": tasks["today"],
            "critical": tasks["critical"], "toReview": other["to_review"],
            "unread": other["unread"], "hearings": other["hearings"]}


def run_staff_digest(now):
    local = _local(now)
    if _weekday(local) not in {int(d) for d in DIGEST_DAYS.split(",")}:
        return 0
    if (local.hour, local.minute) < DIGEST_AT:
        return 0

    with cursor() as cur:
        cur.execute("""select id, firm_id, email, role from users
                        where status = 'active' and email is not null""")
        users = cur.fetchall()

    sent = 0
    for u in users:
        with cursor() as cur:
            counts = staff_digest_counts(cur, u)
        # יום בלי שום דבר אינו מצדיק מייל.
        if not any(counts.values()):
            continue
        status = deliver(
            firm_id=u["firm_id"], subject_type="user", subject_id=u["id"],
            channel="email", to=u["email"], template_key="staff.morning_digest",
            params=counts,
            dedupe_key="digest:%s:%s" % (u["id"], local.date().isoformat()), now=now)
        if status == "sent":
            sent += 1
    return sent


# ================================================================
#  הרצה
# ================================================================

def run_once(now=None):
    now = now or datetime.now(timezone.utc)
    return {
        "reminders": run_reminders(now),
        "updates": run_client_updates(now),
        "hearings": run_hearing_reminders(now),
        "digests": run_staff_digest(now),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m server.notifier")
    parser.add_argument("--loop", action="store_true", help="סבב כל 5 דקות, עד עצירה")
    args = parser.parse_args(argv)
    while True:
        result = run_once()
        print("[notifier] %s" % result, flush=True)
        if not args.loop:
            return 0
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
