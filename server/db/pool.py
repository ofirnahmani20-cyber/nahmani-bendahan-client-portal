"""
pool.py - גישה למסד עבור האפליקציה.

למה שכבה נפרדת ולא connect() ישירות
------------------------------------
connect.py נכתב עבור כלי המיגרציה והזריעה, שפותחים חיבור, עושים
עבודה, וסוגרים. לשרת יש צורך אחר: חיבור פר-בקשה, שורות כמילונים,
ו-rollback אוטומטי בשגיאה. הכל כאן, כדי ש-app.py לא יחזיק לוגיקת
מסד וכדי שהחלפה ל-connection pool אמיתי תהיה שינוי בקובץ אחד.

⚠️ כרגע זהו חיבור פר-בקשה ולא pool. מספיק לעומס פיתוח; לפני ייצור
   יש להחליף ל-psycopg_pool. מסומן כאן ולא מוסתר.
"""

import contextlib
import os

import psycopg
from psycopg.rows import dict_row

from .connect import dsn


class NotConfigured(RuntimeError):
    """המסד אינו זמין. נזרק במקום להחזיר נתונים מזויפים."""


@contextlib.contextmanager
def cursor(commit: bool = False):
    """
    חיבור ו-cursor לבקשה אחת. שורות חוזרות כמילונים.

    שגיאה בתוך הבלוק מגלגלת אחורה את כל השינויים - חצי-פעולה
    במסד של תיקים משפטיים גרועה מכישלון גלוי.
    """
    try:
        conn = psycopg.connect(dsn(), row_factory=dict_row)
    except psycopg.OperationalError as exc:
        raise NotConfigured("אין חיבור למסד הנתונים: %s" % exc) from exc

    try:
        with conn, conn.cursor() as cur:
            yield cur
            if commit:
                conn.commit()
    finally:
        conn.close()


def healthy() -> bool:
    """בדיקת חיים. אינה זורקת - משמשת את /api/health."""
    try:
        with cursor() as cur:
            cur.execute("select 1 as ok")
            return cur.fetchone()["ok"] == 1
    except Exception:
        return False
