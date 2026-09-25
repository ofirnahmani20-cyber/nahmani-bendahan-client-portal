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


@pytest.fixture
def temp_case():
    """
    תיק זמני במשרד הזרע, שנמחק בסוף הבדיקה.

    בדיקות שמשנות מצב לא ירוצו על תיקי ההדגמה: הן היו גורמות
    לנתוני הדמו להיסחף בכל הרצה, ולבלבל כל מי שבודק ידנית
    בדפדפן באותו רגע.
    """
    from server.db.pool import cursor as _cursor
    import uuid as _uuid

    marker = _uuid.uuid4().hex[:8]
    with _cursor(commit=True) as cur:
        cur.execute("""select c.firm_id, c.client_id, c.claim_type_id
                         from cases c limit 1""")
        base = cur.fetchone()
        cur.execute("""insert into cases (firm_id, client_id, claim_type_id,
                                          case_number, opened_at)
                       values (%s, %s, %s, %s, current_date) returning id""",
                    (base["firm_id"], base["client_id"], base["claim_type_id"],
                     "TMP-%s" % marker))
        case_id = cur.fetchone()["id"]
        cur.execute("""insert into case_documents (firm_id, case_id, name,
                                                   guidance, position)
                       values (%s, %s, 'מסמך זמני', 'הנחיה זמנית', 1)
                       returning id""", (base["firm_id"], case_id))
        document_id = cur.fetchone()["id"]

    yield {"case_id": case_id, "document_id": document_id,
           "firm_id": base["firm_id"], "claim_type_id": base["claim_type_id"]}

    with _cursor(commit=True) as cur:
        cur.execute("delete from case_conversation where case_id = %s", (case_id,))
        cur.execute("""delete from reminder_rules where requirement_id in
                       (select id from case_requirements where case_id = %s)""", (case_id,))
        cur.execute("""delete from message_deliveries where requirement_id in
                       (select id from case_requirements where case_id = %s)""", (case_id,))
        cur.execute("update case_documents set requirement_id = null where case_id = %s", (case_id,))
        cur.execute("delete from case_requirements where case_id = %s", (case_id,))
        cur.execute("delete from case_tasks where case_id = %s", (case_id,))
        cur.execute("delete from case_stage_events where case_id = %s", (case_id,))
        cur.execute("delete from messages where case_id = %s", (case_id,))
        cur.execute("delete from document_files where document_id = %s", (document_id,))
        cur.execute("delete from case_documents where case_id = %s", (case_id,))
        cur.execute("delete from audit_log where case_id = %s", (case_id,))
        cur.execute("delete from cases where id = %s", (case_id,))
