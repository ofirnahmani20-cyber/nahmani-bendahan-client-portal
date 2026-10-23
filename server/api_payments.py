"""
api_payments.py - תשלומים ושכר טרחה (docs/10, v1 סעיף 5).

משרד (בהיקף התיקים של איש הצוות):
    GET  /api/office/cases/{id}/payments
    POST /api/office/cases/{id}/fee-agreement        הסכם (יצירה או עדכון)
    POST /api/office/cases/{id}/benefit-payments     תשלום מהגוף ללקוח
    POST /api/office/benefit-payments/{id}/update    סטטוס, תאריכים, סכום
    POST /api/office/benefit-payments/{id}/cancel
    POST /api/office/cases/{id}/fee-payments         תשלום של הלקוח למשרד
    POST /api/office/fee-payments/{id}/cancel

לקוח:
    GET  /api/client/payments                        כל התיקים שלו

החישוב - server/fees.py, אחד לשני הצדדים. רישום כספי אינו נמחק:
ביטול משאיר את השורה, עם מי ומתי, ביומן.

הנתונים מוזנים בידי הצוות. חילוץ אוטומטי של סכומים ממכתבי ביטוח
לאומי ייבנה עם אישור העובדות מול המקור (Layer 2 שלב 5, v2): מספר
מ-OCR לא נכנס לחישוב כספי בלי אדם.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, fees
from .auth import case_scope, require_client, require_csrf, require_staff
from .db.pool import cursor

router = APIRouter()

KINDS = ("retro", "grant", "monthly", "other")
PAYERS = ("nii", "mod", "insurer", "pension_fund", "other")
METHODS = ("transfer", "check", "cash", "card", "offset", "other")


def _date(value, field):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail="%s אינו תאריך תקין." % field)


def _case(cur, case_id, identity):
    cur.execute("select c.id from cases c where c.id = %s and c.firm_id = %s and "
                + case_scope(identity), (case_id, identity.firm_id))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="התיק לא נמצא.")


def _load(cur, case_id, firm_id):
    cur.execute("select * from fee_agreements where case_id = %s", (case_id,))
    agreement = cur.fetchone()
    cur.execute("""select * from benefit_payments
                    where case_id = %s and cancelled_at is null
                    order by coalesce(received_date, expected_date, period_start), created_at""",
                (case_id,))
    payments = cur.fetchall()
    cur.execute("""select * from fee_payments where case_id = %s and cancelled_at is null
                    order by paid_at, created_at""", (case_id,))
    fee_payments = cur.fetchall()
    cur.execute("select vat_percent from firms where id = %s", (firm_id,))
    vat = cur.fetchone()["vat_percent"]
    calc = fees.compute(agreement, payments, fee_payments, vat_percent=vat)
    return agreement, payments, fee_payments, calc


def _iso(d):
    return d.isoformat() if d else None


def _payment_out(p):
    return {"id": str(p["id"]), "kind": p["kind"], "payer": p["payer"],
            "amount": fees.jsonable(fees.money(p["amount"])), "status": p["status"],
            "expectedDate": _iso(p["expected_date"]), "receivedDate": _iso(p["received_date"]),
            "periodStart": _iso(p["period_start"]), "periodEnd": _iso(p["period_end"]),
            "label": p["label"]}


def _agreement_out(a):
    if not a:
        return None
    return {"percent": str(a["percent"]), "fixed": fees.jsonable(fees.money(a["fixed_amount"])),
            "appliesTo": list(a["applies_to"]), "monthlyMonths": a["monthly_months"],
            "vatIncluded": a["vat_included"],
            "cap": fees.jsonable(fees.money(a["cap_amount"])) if a["cap_amount"] else None,
            "signedAt": _iso(a["signed_at"])}


# ================================================================
#  משרד
# ================================================================

@router.get("/api/office/cases/{case_id}/payments")
def office_payments(case_id: str, request: Request, identity=Depends(require_staff)):
    with cursor() as cur:
        _case(cur, case_id, identity)
        agreement, payments, fee_payments, calc = _load(cur, case_id, identity.firm_id)
    return {
        "agreement": _agreement_out(agreement),
        "payments": [_payment_out(p) for p in payments],
        "feePayments": [{"id": str(f["id"]), "amount": fees.jsonable(fees.money(f["amount"])),
                         "paidAt": _iso(f["paid_at"]), "method": f["method"],
                         "reference": f["reference"]} for f in fee_payments],
        "calc": fees.jsonable(calc),
    }


class Agreement(BaseModel):
    percent: float = Field(ge=0, le=100)
    fixed_amount: float = Field(default=0, ge=0, le=10_000_000)
    applies_to: list[str] = ["retro", "grant"]
    monthly_months: int | None = Field(default=None, ge=1, le=120)
    vat_included: bool = False
    cap_amount: float | None = Field(default=None, gt=0, le=10_000_000)
    signed_at: str | None = None


@router.post("/api/office/cases/{case_id}/fee-agreement")
def set_agreement(case_id: str, body: Agreement, request: Request,
                  identity=Depends(require_staff)):
    require_csrf(request)
    applies = sorted(set(body.applies_to))
    if not set(applies) <= set(KINDS):
        raise HTTPException(status_code=400, detail="סוג תשלום לא מוכר בהסכם.")
    if body.percent <= 0 and body.fixed_amount <= 0:
        raise HTTPException(status_code=400, detail="הסכם צריך אחוז, סכום קבוע, או שניהם.")
    if "monthly" in applies and not body.monthly_months:
        raise HTTPException(status_code=400,
                            detail="אחוז מקצבה חודשית - צריך לקבוע על כמה חודשים הוא חל.")
    with cursor(commit=True) as cur:
        _case(cur, case_id, identity)
        cur.execute(
            """insert into fee_agreements (case_id, firm_id, percent, fixed_amount, applies_to,
                                           monthly_months, vat_included, cap_amount, signed_at,
                                           updated_by_user_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (case_id) do update set
                 percent = excluded.percent, fixed_amount = excluded.fixed_amount,
                 applies_to = excluded.applies_to, monthly_months = excluded.monthly_months,
                 vat_included = excluded.vat_included, cap_amount = excluded.cap_amount,
                 signed_at = excluded.signed_at, updated_by_user_id = excluded.updated_by_user_id,
                 updated_at = now()""",
            (case_id, identity.firm_id, body.percent, body.fixed_amount, applies,
             body.monthly_months if "monthly" in applies else None, body.vat_included,
             body.cap_amount, _date(body.signed_at, "תאריך החתימה"), identity.subject_id))
    audit.record(identity, "office.fee_agreement_set", entity_type="case", entity_id=case_id,
                 case_id=case_id, request=request)
    return {"ok": True}


class Benefit(BaseModel):
    kind: str
    payer: str = "nii"
    amount: float = Field(gt=0, le=100_000_000)
    status: str = Field(default="expected", pattern="^(expected|received)$")
    expected_date: str | None = None
    received_date: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    label: str | None = Field(default=None, max_length=120)


def _benefit_values(body):
    if body.kind not in KINDS:
        raise HTTPException(status_code=400, detail="סוג תשלום לא מוכר.")
    if body.payer not in PAYERS:
        raise HTTPException(status_code=400, detail="משלם לא מוכר.")
    start = _date(body.period_start, "תחילת התקופה")
    end = _date(body.period_end, "סוף התקופה")
    received = _date(body.received_date, "תאריך התשלום")
    if body.kind == "monthly" and not start:
        raise HTTPException(status_code=400, detail="קצבה חודשית - צריך חודש התחלה.")
    if end and start and end < start:
        raise HTTPException(status_code=400, detail="סוף התקופה לפני תחילתה.")
    if body.status == "received" and not received:
        if body.kind == "monthly":
            received = start
        else:
            raise HTTPException(status_code=400, detail="תשלום שהתקבל - צריך תאריך.")
    return (body.kind, body.payer, body.amount, body.status,
            _date(body.expected_date, "התאריך הצפוי"), received,
            start if body.kind == "monthly" else None,
            end if body.kind == "monthly" else None,
            (body.label or "").strip() or None)


@router.post("/api/office/cases/{case_id}/benefit-payments")
def add_benefit(case_id: str, body: Benefit, request: Request, identity=Depends(require_staff)):
    require_csrf(request)
    values = _benefit_values(body)
    with cursor(commit=True) as cur:
        _case(cur, case_id, identity)
        cur.execute(
            """insert into benefit_payments (firm_id, case_id, kind, payer, amount, status,
                                             expected_date, received_date, period_start,
                                             period_end, label, created_by_user_id)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, case_id) + values + (identity.subject_id,))
        pid = cur.fetchone()["id"]
    audit.record(identity, "office.benefit_payment_added", entity_type="case",
                 entity_id=case_id, case_id=case_id, request=request,
                 metadata={"kind": body.kind, "to_status": body.status})
    return {"ok": True, "id": str(pid)}


def _owned_payment(cur, table, payment_id, identity):
    cur.execute("select p.id, p.case_id from " + table + " p"
                " join cases c on c.id = p.case_id and c.firm_id = p.firm_id"
                " where p.id = %s and p.firm_id = %s and p.cancelled_at is null and "
                + case_scope(identity), (payment_id, identity.firm_id))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="התשלום לא נמצא.")
    return row


@router.post("/api/office/benefit-payments/{payment_id}/update")
def update_benefit(payment_id: str, body: Benefit, request: Request,
                   identity=Depends(require_staff)):
    """עדכון מלא של השורה (הטופס שולח את כל השדות)."""
    require_csrf(request)
    values = _benefit_values(body)
    with cursor(commit=True) as cur:
        row = _owned_payment(cur, "benefit_payments", payment_id, identity)
        cur.execute(
            """update benefit_payments set kind = %s, payer = %s, amount = %s, status = %s,
                      expected_date = %s, received_date = %s, period_start = %s,
                      period_end = %s, label = %s
                where id = %s""", values + (payment_id,))
    audit.record(identity, "office.benefit_payment_updated", entity_type="case",
                 entity_id=str(row["case_id"]), case_id=str(row["case_id"]), request=request,
                 metadata={"kind": body.kind, "to_status": body.status})
    return {"ok": True}


@router.post("/api/office/benefit-payments/{payment_id}/cancel")
def cancel_benefit(payment_id: str, request: Request, identity=Depends(require_staff)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        row = _owned_payment(cur, "benefit_payments", payment_id, identity)
        cur.execute("update benefit_payments set cancelled_at = now() where id = %s",
                    (payment_id,))
    audit.record(identity, "office.benefit_payment_cancelled", entity_type="case",
                 entity_id=str(row["case_id"]), case_id=str(row["case_id"]), request=request)
    return {"ok": True}


class FeePayment(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)
    paid_at: str
    method: str = "transfer"
    reference: str | None = Field(default=None, max_length=80)


@router.post("/api/office/cases/{case_id}/fee-payments")
def add_fee_payment(case_id: str, body: FeePayment, request: Request,
                    identity=Depends(require_staff)):
    require_csrf(request)
    if body.method not in METHODS:
        raise HTTPException(status_code=400, detail="אמצעי תשלום לא מוכר.")
    paid = _date(body.paid_at, "תאריך התשלום")
    if paid is None:
        raise HTTPException(status_code=400, detail="צריך תאריך תשלום.")
    with cursor(commit=True) as cur:
        _case(cur, case_id, identity)
        cur.execute(
            """insert into fee_payments (firm_id, case_id, amount, paid_at, method, reference,
                                         recorded_by_user_id)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (identity.firm_id, case_id, body.amount, paid, body.method,
             (body.reference or "").strip() or None, identity.subject_id))
        fid = cur.fetchone()["id"]
    audit.record(identity, "office.fee_payment_recorded", entity_type="case",
                 entity_id=case_id, case_id=case_id, request=request,
                 metadata={"channel": body.method})
    return {"ok": True, "id": str(fid)}


@router.post("/api/office/fee-payments/{payment_id}/cancel")
def cancel_fee_payment(payment_id: str, request: Request, identity=Depends(require_staff)):
    require_csrf(request)
    with cursor(commit=True) as cur:
        row = _owned_payment(cur, "fee_payments", payment_id, identity)
        cur.execute("update fee_payments set cancelled_at = now() where id = %s", (payment_id,))
    audit.record(identity, "office.fee_payment_cancelled", entity_type="case",
                 entity_id=str(row["case_id"]), case_id=str(row["case_id"]), request=request)
    return {"ok": True}


# ================================================================
#  לקוח
# ================================================================

@router.get("/api/client/payments")
def client_payments(request: Request, identity=Depends(require_client)):
    """
    כל התיקים של הלקוח שיש בהם נתון כספי. אותו חישוב שהמשרד רואה,
    בלי אסמכתאות פנימיות ובלי מי הזין מה.
    """
    out = []
    with cursor() as cur:
        cur.execute("""select c.id, c.case_number, ct.name as claim_type from cases c
                         join claim_types ct on ct.id = c.claim_type_id
                        where c.client_id = %s and c.firm_id = %s
                        order by c.opened_at desc""", (identity.subject_id, identity.firm_id))
        for c in cur.fetchall():
            agreement, payments, fee_payments, calc = _load(cur, c["id"], identity.firm_id)
            if not (agreement or payments or fee_payments):
                continue
            out.append({
                "caseNumber": c["case_number"], "claimType": c["claim_type"],
                "payments": [_payment_out(p) for p in payments],
                "feePayments": [{"amount": fees.jsonable(fees.money(f["amount"])),
                                 "paidAt": _iso(f["paid_at"])} for f in fee_payments],
                "agreement": _agreement_out(agreement),
                "calc": fees.jsonable(calc),
            })
    audit.record(identity, "client.payments_viewed", request=request)
    return {"cases": out}
