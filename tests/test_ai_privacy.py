"""
PII אינו יוצא לספק ה-AI.

עד 13.09 assert_clean בדק *שמות שדות* בלבד, ולכן ת"ז שנכתבה בתוך
שדה note מותר עברה בשלום. הבדיקות כאן מכסות את שתי השכבות:
החיטוי (redact) ורשת הביטחון (assert_clean).
"""

import pytest

from server.policy import assert_clean, build_context
from server.redact import find_pii, is_israeli_id, redact

# ת"ז עם ספרת ביקורת תקינה - אלה נחסמות.
# שים לב: 987654321 (אחת מתעודות ההדגמה) *אינה* ת"ז חוקית, ולכן
# היא לא תיחסם. זה נכון ומכוון - החיסום הוא על ת"ז אמיתית.
VALID_IDS = ["123456782", "100000009", "100000017"]


def _case_with(note="", reject=""):
    return {
        "claimType": "נכות כללית",
        "currentStage": 4,
        "documents": [{
            "name": "סיכומי אשפוז", "required": True, "status": "rejected",
            "note": note, "rejectReason": reject, "file": "x.pdf",
        }],
    }


@pytest.mark.parametrize("nid", VALID_IDS)
def test_israeli_id_is_detected(nid):
    assert is_israeli_id(nid)
    assert "israeli_id" in find_pii("ת\"ז %s" % nid)


def test_nine_digits_that_is_not_an_id_is_kept():
    """מספר תיק בן תשע ספרות אינו נמחק - חיטוי יתר פוגע בתועלת."""
    assert not is_israeli_id("123456789")
    assert "123456789" in redact("תיק מספר 123456789")


@pytest.mark.parametrize("text,kind", [
    ("נייד 052-1234567", "phone"),
    ("טלפון 03-5551234", "phone"),
    ("נייד +972521234567", "phone"),
    ("מייל israel@example.co.il", "email"),
    ("IL620108000000099999999", "iban"),
    ("כרטיס 4580 1234 5678 9012", "credit_card"),
])
def test_pattern_is_detected_and_removed(text, kind):
    assert kind in find_pii(text)
    assert kind not in find_pii(redact(text))


@pytest.mark.parametrize("field", ["note", "reject"])
def test_pii_in_free_text_does_not_reach_the_model(field):
    """הבדיקה שנכשלה לפני התיקון."""
    payload = "לתאם מול הלקוח, ת\"ז 123456782, נייד 052-1234567, מייל a@b.com"
    case = _case_with(**{field: payload})
    ctx = build_context(case)
    doc = ctx["documents"][0]
    blob = "%s %s" % (doc.get("note") or "", doc.get("rejectReason") or "")
    assert find_pii(blob) == [], "PII שרד את החיטוי: %s" % blob
    assert_clean(ctx)          # לא אמור לזרוק


def test_failsafe_catches_pii_injected_after_redaction():
    """אם מישהו יעקוף את החיטוי בעתיד, רשת הביטחון עדיין תופסת."""
    ctx = build_context(_case_with(note="בסדר"))
    ctx["documents"][0]["note"] = "ת\"ז 123456782"
    with pytest.raises(ValueError, match="israeli_id"):
        assert_clean(ctx)


def test_blocked_field_name_still_raises():
    """הבדיקה הישנה לפי שם שדה לא נשברה."""
    ctx = build_context(_case_with())
    ctx["phone"] = "050-0000000"
    with pytest.raises(ValueError):
        assert_clean(ctx)


def test_document_filename_never_leaves():
    """שם הקובץ מוחלף ב-hasFile בלבד."""
    ctx = build_context(_case_with())
    doc = ctx["documents"][0]
    assert "file" not in doc
    assert doc["hasFile"] is True
