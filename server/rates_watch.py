"""
rates_watch.py - עדכון אוטומטי של פרמטרי המחשבונים (docs/10, v1 סעיף 6).

החלטת המוצר: "עדכון אוטומטי מאתרי הממשלה". מימוש זהיר שלה
(docs/10, נקודה 3): לכל פרמטר אפשר להגדיר דף מקור וביטוי שקבוצה 1
שלו היא המספר. פעם ביום (בתוך notifier):

  * אותו מספר              -> רק last_checked_at
  * שינוי של עד MAX_AUTO_CHANGE -> שורה חדשה, active, בתוקף מהיום
  * שינוי גדול יותר, או דף שלא נקרא / שהביטוי לא מצא בו מספר
                            -> שורה pending שמחכה לאדם; החישוב ממשיך
                               עם הערך הקודם

למה עצירה בשינוי חריג: אתר שמשנה עיצוב יכול להפוך "4,711" ל"4" או
ל"471,100". מספר כזה מול לקוח הוא בדיוק מה שאסור.

אבטחה: רק https, ורק לאתרים ברשימה (ALLOWED_HOSTS). כתובת שמנהל
מקליד אינה פותחת לשרת דלת לרשת הפנימית (SSRF).
"""

import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from . import audit
from .db.pool import cursor

MAX_AUTO_CHANGE = Decimal("0.10")
CHECK_EVERY = timedelta(hours=20)
FETCH_TIMEOUT = 15
MAX_PAGE_BYTES = 2 * 1024 * 1024

ALLOWED_HOSTS = ("btl.gov.il", "www.btl.gov.il", "gov.il", "www.gov.il",
                 "mod.gov.il", "www.mod.gov.il", "shikum.mod.gov.il",
                 "kolzchut.org.il", "www.kolzchut.org.il")


def allowed_url(url: str) -> bool:
    try:
        u = urlparse(url or "")
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "") in ALLOWED_HOSTS


def fetch(url: str) -> str:
    """מוחלף בבדיקות. בלי הפניות לאתר אחר: urllib עוקב אחריהן, ולכן נבדק שוב."""
    req = urllib.request.Request(url, headers={"User-Agent": "NahmaniPortal-RatesWatch/1"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:     # noqa: S310
        if not allowed_url(resp.geturl()):
            raise ValueError("redirected outside the allowlist")
        return resp.read(MAX_PAGE_BYTES).decode("utf-8", errors="replace")


def extract(page: str, pattern: str):
    m = re.search(pattern, page, flags=re.S)
    if not m or not m.groups():
        return None
    raw = re.sub(r"[,\s₪]", "", m.group(1))
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def run(now=None):
    now = now or datetime.now(timezone.utc)
    with cursor() as cur:
        # הערך האחרון בתוקף לכל (משרד, מחשבון, מפתח) שמוגדר לו מקור
        cur.execute(
            """select distinct on (firm_id, calculator, key)
                      id, firm_id, calculator, key, label, value, source_url, extract_pattern,
                      last_checked_at
                 from calc_parameters
                where status = 'active' and source_url is not null
                  and extract_pattern is not null
                order by firm_id, calculator, key, effective_from desc, created_at desc""")
        rows = cur.fetchall()

    counts = {"checked": 0, "updated": 0, "held": 0, "skipped": 0}
    for r in rows:
        if r["last_checked_at"] and now - r["last_checked_at"] < CHECK_EVERY:
            counts["skipped"] += 1
            continue
        counts["checked"] += 1
        new = None
        if allowed_url(r["source_url"]):
            try:
                new = extract(fetch(r["source_url"]), r["extract_pattern"])
            except Exception:                            # noqa: BLE001
                new = None

        old = Decimal(str(r["value"]))
        with cursor(commit=True) as cur:
            cur.execute("update calc_parameters set last_checked_at = %s where id = %s",
                        (now, r["id"]))
            if new is not None and new == old:
                continue
            ok = (new is not None and new > 0 and old > 0
                  and abs(new - old) / old <= MAX_AUTO_CHANGE)
            # ערך שלא נקרא נשמר כ-pending עם הערך הישן, כדי שאדם יראה שיש בעיה
            cur.execute(
                """insert into calc_parameters
                     (firm_id, calculator, key, label, value, effective_from, status,
                      source_url, extract_pattern, origin, last_checked_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'auto', %s)""",
                (r["firm_id"], r["calculator"], r["key"], r["label"],
                 new if new is not None else old, now.date(),
                 "active" if ok else "pending",
                 r["source_url"], r["extract_pattern"], now))
        counts["updated" if ok else "held"] += 1
        audit.record_actor(r["firm_id"], "system", None,
                           "system.calc_parameter_auto" if ok else "system.calc_parameter_held",
                           metadata={"outcome": "updated" if ok else
                                     ("unreadable" if new is None else "large_change")})
    return counts
