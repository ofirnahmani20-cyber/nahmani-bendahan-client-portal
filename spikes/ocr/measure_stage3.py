"""
measure_stage3.py - מדדי איכות של צנרת הייצור (שלב 3) על הקורפוס הסינתטי.

    python spikes/ocr/measure_stage3.py

שלא כמו run_spike.py, כאן רץ הקוד האמיתי: worker.run_sandbox עם
המשימה extract - אותו sandbox, אותן מגבלות, אותו textfix ו-ocr.
עובדות נספרות בכללי ה-spike (pipeline.extract_facts) - רק כמדד:
שלב 3 אינו שומר עובדות.

השאלה שההחלטה מ-2026-10-02 דורשת לענות עליה: האם hybrid (fast, ו-best
לעמוד/שורה עם ביטחון נמוך) **מוסיף שגיאות** לעומת fast. לכל עמוד
ולכל עובדה נבדק מה היה נכון ב-fast והפך לשגוי ב-hybrid.

פלט: spikes/ocr/STAGE3-QUALITY.md (נכנס ל-git), ו-JSON מלא ב-
%LOCALAPPDATA%\\nahmani-ocr-spike\\out\\stage3.json.
"""

import json
import os
import pathlib
import statistics
import sys
import time
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from server import textfix, worker  # noqa: E402

import pipeline as spike  # noqa: E402  (כללי העובדות - למדידה בלבד)

HOME = pathlib.Path(os.environ["LOCALAPPDATA"]) / "nahmani-ocr-spike"
CORPUS = HOME / "corpus"
OUT_MD = pathlib.Path(__file__).resolve().parent / "STAGE3-QUALITY.md"

VARIANTS = {"pdf-text": ".pdf", "pdf-visual": ".visual.pdf", "scan-300dpi": ".scan.png",
            "scan-degraded": ".degraded.jpg", "phone-photo": ".phone.jpg"}
# תצורות שנמדדות. hybrid-replace היא מה שנבנה ראשון; המדידה הראתה
# שהיא מוסיפה שגיאות מספריות, ולכן נבדקו שתי חלופות שמרניות.
CONFIGS = {
    "fast":           {"SANDBOX_OCR_MODE": "fast"},
    "best":           {"SANDBOX_OCR_MODE": "best"},
    "hybrid-replace": {"SANDBOX_OCR_MODE": "hybrid", "SANDBOX_PAGE_SWITCH": "1",
                       "SANDBOX_LINE_MODE": "replace"},
    "hybrid-page":    {"SANDBOX_OCR_MODE": "hybrid", "SANDBOX_PAGE_SWITCH": "1",
                       "SANDBOX_LINE_MODE": "off"},
    "fast+alt":       {"SANDBOX_OCR_MODE": "hybrid", "SANDBOX_PAGE_SWITCH": "0",
                       "SANDBOX_LINE_MODE": "alternative"},
}
OCR_MODES = list(CONFIGS)
DEFAULT = "fast"
TYPE_NAMES = {"discharge": "סיכום אשפוז", "emg": "EMG", "mri": "MRI",
              "opinion": "חוות דעת", "form7801": "טופס תביעה", "committee": "החלטת ועדה"}
NUMERIC = {"date", "percent", "icd10"}


def flat(t):
    return " ".join(textfix.normalize(t).split())


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def facts_of(page):
    norm = spike.normalize(page["text"])
    got = {(f["kind"], f["value"]) for f in spike.extract_facts(norm)}
    got |= {("icd10", c["code"]) for c in page.get("icd", [])}
    return got


def alt_text(page):
    """הטקסט כפי שהיה אילו כל קריאה חלופית הייתה מחליפה את שורתה."""
    lines = page["text"].split("\n")
    for alt in page.get("alternatives", []):
        if alt["segment"] < len(lines):
            lines[alt["segment"]] = alt["text"]
    return "\n".join(lines)


def main():
    if sys.argv[1:] == ["report"]:
        report(json.loads((HOME / "out" / "stage3.json").read_text(encoding="utf-8")))
        return
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    truths = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted(CORPUS.glob("*.truth.json"))]
    out_json = HOME / "out" / "stage3.json"
    rows = []
    if only and out_json.exists():
        rows = [r for r in json.loads(out_json.read_text(encoding="utf-8"))
                if r["mode"] not in only]
    for t in truths:
        ref = flat(t["text"])
        expected = {(f["kind"], f["value"]) for f in t["facts"] if f["kind"] != "doc_type"}
        for variant, suffix in VARIANTS.items():
            modes = OCR_MODES if not variant.startswith("pdf") else ["layer"]
            for mode in modes:
                if only and mode not in only:
                    continue
                env = worker.extract_env([1])
                env.update(CONFIGS.get(mode, {}))
                started = time.perf_counter()
                outcome, res = worker.run_sandbox((CORPUS / (t["id"] + suffix)).read_bytes(),
                                                  timeout=120, extra_env=env)
                seconds = time.perf_counter() - started
                if outcome != "ok":
                    rows.append({"doc": t["id"], "type": t["type"], "variant": variant,
                                 "mode": mode, "error": res})
                    print(t["id"], variant, mode, "ERROR", res, flush=True)
                    continue
                page = res["pages"][0]
                got = facts_of(page)
                alt_got = (facts_of(dict(page, text=alt_text(page)))
                           if page.get("alternatives") else got)
                rows.append({
                    "doc": t["id"], "type": t["type"], "variant": variant, "mode": mode,
                    "model": page["model"], "confidence": page.get("confidence"),
                    "cer": lev(ref, flat(page["text"])) / len(ref), "seconds": seconds,
                    "expected": sorted(map(list, expected)),
                    "correct": sorted(map(list, expected & got)),
                    "spurious": sorted(map(list, got - expected)),
                    "alternatives": len(page.get("alternatives", [])),
                    # עובדה שהקריאה החלופית אינה מסכימה איתה = "לבדיקה"
                    "disputed": sorted(map(list, got - alt_got)),
                })
                r = rows[-1]
                print("%-12s %-13s %-6s %-9s cer=%.3f facts=%d/%d %.1fs" % (
                    t["id"], variant, mode, r["model"], r["cer"], len(r["correct"]),
                    len(expected), seconds), flush=True)

    (HOME / "out").mkdir(exist_ok=True)
    out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
    report(rows)


def _agg(rs):
    ok = [r for r in rs if "error" not in r]
    exp = sum(len(r["expected"]) for r in ok)
    cor = sum(len(r["correct"]) for r in ok)
    spu = sum(len(r["spurious"]) for r in ok)
    return {"n": len(rs), "errors": len(rs) - len(ok),
            "cer": statistics.mean(r["cer"] for r in ok) if ok else None,
            "recall": cor / exp if exp else None,
            "precision": cor / (cor + spu) if cor + spu else None,
            "seconds": statistics.mean(r["seconds"] for r in ok) if ok else None}


def pct(x):
    return "-" if x is None else "%.1f%%" % (100 * x)


def _compare(rows, config):
    """config מול fast, אותו עמוד: CER, עובדות שאבדו, ושגויות חדשות."""
    pairs = defaultdict(dict)
    for r in rows:
        if "error" not in r and r["mode"] in ("fast", config):
            pairs[(r["doc"], r["variant"])][r["mode"]] = r
    out = {"pages": 0, "switched": 0, "better": 0, "same": 0, "worse": [],
           "lost": [], "new_wrong": [], "gained": 0}
    for (doc, variant), m in sorted(pairs.items()):
        if "fast" not in m or config not in m:
            continue
        f, c = m["fast"], m[config]
        out["pages"] += 1
        out["switched"] += c["model"] != "fast"
        if c["cer"] > f["cer"] + 0.005:
            out["worse"].append("%s/%s: %.1f%% -> %.1f%%" % (doc, variant, 100 * f["cer"],
                                                            100 * c["cer"]))
        elif c["cer"] < f["cer"] - 0.005:
            out["better"] += 1
        else:
            out["same"] += 1
        fc, cc = {tuple(x) for x in f["correct"]}, {tuple(x) for x in c["correct"]}
        fs, cs = {tuple(x) for x in f["spurious"]}, {tuple(x) for x in c["spurious"]}
        out["lost"] += ["%s/%s: %s=%s" % (doc, variant, k, v) for k, v in sorted(fc - cc)]
        out["new_wrong"] += ["%s/%s: %s=%s" % (doc, variant, k, v) for k, v in sorted(cs - fs)]
        out["gained"] += len(cc - fc)
    return out


def report(rows):
    layer = {"pdf-text", "pdf-visual"}
    L = ["# מדדי איכות - שלב 3 (צנרת הייצור, קורפוס סינתטי)", "",
         "> נוצר על ידי `spikes/ocr/measure_stage3.py` · 18 מסמכים סינתטיים · "
         "Tesseract 5.5.3 · Windows (פיתוח). הקוד שנמדד הוא ה-sandbox של הייצור.",
         "> CER = שיעור שגיאות תווים. עובדות = תאריכים, ICD, אחוזים, רופא, מוסד, סוג "
         "בדיקה (כללי ה-spike, למדידה בלבד - שלב 3 אינו שומר עובדות).",
         "> תצורת ברירת המחדל שנבחרה: **`%s`**." % DEFAULT, "",
         "## ההחלטה", "",
         "ההנחיה הייתה fast כברירת מחדל ו-best לעמודים עם ביטחון נמוך, **בתנאי שהמעבר "
         "אינו יוצר שגיאות חדשות**. הטבלה \"האם המעבר ל-best מוסיף שגיאות\" למטה מראה "
         "שהתנאי אינו מתקיים באף צורת מעבר שנבדקה:", "",
         "- `best` קורא 2026 כ-**2076** - שגיאה מספרית שנראית סבירה.",
         "- מעבר ברמת שורה (`hybrid-replace`) הוסיף 2 תאריכים שגויים ואיבד 6 עובדות נכונות.",
         "- מעבר ברמת עמוד (`hybrid-page`) לא הוסיף שגויות, אבל איבד 2 עובדות נכונות "
         "(קוד ICD ושם רופא).",
         "- קריאה חלופית בלי החלפה (`fast+alt`) לא תפסה אף אחד מ-9 המספרים השגויים, "
         "והוסיפה 3 התרעות שווא.", "",
         "לכן ברירת המחדל היא **fast בלבד**. ביטחון (confidence) מודד כמה Tesseract "
         "בטוח, לא האם הוא צודק. התצורות האחרות זמינות בהגדרה, למדידה חוזרת.", ""]

    L += ["## לפי סוג מסמך (ברירת המחדל: %s לסריקות, שכבת טקסט ל-PDF)" % DEFAULT, "",
          "| סוג | PDF דיגיטלי | סריקה 300dpi | סריקה גרועה | צילום טלפון | PDF הפוך (ישן) | "
          "recall עובדות (סריקות) | precision (סריקות) |", "|---|---|---|---|---|---|---|---|"]
    for typ, name in TYPE_NAMES.items():
        cells = []
        for variant in ("pdf-text", "scan-300dpi", "scan-degraded", "phone-photo", "pdf-visual"):
            mode = "layer" if variant in layer else DEFAULT
            cells.append(pct(_agg([r for r in rows if r["type"] == typ
                                   and r["variant"] == variant and r["mode"] == mode])["cer"]))
        a = _agg([r for r in rows if r["type"] == typ and r["mode"] == DEFAULT])
        L.append("| %s | %s | %s | %s |" % (name, " | ".join(cells), pct(a["recall"]),
                                            pct(a["precision"])))

    L += ["", "## השוואת תצורות (סריקות וצילומים)", "",
          "| גרסה | תצורה | CER | recall | precision | שניות לעמוד |", "|---|---|---|---|---|---|"]
    for variant in ("scan-300dpi", "scan-degraded", "phone-photo"):
        for mode in OCR_MODES:
            a = _agg([r for r in rows if r["variant"] == variant and r["mode"] == mode])
            if a["n"]:
                L.append("| %s | %s | %s | %s | %s | %.1f |" % (
                    variant, mode, pct(a["cer"]), pct(a["recall"]), pct(a["precision"]),
                    a["seconds"] or 0))
    for variant in sorted(layer):
        a = _agg([r for r in rows if r["variant"] == variant])
        L.append("| %s | layer | %s | %s | %s | %.2f |" % (
            variant, pct(a["cer"]), pct(a["recall"]), pct(a["precision"]), a["seconds"] or 0))

    L += ["", "## האם המעבר ל-best מוסיף שגיאות? (כל תצורה מול fast, אותו עמוד)", "",
          "| תצורה | עמודים שעברו ל-best | CER טוב יותר | ללא שינוי | CER גרוע יותר | "
          "עובדות נכונות שאבדו | **עובדות שגויות חדשות** | עובדות נכונות שנוספו |",
          "|---|---|---|---|---|---|---|---|"]
    details = []
    for config in ("hybrid-replace", "hybrid-page", "best", "fast+alt"):
        c = _compare(rows, config)
        if not c["pages"]:
            continue
        L.append("| %s | %d/%d | %d | %d | %d | %d | **%d** | %d |" % (
            config, c["switched"], c["pages"], c["better"], c["same"], len(c["worse"]),
            len(c["lost"]), len(c["new_wrong"]), c["gained"]))
        if c["lost"] or c["new_wrong"]:
            details += ["", "**%s**" % config]
            details += ["- אבדה: " + x for x in c["lost"]]
            details += ["- שגויה חדשה: " + x for x in c["new_wrong"]]
    L += details

    # ערך הקריאה החלופית: האם היא מסמנת את המספרים השגויים של fast?
    alt = [r for r in rows if r["mode"] == "fast+alt" and "error" not in r]
    if alt:
        wrong_numeric = [(r["doc"], r["variant"], k, v) for r in alt
                         for k, v in r["spurious"] if k in NUMERIC]
        disputed = {(r["doc"], r["variant"], k, v) for r in alt for k, v in r["disputed"]}
        caught = [x for x in wrong_numeric if x in disputed]
        correct_numeric = [(r["doc"], r["variant"], k, v) for r in alt
                           for k, v in r["correct"] if k in NUMERIC]
        false_alarm = [x for x in correct_numeric if x in disputed]
        L += ["", "## הקריאה החלופית כאות לבדיקה (fast+alt)", "",
              "שורה חלשה נקראת שוב ב-best, **והטקסט אינו מוחלף**. עובדה ששתי הקריאות חלוקות "
              "עליה מסומנת \"לבדיקה\" בשלב 5.", "",
              "- שורות עם קריאה חלופית: %d, ב-%d מתוך %d עמודים" % (
                  sum(r["alternatives"] for r in alt), sum(1 for r in alt if r["alternatives"]),
                  len(alt)),
              "- מספרים שגויים שהפיק fast: **%d** · מתוכם סומנו כחלוקים: **%d**" % (
                  len(wrong_numeric), len(caught)),
              "- מספרים נכונים שסומנו כחלוקים (התרעת שווא - מוסיפה בדיקה, לא שגיאה): %d מתוך %d" % (
                  len(false_alarm), len(correct_numeric)),
              "", "> גם מספר שלא סומן **אינו** נכנס לתיק בלי אישור אדם. הסימון קובע מה "
              "יוצג ראשון ובולט, לא מה מותר לדלג עליו."]

    L += ["", "## שגיאות מספריות שה-OCR הפיק (%s)" % DEFAULT, "",
          "**אף אחת מהן אינה נכנסת לתיק בלי אישור אדם** (`fact_review_has_reviewer`, "
          "`requiresHumanVerification`).", ""]
    numeric = sorted({(r["doc"], r["variant"], k, v) for r in rows
                      if "error" not in r and r["mode"] == DEFAULT
                      for k, v in r["spurious"] if k in NUMERIC})
    L += ["- `%s` / %s: %s=%s" % x for x in numeric] or ["- אין"]

    errors = [r for r in rows if "error" in r]
    L += ["", "## כשלים", "", ("- %d ריצות נכשלו: %s" % (len(errors), ", ".join(
        "%s/%s/%s=%s" % (r["doc"], r["variant"], r["mode"], r["error"]) for r in errors))
        if errors else "- אין. כל %d הריצות הסתיימו." % len(rows))]
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\nreport:", OUT_MD)


if __name__ == "__main__":
    main()
