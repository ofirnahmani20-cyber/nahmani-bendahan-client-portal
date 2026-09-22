"""
הקבצים הרגישים אינם ניתנים להגשה.

עד 13.09 השרת הגיש את שורש הפרויקט דרך StaticFiles, ולכן /.env,
/server/policy.py ו-/docs/* היו כולם זמינים ב-HTTP. הבדיקות כאן
נכשלות אם מישהו יחזיר את המצב הזה.
"""

import pytest

SENSITIVE_PATHS = [
    "/.env",
    "/.env.example",
    "/server/app.py",
    "/server/policy.py",
    "/server/db/schema.sql",
    "/server/db/connect.py",
    "/requirements.txt",
    "/.gitignore",
    "/scripts/pg-local.ps1",
    "/README.md",
]


@pytest.mark.parametrize("path", SENSITIVE_PATHS)
def test_sensitive_path_is_not_served(client, path):
    response = client.get(path)
    assert response.status_code == 404, (
        "הנתיב %s הוגש עם קוד %s - קובץ רגיש אינו אמור להיות נגיש"
        % (path, response.status_code)
    )


def test_traversal_out_of_assets_is_blocked(client):
    response = client.get("/assets/../server/policy.py")
    assert response.status_code in (404, 400)


@pytest.mark.parametrize("page", ["/", "/index.html", "/dashboard.html",
                                  "/admin.html", "/info.html"])
def test_public_pages_still_served(client, page):
    """ההקשחה לא שברה את האתר."""
    response = client.get(page)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_assets_still_served(client):
    response = client.get("/assets/css/style.css")
    assert response.status_code == 200


def test_html_pages_are_not_cached_by_the_browser(client):
    """
    דפי ה-HTML מחזיקים את חותמי הגרסה של ה-CSS וה-JS. בלי
    הנחיית קאש הדפדפן הגיש דף ישן, הדף הפנה לחותמים ישנים,
    וקידום החותם לעולם לא הגיע למשתמש. זה הסתיר שלושה תיקונים
    שכבר היו בקוד, ולכן יש לזה בדיקה.
    """
    for page in ["/", "/admin.html", "/index.html", "/dashboard.html", "/info.html"]:
        r = client.get(page)
        assert r.status_code == 200, page
        assert r.headers.get("cache-control") == "no-cache", (
            "לדף %s אין הנחיית no-cache" % page)


def test_versioned_assets_stay_cacheable(client):
    """הנכסים נושאים חותם גרסה, ולכן אין סיבה למנוע מהם קאש."""
    r = client.get("/assets/js/admin.js")
    assert r.status_code == 200
    assert r.headers.get("cache-control") != "no-cache"
