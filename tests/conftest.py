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
