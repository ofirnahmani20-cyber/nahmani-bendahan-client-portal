"""
keygen.py - מייצר זוג מפתחות לפיתוח ומדפיס אותם.

    python -m server.db.keygen

המפתחות אינם נשמרים לשום קובץ. יש להעתיק אותם ל-.env המקומי,
שאינו נכנס ל-git.

⚠️ מפתחות ייצור אינם נוצרים כאן ואינם עוברים דרך המסך. הם
   נוצרים בסביבת הייצור עצמה ונשמרים במנהל סודות.
   החלפת מפתח ה-HMAC אחרי שיש נתונים מחייבת חישוב מחדש של
   national_id_lookup לכל הלקוחות - זה לא שינוי הפיך בקלות.
"""

from .connect import new_keys

if __name__ == "__main__":
    # הפלט הוא ASCII בלבד במכוון: הוא מופנה היישר אל .env,
    # והקונסולה של Windows כותבת עברית ב-cp1255 - מה שהופך
    # את הקובץ לבלתי קריא כ-UTF-8.
    keys = new_keys()
    print("# dev keys only - do not use in production")
    for name, value in keys.items():
        print(f"{name}={value}")
