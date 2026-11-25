"""
throttle.py - הגבלת קצב שנשמרת במסד.

למה במסד ולא בזיכרון
---------------------
המונה הקודם ישב ב-dict בתוך התהליך. שתי בעיות:
  1. הפעלה מחדש של השרת איפסה אותו - כלומר תוקף שגרם לקריסה
     (או פשוט חיכה לפריסה) קיבל סבב ניחושים חדש.
  2. עם יותר מ-worker אחד של uvicorn, לכל תהליך מונה משלו, והמגבלה
     בפועל מוכפלת במספר התהליכים.
Postgres כבר קיים, ולכן זה לא שירות נוסף (Redis) שצריך לתחזק ולאבטח.

מה נשמר
--------
לא כתובת IP, לא אימייל ולא ת"ז: רק HMAC של "סוג|נושא" עם מפתח
השרת. בלי המפתח אי אפשר לשחזר מהטבלה מי ניסה להתחבר - גם לא
בניחוש כל כתובות ה-IPv4. שורה חיה לכל היותר חלון אחד, ו-purge()
(מתוך notifier) מוחק את מה שנשאר.

נעילה
------
pg_advisory_xact_lock לפי המפתח: שתי בקשות במקביל לא יכולות שתיהן
לספור 9 ולהכניס את ה-10 וה-11. הנעילה משתחררת עם סוף הטרנזקציה.
"""

import hashlib
import hmac
import os

from .db.pool import cursor

# שורות ישנות מזה נמחקות ב-purge(), גם אם החלון שלהן ארוך יותר.
MAX_WINDOW_SECONDS = 24 * 3600


def _key(scope: str, subject: str) -> str:
    secret = os.environ.get("PORTAL_ID_HMAC_KEY", "")
    if not secret:
        # בלי מפתח השרת לא עולה ממילא (app.py בודק בהפעלה); כאן רק
        # לא נופלים, כדי שהגבלת הקצב לא תהפוך לנקודת כשל.
        return hashlib.sha256(("%s|%s" % (scope, subject)).encode("utf-8")).hexdigest()
    return hmac.new(secret.encode("utf-8"), ("%s|%s" % (scope, subject)).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def hit(scope: str, subject: str, limit: int, window_seconds: int) -> bool:
    """רושם ניסיון. False אם כבר היו limit ניסיונות בחלון - ואז
    הניסיון הנוכחי אינו נרשם, כדי שהצפה לא תאריך את החסימה לנצח."""
    key = _key(scope, subject)
    with cursor(commit=True) as cur:
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
        cur.execute(
            """delete from rate_limit_hits
                where key = %s and hit_at < now() - make_interval(secs => %s)""",
            (key, window_seconds),
        )
        cur.execute("select count(*) as n from rate_limit_hits where key = %s", (key,))
        if cur.fetchone()["n"] >= limit:
            return False
        cur.execute("insert into rate_limit_hits (key) values (%s)", (key,))
    return True


def purge() -> int:
    """מחיקת שורות שכבר אינן בשום חלון. מורץ מתוך notifier."""
    with cursor(commit=True) as cur:
        cur.execute(
            "delete from rate_limit_hits where hit_at < now() - make_interval(secs => %s)",
            (MAX_WINDOW_SECONDS,),
        )
        return cur.rowcount


def clear() -> None:
    """לבדיקות בלבד: כל הבדיקות מגיעות מאותו IP."""
    with cursor(commit=True) as cur:
        cur.execute("delete from rate_limit_hits")
