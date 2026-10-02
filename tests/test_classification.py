"""
שלב 4 של Layer 2: סיווג מסמכים לפי כללים דטרמיניסטיים.

מה ננעל כאן (ההחלטות מ-2026-10-02)
------------------------------------
1. סוג המסמך נפרד מההתאמה לדרישה. סיווג אינו מאשר מסמך ואינו
   משלים דרישה - case_documents.status אינו משתנה, לא בסיווג ולא
   באישור הסיווג.
2. אין בחירה שרירותית: שני סוגים קרובים -> דו-משמעי; עמודים סותרים
   -> מעורב או דו-משמעי; בלי כותרת -> לא ידוע. המסד אוכף שסוג מוצע
   קיים רק בהחלטה "ברור".
3. מספר מ-OCR (מספר טופס) תומך בלבד - לעולם אינו מכריע.
4. נימוק = ביטויים מהמילון, מיקום ומשקל. אין טקסט מהמסמך.
5. רק can_view_medical; משרד אחר 404; הלקוח - שום נתיב.
6. אישור מסמך שאינו תואם לדרישה: אזהרה (409), ואישור מפורש עובר.

הכול על טקסט סינתטי ועל tests/fixtures/synthetic.
"""

import json
import time

import psycopg
import pytest
from starlette.testclient import TestClient

from server import (audit, classify, classify_rules, doc_taxonomy, policy, processing,
                    worker)
from server.app import app
from server.db.pool import cursor
from server.db import seed_templates

from .test_extraction import FIX, read
from .test_firm_isolation import second_firm  # noqa: F401  (fixture)
from .test_processing import OTHER_STAFF, STAFF, enqueue, login, make_file
from .test_uploads import LOCAL


# שורה נייטרלית: טקסט קצר מ-40 תווים הוא "לא קריא" עוד לפני חיפוש כותרת,
# ובדיקה של כלל מסוים צריכה להגיע אליו.
STAMP = "\nמסמך סינתטי לבדיקה בלבד - אינו מסמך רפואי אמיתי"


def page(text, n=1, source="layer", confidence=None):
    return {"page": n, "text": text + STAMP, "source": source, "confidence": confidence}


def decide(*pages):
    return classify.classify_document(list(pages))


DISCHARGE = ("סיכום אשפוז\nמרכז רפואי סינתטי\nתאריך קבלה: 1/1/2026 תאריך שחרור: 5/1/2026\n"
             "מהלך האשפוז: טיפול שמרני.")
EMG = "בדיקת EMG והולכה עצבית\nמכון סינתטי\nנמצאה האטה בהולכה של העצב המדיאני."


@pytest.fixture
def api():
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1") as c:
        yield c


# ================================================================
#  1. רשימת הסוגים והמילון
# ================================================================

def test_every_rule_points_to_a_real_kind_and_subtype():
    for kind, spec in classify_rules.RULES.items():
        assert doc_taxonomy.is_valid(kind), kind
        for sub in spec.get("subtypes", {}):
            assert doc_taxonomy.is_valid("%s.%s" % (kind, sub)), (kind, sub)


def test_every_kind_without_rules_is_a_deliberate_fallback():
    """סוג בלי כללים = "אחר" בקטגוריה - נבחר רק בידי אדם."""
    base = {code for code, row in doc_taxonomy.ALL.items() if row["parent"] is None}
    assert base - set(classify_rules.RULES) == {"medical_other", "nii_other", "legal_other"}


def test_template_mapping_uses_only_real_kinds():
    for name, kinds in seed_templates.KINDS_BY_NAME.items():
        for k in kinds:
            assert doc_taxonomy.is_valid(k), (name, k)


def test_db_kind_table_is_in_sync_with_code():
    with cursor() as cur:
        cur.execute("select code from document_kinds where active")
        assert {r["code"] for r in cur.fetchall()} == set(doc_taxonomy.ALL)


@pytest.mark.parametrize("accepted,kind,expected", [
    (["imaging"], "imaging.mri", "match"),
    (["imaging.mri"], "imaging.ct", "mismatch"),
    (["nii_form.250"], "nii_form", "undetermined"),       # סוג נכון, תת-סוג לא נקבע
    (["hospital_discharge"], "nerve_conduction", "mismatch"),
    (["expert_opinion", "treating_opinion"], "treating_opinion", "match"),
    (["imaging"], None, "undetermined"),
])
def test_type_and_requirement_are_separate_questions(accepted, kind, expected):
    assert doc_taxonomy.accepts(accepted, kind) == expected


def test_unmapped_requirement_is_not_a_mismatch():
    assert processing.requirement_match([], "nerve_conduction") == "unmapped"


# ================================================================
#  2. המנוע
# ================================================================

def test_clear_classification_with_reasons_from_the_dictionary_only():
    r = decide(page(DISCHARGE))
    assert (r["decision"], r["kind"]) == ("clear", "hospital_discharge")
    dictionary = {p for spec in classify_rules.RULES.values()
                  for role in ("title", "support", "negative") for p in spec.get(role, [])}
    for reason in r["reasons"]:
        assert reason["phrase"] in dictionary | {"מספר טופס"}, "טקסט מהמסמך דלף לנימוק"
        assert {"rule", "page", "line", "zone", "weight"} <= set(reason)


def test_title_inside_a_sentence_is_not_a_title():
    """המדידה הראשונה: "בוצעה בדיקת MRI" בשורה 4 סיווג סיכום אשפוז כ-MRI."""
    text = ("מחלקה פנימית\nשם: סינתטי\nבמהלך האשפוז בוצעה בדיקת MRI שהדגימה בלט.\n"
            "מומלץ להשלים חוות דעת רפואית")
    r = decide(page(text))
    assert r["decision"] == "unknown"
    assert r["kind"] is None


def test_title_after_a_separator_counts():
    r = decide(page("מדינת ישראל - תעודת זהות\nשם: סינתטי\nתאריך לידה: 1/1/1990 מקום לידה: חיפה"))
    assert r["kind"] == "id_card"


def test_title_outside_the_header_zone_is_not_decisive():
    filler = "\n".join("שורה %d" % i for i in range(10))
    assert decide(page(filler + "\nסיכום אשפוז"))["decision"] == "unknown"


def test_form_number_alone_never_classifies():
    """מספר טופס לבדו - כלום. OCR מחליף ספרות (25 -> 75)."""
    r = decide(page("7801\nמספר 7801\nטופס מספר 7801 ו-250 ו-1811", source="ocr", confidence=95))
    assert r["decision"] == "unknown"


def test_form_number_only_supports_a_textual_title():
    with_number = decide(page("תביעה לקצבת נכות כללית\nפרטי התובע\nטופס 7801"))
    without = decide(page("תביעה לקצבת נכות כללית\nפרטי התובע\nטופס"))
    assert with_number["kind"] == without["kind"] == "nii_form.7801"
    assert with_number["score"] - without["score"] == classify_rules.WEIGHTS["number"]


def test_two_close_kinds_are_ambiguous_not_a_pick():
    """'הודעה על דחיית תביעה' היא כותרת גם של ביטוח לאומי וגם של חברת ביטוח."""
    r = decide(page("הודעה על דחיית תביעה\nלכבוד סינתטי\nתביעתך נדחתה."))
    assert r["decision"] == "ambiguous"
    assert r["kind"] is None
    assert {c["kind"] for c in r["candidates"][:2]} == {"nii_decision", "insurer_decision"}


def test_conflicting_pages_go_to_a_human():
    r = decide(page(DISCHARGE, 1), page("הודעה על דחיית תביעה\nתביעתך נדחתה.", 2))
    assert r["decision"] == "ambiguous" and r["kind"] is None


def test_two_documents_in_one_file_are_mixed_with_a_kind_per_page():
    r = decide(page(DISCHARGE, 1), page(EMG, 2))
    assert r["decision"] == "mixed" and r["kind"] is None
    assert [p["kind"] for p in r["page_kinds"]] == ["hospital_discharge", "nerve_conduction"]


def test_continuation_page_is_not_a_second_document():
    r = decide(page(DISCHARGE, 1),
               page("המשך מהלך האשפוז:\nבוצעו בדיקות דם חוזרות, ללא ממצא חריג.", 2))
    assert (r["decision"], r["kind"]) == ("clear", "hospital_discharge")
    assert r["page_kinds"][1]["decision"] == "continuation"


def test_unreadable_short_or_low_confidence():
    short = {"page": 1, "text": "א ב", "source": "ocr", "confidence": 90}
    assert decide(short)["decision"] == "unreadable"
    assert decide(page(DISCHARGE, source="ocr", confidence=30))["decision"] == "unreadable"


def test_approximate_title_only_for_ocr_and_marked():
    noisy = DISCHARGE.replace("סיכום אשפוז", "סיכום אשפו")
    ocr = decide(page(noisy, source="ocr", confidence=90))
    assert ocr["kind"] == "hospital_discharge"
    assert any(r.get("approximate") for r in ocr["reasons"])
    layer = decide(page(noisy, source="layer"))
    assert layer["decision"] == "unknown", "התאמה חלקית הוחלה על שכבת טקסט"


def test_subtype_only_when_unambiguous():
    assert decide(page("ועדה רפואית לעררים\nהוועדה קבעה נכות"))["kind"] == "nii_committee.appeal"
    assert decide(page("פרוטוקול ועדה רפואית\nהוועדה קבעה נכות"))["kind"] == "nii_committee"


def test_deterministic():
    a, b = decide(page(DISCHARGE), page(EMG, 2)), decide(page(EMG, 2), page(DISCHARGE))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["rules_version"] == classify_rules.RULES_VERSION


def test_hostile_text_cannot_stall_the_classifier():
    nasty = ("סיכום " * 20000 + "\n") * 5 + ("א" * 100000)
    started = time.monotonic()
    decide(page(nasty), page(nasty, 2))
    assert time.monotonic() - started < 10


# ================================================================
#  3. המסד
# ================================================================

def _insert_classification(fid, firm_id, **over):
    values = dict(decision="clear", suggested_type="payslip", status="suggested",
                  confirmed_type=None)
    values.update(over)
    with cursor(commit=True) as cur:
        cur.execute(
            """insert into document_classifications
                 (firm_id, file_id, decision, suggested_type, score, margin, status,
                  confirmed_type, engine)
               values (%s, %s, %s, %s, 10, 10, %s, %s, 'test')""",
            (firm_id, fid, values["decision"], values["suggested_type"], values["status"],
             values["confirmed_type"]))


@pytest.mark.parametrize("over", [
    {"decision": "ambiguous"},                       # סוג מוצע בלי "ברור"
    {"decision": "clear", "suggested_type": None},   # "ברור" בלי סוג
    {"suggested_type": "no_such_kind"},              # סוג לא קיים
])
def test_db_refuses_arbitrary_or_invalid_suggestion(temp_case, over):
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation)):
        _insert_classification(fid, temp_case["firm_id"], **over)


def test_db_refuses_confirmation_without_type_or_reviewer(temp_case):
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_classification(fid, temp_case["firm_id"], status="confirmed")


def test_db_refuses_unknown_accepted_kind(temp_case):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with cursor(commit=True) as cur:
            cur.execute("update case_documents set accepted_kinds = '{no_such_kind}'"
                        " where id = %s", (temp_case["document_id"],))


# ================================================================
#  4. worker + אחסון: סיווג אינו נוגע בסטטוס המסמך
# ================================================================

def _doc_status(document_id):
    with cursor() as cur:
        cur.execute("select status, reviewed_by_user_id from case_documents where id = %s",
                    (document_id,))
        return dict(cur.fetchone())


def _accept(document_id, kinds):
    with cursor(commit=True) as cur:
        cur.execute("update case_documents set accepted_kinds = %s where id = %s",
                    (kinds, document_id))


@pytest.fixture
def classified(temp_case):
    """emg-1.pdf (שכבת טקסט) בדרישה שמבקשת סיכום אשפוז - אי-התאמה מכוונת."""
    _accept(temp_case["document_id"], ["hospital_discharge"])
    with cursor(commit=True) as cur:
        cur.execute("update case_documents set status = 'pending_review' where id = %s",
                    (temp_case["document_id"],))
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    before = _doc_status(temp_case["document_id"])
    enqueue(fid, temp_case["firm_id"])
    worker.process_one("t", [fid])
    return {"file_id": fid, "document_id": str(temp_case["document_id"]),
            "firm_id": temp_case["firm_id"], "before": before}


def _row(fid):
    with cursor() as cur:
        cur.execute("select * from document_classifications where file_id = %s", (fid,))
        return cur.fetchone()


def test_worker_classifies_and_flags_requirement_mismatch(classified):
    row = _row(classified["file_id"])
    assert (row["decision"], row["suggested_type"]) == ("clear", "nerve_conduction")
    assert row["requirement_match"] == "mismatch"
    assert row["status"] == "suggested"
    assert row["rules_version"] == classify_rules.RULES_VERSION


def test_classification_never_touches_the_document_status(classified, api):
    assert _doc_status(classified["document_id"]) == classified["before"]
    login(api)
    url = "/api/office/documents/%s/files/%s/classification" % (
        classified["document_id"], classified["file_id"])
    r = api.post(url + "/review", json={"action": "confirm"},
                 headers={"X-CSRF-Token": api.cookies.get("portal_csrf")})
    assert r.status_code == 200, r.text
    assert _doc_status(classified["document_id"]) == classified["before"], (
        "אישור סיווג שינה את סטטוס המסמך")


def test_reclassification_keeps_the_human_decision(classified):
    processing.review_classification(classified["file_id"], classified["firm_id"],
                                     _staff_id(), "choose", "hospital_discharge")
    processing.reclassify(classified["file_id"], classified["firm_id"])
    row = _row(classified["file_id"])
    assert row["status"] == "confirmed" and row["confirmed_type"] == "hospital_discharge"
    assert row["suggested_type"] == "nerve_conduction"
    assert row["differs_from_confirmed"] is True


def _staff_id(email=STAFF[0]):
    with cursor() as cur:
        cur.execute("select id from users where email = %s", (email,))
        return cur.fetchone()["id"]


# ================================================================
#  5. API: הרשאות, נימוק, יומן
# ================================================================

def _url(c):
    return "/api/office/documents/%s/files/%s/classification" % (c["document_id"], c["file_id"])


def test_medical_user_sees_suggestion_reasons_and_mismatch(api, classified):
    login(api)
    r = api.get(_url(classified))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "clear" and body["suggested"]["code"] == "nerve_conduction"
    assert body["requirement"]["match"] == "mismatch"
    assert body["requirement"]["accepted"][0]["code"] == "hospital_discharge"
    assert body["reasons"] and all("phrase" in x and "line" in x for x in body["reasons"])
    # "נמצאה האטה בהולכה" מופיע במסמך ולא במילון - אסור שיחזור.
    assert "האטה" not in json.dumps(body, ensure_ascii=False), (
        "קטע חופשי מהמסמך הוחזר בנימוק")

    with cursor() as cur:
        cur.execute("""select metadata from audit_log where action = 'office.classification_viewed'
                        order by id desc limit 1""")
        assert set(cur.fetchone()["metadata"]) <= audit.SAFE_METADATA_KEYS


def test_without_medical_permission_403(api, classified):
    with cursor(commit=True) as cur:
        cur.execute("update users set can_view_medical = false where email = %s",
                    (OTHER_STAFF[0],))
    try:
        login(api, OTHER_STAFF)
        assert api.get(_url(classified)).status_code == 403
        assert api.get("/api/office/document-kinds").status_code == 403
        assert api.get("/api/me").json()["canViewMedical"] is False
    finally:
        with cursor(commit=True) as cur:
            cur.execute("update users set can_view_medical = true where email = %s",
                        (OTHER_STAFF[0],))


def test_both_partners_have_the_permission(api):
    for who in (STAFF, OTHER_STAFF):
        login(api, who)
        assert api.get("/api/me").json()["canViewMedical"] is True


def test_other_firm_404_even_with_permission(api, classified, second_firm):
    with cursor(commit=True) as cur:
        cur.execute("update users set can_view_medical = true where id = %s",
                    (second_firm["user_id"],))
    login(api, (second_firm["email"], second_firm["password"]))
    assert api.get(_url(classified)).status_code == 404
    r = api.post(_url(classified) + "/review", json={"action": "confirm"},
                 headers={"X-CSRF-Token": api.cookies.get("portal_csrf")})
    assert r.status_code == 404


def test_anonymous_is_refused(api, classified):
    assert api.get(_url(classified)).status_code == 401


def test_review_actions_and_audit(api, classified):
    login(api)
    csrf = {"X-CSRF-Token": api.cookies.get("portal_csrf")}
    r = api.post(_url(classified) + "/review", json={"action": "choose", "kind": "nope"},
                 headers=csrf)
    assert r.status_code == 400
    r = api.post(_url(classified) + "/review",
                 json={"action": "choose", "kind": "hospital_discharge"}, headers=csrf)
    assert r.status_code == 200 and r.json()["confirmed"]["code"] == "hospital_discharge"
    after = api.get(_url(classified)).json()
    assert after["status"] == "confirmed" and after["requirement"]["match"] == "match"

    r = api.post(_url(classified) + "/review", json={"action": "reject"}, headers=csrf)
    assert r.json()["status"] == "rejected"
    with cursor() as cur:
        cur.execute("""select metadata from audit_log where action = 'office.classification_reviewed'
                        order by id desc limit 1""")
        meta = cur.fetchone()["metadata"]
    assert set(meta) <= audit.SAFE_METADATA_KEYS and meta["decision"] == "reject"
    assert "kind" not in meta, "קוד סוג רפואי ביומן שכל הצוות קורא"


def test_audit_log_reader_without_medical_permission_sees_no_document_kind(api, classified):
    """
    ממצא סקירת האבטחה: היומן פתוח ל-require_staff, ואישור סיווג כתב אליו
    קוד סוג ("psychiatric"). איש צוות בלי can_view_medical למד ממנו מידע רפואי.
    """
    login(api)
    api.post(_url(classified) + "/review", json={"action": "choose", "kind": "psychiatric"},
             headers={"X-CSRF-Token": api.cookies.get("portal_csrf")})
    with cursor(commit=True) as cur:
        cur.execute("update users set can_view_medical = false where email = %s",
                    (OTHER_STAFF[0],))
    try:
        other = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
        login(other, OTHER_STAFF)
        with cursor() as cur:
            cur.execute("select case_id from case_documents where id = %s",
                        (classified["document_id"],))
            case_id = cur.fetchone()["case_id"]
        log = other.get("/api/office/audit-log?case_id=%s" % case_id)
        assert log.status_code == 200
        dump = json.dumps(log.json(), ensure_ascii=False)
        for code in doc_taxonomy.ALL:
            assert '"%s"' % code not in dump, "קוד סוג %s חשוף ביומן" % code
    finally:
        with cursor(commit=True) as cur:
            cur.execute("update users set can_view_medical = true where email = %s",
                        (OTHER_STAFF[0],))


def test_confirm_is_refused_when_there_is_no_clear_suggestion(temp_case):
    fid = make_file(temp_case, data=read("emg-1.pdf"))
    _insert_classification(fid, temp_case["firm_id"], decision="ambiguous", suggested_type=None)
    with pytest.raises(ValueError):
        processing.review_classification(fid, temp_case["firm_id"], _staff_id(), "confirm")


# ================================================================
#  6. אישור מסמך שאינו תואם לדרישה
# ================================================================

def _approve(api, document_id, **extra):
    return api.post("/api/office/documents/%s/review" % document_id,
                    json=dict({"decision": "approve"}, **extra),
                    headers={"X-CSRF-Token": api.cookies.get("portal_csrf")})


def test_mismatch_warns_and_explicit_approval_goes_through(api, classified):
    login(api)
    r = _approve(api, classified["document_id"])
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "classification_mismatch"
    assert _doc_status(classified["document_id"])["status"] == "pending_review"

    r = _approve(api, classified["document_id"], acknowledge_mismatch=True)
    assert r.status_code == 200, r.text
    assert _doc_status(classified["document_id"])["status"] == "approved"
    with cursor() as cur:
        cur.execute("""select metadata from audit_log where action = 'office.document_reviewed'
                        and entity_id = %s order by id desc limit 1""",
                    (classified["document_id"],))
        assert cur.fetchone()["metadata"]["mismatch_acknowledged"] is True


def test_matching_or_unmapped_document_needs_no_acknowledgement(api, classified):
    login(api)
    _accept(classified["document_id"], ["nerve_conduction"])
    assert _approve(api, classified["document_id"]).status_code == 200
    with cursor(commit=True) as cur:
        cur.execute("update case_documents set status = 'pending_review' where id = %s",
                    (classified["document_id"],))
    _accept(classified["document_id"], [])
    assert _approve(api, classified["document_id"]).status_code == 200


def test_requirement_from_template_inherits_accepted_kinds(api, temp_case):
    login(api)
    with cursor() as cur:
        cur.execute("""select t.id, t.accepted_kinds from required_document_templates t
                        where t.claim_type_id = %s and t.accepted_kinds <> '{}' limit 1""",
                    (temp_case["claim_type_id"],))
        t = cur.fetchone()
    r = api.post("/api/office/cases/%s/documents" % temp_case["case_id"],
                 json={"name": "דרישה מהקטלוג", "guidance": "הנחיה סינתטית",
                       "template_id": str(t["id"])},
                 headers={"X-CSRF-Token": api.cookies.get("portal_csrf")})
    assert r.status_code == 200, r.text
    with cursor() as cur:
        cur.execute("select accepted_kinds from case_documents where id = %s",
                    (r.json()["documentId"],))
        assert list(cur.fetchone()["accepted_kinds"]) == list(t["accepted_kinds"])


# ================================================================
#  7. METADATA_ONLY והלקוח
# ================================================================

def test_classification_never_reaches_the_client_or_the_ai():
    assert policy.POLICY_MODE == "METADATA_ONLY"
    root = worker.ROOT / "server"
    for name in ("api_client.py", "policy.py", "app.py"):
        src = (root / name).read_text(encoding="utf-8")
        for needle in ("document_classifications", "classify", "requirement_match"):
            assert needle not in src, "%s מופיע ב-%s" % (needle, name)
