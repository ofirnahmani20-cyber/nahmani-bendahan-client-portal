"""
classify.py - סיווג מסמך לפי כללים דטרמיניסטיים. פונקציה טהורה, בלי I/O.

קלט: עמודי הטקסט שחולצו (שלב 3). פלט: הצעה, ונימוק שכולו מזהי כללים,
ביטויים מתוך המילון, עמוד, שורה ומשקל - לעולם לא טקסט מהמסמך.

ההחלטות שהקוד הזה אוכף (2026-10-02):
  * אין סיווג ברור בלי ביטוי כותרת באזור הכותרת.
  * כשיש ספק - שני סוגים קרובים, או עמודים שסותרים זה את זה - אין
    בחירה. המסמך מסומן לבדיקה אנושית.
  * מספר (מספר טופס) תומך בלבד. OCR מחליף ספרות.
  * הסיווג הוא הצעה. הוא אינו נוגע בסטטוס המסמך או בדרישה.

ביצועים: הביטויים מתורגמים פעם אחת לביטויים רגולריים פשוטים (בלי
קינון, בלי חזרות על קבוצות), והטקסט לכל עמוד נחתך ל-MAX_PAGE_CHARS -
טקסט עוין אינו יכול לגרום לזמן ריצה מעריכי.
"""

import re

from . import classify_rules as R
from . import textfix

MAX_PAGE_CHARS = 100_000
MIN_READABLE_CHARS = 40
MIN_OCR_CONFIDENCE = 50.0
CLEAR_MIN = R.WEIGHTS["title_header"]      # בלי כותרת באזור הכותרת - אין סיווג ברור
MARGIN = 5                                 # פער מינימלי בין הסוג הראשון לשני
MAX_SUPPORT = 4                            # תומכים לא "יקנו" סיווג בלי כותרת

DECISIONS = ("clear", "ambiguous", "unknown", "unreadable", "mixed")

_HEB = re.compile(r"[א-ת]")
_PREFIX = "(?:[והבלמשכ]{1,2})?"   # ו ה ב ל מ ש כ


def _norm(text):
    text = textfix.normalize(text).replace("''", '"').replace("”", '"')
    return text.replace("“", '"')


def _pattern(phrase):
    phrase = _norm(phrase)
    body = r"\s+".join(re.escape(w) for w in phrase.split())
    if _HEB.search(phrase[:1]):
        return re.compile(r"(?<![\wא-ת])" + _PREFIX + body + r"(?![\wא-ת])")
    return re.compile(r"(?<![\wא-ת])" + body + r"(?![\wא-ת])")


def _compile():
    compiled = {}
    for kind, spec in R.RULES.items():
        c = {}
        for role in ("title", "support", "negative"):
            c[role] = [("%s:%s:%d" % (kind, role, i), p, _pattern(p))
                       for i, p in enumerate(spec.get(role, []))]
        c["numbers"] = [("%s:number:%d" % (kind, i), p, re.compile(p))
                        for i, p in enumerate(spec.get("numbers", []))]
        c["subtypes"] = {sub: [_pattern(p) for p in phrases]
                         for sub, phrases in spec.get("subtypes", {}).items()}
        compiled[kind] = c
    return compiled


_RULES = _compile()


def _find(pattern, lines):
    """(מספר שורה מבוסס 1) של המופע הראשון, או None."""
    for i, line in enumerate(lines):
        if pattern.search(line):
            return i + 1
    return None


# ----------------------------------------------------------------
#  כותרת - מתי ביטוי הוא כותרת ולא אזכור
# ----------------------------------------------------------------
#  ביטוי כותרת מכריע רק כשהוא פותח את השורה, או בא מיד אחרי מפריד
#  ("מדינת ישראל - תעודת זהות"). "במהלך האשפוז בוצעה בדיקת MRI" ו-
#  "מומלץ להשלים חוות דעת רפואית" אינן כותרות, גם בשורות הראשונות
#  (מדידה ראשונה: סיווג שגוי בביטחון). כותרת שמוטמעת באמצע משפט
#  תוביל ל"לא ידוע" - כלומר לבדיקה אנושית, לא לניחוש.
# ----------------------------------------------------------------

FUZZY_TITLE_WEIGHT = 8          # התאמה חלקית ב-OCR - פחות מכותרת מלאה (10)
CONTINUATION = re.compile(r"^\s*(?:המשך|עמוד\s*\d+\s*(?:מתוך|/))")
_SEPARATORS = "-:|–—"


def _title_line(pattern, line):
    m = pattern.search(line)
    if not m:
        return False
    before = line[:m.start()].rstrip()
    return not before or before[-1] in _SEPARATORS


def _lev1(a, b):
    """האם מרחק העריכה בין a ל-b הוא לכל היותר 1."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            edits += 1
            if edits > 1:
                return False
            if len(a) == len(b):
                i += 1
            j += 1
        else:
            i += 1
            j += 1
    return edits + (len(b) - j) <= 1


def _fuzzy_title(phrase, line):
    """
    OCR בלבד, כותרת בלבד: כל מילה של הביטוי מול מילה רצופה בתחילת
    השורה, עם עד שגיאת תו אחת למילה ארוכה (4+ אותיות) ובלי שגיאה
    למילה קצרה. "סיכום אשפו" -> "סיכום אשפוז". דטרמיניסטי.
    """
    want = _norm(phrase).split()
    got = line.split()[:len(want)]
    if len(got) < len(want) or not any(len(w) >= 4 for w in want):
        return False
    return all(g == w or (len(w) >= 4 and _lev1(g.strip(":.,"), w)) for g, w in zip(got, want))


def classify_page(page):
    """סיווג עמוד אחד: dict עם decision, kind, scores, reasons."""
    text = _norm(page.get("text") or "")[:MAX_PAGE_CHARS]
    lines = text.split("\n")
    result = {"page": page["page"], "decision": None, "kind": None,
              "scores": {}, "reasons": {}, "title": set()}

    readable_chars = len(re.sub(r"\s", "", text))
    conf = page.get("confidence")
    if readable_chars < MIN_READABLE_CHARS or (
            page.get("source") == "ocr" and conf is not None and conf < MIN_OCR_CONFIDENCE):
        result["decision"] = "unreadable"
        return result

    header = lines[:R.HEADER_LINES]
    is_ocr = page.get("source") == "ocr"
    # "המשך..." בראש העמוד: זה עמוד המשך, ושום ביטוי בו אינו כותרת.
    continuation = bool(lines and CONTINUATION.match(lines[0]))
    for kind, c in _RULES.items():
        score, reasons = 0, []

        for rule_id, phrase, pat in c["title"]:
            at = None if continuation else next(
                (i + 1 for i, line in enumerate(header) if _title_line(pat, line)), None)
            fuzzy = None
            if at is None and is_ocr and not continuation:
                fuzzy = next((i + 1 for i, line in enumerate(header)
                              if _fuzzy_title(phrase, line)), None)
            if at is not None or fuzzy is not None:
                w = R.WEIGHTS["title_header"] if at is not None else FUZZY_TITLE_WEIGHT
                reason = {"rule": rule_id, "phrase": phrase, "line": at or fuzzy,
                          "zone": "header", "weight": w}
                if at is None:
                    reason["approximate"] = True       # מוצג במסך: "התאמה חלקית (OCR)"
                reasons.append(reason)
                result["title"].add(kind)
            else:
                at = _find(pat, lines)
                if at is None:
                    continue
                w = R.WEIGHTS["title_body"]
                reasons.append({"rule": rule_id, "phrase": phrase, "line": at,
                                "zone": "body", "weight": w})
            score += w

        support = 0
        for rule_id, phrase, pat in c["support"]:
            if support >= MAX_SUPPORT:
                break
            at = _find(pat, lines)
            if at is not None:
                support += 1
                w = R.WEIGHTS["support"]
                score += w
                reasons.append({"rule": rule_id, "phrase": phrase, "line": at,
                                "zone": "header" if at <= R.HEADER_LINES else "body",
                                "weight": w})

        for rule_id, phrase, pat in c["negative"]:
            at = _find(pat, lines)
            if at is not None:
                w = R.WEIGHTS["negative"]
                score += w
                reasons.append({"rule": rule_id, "phrase": phrase, "line": at,
                                "zone": "header" if at <= R.HEADER_LINES else "body",
                                "weight": w})

        # מספר טופס: רק אם כבר יש סימן מילולי לאותו סוג. לבדו - כלום.
        if reasons:
            for rule_id, pattern, pat in c["numbers"]:
                at = _find(pat, lines)
                if at is not None:
                    w = R.WEIGHTS["number"]
                    score += w
                    reasons.append({"rule": rule_id, "phrase": "מספר טופס", "line": at,
                                    "zone": "header" if at <= R.HEADER_LINES else "body",
                                    "weight": w})

        if reasons:
            result["scores"][kind] = score
            result["reasons"][kind] = reasons

    candidates = sorted((k for k in result["title"] if result["scores"].get(k, 0) >= CLEAR_MIN),
                        key=lambda k: (-result["scores"][k], k))
    if not candidates:
        result["decision"] = "unknown"
    elif len(candidates) > 1 and \
            result["scores"][candidates[0]] - result["scores"][candidates[1]] < MARGIN:
        result["decision"] = "ambiguous"
        result["tied"] = candidates[:2]
    else:
        result["decision"] = "clear"
        result["kind"] = candidates[0]
    return result


def _subtype(kind, pages_text):
    """תת-סוג רק כשהוא חד-משמעי: תת-סוג אחד עם הכי הרבה התאמות."""
    subs = _RULES[kind]["subtypes"]
    if not subs:
        return kind
    hits = {sub: sum(1 for pat in pats if pat.search(pages_text)) for sub, pats in subs.items()}
    best = max(hits.values())
    winners = [s for s, h in hits.items() if h == best]
    if best == 0 or len(winners) > 1:
        return kind
    return "%s.%s" % (kind, winners[0])


def classify_document(pages):
    """
    pages: [{page, text, source, confidence}] (כפי ש-read_pages מחזיר, או
    כפי שה-sandbox מחזיר). מחזיר את ההצעה למסמך כולו.
    """
    results = [classify_page(p) for p in sorted(pages, key=lambda p: p["page"])]

    # עמוד בלי כותרת שבא אחרי עמוד ברור - המשך של אותו מסמך, לא מסמך זר.
    page_kinds, current = [], None
    for r in results:
        if r["decision"] == "clear":
            current = r["kind"]
            page_kinds.append({"page": r["page"], "decision": "clear", "kind": r["kind"]})
        elif r["decision"] == "unknown" and current:
            page_kinds.append({"page": r["page"], "decision": "continuation", "kind": current})
        else:
            page_kinds.append({"page": r["page"], "decision": r["decision"], "kind": None,
                               "tied": r.get("tied")})

    clear_kinds = []
    for pk in page_kinds:
        if pk["decision"] == "clear" and pk["kind"] not in clear_kinds:
            clear_kinds.append(pk["kind"])

    totals = {}
    for r in results:
        for k, s in r["scores"].items():
            totals[k] = totals.get(k, 0) + s
    ranked = sorted(totals, key=lambda k: (-totals[k], k))[:3]

    if len(clear_kinds) > 1:
        decision = "mixed"
    elif any(r["decision"] == "ambiguous" for r in results):
        decision = "ambiguous"            # סתירה - לבדיקה אנושית, גם אם עמוד אחר ברור
    elif clear_kinds:
        decision = "clear"
    elif results and all(r["decision"] == "unreadable" for r in results):
        decision = "unreadable"
    else:
        decision = "unknown"

    kind = None
    if decision == "clear":
        text = "\n".join(_norm(p.get("text") or "")[:MAX_PAGE_CHARS] for p in pages)
        kind = _subtype(clear_kinds[0], text)

    top = totals.get(ranked[0], 0) if ranked else 0
    second = totals.get(ranked[1], 0) if len(ranked) > 1 else 0
    reasons = [dict(reason, page=r["page"], kind=k)
               for r in results for k in ranked for reason in r["reasons"].get(k, [])]
    return {
        "decision": decision,
        "kind": kind,
        "score": max(top, 0),
        "margin": max(top - second, 0),
        "candidates": [{"kind": k, "score": totals[k]} for k in ranked],
        "reasons": reasons,
        "page_kinds": page_kinds,
        "unreadable_pages": [r["page"] for r in results if r["decision"] == "unreadable"],
        "rules_version": R.RULES_VERSION,
    }
