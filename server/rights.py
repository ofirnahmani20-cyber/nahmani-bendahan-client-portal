"""
rights.py - התאמת זכויות חיצוניות ללקוח (docs/10, v1 סעיף 6).

זכות מהקטלוג (rights_catalog) מוצגת ללקוח אם היא מאושרת ומתאימה
לפרופיל שלו. הפרופיל נגזר מהתיקים - לא מטופס שהלקוח ממלא:
  max_percent   - אחוז הנכות הגבוה בהחלטות שנרשמו בתיקיו
  claim_codes   - סוגי התביעות שלו
  has_monthly   - האם יש לו קצבה חודשית פעילה או צפויה

תנאי שהמערכת אינה יודעת לבדוק אינו נחשב כ"עומד": זכות בלי תנאים
מוצגת לכולם, זכות עם תנאי שחסר לו מידע - לא. לקוח שרואה זכות שאינה
שלו בונה עליה; לקוח שלא רואה זכות - ישמע עליה מעורך הדין.
"""

KNOWN = {"min_percent", "claim_codes", "needs_monthly"}


def profile(cur, client_id, firm_id):
    cur.execute(
        """select coalesce(max(d.percent), -1) as max_percent,
                  array_agg(distinct ct.code) as claim_codes,
                  bool_or(exists (select 1 from benefit_payments p
                                   where p.case_id = c.id and p.kind = 'monthly'
                                     and p.cancelled_at is null)) as has_monthly
             from cases c
             join claim_types ct on ct.id = c.claim_type_id
             left join case_decisions d on d.case_id = c.id
            where c.client_id = %s and c.firm_id = %s""",
        (client_id, firm_id))
    row = cur.fetchone()
    return {"max_percent": row["max_percent"] if row["max_percent"] >= 0 else None,
            "claim_codes": [c for c in (row["claim_codes"] or []) if c],
            "has_monthly": bool(row["has_monthly"])}


def matches(conditions, prof):
    conditions = conditions or {}
    if set(conditions) - KNOWN:
        return False                      # תנאי לא מוכר - לא מציגים
    if "min_percent" in conditions:
        if prof["max_percent"] is None or prof["max_percent"] < conditions["min_percent"]:
            return False
    if conditions.get("claim_codes"):
        if not set(conditions["claim_codes"]) & set(prof["claim_codes"]):
            return False
    if conditions.get("needs_monthly") and not prof["has_monthly"]:
        return False
    return True
