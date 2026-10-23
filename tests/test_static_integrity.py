"""
שלמות הנכסים הסטטיים.

הבדיקה הזאת נולדה מתקלה אמיתית ב-27.09: רצף בריחה שגוי
בכלי עריכה הכניס בייט NUL לתוך ds-admin.css. הקובץ נראה
תקין בעורך, נשמר בלי שגיאה, והוגש ללקוחות - אבל grep כבר
זיהה אותו כקובץ בינארי, והדפדפן היה מפסיק לפרש את ה-CSS
מאותה נקודה ואילך.

שום בדיקה קיימת לא הייתה תופסת את זה: הן בודקות את השרת,
ו-CSS אינו נטען בהן כלל.

מה נבדק כאן, ולמה דווקא זה:

1. בייט NUL - הסימן המובהק לקובץ טקסט שנפגם.
2. איזון סוגריים ב-CSS - סוגר חסר בולע את כל מה שאחריו
   בשקט, בלי שגיאה בקונסולה.
3. חותם הגרסה זהה בארבעת הדפים - חותם שלא סונכרן אומר
   שחלק מהמשתמשים יקבלו CSS ישן מול HTML חדש.
4. אין קישור חיצוני בשכבת העיצוב - ה-CSP אוסר אותו,
   וכישלון כזה שקט לגמרי: הדפדפן פשוט לא טוען.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["index.html", "info.html", "dashboard.html", "admin.html"]


def _text_assets():
    for pattern in ("assets/css/*.css", "assets/js/*.js"):
        yield from sorted(ROOT.glob(pattern))
    for page in PAGES:
        yield ROOT / page


@pytest.mark.parametrize("path", list(_text_assets()), ids=lambda p: p.name)
def test_no_nul_byte(path):
    """
    בייט NUL בקובץ טקסט אינו תקלה תיאורטית - הוא קרה.
    """
    data = path.read_bytes()
    assert b"\x00" not in data, (
        "%s מכיל בייט NUL בהיסט %d - הקובץ נפגם"
        % (path.name, data.find(b"\x00"))
    )


@pytest.mark.parametrize("path", sorted(ROOT.glob("assets/css/*.css")),
                         ids=lambda p: p.name)
def test_css_braces_balance(path):
    """
    סוגר חסר אינו מייצר שגיאה - הוא בולע את שאר הקובץ.
    """
    css = path.read_text(encoding="utf-8")
    assert css.count("{") == css.count("}"), (
        "%s: %d פותחים מול %d סוגרים"
        % (path.name, css.count("{"), css.count("}"))
    )


@pytest.mark.parametrize("path", sorted(ROOT.glob("assets/css/*.css")),
                         ids=lambda p: p.name)
def test_css_has_no_external_url(path):
    """
    ה-CSP הוא script-src 'self' ו-img-src 'self' data:. נכס
    חיצוני אינו נחסם ברעש - הוא פשוט אינו נטען.
    """
    css = path.read_text(encoding="utf-8")
    external = re.findall(r"url\(\s*['\"]?(https?:)?//", css)
    assert not external, "%s מפנה לנכס חיצוני" % path.name


def test_the_version_stamp_is_the_same_on_every_page():
    """
    חותם שלא סונכרן אומר שדפדפן יחזיק CSS ישן מול HTML חדש -
    בדיוק סוג התקלה שנראית כמו באג עיצוב ואינה.
    """
    stamps = {}
    for page in PAGES:
        found = set(re.findall(r"\?v=(\d{14})", (ROOT / page).read_text(encoding="utf-8")))
        assert found, "%s בלי חותם גרסה כלל" % page
        assert len(found) == 1, "%s נושא כמה חותמים: %s" % (page, sorted(found))
        stamps[page] = found.pop()

    assert len(set(stamps.values())) == 1, "החותמים אינם זהים: %s" % stamps


@pytest.mark.parametrize("path", sorted(ROOT.glob("assets/css/*.css")),
                         ids=lambda p: p.name)
def test_every_stylesheet_is_linked_from_a_page(path):
    """
    קובץ סגנון שאיש אינו טוען הוא קוד מת שנראה חי.
    """
    linked = any(path.name in (ROOT / page).read_text(encoding="utf-8")
                 for page in PAGES)
    assert linked, "%s אינו מקושר מאף דף" % path.name
