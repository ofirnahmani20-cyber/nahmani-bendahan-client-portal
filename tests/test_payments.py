"""
תשלומים ושכר טרחה - fees.py ו-api_payments.py (docs/10, v1 סעיף 5).

החישוב נבדק כפונקציה טהורה במספרים עגולים שאפשר לבדוק בראש, ואז
דרך ה-API של המשרד ושל הלקוח - שחייבים להראות אותו מספר בדיוק.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from starlette.testclient import TestClient

from server import fees
from server.app import app
from server.db.pool import cursor

from .test_authz import client_login
from .test_processing import LOCAL, STAFF, login

TODAY = date(2031, 6, 15)
OTHER = ("bendahan@nahmani-bendahan.co.il", "office2026")


def agreement(**kw):
    a = {"percent": 20, "fixed_amount": 0, "applies_to": ["retro", "grant"],
         "monthly_months": None, "vat_included": False, "cap_amount": None}
    a.update(kw)
    return a


def pay(kind="retro", amount=10000, status="received", **kw):
    p = {"id": uuid.uuid4(), "kind": kind, "amount": amount, "status": status,
         "period_start": None, "period_end": None, "label": None}
    p.update(kw)
    return p


def calc(a, payments, paid=(), vat=18):
    return fees.compute(a, payments, [{"amount": x} for x in paid],
                        vat_percent=vat, today=TODAY)


# ================================================================
#  החישוב
# ================================================================

def test_months_between_counts_both_ends():
    assert fees.months_between(date(2031, 1, 1), date(2031, 1, 31)) == 1
    assert fees.months_between(date(2031, 1, 20), date(2031, 6, 1)) == 6
    assert fees.months_between(date(2031, 6, 1), date(2031, 1, 1)) == 0


def test_percent_of_received_retro_plus_vat():
    fee = calc(agreement(), [pay(amount=10000)])["fee"]
    assert fee["baseNow"] == Decimal("10000.00")
    assert fee["beforeVatNow"] == Decimal("2000.00")
    assert fee["vatNow"] == Decimal("360.00")
    assert fee["dueNow"] == Decimal("2360.00") and fee["dueLater"] == Decimal("0.00")


def test_expected_payment_is_due_later_not_now():
    fee = calc(agreement(), [pay(amount=10000, status="expected")])["fee"]
    assert fee["dueNow"] == Decimal("0.00")
    assert fee["dueLater"] == Decimal("2360.00")


def test_vat_included_adds_nothing():
    fee = calc(agreement(vat_included=True), [pay(amount=10000)])["fee"]
    assert fee["dueNow"] == Decimal("2000.00") and fee["vatNow"] == Decimal("0.00")


def test_fixed_amount_is_due_now_even_before_any_payment():
    fee = calc(agreement(percent=0, fixed_amount=1000), [])["fee"]
    assert fee["dueNow"] == Decimal("1180.00")


def test_payments_outside_applies_to_are_not_in_the_base():
    fee = calc(agreement(applies_to=["retro"]), [pay(amount=10000), pay("grant", 5000)])["fee"]
    assert fee["baseNow"] == Decimal("10000.00")


def test_monthly_counts_only_the_window_and_splits_by_elapsed_months():
    # התחילה במרץ 2031, היום 15.6 -> 4 חודשים שולמו (3,4,5,6); חלון 6 -> 2 צפויים
    p = pay("monthly", 1000, period_start=date(2031, 3, 1))
    fee = calc(agreement(applies_to=["monthly"], monthly_months=6, vat_included=True), [p])["fee"]
    assert fee["baseNow"] == Decimal("4000.00") and fee["baseLater"] == Decimal("2000.00")
    assert fee["dueNow"] == Decimal("800.00") and fee["dueLater"] == Decimal("400.00")


def test_monthly_that_ended_early_is_not_counted_past_its_end():
    p = pay("monthly", 1000, period_start=date(2031, 1, 1), period_end=date(2031, 2, 28))
    fee = calc(agreement(applies_to=["monthly"], monthly_months=12, vat_included=True), [p])["fee"]
    assert fee["baseNow"] == Decimal("2000.00") and fee["baseLater"] == Decimal("0.00")


def test_cap_applies_to_the_total_and_keeps_now_whole():
    fee = calc(agreement(cap_amount=3000),
               [pay(amount=10000), pay(amount=10000, status="expected")])["fee"]
    # בלי תקרה: 2360 עכשיו + 2360 אחר כך. עם תקרה 3000: 2360 + 640
    assert fee["capped"] is True
    assert fee["dueNow"] == Decimal("2360.00") and fee["dueLater"] == Decimal("640.00")


def test_balance_subtracts_what_was_paid():
    fee = calc(agreement(), [pay(amount=10000)], paid=[1000, 360])["fee"]
    assert fee["paid"] == Decimal("1360.00") and fee["balance"] == Decimal("1000.00")


def test_rounding_is_to_agorot():
    fee = calc(agreement(percent=12.5), [pay(amount=333.33)])["fee"]
    assert fee["beforeVatNow"] == Decimal("41.67")


def test_benefits_summary_for_the_client():
    b = calc(None, [pay(amount=10000), pay("grant", 5000, status="expected"),
                    pay("monthly", 1500, period_start=date(2031, 5, 1)),
                    pay("monthly", 700, status="expected", period_start=date(2031, 9, 1))])
    assert b["fee"] is None
    assert b["benefits"]["received"] == Decimal("13000.00")      # 10000 + 2 × 1500
    assert b["benefits"]["expected"] == Decimal("5000.00")
    assert b["benefits"]["monthly"] == Decimal("1500.00")
    assert b["benefits"]["monthlyExpected"] == Decimal("700.00")


def test_jsonable_sends_money_as_exact_strings():
    assert fees.jsonable({"a": [Decimal("1.50")]}) == {"a": ["1.50"]}


# ================================================================
#  ה-API
# ================================================================

def _api(who=STAFF):
    api = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    login(api, who)
    api.headers["X-CSRF-Token"] = api.cookies.get("portal_csrf")
    return api


@pytest.fixture
def money_case(temp_case):
    yield temp_case
    with cursor(commit=True) as cur:
        for t in ("fee_payments", "benefit_payments", "fee_agreements"):
            cur.execute("delete from %s where case_id = %%s" % t, (temp_case["case_id"],))


def test_office_flow_agreement_payments_and_balance(money_case):
    api = _api()
    cid = money_case["case_id"]
    assert api.post("/api/office/cases/%s/fee-agreement" % cid,
                    json={"percent": 20, "applies_to": ["retro"]}).status_code == 200
    assert api.post("/api/office/cases/%s/benefit-payments" % cid,
                    json={"kind": "retro", "amount": 10000, "status": "received",
                          "received_date": "2031-01-10"}).status_code == 200
    assert api.post("/api/office/cases/%s/fee-payments" % cid,
                    json={"amount": 1000, "paid_at": "2031-01-20"}).status_code == 200
    body = api.get("/api/office/cases/%s/payments" % cid).json()
    assert body["calc"]["fee"]["dueNow"] == "2360.00"
    assert body["calc"]["fee"]["balance"] == "1360.00"
    assert body["agreement"]["appliesTo"] == ["retro"]


def test_client_sees_the_same_numbers_without_internal_references(money_case):
    api = _api()
    cid = money_case["case_id"]
    api.post("/api/office/cases/%s/fee-agreement" % cid, json={"percent": 20})
    api.post("/api/office/cases/%s/benefit-payments" % cid,
             json={"kind": "retro", "amount": 10000, "status": "received",
                   "received_date": "2031-01-10"})
    api.post("/api/office/cases/%s/fee-payments" % cid,
             json={"amount": 500, "paid_at": "2031-01-20", "reference": "אסמכתא-פנימית-77"})
    office = api.get("/api/office/cases/%s/payments" % cid).json()["calc"]

    with cursor() as cur:
        cur.execute("select client_id, case_number from cases where id = %s", (cid,))
        row = cur.fetchone()
    portal = TestClient(app, client=LOCAL, base_url="http://127.0.0.1")
    client_login(portal, row["client_id"])
    cases = portal.get("/api/client/payments").json()["cases"]
    mine = [c for c in cases if c["caseNumber"] == row["case_number"]][0]
    assert mine["calc"] == office
    assert "אסמכתא-פנימית-77" not in str(mine)


def test_cancelled_payment_leaves_the_calculation_and_stays_on_record(money_case):
    api = _api()
    cid = money_case["case_id"]
    api.post("/api/office/cases/%s/fee-agreement" % cid, json={"percent": 20})
    pid = api.post("/api/office/cases/%s/benefit-payments" % cid,
                   json={"kind": "retro", "amount": 10000, "status": "received",
                         "received_date": "2031-01-10"}).json()["id"]
    assert api.post("/api/office/benefit-payments/%s/cancel" % pid).status_code == 200
    assert api.get("/api/office/cases/%s/payments" % cid).json()["calc"]["fee"]["dueNow"] == "0.00"
    with cursor() as cur:
        cur.execute("select cancelled_at from benefit_payments where id = %s", (pid,))
        assert cur.fetchone()["cancelled_at"] is not None
    assert api.post("/api/office/benefit-payments/%s/cancel" % pid).status_code == 404


@pytest.mark.parametrize("body", [
    {"percent": 0, "fixed_amount": 0},
    {"percent": 20, "applies_to": ["monthly"]},           # חסר monthly_months
    {"percent": 20, "applies_to": ["lottery"]},
    {"percent": 120},
])
def test_invalid_agreements_are_refused(money_case, body):
    r = _api().post("/api/office/cases/%s/fee-agreement" % money_case["case_id"], json=body)
    assert r.status_code in (400, 422)


@pytest.mark.parametrize("body", [
    {"kind": "monthly", "amount": 1000},                                 # בלי חודש התחלה
    {"kind": "retro", "amount": 1000, "status": "received"},             # בלי תאריך
    {"kind": "retro", "amount": -5},
    {"kind": "monthly", "amount": 1000, "period_start": "2031-05-01", "period_end": "2031-01-01"},
])
def test_invalid_benefit_payments_are_refused(money_case, body):
    r = _api().post("/api/office/cases/%s/benefit-payments" % money_case["case_id"], json=body)
    assert r.status_code in (400, 422)


def test_payments_respect_case_scope(money_case):
    """תיק זמני בלי אחראי - גלוי למנהל בלבד."""
    api = _api(OTHER)
    cid = money_case["case_id"]
    assert api.get("/api/office/cases/%s/payments" % cid).status_code == 404
    assert api.post("/api/office/cases/%s/fee-payments" % cid,
                    json={"amount": 1, "paid_at": "2031-01-01"}).status_code == 404


def test_payment_writes_require_csrf(money_case):
    api = _api()
    del api.headers["X-CSRF-Token"]
    r = api.post("/api/office/cases/%s/fee-agreement" % money_case["case_id"], json={"percent": 20})
    assert r.status_code == 403
