"""
calculators.py - מחשבוני זכויות ללקוח (docs/10, v1 סעיף 6).

ההפרדה שעליה הכול בנוי
-----------------------
* הנוסחה - כאן, בקוד, נבדקת בבדיקות.
* המספרים (סכום קצבה, אחוז, סף) - ב-calc_parameters, עם תאריך תחולה
  ומקור, ומתעדכנים בלי פריסה (ידנית, או rates_watch.py).

אין בקובץ הזה אף סכום או אחוז של ביטוח לאומי או של משרד הביטחון.
הם משתנים כל שנה, ומספר שנכתב כאן מהזיכרון היה מוצג ללקוח כעובדה.
מחשבון שחסר לו פרמטר מחזיר "טרם הוגדר" - לא ניחוש.

התוצאה היא טווח (± uncertainty_percent, גם הוא פרמטר) עם הסתייגות,
ולא סכום מדויק: הסכום בפועל תלוי בנסיבות שהמחשבון אינו יודע.

שני סוגי מחשבון
---------------
formula - נוסחה מוגדרת (נכות מעבודה, נכי צה"ל, נכות כללית).
table   - בחירה מטבלה שהמשרד מגדיר: פרמטרים בשם option.<מפתח>,
          והלקוח בוחר אחד (ניידות, שירותים מיוחדים, ילד נכה). כך
          מבנה שמשתנה בין שנים - מספר הדרגות, למשל - אינו דורש קוד.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

DEFAULT_UNCERTAINTY = Decimal("10")

DISCLAIMER = ("הערכה כללית בלבד, לפי הנתונים שהזנת ולפי שיעורי הקצבאות שבתוקף. "
              "הסכום בפועל נקבע בידי הגוף המשלם ותלוי בנסיבות נוספות. "
              "זו אינה התחייבות של המשרד.")


class NotConfigured(Exception):
    """חסרים פרמטרים. ה-API מציג "טרם הוגדר", לא שגיאה."""
    def __init__(self, missing):
        super().__init__(", ".join(missing))
        self.missing = missing


class BadInput(ValueError):
    pass


def _num(inputs, name, *, low=None, high=None, integer=False):
    raw = inputs.get(name)
    try:
        value = Decimal(str(raw))
    except Exception:
        raise BadInput("שדה חסר או לא תקין: %s" % name)
    if integer and value != value.to_integral_value():
        raise BadInput("נדרש מספר שלם: %s" % name)
    if (low is not None and value < low) or (high is not None and value > high):
        raise BadInput("ערך מחוץ לטווח: %s" % name)
    return value


def _need(params, *keys):
    missing = [k for k in keys if k not in params]
    if missing:
        raise NotConfigured(missing)
    return [params[k] for k in keys]


# ================================================================
#  נוסחאות
# ================================================================

def _percent_pension(params, inputs, *, salary_based):
    """
    נכות מעבודה (לפי שכר) ונכי צה"ל (לפי סכום בסיס):
      מעל סף הקצבה      -> קצבה חודשית = בסיס × שיעור × אחוז הנכות
      בין סף המענק לקצבה -> מענק חד-פעמי = אותו חישוב × מכפיל המענק
      מתחת לסף המענק    -> אין זכאות לפי המחשבון
    השיעור, הספים והמכפיל - פרמטרים, לא קבועים.
    """
    percent = _num(inputs, "percent", low=0, high=100)
    if salary_based:
        base = _num(inputs, "salary", low=0, high=1_000_000)
        (rate,) = _need(params, "pension_rate")
    else:
        (base,) = _need(params, "base_amount")
        rate = Decimal("1")
    pension_from, grant_from, grant_months = _need(
        params, "pension_threshold_percent", "grant_threshold_percent", "grant_multiplier")
    monthly = base * rate * percent / 100
    if percent >= pension_from:
        return {"kind": "monthly", "amount": monthly}
    if percent >= grant_from:
        return {"kind": "grant", "amount": monthly * grant_months}
    return {"kind": "none", "amount": Decimal("0")}


def _general_disability(params, inputs):
    """קצבה לפי דרגת אי-הכושר (טבלה) + תוספות לבן/בת זוג ולילדים."""
    degree = str(int(_num(inputs, "degree", integer=True)))
    key = "pension.%s" % degree
    if key not in params:
        options = [k.split(".", 1)[1] for k in params if k.startswith("pension.")]
        if not options:
            raise NotConfigured(["pension.<דרגה>"])
        raise BadInput("דרגת אי-כושר שאינה בטבלה: %s" % degree)
    amount = params[key]
    children = _num(inputs, "children", low=0, high=20, integer=True)
    if inputs.get("spouse"):
        (spouse_add,) = _need(params, "spouse_addition")
        amount += spouse_add
    if children:
        (child_add,) = _need(params, "child_addition")
        amount += child_add * children
    return {"kind": "monthly", "amount": amount}


def _table(params, inputs):
    choice = str(inputs.get("option") or "")
    key = "option." + choice
    if not any(k.startswith("option.") for k in params):
        raise NotConfigured(["option.<בחירה>"])
    if key not in params:
        raise BadInput("בחירה שאינה בטבלה.")
    return {"kind": "monthly", "amount": params[key]}


CALCULATORS = {
    "nii.general_disability": {
        "title": "קצבת נכות כללית (ביטוח לאומי)",
        "claims": ["general-disability"],
        "type": "formula",
        "inputs": [
            {"name": "degree", "label": "דרגת אי-כושר (%)", "type": "options_from", "prefix": "pension."},
            {"name": "spouse", "label": "בן/בת זוג", "type": "bool"},
            {"name": "children", "label": "מספר ילדים", "type": "int", "min": 0, "max": 20},
        ],
        "params": [("pension.<דרגה>", "קצבה חודשית לפי דרגת אי-כושר, לכל דרגה"),
                   ("spouse_addition", "תוספת לבן/בת זוג"),
                   ("child_addition", "תוספת לכל ילד")],
        "compute": _general_disability,
    },
    "nii.work_injury": {
        "title": "נכות מעבודה (ביטוח לאומי)",
        "claims": ["work-injury"],
        "type": "formula",
        "inputs": [
            {"name": "percent", "label": "אחוז נכות", "type": "int", "min": 0, "max": 100,
             "prefill": "decision_percent"},
            {"name": "salary", "label": "שכר חודשי ממוצע לפני הפגיעה (₪)", "type": "money"},
        ],
        "params": [("pension_rate", "שיעור הקצבה מהשכר (למשל 0.75)"),
                   ("pension_threshold_percent", "אחוז נכות מינימלי לקצבה חודשית"),
                   ("grant_threshold_percent", "אחוז נכות מינימלי למענק"),
                   ("grant_multiplier", "מכפיל המענק (בחודשי קצבה)")],
        "compute": lambda p, i: _percent_pension(p, i, salary_based=True),
    },
    "mod.idf_disability": {
        "title": "נכי צה\"ל (משרד הביטחון)",
        "claims": [],
        "type": "formula",
        "inputs": [
            {"name": "percent", "label": "דרגת נכות (%)", "type": "int", "min": 0, "max": 100,
             "prefill": "decision_percent"},
        ],
        "params": [("base_amount", "תגמול חודשי ל-100% נכות"),
                   ("pension_threshold_percent", "דרגת נכות מינימלית לתגמול חודשי"),
                   ("grant_threshold_percent", "דרגת נכות מינימלית למענק"),
                   ("grant_multiplier", "מכפיל המענק")],
        "compute": lambda p, i: _percent_pension(p, i, salary_based=False),
    },
    "nii.mobility": {
        "title": "גמלת ניידות (ביטוח לאומי)",
        "claims": ["general-disability", "work-injury"],
        "type": "table",
        "inputs": [{"name": "option", "label": "אחוז המוגבלות בניידות / מצב רכב",
                    "type": "options_from", "prefix": "option."}],
        "params": [("option.<בחירה>", "קצבה חודשית לכל מצב (המשרד מגדיר את הרשימה)")],
        "compute": _table,
    },
    "nii.special_services": {
        "title": "שירותים מיוחדים (ביטוח לאומי)",
        "claims": ["general-disability"],
        "type": "table",
        "inputs": [{"name": "option", "label": "רמת התלות בעזרת הזולת",
                    "type": "options_from", "prefix": "option."}],
        "params": [("option.<בחירה>", "קצבה חודשית לכל רמה")],
        "compute": _table,
    },
    "nii.disabled_child": {
        "title": "גמלת ילד נכה (ביטוח לאומי)",
        "claims": ["general-disability"],
        "type": "table",
        "inputs": [{"name": "option", "label": "שיעור הגמלה שנקבע",
                    "type": "options_from", "prefix": "option."}],
        "params": [("option.<בחירה>", "קצבה חודשית לכל שיעור")],
        "compute": _table,
    },
}


# ================================================================
#  פרמטרים בתוקף
# ================================================================

def active_params(cur, firm_id, calculator, on=None):
    """{key: (value, effective_from, source_url, label)} - הערך האחרון בתוקף לכל מפתח."""
    cur.execute(
        """select distinct on (key) key, value, effective_from, source_url, label
             from calc_parameters
            where firm_id = %s and calculator = %s and status = 'active'
              and effective_from <= %s
            order by key, effective_from desc, created_at desc""",
        (firm_id, calculator, on or date.today()))
    return {r["key"]: r for r in cur.fetchall()}


def options(rows, prefix):
    return [{"value": k[len(prefix):], "label": (rows[k]["label"] or k[len(prefix):])}
            for k in sorted(rows) if k.startswith(prefix)]


def compute(calculator, rows, inputs):
    """
    rows - active_params. מחזיר dict לתצוגה, או זורק NotConfigured/BadInput.
    """
    spec = CALCULATORS[calculator]
    params = {k: Decimal(str(r["value"])) for k, r in rows.items()}
    result = spec["compute"](params, inputs or {})
    margin = params.get("uncertainty_percent", DEFAULT_UNCERTAINTY) / 100
    amount = result["amount"]

    def r(v):
        return v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    used = sorted({k for k in rows if k in _used_keys(spec, inputs, rows)})
    return {
        "calculator": calculator,
        "kind": result["kind"],
        "low": r(amount * (1 - margin)),
        "high": r(amount * (1 + margin)),
        "disclaimer": DISCLAIMER,
        "basedOn": [{"key": k, "label": rows[k]["label"], "from": rows[k]["effective_from"].isoformat(),
                     "source": rows[k]["source_url"]} for k in used],
    }


def _used_keys(spec, inputs, rows):
    """לשקיפות: אילו פרמטרים השתתפו, עם תאריך ומקור - מוצג ללקוח."""
    keys = set()
    for name, _ in spec["params"]:
        if "<" in name:
            prefix = name.split("<", 1)[0]
            pick = inputs.get("degree") if prefix == "pension." else inputs.get("option")
            if pick is not None:
                keys.add(prefix + str(pick))
        else:
            keys.add(name)
    return keys & set(rows)
