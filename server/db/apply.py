"""
apply.py - מריץ את הסכמה ואת נתוני ההדגמה.

    python -m server.db.apply            סכמה + זריעה
    python -m server.db.apply --schema   סכמה בלבד

ההרצה חוזרת על עצמה: אפשר להריץ שוב על מסד קיים.
"""

import pathlib
import sys

from .connect import connect, MissingKey

HERE = pathlib.Path(__file__).resolve().parent
SCHEMA = HERE / "schema.sql"


def apply_schema(conn) -> None:
    """
    הקובץ נקרא ב-Python ולא דרך \\i של psql, כי נתיב הפרויקט
    מכיל עברית ו-psql לא מצליח לפענח אותו.
    """
    sql = SCHEMA.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def main(argv) -> int:
    schema_only = "--schema" in argv

    try:
        with connect() as conn:
            apply_schema(conn)
            print(f"סכמה הוחלה  ({SCHEMA.name})")

            if schema_only:
                return 0

            from . import seed
            counts = seed.run(conn)
            print("נתוני הדגמה נזרעו:")
            for key in sorted(counts):
                print(f"  {key:<16} {counts[key]}")
        return 0

    except MissingKey as e:
        print(f"\n{e}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
