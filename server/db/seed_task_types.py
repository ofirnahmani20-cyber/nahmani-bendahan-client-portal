"""
seed_task_types.py - קטלוג סוגי המשימות.

הסוגים יושבים ב-task_types ולא כקבוע ב-JavaScript, מאותה סיבה
שקטלוג המסמכים עבר למסד: שינוי ברשימת הסוגים לא צריך לחייב
פריסה, והדפדפן לא צריך להחזיק עותק שיכול לצאת מסנכרון.

ההרצה חוזרת על עצמה: סוג שכבר קיים אינו משוכפל ואינו נדרס.
מריצים אותה אחרי `apply --schema` כדי לקבל את הקטלוג בלי
לעשות reseed הרסני של כל המסד.

    python -m server.db.seed_task_types
"""

import sys

from .connect import MissingKey
from .pool import cursor

# מקור אחד לרשימה: היא מוגדרת ב-seed.py, וכאן רק נזרעת
# אידמפוטנטית. שתי רשימות היו מתפצלות ביום שמישהו יוסיף סוג.
from .seed import TASK_TYPES


def run(cur) -> int:
    added = 0
    cur.execute("select id from firms order by created_at")
    for firm in cur.fetchall():
        for name, code, position in TASK_TYPES:
            cur.execute(
                "select 1 from task_types where firm_id = %s and code = %s",
                (firm["id"], code),
            )
            if cur.fetchone():
                continue
            cur.execute(
                """insert into task_types (firm_id, name, code, position)
                   values (%s, %s, %s, %s)
                   on conflict (firm_id, code) do nothing""",
                (firm["id"], name, code, position),
            )
            added += cur.rowcount
    return added


def main() -> int:
    try:
        with cursor(commit=True) as cur:
            added = run(cur)
        print("סוגי משימות שנוספו: %d" % added)
        return 0
    except MissingKey as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
