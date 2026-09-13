"""עזרי בדיקה משותפים."""

import os
import pathlib
import sys

import pytest

# שורש הפרויקט ב-sys.path כדי ש-`server` יהיה ניתן לייבוא בלי התקנה.
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# הבדיקות אינן צריכות מפתחות אמיתיים, אך connect.py דורש נוכחות.
os.environ.setdefault("PORTAL_ID_HMAC_KEY", "test-hmac-key-not-secret")
os.environ.setdefault("PORTAL_ID_ENC_KEY", "TESTKEYTESTKEYTESTKEYTESTKEYTESTKEYTESTKEY0=")


@pytest.fixture
def client():
    """TestClient עם PORTAL_MODE=demo (ברירת המחדל)."""
    from starlette.testclient import TestClient
    from server.app import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_throttles():
    """
    הגבלות הקצב אמיתיות ונאכפות לפי IP. כל הבדיקות מגיעות מאותו
    IP, ולכן בלי איפוס הבדיקה השתים-עשרה נחסמת ב-429 - כלומר
    המנגנון עובד, אבל הוא מסתיר את מה שבאמת נבדק.
    """
    try:
        from server import api_auth
        api_auth._attempts.clear()
    except Exception:
        pass
    try:
        from server import app as app_module
        app_module._ai_calls.clear()
    except Exception:
        pass
    yield
