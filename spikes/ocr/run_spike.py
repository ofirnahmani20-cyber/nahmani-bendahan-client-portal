"""
run_spike.py - מדידת חלופה A על הקורפוס הסינתטי.

    spikes/ocr/.venv/Scripts/python spikes/ocr/run_spike.py

פלט: %LOCALAPPDATA%\\nahmani-ocr-spike\\out\\{results.json, report.md}
"""

import json
import statistics
import sys
import threading
import time
from collections import defaultdict

import psutil
from rapidfuzz.distance import Levenshtein

import pipeline as P

CORPUS = P.HOME / "corpus"
OUT = P.HOME / "out"

# variant -> (סיומת, האם OCR)
VARIANTS = {
    "pdf-text":     (".pdf", False),
    "pdf-visual":   (".visual.pdf", False),
    "scan-300dpi":  (".scan.png", True),
    "scan-degraded": (".degraded.jpg", True),
    "phone-photo":  (".phone.jpg", True),
}
MODELS = ["fast", "best"]


class PeakRSS:
    """דוגם את הזיכרון של התהליך ושל ילדיו (Tesseract) כל 30ms."""

    def __enter__(self):
        self.peak, self._run = 0, True
        self._t = threading.Thread(target=self._sample, daemon=True)
        self._t.start()
        return self

    def _sample(self):
        me = psutil.Process()
        while self._run:
            try:
                rss = me.memory_info().rss + sum(
                    c.memory_info().rss for c in me.children(recursive=True))
                self.peak = max(self.peak, rss)
            except psutil.Error:
                pass
            time.sleep(0.03)

    def __exit__(self, *exc):
        self._run = False
        self._t.join()


def flat(text):
    return " ".join(P.normalize(text).split())


def fact_set(facts):
    return {(f["kind"], f["value"]) for f in facts if f["kind"] != "doc_type"}


def main():
    OUT.mkdir(exist_ok=True)
    truths = [json.loads(p.read_text(encoding="utf-8"))
              for p in sorted(CORPUS.glob("*.truth.json"))]
    rows = []
    for truth in truths:
        ref = flat(truth["text"])
        ref_words = ref.split()
        expected = fact_set(truth["facts"])
        for variant, (suffix, needs_ocr) in VARIANTS.items():
            for model in (MODELS if needs_ocr else ["-"]):
                path = CORPUS / (truth["id"] + suffix)
                t0 = time.perf_counter()
                with PeakRSS() as mem:
                    text, info = P.extract_text(path, model if model != "-" else "best")
                seconds = time.perf_counter() - t0
                hyp = flat(text)
                norm = P.normalize(text)
                kind, score, margin = P.classify(norm)
                facts = P.extract_facts(norm)
                facts += [f for f in P.extract_facts(P.normalize(info["aux_text"]))
                          if f["kind"] == "icd10"]
                got = fact_set(facts)
                rows.append({
                    "doc": truth["id"], "type": truth["type"], "variant": variant,
                    "model": model, "pages": info["pages"],
                    "ocr_pages": info["ocr_pages"], "visual_fixed": info["visual_fixed"],
                    "cer": Levenshtein.normalized_distance(ref, hyp),
                    "wer": Levenshtein.normalized_distance(ref_words, hyp.split()),
                    "classified": kind, "class_ok": kind == truth["type"],
                    "class_margin": margin,
                    "facts_expected": len(expected), "facts_got": len(got),
                    "facts_tp": len(expected & got),
                    "missed": sorted("%s=%s" % f for f in expected - got),
                    "spurious": sorted("%s=%s" % f for f in got - expected),
                    "seconds": seconds, "peak_mb": mem.peak / 1e6,
                })
                r = rows[-1]
                print("%-12s %-14s %-5s cer=%.3f class=%s facts=%d/%d %.1fs"
                      % (r["doc"], variant, model, r["cer"], "ok" if r["class_ok"] else kind,
                         r["facts_tp"], r["facts_expected"], seconds), flush=True)

    (OUT / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    report(rows)


def report(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["variant"], r["model"])].append(r)

    lines = ["# OCR spike - תוצאות (קורפוס סינתטי, %d מסמכים)" %
             len({r["doc"] for r in rows}), "",
             "| גרסה | מודל | CER ממוצע | CER חציוני | WER ממוצע | סיווג נכון | "
             "עובדות precision | עובדות recall | שניות לעמוד | זיכרון שיא MB |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for (variant, model), rs in groups.items():
        tp = sum(r["facts_tp"] for r in rs)
        got = sum(r["facts_got"] for r in rs)
        exp = sum(r["facts_expected"] for r in rs)
        pages = sum(r["pages"] for r in rs)
        lines.append("| %s | %s | %.3f | %.3f | %.3f | %d/%d | %.2f | %.2f | %.2f | %.0f |" % (
            variant, model,
            statistics.mean(r["cer"] for r in rs), statistics.median(r["cer"] for r in rs),
            statistics.mean(r["wer"] for r in rs),
            sum(r["class_ok"] for r in rs), len(rs),
            tp / got if got else 0, tp / exp if exp else 0,
            sum(r["seconds"] for r in rs) / pages, max(r["peak_mb"] for r in rs)))

    # recall לפי סוג עובדה, לכל גרסה במודל best
    lines += ["", "## recall לפי סוג עובדה (מודל best / שכבת טקסט)", ""]
    kinds = ["date", "icd10", "percent", "physician", "institution", "test_type"]
    lines.append("| גרסה | " + " | ".join(kinds) + " |")
    lines.append("|---|" + "---|" * len(kinds))
    for variant in VARIANTS:
        rs = [r for r in rows if r["variant"] == variant and r["model"] in ("best", "-")]
        cells = []
        for k in kinds:
            missed = sum(1 for r in rs for m in r["missed"] if m.startswith(k + "="))
            exp_k = sum(1 for r in rs for _ in range(1)
                        for f in json.loads((CORPUS / (r["doc"] + ".truth.json"))
                                            .read_text(encoding="utf-8"))["facts"]
                        if f["kind"] == k)
            cells.append("%d/%d" % (exp_k - missed, exp_k) if exp_k else "-")
        lines.append("| %s | %s |" % (variant, " | ".join(cells)))

    lines += ["", "## החטאות לדוגמה (best)", ""]
    for r in rows:
        if r["model"] in ("best", "-") and (r["missed"] or r["spurious"] or not r["class_ok"]):
            lines.append("- `%s` / %s: class=%s missed=%s spurious=%s" % (
                r["doc"], r["variant"], r["classified"], r["missed"], r["spurious"]))
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\nreport:", OUT / "report.md")


if __name__ == "__main__":
    sys.exit(main())
