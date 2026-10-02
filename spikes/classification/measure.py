"""
measure.py - מדדי הסיווג (שלב 4) על הקורפוס הסינתטי, בצנרת הייצור.

    python spikes/classification/measure.py

לכל מסמך, בשתי גרסאות (PDF דיגיטלי, וסריקה שעוברת OCR): sandbox
inspect -> extract באצוות -> classify.classify_document. בדיוק מה
שה-worker עושה.

מה נמדד (ההחלטה מ-2026-10-02):
  * precision ו-recall לכל סוג מסמך
  * שיעור המסמכים שנותרו בלי סיווג (דו-משמעי / לא ידוע / לא קריא)
  * סיווג שגוי בביטחון - המדד החשוב. היעד: 0.
  * תת-סוג: נכון / לא נקבע / שגוי
  * מעורב, המשך, לא ידוע, לא קריא - כל קבוצה מול ההתנהגות הנדרשת

פלט: spikes/classification/STAGE4-RESULTS.md
"""

import json
import os
import pathlib
import sys
import time
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server import classify, doc_taxonomy, worker  # noqa: E402

CORPUS = pathlib.Path(os.environ.get("CLASSIFY_CORPUS") or
                      pathlib.Path(os.environ["LOCALAPPDATA"]) / "nahmani-classify-corpus")
OUT = pathlib.Path(__file__).resolve().parent / os.environ.get("CLASSIFY_REPORT",
                                                               "STAGE4-RESULTS.md")


def run(path):
    data = path.read_bytes()
    outcome, info = worker.run_sandbox(data)
    if outcome != "ok":
        return None, "inspect:%s" % info
    pages = []
    numbers = [p["page"] for p in info["pages"]]
    for i in range(0, len(numbers), worker.BATCH_PAGES):
        outcome, res = worker.run_sandbox(data, timeout=180,
                                          extra_env=worker.extract_env(numbers[i:i + 4]))
        if outcome != "ok":
            return None, "extract:%s" % res
        pages += res["pages"]
    return classify.classify_document(pages), None


def base(kind):
    return kind.split(".")[0] if kind else None


def main():
    rows = []
    truths = sorted(CORPUS.glob("*.truth.json"))
    for tp in truths:
        name = tp.name[:-len(".truth.json")]
        truth = json.loads(tp.read_text(encoding="utf-8"))
        variants = [("scan", CORPUS / (name + ".scan.pdf"))]
        if (CORPUS / (name + ".pdf")).exists():
            variants.insert(0, ("pdf", CORPUS / (name + ".pdf")))
        for variant, path in variants:
            t0 = time.perf_counter()
            result, error = run(path)
            row = {"doc": name, "variant": variant, "group": truth["group"],
                   "expect": truth["expect"], "truth_kind": truth.get("kind"),
                   "truth_pages": truth.get("pages"), "seconds": time.perf_counter() - t0}
            if error:
                row["error"] = error
            else:
                row.update(decision=result["decision"], kind=result["kind"],
                           candidates=result["candidates"], page_kinds=result["page_kinds"])
            rows.append(row)
            print("%-42s %-4s %-10s %-28s %s" % (name, variant, row.get("decision"),
                  row.get("kind"), truth.get("kind") or truth["expect"]), flush=True)
    (CORPUS / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    report(rows)


def pct(a, b):
    return "-" if not b else "%.0f%%" % (100.0 * a / b)


def report(rows):
    from server import classify_rules
    title = os.environ.get("CLASSIFY_TITLE", "תוצאות סיווג - שלב 4 (קורפוס סינתטי, צנרת הייצור)")
    L = ["# " + title, "",
         "> נוצר על ידי `spikes/classification/measure.py`. %d מסמכים סינתטיים, כל אחד כ-PDF "
         "דיגיטלי וכסריקה (OCR fast). כללים: `%s`, טקסונומיה: `%s`." % (
             len({r["doc"] for r in rows}), classify_rules.RULES_VERSION,
             doc_taxonomy.TAXONOMY_VERSION), ""]

    expect_clear = [r for r in rows if r["expect"] == "clear"]
    errors = [r for r in rows if "error" in r]
    wrong = [r for r in expect_clear if r.get("decision") == "clear"
             and base(r["kind"]) != base(r["truth_kind"])]
    false_clear = [r for r in rows if r["expect"] in ("unknown", "unreadable")
                   and r.get("decision") == "clear"]
    unclassified = [r for r in expect_clear if r.get("decision") in
                    ("ambiguous", "unknown", "unreadable")]

    for variant in ("pdf", "scan"):
        ec = [r for r in expect_clear if r["variant"] == variant]
        ok = [r for r in ec if r.get("decision") == "clear" and base(r["kind"]) == base(r["truth_kind"])]
        un = [r for r in ec if r.get("decision") in ("ambiguous", "unknown", "unreadable")]
        wr = [r for r in ec if r.get("decision") == "clear" and base(r["kind"]) != base(r["truth_kind"])]
        L.append("**%s:** %d מסמכים שיש להם סוג · סווגו נכון %s · **נותרו ללא סיווג %s** · "
                 "**סווגו שגוי בביטחון: %d**" % ("PDF דיגיטלי" if variant == "pdf" else "סריקה",
                                                 len(ec), pct(len(ok), len(ec)),
                                                 pct(len(un), len(ec)), len(wr)))
        L.append("")
    L += ["## לפי סוג מסמך", "",
          "precision = מתוך המסמכים שסווגו בביטחון לסוג הזה, כמה באמת מהסוג. "
          "recall = מתוך המסמכים מהסוג הזה, כמה סווגו אליו בביטחון. "
          "\"ללא סיווג\" = דו-משמעי / לא ידוע / לא קריא - הועברו לאדם, לא שגו.", "",
          "| קטגוריה | סוג | מסמכים | precision | recall | ללא סיווג | תת-סוג נכון / לא נקבע / שגוי |",
          "|---|---|---|---|---|---|---|"]
    kinds = sorted({base(r["truth_kind"]) for r in expect_clear},
                   key=lambda k: (doc_taxonomy.ALL[k]["category"], k))
    for k in kinds:
        truth_rows = [r for r in expect_clear if base(r["truth_kind"]) == k]
        predicted = [r for r in rows if r.get("decision") == "clear" and base(r.get("kind")) == k]
        tp = [r for r in predicted if base(r["truth_kind"]) == k]
        un = [r for r in truth_rows if r.get("decision") in ("ambiguous", "unknown", "unreadable")]
        sub_ok = sum(1 for r in tp if "." in (r["truth_kind"] or "") and r["kind"] == r["truth_kind"])
        sub_undet = sum(1 for r in tp if "." in (r["truth_kind"] or "") and "." not in r["kind"])
        sub_bad = sum(1 for r in tp if "." in (r["truth_kind"] or "") and "." in r["kind"]
                      and r["kind"] != r["truth_kind"])
        has_sub = any("." in (r["truth_kind"] or "") for r in truth_rows)
        L.append("| %s | %s | %d | %s | %s | %s | %s |" % (
            doc_taxonomy.CATEGORY_LABELS[doc_taxonomy.ALL[k]["category"]],
            doc_taxonomy.label(k), len(truth_rows), pct(len(tp), len(predicted)),
            pct(len(tp), len(truth_rows)), pct(len(un), len(truth_rows)),
            "%d / %d / %d" % (sub_ok, sub_undet, sub_bad) if has_sub else "-"))

    L += ["", "## קבוצות מיוחדות", "", "| קבוצה | נדרש | גרסה | תוצאות |", "|---|---|---|---|"]
    for group, need in (("confuser", "הסוג הנכון, או דו-משמעי"), ("continuation", "ברור (לא מעורב)"),
                        ("mixed", "מעורב, עם סוג נכון לכל עמוד"), ("unknown", "לא ידוע"),
                        ("unreadable", "לא קריא")):
        for variant in ("pdf", "scan"):
            rs = [r for r in rows if r["group"] == group and r["variant"] == variant]
            if not rs:
                continue
            cells = []
            for r in rs:
                ok = r.get("decision") == r["expect"]
                if group == "confuser":
                    ok = r.get("decision") == "ambiguous" or (
                        r.get("decision") == "clear" and base(r["kind"]) == base(r["truth_kind"]))
                if group == "mixed" and ok:
                    got = [pk["kind"] for pk in r["page_kinds"]]
                    ok = [base(k) for k in got] == r["truth_pages"]
                cells.append("%s %s=%s" % ("✓" if ok else "✗", r["doc"].split("-", 1)[-1],
                                            r.get("decision") if r.get("decision") != "clear"
                                            else r.get("kind")))
            L.append("| %s | %s | %s | %s |" % (group, need, variant, " · ".join(cells)))

    L += ["", "## סיווגים שגויים בביטחון", ""]
    L += ["- `%s` (%s): הוצע **%s**, נכון: %s. מועמדים: %s" % (
        r["doc"], r["variant"], r["kind"], r["truth_kind"], r["candidates"]) for r in wrong] or ["- אין."]
    L += ["", "## \"לא ידוע\" / \"לא קריא\" שסווגו בכל זאת", ""]
    L += ["- `%s` (%s): %s" % (r["doc"], r["variant"], r["kind"]) for r in false_clear] or ["- אין."]
    L += ["", "## מסמכים שנותרו ללא סיווג (הועברו לאדם)", ""]
    L += ["- `%s` (%s): %s, מועמדים: %s" % (r["doc"], r["variant"], r["decision"],
          [c["kind"] for c in r["candidates"]]) for r in unclassified] or ["- אין."]
    L += ["", "## כשלים", ""]
    L += ["- `%s` (%s): %s" % (r["doc"], r["variant"], r["error"]) for r in errors] or [
        "- אין. כל %d הריצות הסתיימו." % len(rows)]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\nreport:", OUT)


if __name__ == "__main__":
    if sys.argv[1:] == ["report"]:
        report(json.loads((CORPUS / "results.json").read_text(encoding="utf-8")))
    else:
        main()
