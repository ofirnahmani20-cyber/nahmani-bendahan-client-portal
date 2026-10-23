"""
fees.py - חישוב התשלומים ושכר הטרחה של תיק (docs/10, v1 סעיף 5).

פונקציה טהורה אחת, compute, שמשרתת את שני הצדדים: המשרד רואה את
החישוב ומנהל אותו, והלקוח רואה את *אותו* חישוב, שורה אחר שורה.
שני חישובים נפרדים היו נפרדים זה מזה בשקט ביום שבו אחד מהם תוקן.

הבסיס לשכר הטרחה
-----------------
רק תשלומים שההסכם חל עליהם (applies_to), ושלא בוטלו:
  רטרו / מענק / אחר - הסכום.
  קצבה חודשית      - הסכום לחודש × החודשים בתוך חלון ההסכם
                      (monthly_months הראשונים, ולא מעבר לסוף הקצבה).

"מגיע עכשיו" מול "צפוי": תשלום שהתקבל מזכה את המשרד עכשיו; תשלום
שנקבע וטרם הגיע - בעתיד. בקצבה חודשית - לפי החודשים שכבר חלפו.

סדר החישוב: אחוז על הבסיס + סכום קבוע, מע"מ (אם אינו כלול), ואז
תקרה - על הסך הכולל. התקרה מתחלקת בין "עכשיו" ל"עתיד" כך ש"עכשיו"
נשאר שלם ככל האפשר: הלקוח לא משלם היום על כסף שעוד לא קיבל.

כסף ב-Decimal ובאגורות. float אינו מתאים לכסף.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0")


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def months_between(start: date, end: date) -> int:
    """מספר החודשים הקלנדריים מ-start עד end, כולל שניהם. 0 אם end לפני start."""
    if end < start:
        return 0
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def _monthly_split(p, window, today):
    """(חודשים שכבר שולמו, חודשים צפויים) בתוך החלון, לקצבה חודשית."""
    start, end = p["period_start"], p.get("period_end")
    total = window if end is None else min(window, months_between(start, end))
    if p["status"] != "received":
        return 0, total
    elapsed = months_between(start, min(today, end) if end else today)
    done = min(total, elapsed)
    return done, total - done


def compute(agreement, payments, fee_payments, *, vat_percent, today=None):
    """
    agreement    - dict מ-fee_agreements, או None
    payments     - שורות benefit_payments (בלי מבוטלות)
    fee_payments - שורות fee_payments (בלי מבוטלות)

    מחזיר dict עם:
      benefits - מה הלקוח קיבל, מה צפוי, וקצבה חודשית פעילה
      fee      - None בלי הסכם; אחרת השורות, הסכומים והיתרה
    """
    today = today or date.today()
    vat_rate = Decimal(str(vat_percent)) / 100

    # ---- מה הגוף משלם ללקוח ----
    received = expected = ZERO
    monthly_now = monthly_soon = ZERO
    for p in payments:
        amount = money(p["amount"])
        if p["kind"] == "monthly":
            start, end = p["period_start"], p.get("period_end")
            if p["status"] == "received" and start <= today:
                months = months_between(start, min(today, end) if end else today)
                received += amount * months
                if end is None or end >= today:
                    monthly_now += amount
            elif p["status"] == "expected":
                # נקבעה, טרם התחילה להשתלם
                monthly_soon += amount
        elif p["status"] == "received":
            received += amount
        else:
            expected += amount
    benefits = {"received": money(received), "expected": money(expected),
                "monthly": money(monthly_now), "monthlyExpected": money(monthly_soon)}

    if not agreement:
        return {"benefits": benefits, "fee": None}

    # ---- הבסיס לשכר הטרחה ----
    applies = set(agreement["applies_to"] or ())
    window = agreement.get("monthly_months") or 0
    lines = []
    base_now = base_later = ZERO
    for p in payments:
        if p["kind"] not in applies:
            continue
        amount = money(p["amount"])
        if p["kind"] == "monthly":
            done, later = _monthly_split(p, window, today)
            now_part, later_part = amount * done, amount * later
            label = "%s × %d חודשים" % (amount, done + later)
        else:
            now_part = amount if p["status"] == "received" else ZERO
            later_part = ZERO if p["status"] == "received" else amount
            label = str(amount)
        base_now += now_part
        base_later += later_part
        lines.append({"paymentId": str(p["id"]), "kind": p["kind"], "label": p.get("label"),
                      "basis": label, "now": money(now_part), "later": money(later_part)})

    percent = Decimal(str(agreement["percent"] or 0)) / 100
    fixed = money(agreement["fixed_amount"])
    # הסכום הקבוע מגיע עם חתימת ההסכם, לא עם תשלום מהגוף
    before_vat_now = money(base_now * percent) + fixed
    before_vat_later = money(base_later * percent)
    if agreement["vat_included"]:
        vat_now = vat_later = ZERO
    else:
        vat_now = money(before_vat_now * vat_rate)
        vat_later = money(before_vat_later * vat_rate)
    total_now = before_vat_now + vat_now
    total_later = before_vat_later + vat_later

    capped = False
    cap = agreement.get("cap_amount")
    if cap is not None:
        cap = money(cap)
        if total_now + total_later > cap:
            capped = True
            total_now = min(total_now, cap)
            total_later = cap - total_now

    paid = money(sum((money(f["amount"]) for f in fee_payments), ZERO))
    return {
        "benefits": benefits,
        "fee": {
            "lines": lines,
            "percent": str(agreement["percent"]),
            "fixed": fixed,
            "baseNow": money(base_now), "baseLater": money(base_later),
            "beforeVatNow": before_vat_now, "beforeVatLater": before_vat_later,
            "vatNow": vat_now, "vatLater": vat_later,
            "vatIncluded": bool(agreement["vat_included"]),
            "vatPercent": str(vat_percent),
            "cap": cap, "capped": capped,
            "dueNow": money(total_now), "dueLater": money(total_later),
            "paid": paid,
            # יתרה שלילית = שולם יותר מהמגיע עד עכשיו (מקדמה)
            "balance": money(total_now - paid),
        },
    }


def jsonable(value):
    """Decimal -> מחרוזת עם שתי ספרות, כדי שהדפדפן לא יעגל כסף כ-float."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value
