"""
מחשבונים, זכויות ועדכון אוטומטי - calculators.py, rights.py,
rates_watch.py, api_benefits.py (docs/10, v1 סעיף 6).

כל המספרים כאן סינתטיים ועגולים בכוונה - הם בודקים את הנוסחה, לא
את שיעורי הקצבאות. בקוד עצמו אין שיעור אמיתי אחד.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from starlette.testclient import TestClient

from server import calculators, rates_watch, rights
from server.app import app
from server.db.pool import cursor

from .test_authz import client_login
from .test_processing import LOCAL, STAFF, login

OTHER = ("bendahan@nahmani-bendahan.co.il", "office2026")
D = date(2020, 1, 1)


def rows(**values):
    return {k.replace("__", "."): {"value": Decimal(str(v)), "effective_from": D,
                                   "source_url": None, "label": None}
            for k, v in values.items()}


WORK = dict(pension_rate=0.5, pension_threshold_percent=20, grant_threshold_percent=10,
            grant_multiplier=40)


# ================================================================
#  נוסחאות
# ================================================================

def test_work_injury_monthly_above_threshold_as_a_range():
    r = calculators.compute("nii.work_injury", rows(**WORK), {"percent": 40, "salary": 10000})
    # 10000 × 0.5 × 40% = 2000, ±10%
    assert r["kind"] == "monthly" and (r["low"], r["high"]) == (Decimal("1800"), Decimal("2200"))
    assert r["disclaimer"]


def test_work_injury_grant_between_thresholds():
    r = calculators.compute("nii.work_injury", rows(**WORK), {"percent": 15, "salary": 10000})
    # 10000 × 0.5 × 15% = 750 × 40 = 30000
    assert r["kind"] == "grant" and r["low"] == Decimal("27000")


def test_work_injury_below_grant_threshold_is_none():
    r = calculators.compute("nii.work_injury", rows(**WORK), {"percent": 5, "salary": 10000})
    assert r["kind"] == "none" and r["high"] == Decimal("0")


def test_uncertainty_is_a_parameter_too():
    r = calculators.compute("nii.work_injury", rows(uncertainty_percent=0, **WORK),
                            {"percent": 40, "salary": 10000})
    assert r["low"] == r["high"] == Decimal("2000")


def test_general_disability_table_plus_additions():
    p = rows(pension__74=3000, pension__100=4000, spouse_addition=500, child_addition=200,
             uncertainty_percent=0)
    r = calculators.compute("nii.general_disability", p, {"degree": 74, "spouse": True, "children": 2})
    assert r["low"] == Decimal("3900")


def test_table_calculator_picks_the_option():
    p = rows(option__high=1500, option__low=700, uncertainty_percent=0)
    r = calculators.compute("nii.special_services", p, {"option": "low"})
    assert r["low"] == Decimal("700")


def test_missing_parameters_say_not_configured_never_a_guess():
    with pytest.raises(calculators.NotConfigured) as exc:
        calculators.compute("nii.work_injury", rows(pension_rate=0.5), {"percent": 40, "salary": 1})
    assert "grant_multiplier" in exc.value.missing
    with pytest.raises(calculators.NotConfigured):
        calculators.compute("nii.mobility", {}, {"option": "x"})


@pytest.mark.parametrize("inputs", [{"percent": 140, "salary": 1}, {"percent": "abc", "salary": 1},
                                    {"salary": 1000}])
def test_bad_inputs_are_refused(inputs):
    with pytest.raises(calculators.BadInput):
        calculators.compute("nii.work_injury", rows(**WORK), inputs)


def test_based_on_lists_the_parameters_with_dates():
    r = calculators.compute("nii.work_injury", rows(**WORK), {"percent": 40, "salary": 10000})
    assert {b["key"] for b in r["basedOn"]} == set(WORK)
    assert all(b["from"] == "2020-01-01" for b in r["basedOn"])


# ================================================================
#  זכויות
# ================================================================

PROFILE = {"max_percent": 90, "claim_codes": ["general-disability"], "has_monthly": True}


@pytest.mark.parametrize("conditions, shown", [
    ({}, True),
    ({"min_percent": 90}, True),
    ({"min_percent": 91}, False),
    ({"claim_codes": ["work-injury"]}, False),
    ({"claim_codes": ["general-disability", "work-injury"]}, True),
    ({"needs_monthly": True}, True),
    ({"unknown_condition": 1}, False),
])
def test_right_matching(conditions, shown):
    assert rights.matches(conditions, PROFILE) is shown


def test_condition_without_data_is_not_met():
    assert not rights.matches({"min_percent": 20}, dict(PROFILE, max_percent=None))


# ================================================================
#  עדכון אוטומטי
# ================================================================

@pytest.fixture
def firm():
    """
    מוחק בסוף רק את מה שהבדיקה יצרה. שורות שהיו לפני - למשל נתונים
    שהוזנו ידנית במסד הפיתוח - נשארות. הבדיקות כאן מניחות שאין
    פרמטרים קיימים לאותם מחשבונים; אם יש, הן נכשלות ולא מוחקות.
    """
    with cursor() as cur:
        cur.execute("select id from firms order by created_at limit 1")
        fid = cur.fetchone()["id"]
        cur.execute("select id from calc_parameters where firm_id = %s", (fid,))
        params_before = [r["id"] for r in cur.fetchall()]
        cur.execute("select id from rights_catalog where firm_id = %s", (fid,))
        rights_before = [r["id"] for r in cur.fetchall()]
    yield fid
    with cursor(commit=True) as cur:
        cur.execute("delete from calc_parameters where firm_id = %s and not (id = any(%s))",
                    (fid, params_before))
        cur.execute("delete from rights_catalog where firm_id = %s and not (id = any(%s))",
                    (fid, rights_before))


def _param(firm, key="base_amount", value=1000, url="https://www.btl.gov.il/rates",
           pattern=r"סכום:\s*([\d,]+)"):
    with cursor(commit=True) as cur:
        cur.execute("""insert into calc_parameters (firm_id, calculator, key, value, effective_from,
                                                    source_url, extract_pattern)
                       values (%s, 'mod.idf_disability', %s, %s, %s, %s, %s) returning id""",
                    (firm, key, value, D, url, pattern))
        return cur.fetchone()["id"]


def _latest(firm, key="base_amount"):
    with cursor() as cur:
        cur.execute("""select value, status, origin from calc_parameters
                        where firm_id = %s and key = %s order by created_at desc limit 1""",
                    (firm, key))
        return cur.fetchone()


@pytest.mark.parametrize("url, ok", [
    ("https://www.btl.gov.il/x", True), ("http://www.btl.gov.il/x", False),
    ("https://evil.example.com/x", False), ("https://btl.gov.il.evil.com/x", False),
    ("https://localhost/x", False),
])
def test_only_https_government_sources(url, ok):
    assert rates_watch.allowed_url(url) is ok


def test_small_change_is_applied_automatically(firm, monkeypatch):
    _param(firm, value=1000)
    monkeypatch.setattr(rates_watch, "fetch", lambda url: "<p>סכום: 1,050</p>")
    assert rates_watch.run()["updated"] == 1
    row = _latest(firm)
    assert (row["value"], row["status"], row["origin"]) == (Decimal("1050.0000"), "active", "auto")


def test_large_change_is_held_for_a_person(firm, monkeypatch):
    _param(firm, value=1000)
    monkeypatch.setattr(rates_watch, "fetch", lambda url: "<p>סכום: 4</p>")
    assert rates_watch.run()["held"] == 1
    assert _latest(firm)["status"] == "pending"
    with cursor() as cur:
        rows_ = calculators.active_params(cur, firm, "mod.idf_disability")
    assert rows_["base_amount"]["value"] == Decimal("1000.0000")      # החישוב לא השתנה


def test_unreadable_page_is_held(firm, monkeypatch):
    _param(firm)

    def boom(url):
        raise OSError("down")

    monkeypatch.setattr(rates_watch, "fetch", boom)
    assert rates_watch.run()["held"] == 1


def test_same_value_only_marks_checked_and_not_twice_a_day(firm, monkeypatch):
    _param(firm, value=1000)
    monkeypatch.setattr(rates_watch, "fetch", lambda url: "סכום: 1000")
    now = datetime.now(timezone.utc)
    assert rates_watch.run(now)["checked"] == 1
    assert rates_watch.run(now + timedelta(hours=1))["skipped"] == 1
    with cursor() as cur:
        cur.execute("select count(*) as n from calc_parameters where firm_id = %s", (firm,))
        assert cur.fetchone()["n"] == 1


# ================================================================
#  ה-API
# ================================================================

def _office(who=STAFF):
    api = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    login(api, who)
    api.headers["X-CSRF-Token"] = api.cookies.get("portal_csrf")
    return api


def _portal():
    with cursor() as cur:
        cur.execute("""select c.client_id from cases c join claim_types ct on ct.id = c.claim_type_id
                        where ct.code = 'work-injury' limit 1""")
        client_id = cur.fetchone()["client_id"]
    api = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    csrf = client_login(api, client_id)
    api.headers["X-CSRF-Token"] = csrf
    return api


def test_admin_sets_parameters_and_the_client_computes(firm):
    office = _office()
    for key, value in WORK.items():
        r = office.post("/api/office/calc-parameters",
                        json={"calculator": "nii.work_injury", "key": key, "value": value,
                              "effective_from": "2020-01-01"})
        assert r.status_code == 200, r.text
    portal = _portal()
    calcs = {c["id"]: c for c in portal.get("/api/client/calculators").json()["calculators"]}
    assert calcs["nii.work_injury"]["configured"] is True
    r = portal.post("/api/client/calculators/nii.work_injury",
                    json={"inputs": {"percent": 40, "salary": 10000}})
    assert r.status_code == 200, r.text
    assert (r.json()["low"], r.json()["high"]) == ("1800", "2200")


def test_unconfigured_calculator_is_409_for_the_client(firm):
    r = _portal().post("/api/client/calculators/nii.work_injury",
                       json={"inputs": {"percent": 40, "salary": 10000}})
    assert r.status_code == 409


def test_only_admin_writes_parameters_and_keys_are_checked(firm):
    body = {"calculator": "nii.work_injury", "key": "pension_rate", "value": 0.5}
    assert _office(OTHER).post("/api/office/calc-parameters", json=body).status_code == 403
    bad = dict(body, key="made_up_key")
    assert _office().post("/api/office/calc-parameters", json=bad).status_code == 400
    evil = dict(body, source_url="https://evil.example.com/rates")
    assert _office().post("/api/office/calc-parameters", json=evil).status_code == 400


def test_held_update_can_be_approved(firm, monkeypatch):
    _param(firm, value=1000)
    monkeypatch.setattr(rates_watch, "fetch", lambda url: "סכום: 5000")
    rates_watch.run()
    with cursor() as cur:
        cur.execute("select id from calc_parameters where firm_id = %s and status = 'pending'", (firm,))
        pid = cur.fetchone()["id"]
    assert _office().post("/api/office/calc-parameters/%s/review" % pid,
                          json={"action": "approve"}).status_code == 200
    with cursor() as cur:
        assert calculators.active_params(cur, firm, "mod.idf_disability")["base_amount"]["value"] \
            == Decimal("5000.0000")


def test_right_is_shown_only_after_publishing_and_editing_unpublishes(firm):
    office = _office()
    rid = office.post("/api/office/rights", json={
        "title": "זכות בדיקה", "summary": "תיאור זכות לבדיקה בלבד, לא אמיתי.",
        "conditions": {}}).json()["id"]
    portal = _portal()
    assert rid not in [r["id"] for r in portal.get("/api/client/rights").json()["rights"]]
    assert office.post("/api/office/rights/%s/status" % rid,
                       json={"status": "published"}).status_code == 200
    assert rid in [r["id"] for r in portal.get("/api/client/rights").json()["rights"]]
    office.post("/api/office/rights", json={"id": rid, "title": "זכות בדיקה",
                                           "summary": "נוסח ששונה ודורש אישור מחדש.",
                                           "conditions": {}})
    assert rid not in [r["id"] for r in portal.get("/api/client/rights").json()["rights"]]


def test_unknown_right_condition_is_refused(firm):
    r = _office().post("/api/office/rights", json={"title": "x" * 3, "summary": "y" * 12,
                                                   "conditions": {"age_over": 60}})
    assert r.status_code == 400
