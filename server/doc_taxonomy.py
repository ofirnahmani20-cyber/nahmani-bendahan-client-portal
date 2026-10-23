"""
doc_taxonomy.py - סוגי המסמכים שהמשרד מקבל. מקור האמת היחיד.

היררכיה של שלוש רמות:

    קטגוריה  ->  סוג  ->  תת-סוג (אופציונלי)
    medical      imaging   mri

קוד של סוג הוא נתיב: "imaging" או "imaging.mri". דרישה בתיק שמקבלת
"imaging" מקבלת כל תת-סוג שלו; דרישה שמקבלת "nii_form.250" מקבלת רק
אותו. הוספת סוג או תת-סוג היא שורה כאן ושורה ב-classify_rules.py -
בלי שינוי בסכמה, ב-API או במסך.

הרשימה נסנכרנת לטבלה document_kinds (sync), כדי שהמסד יוכל לאכוף
שסיווג מאושר הוא סוג קיים, וכדי שממשק מנהל עתידי יוכל לנהל אותה.

נוספו רק סוגים שהמשרד מקבל בפועל בתביעות ביטוח לאומי, נכות ותאונות
עבודה (החלטה מ-2026-10-02). "אחר" קיים בכל קטגוריה כדי שמסמך שזוהה
רק ברמת הקטגוריה לא ייאלץ לסוג שגוי.
"""

TAXONOMY_VERSION = "2026.10.1"

CATEGORIES = [
    ("medical", "מסמכים רפואיים"),
    ("opinion", "חוות דעת"),
    ("nii", "ביטוח לאומי"),
    ("employment", "העסקה והכנסה"),
    ("insurance", "ביטוח ופנסיה"),
    ("legal", "משפטי"),
    ("personal", "מסמכים אישיים"),
]

# (קוד, קטגוריה, תווית, [(תת-סוג, תווית)])
KINDS = [
    # --- רפואי
    ("hospital_discharge", "medical", "סיכום אשפוז", []),
    ("er_record", "medical", "תיעוד חדר מיון", []),
    ("physician_visit", "medical", "סיכום ביקור אצל רופא", []),
    ("sick_leave", "medical", "אישור מחלה", []),
    ("hmo_document", "medical", "מסמך קופת חולים", [
        ("referral", "הפניה"), ("medication_list", "רשימת תרופות"),
        ("history", "היסטוריה רפואית / גיליון")]),
    ("lab_results", "medical", "בדיקות מעבדה", []),
    ("imaging", "medical", "בדיקת דימות", [
        ("mri", "MRI"), ("ct", "CT"), ("xray", "רנטגן"), ("ultrasound", "אולטרסאונד")]),
    ("nerve_conduction", "medical", "EMG / הולכה עצבית", []),
    ("physiotherapy", "medical", "תיעוד פיזיותרפיה", []),
    ("psychiatric", "medical", "מסמך פסיכיאטרי", []),
    ("psychological", "medical", "אבחון / דוח פסיכולוגי", []),
    ("functional_report", "medical", "דוח תפקוד", []),
    ("occupational_physician", "medical", "מסמך רופא תעסוקתי", []),
    ("medical_other", "medical", "מסמך רפואי אחר", []),
    # --- חוות דעת
    ("treating_opinion", "opinion", "חוות דעת / מכתב רופא מטפל", []),
    ("expert_opinion", "opinion", "חוות דעת מומחה", []),
    # --- ביטוח לאומי
    ("nii_committee", "nii", "פרוטוקול ועדה רפואית", [
        ("first_instance", "ועדה מדרג ראשון"), ("appeal", "ועדה רפואית לעררים")]),
    ("nii_decision", "nii", "מכתב החלטה מביטוח לאומי", [
        ("approval", "אישור / קביעת זכאות"), ("rejection", "דחייה")]),
    ("nii_form", "nii", "טופס ביטוח לאומי", [
        ("7801", "7801 - תביעה לקצבת נכות כללית"), ("211", "211 - תביעה לדמי פגיעה"),
        ("250", "250 - הודעה על פגיעה בעבודה"), ("1811", "1811 - ויתור על סודיות רפואית")]),
    ("nii_other", "nii", "מסמך ביטוח לאומי אחר", []),
    # --- העסקה והכנסה
    ("payslip", "employment", "תלוש שכר", []),
    ("employment_confirmation", "employment", "אישור העסקה", []),
    ("employer_accident_report", "employment", "אישור מעסיק על תאונה", []),
    ("benefits_confirmation", "employment", "אישור על קצבה / גמלה אחרת", []),
    # --- ביטוח ופנסיה
    ("pension_fund", "insurance", "מסמך קרן פנסיה", []),
    ("insurance_policy", "insurance", "פוליסת ביטוח", []),
    ("insurer_decision", "insurance", "החלטת חברת ביטוח", [
        ("approval", "אישור"), ("rejection", "דחייה")]),
    # --- משפטי
    ("power_of_attorney", "legal", "ייפוי כוח", []),
    ("affidavit", "legal", "תצהיר", []),
    ("statement_of_claim", "legal", "כתב תביעה", []),
    ("appeal_filing", "legal", "כתב ערר / ערעור", []),
    ("court_decision", "legal", "החלטה / פסק דין", []),
    ("legal_other", "legal", "מסמך משפטי אחר", []),
    # --- אישי
    ("id_card", "personal", "תעודת זהות וספח", []),
]


def _build():
    out = {}
    for code, category, label, subtypes in KINDS:
        out[code] = {"code": code, "category": category, "label": label, "parent": None}
        for sub, sub_label in subtypes:
            path = "%s.%s" % (code, sub)
            out[path] = {"code": path, "category": category,
                         "label": "%s - %s" % (label, sub_label), "parent": code}
    return out


ALL = _build()
CATEGORY_LABELS = dict(CATEGORIES)


def label(code):
    return ALL[code]["label"] if code in ALL else code


def is_valid(code):
    return code in ALL


def accepts(accepted, kind):
    """
    'match' | 'mismatch' | 'undetermined'.

    דרישה שמקבלת "imaging" מקבלת "imaging.mri". דרישה שמקבלת
    "nii_form.250" ומסמך שזוהה רק כ-"nii_form" - undetermined: הסוג
    נכון, התת-סוג לא נקבע, ואדם צריך להחליט.
    """
    if not accepted or kind is None:
        return "undetermined"
    parent = kind.split(".")[0]
    for a in accepted:
        if a == kind or (a == parent and "." not in a) or kind.startswith(a + "."):
            return "match"
        if "." in a and a.split(".")[0] == kind:          # הדרישה ספציפית, הסיווג כללי
            return "undetermined"
    return "mismatch"


def sync(cur):
    """מסנכרן את הרשימה לטבלה document_kinds. סוג שהוסר מסומן לא-פעיל
    ולא נמחק - ייתכן שסיווגים מאושרים מפנים אליו."""
    for code, row in ALL.items():
        cur.execute(
            """insert into document_kinds (code, category, parent, label, active)
               values (%s, %s, %s, %s, true)
               on conflict (code) do update set category = excluded.category,
                   parent = excluded.parent, label = excluded.label, active = true""",
            (code, row["category"], row["parent"], row["label"]))
    cur.execute("update document_kinds set active = false where not (code = any(%s))",
                (list(ALL),))
