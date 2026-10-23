"""
החלטה אוטומטית על מסמך - server/doc_decision.py (docs/10, 23.10).

רק שני מקרים חד-משמעיים: "לא קריא" נדחה, "ברור ותואם וקריא היטב"
מאושר. כל השאר נשאר לאדם. החלטת אדם דורסת, והמשרד יכול לכבות.

דורש מסד נתונים חי ו-Tesseract (לבדיקות מקצה לקצה).
"""

import io

import pytest
from PIL import Image
from starlette.testclient import TestClient

from server import doc_decision, worker
from server.app import app
from server.db.pool import cursor

from .test_extraction import read
from .test_processing import LOCAL, STAFF, enqueue, login, make_file

CLEAR = {"decision": "clear", "kind": "nerve_conduction", "unreadable_pages": []}
LAYER = [{"page": 1, "source": "layer", "confidence": None}]


# ================================================================
#  הכללים - בלי מסד
# ================================================================

def test_clear_matching_text_layer_is_eligible():
    assert doc_decision.eligible_for_approval(CLEAR, ["nerve_conduction"], LAYER)


@pytest.mark.parametrize("result, accepted, pages", [
    (dict(CLEAR, decision="ambiguous"), ["nerve_conduction"], LAYER),
    (dict(CLEAR, decision="mixed"), ["nerve_conduction"], LAYER),
    (dict(CLEAR, decision="unknown", kind=None), ["nerve_conduction"], LAYER),
    (CLEAR, ["hospital_discharge"], LAYER),                 # לא תואם
    (CLEAR, [], LAYER),                                     # דרישה לא ממופה
    (dict(CLEAR, kind="nii_form"), ["nii_form.250"], LAYER),  # תת-סוג לא נקבע
    (dict(CLEAR, unreadable_pages=[2]), ["nerve_conduction"], LAYER),
    (CLEAR, ["nerve_conduction"], [{"page": 1, "source": "ocr", "confidence": 74.9}]),
    (CLEAR, ["nerve_conduction"], [{"page": 1, "source": "ocr", "confidence": None}]),
])
def test_anything_less_than_certain_is_not_eligible(result, accepted, pages):
    assert not doc_decision.eligible_for_approval(result, accepted, pages)


def test_high_confidence_ocr_is_eligible():
    pages = [{"page": 1, "source": "ocr", "confidence": 88.0},
             {"page": 2, "source": "layer", "confidence": None}]
    assert doc_decision.eligible_for_approval(CLEAR, ["nerve_conduction"], pages)


# ================================================================
#  מול המסד
# ================================================================

@pytest.fixture
def pending(temp_case):
    with cursor(commit=True) as cur:
        cur.execute("""update case_documents set status = 'pending_review',
                              accepted_kinds = '{nerve_conduction}'
                        where id = %s""", (temp_case["document_id"],))
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    yield dict(temp_case, file_id=fid)
    with cursor(commit=True) as cur:
        cur.execute("update firms set auto_document_decisions = true where id = %s",
                    (temp_case["firm_id"],))


def _doc(document_id):
    with cursor() as cur:
        cur.execute("""select status, reject_reason, reviewed_by_user_id, decided_by_system
                         from case_documents where id = %s""", (document_id,))
        return cur.fetchone()


def test_decide_approves_and_marks_it_automatic(pending):
    assert doc_decision.decide(pending["file_id"], pending["firm_id"], CLEAR, LAYER) == "approved"
    row = _doc(pending["document_id"])
    assert row["status"] == "approved" and row["decided_by_system"] == "approved"
    assert row["reviewed_by_user_id"] is None


def test_decide_rejects_unreadable_with_guidance(pending):
    result = {"decision": "unreadable", "kind": None, "unreadable_pages": [1]}
    assert doc_decision.decide(pending["file_id"], pending["firm_id"], result, LAYER) == "unreadable"
    row = _doc(pending["document_id"])
    assert row["status"] == "rejected" and row["decided_by_system"] == "unreadable"
    assert "לצלם" in row["reject_reason"] or "להעלות שוב" in row["reject_reason"]


def test_doubt_stays_with_a_person(pending):
    result = dict(CLEAR, decision="ambiguous")
    assert doc_decision.decide(pending["file_id"], pending["firm_id"], result, LAYER) is None
    assert _doc(pending["document_id"])["status"] == "pending_review"


def test_never_overrides_a_human_decision(pending):
    with cursor(commit=True) as cur:
        cur.execute("update case_documents set status = 'rejected', reject_reason = 'חסר עמוד שני'"
                    " where id = %s", (pending["document_id"],))
    assert doc_decision.decide(pending["file_id"], pending["firm_id"], CLEAR, LAYER) is None
    assert _doc(pending["document_id"])["status"] == "rejected"


def test_replaced_file_is_not_decided(pending):
    with cursor(commit=True) as cur:
        cur.execute("update document_files set is_current = false where id = %s",
                    (pending["file_id"],))
    assert doc_decision.decide(pending["file_id"], pending["firm_id"], CLEAR, LAYER) is None


def test_firm_can_turn_it_off(pending):
    with cursor(commit=True) as cur:
        cur.execute("update firms set auto_document_decisions = false where id = %s",
                    (pending["firm_id"],))
    assert doc_decision.decide(pending["file_id"], pending["firm_id"], CLEAR, LAYER) is None
    assert _doc(pending["document_id"])["status"] == "pending_review"


def test_audit_holds_no_document_kind(pending):
    doc_decision.decide(pending["file_id"], pending["firm_id"], CLEAR, LAYER)
    with cursor() as cur:
        cur.execute("""select metadata from audit_log where action = 'system.document_auto_decided'
                        order by id desc limit 1""")
        meta = cur.fetchone()["metadata"]
    assert meta["outcome"] == "approved"
    assert "nerve" not in str(meta)


def test_staff_review_overrides_and_clears_the_automatic_mark(pending):
    doc_decision.decide(pending["file_id"], pending["firm_id"], CLEAR, LAYER)
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as api:
        login(api, STAFF)
        case = api.get("/api/office/cases/%s" % pending["case_id"]).json()
        doc = [d for d in case["documents"] if d["id"] == str(pending["document_id"])][0]
        assert doc["auto"] == "approved"
        r = api.post("/api/office/documents/%s/review" % pending["document_id"],
                     json={"decision": "reject", "reject_reason": "חסרה החתימה של הרופא"},
                     headers={"X-CSRF-Token": api.cookies.get("portal_csrf")})
        assert r.status_code == 200, r.text
    row = _doc(pending["document_id"])
    assert row["status"] == "rejected" and row["decided_by_system"] is None


def test_only_admin_toggles_the_setting(pending):
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as api:
        login(api, ("bendahan@nahmani-bendahan.co.il", "office2026"))
        r = api.post("/api/office/settings/auto-documents", json={"enabled": False},
                     headers={"X-CSRF-Token": api.cookies.get("portal_csrf")})
        assert r.status_code == 403
        login(api, STAFF)
        h = {"X-CSRF-Token": api.cookies.get("portal_csrf")}
        assert api.post("/api/office/settings/auto-documents", json={"enabled": False},
                        headers=h).json()["enabled"] is False
        assert api.get("/api/office/catalogs").json()["autoDocuments"] is False


# ================================================================
#  מקצה לקצה - דרך ה-worker
# ================================================================

def test_worker_auto_approves_a_clear_matching_document(pending):
    enqueue(pending["file_id"], pending["firm_id"])
    worker.process_one("t", [pending["file_id"]])
    row = _doc(pending["document_id"])
    assert (row["status"], row["decided_by_system"]) == ("approved", "approved")


def test_worker_rejects_a_blank_photo(temp_case):
    with cursor(commit=True) as cur:
        cur.execute("""update case_documents set status = 'pending_review',
                              accepted_kinds = '{nerve_conduction}' where id = %s""",
                    (temp_case["document_id"],))
    buf = io.BytesIO()
    Image.new("RGB", (900, 1200), (236, 232, 225)).save(buf, "JPEG", quality=90)
    fid = make_file(temp_case, data=buf.getvalue())
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    row = _doc(temp_case["document_id"])
    assert (row["status"], row["decided_by_system"]) == ("rejected", "unreadable")
