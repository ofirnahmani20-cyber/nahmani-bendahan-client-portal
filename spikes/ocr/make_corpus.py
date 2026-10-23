"""
make_corpus.py - קורפוס סינתטי לבדיקת ההיתכנות של OCR.

כל מה שכאן מומצא: שמות, מספרי זהות (ספרת ביקורת שגויה בכוונה -
אינם יכולים להיות ת"ז אמיתית), מוסדות ("סינתטי" בשם), רופאים
ותאריכים. כל עמוד נושא חותמת "מסמך סינתטי לבדיקה".

    spikes/ocr/.venv/Scripts/python spikes/ocr/make_corpus.py

לכל מסמך נוצרים:
  <id>.html            המקור
  <id>.pdf             PDF עם שכבת טקסט (Edge headless, כמו Word/מערכת בית חולים)
  <id>.visual.pdf      PDF שבו העברית שמורה בסדר ויזואלי (הפוך) - תקלה נפוצה
                       במסמכים ישראליים שמיוצאים ממערכות ישנות
  <id>.scan.png        סריקה נקייה, 300dpi
  <id>.degraded.jpg    סריקה גרועה: הטיה, רעש, טשטוש, 200dpi, JPEG איכות נמוכה
  <id>.phone.jpg       צילום טלפון: פרספקטיבה, תאורה לא אחידה, טשטוש
  <id>.truth.json      האמת: סוג, טקסט, עובדות
"""

import html
import json
import os
import pathlib
import random
import subprocess

import pypdfium2 as pdfium
from PIL import Image, ImageFilter, ImageOps

HOME = pathlib.Path(os.environ["LOCALAPPDATA"]) / "nahmani-ocr-spike"
CORPUS = HOME / "corpus"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

FIRST = ["נועה", "איתי", "מיכל", "עומר", "שירה", "יונתן", "רוני", "תמר", "אליה", "גיל"]
LAST = ["ברקן", "אלמוג", "שחר", "דגני", "צורף", "פלג", "רימון", "גפני", "תבור", "לביא"]
INSTITUTIONS = ["מרכז רפואי הדר סינתטי", "בית חולים גליל סינתטי", "מכון נוירולוגי אופק סינתטי",
                "מכון דימות כרמל סינתטי"]

STAMP = "מסמך סינתטי לבדיקה בלבד — אינו מסמך רפואי אמיתי"


def fake_id(rng):
    """9 ספרות שספרת הביקורת שלהן *שגויה* - לעולם אינה ת"ז תקפה."""
    while True:
        digits = "".join(rng.choice("0123456789") for _ in range(9))
        total = sum((d if d < 10 else d - 9) for d in
                    (int(c) * (1 if i % 2 == 0 else 2) for i, c in enumerate(digits)))
        if total % 10 != 0:
            return digits


def person(rng):
    return "%s %s" % (rng.choice(FIRST), rng.choice(LAST))


def date(rng, year=2026):
    return "%02d/%02d/%d" % (rng.randint(1, 28), rng.randint(1, 12), year)


def iso(d):
    dd, mm, yyyy = d.split("/")
    return "%s-%s-%s" % (yyyy, mm, dd)


# ------------------------------------------------------------------
#  תבניות. כל אחת מחזירה (שורות, עובדות). שורה = (סגנון, טקסט).
# ------------------------------------------------------------------

def discharge(rng):
    inst, doc, adm = rng.choice(INSTITUTIONS[:2]), person(rng), date(rng)
    dis = date(rng)
    icd, dx = rng.choice([("S82.1", "שבר בקצה העליון של עצם השוקה"),
                          ("S42.2", "שבר בקצה העליון של עצם הזרוע"),
                          ("S32.0", "שבר בחוליה מותנית")])
    lines = [("h1", inst), ("h2", "מחלקה אורתופדית א'"), ("h1", "סיכום אשפוז"),
             ("p", "שם המטופל: %s    ת.ז.: %s" % (person(rng), fake_id(rng))),
             ("p", "תאריך קבלה: %s    תאריך שחרור: %s" % (adm, dis)),
             ("h3", "אבחנות"), ("p", "%s — %s" % (icd, dx)),
             ("h3", "מהלך האשפוז"),
             ("p", "המטופל התקבל לאחר נפילה בעבודה. בוצע קיבוע פנימי בהרדמה כללית. "
                   "מהלך ניתוחי ללא סיבוכים, ההחלמה תקינה."),
             ("table", [("בדיקה", "תוצאה", "טווח תקין"), ("המוגלובין", "13.2", "12-16"),
                        ("לויקוציטים", "8.4", "4-10"), ("CRP", "12", "0-5")]),
             ("h3", "המלצות"),
             ("p", "מנוחה, פיזיותרפיה, ביקורת במרפאה בעוד שישה שבועות. אי כושר עבודה עד להחלטה."),
             ("sig", 'ד"ר %s, מנהל/ת המחלקה' % doc)]
    facts = [("doc_type", "discharge"), ("institution", inst), ("physician", doc),
             ("date", iso(adm)), ("date", iso(dis)), ("icd10", icd)]
    return lines, facts


def emg(rng):
    inst, doc, d = INSTITUTIONS[2], person(rng), date(rng)
    side = rng.choice(["הימנית", "השמאלית"])
    grade = rng.choice(["קלה", "בינונית", "קשה"])
    lines = [("h1", inst), ("h1", "בדיקת EMG והולכה עצבית"),
             ("p", "שם הנבדק: %s    ת.ז.: %s    תאריך הבדיקה: %s" % (person(rng), fake_id(rng), d)),
             ("h3", "ממצאים"),
             ("p", "נמצאה האטה בהולכה החושית של העצב המדיאני בשורש כף היד %s. "
                   "ההולכה המוטורית תקינה. לא נמצאו סימני דנרבציה." % side),
             ("h3", "מסקנה"),
             ("p", "הממצאים מתאימים לתסמונת התעלה הקרפלית (G56.0) בדרגה %s." % grade),
             ("sig", 'ד"ר %s, נוירולוג/ית' % doc)]
    facts = [("doc_type", "emg"), ("institution", inst), ("physician", doc),
             ("date", iso(d)), ("icd10", "G56.0"), ("test_type", "EMG")]
    return lines, facts


def mri(rng):
    inst, doc, d = INSTITUTIONS[3], person(rng), date(rng)
    level = rng.choice(["L4-L5", "L5-S1", "L3-L4"])
    lines = [("h1", inst), ("h1", "בדיקת MRI של עמוד השדרה המותני"),
             ("p", "שם: %s    ת.ז.: %s    תאריך: %s" % (person(rng), fake_id(rng), d)),
             ("h3", "ממצאים"),
             ("p", "בגובה %s נצפה בלט דיסק מרכזי הלוחץ על השק הדוראלי, ללא היצרות "
                   "משמעותית של התעלה. שינויים ניווניים במפרקים הפסטיים." % level),
             ("h3", "סיכום"), ("p", "בלט דיסק %s (M51.2). מומלץ המשך בירור אורתופדי." % level),
             ("sig", 'ד"ר %s, רדיולוג/ית' % doc)]
    facts = [("doc_type", "mri"), ("institution", inst), ("physician", doc),
             ("date", iso(d)), ("icd10", "M51.2"), ("test_type", "MRI")]
    return lines, facts


def opinion(rng):
    doc, d = person(rng), date(rng)
    pct = rng.choice([5, 10, 15, 20])
    lines = [("h1", "חוות דעת רפואית"), ("h2", "מומחה/ית בתחום האורתופדיה"),
             ("p", "תאריך: %s" % d),
             ("p", "הנדון: %s, ת.ז. %s" % (person(rng), fake_id(rng))),
             ("p", "נתבקשתי לחוות דעתי בדבר מצבו הרפואי של הנ\"ל בעקבות תאונת עבודה. "
                   "אני מצהיר/ה כי אין לי עניין אישי בתוצאות התביעה."),
             ("h3", "דיון ומסקנות"),
             ("p", "לאור הממצאים בבדיקה הקלינית ובבדיקות ההדמיה, יש להעריך נכות צמיתה "
                   "בשיעור %d%% לפי סעיף 35(1)(ב) לתקנות." % pct),
             ("sig", 'ד"ר %s' % doc)]
    facts = [("doc_type", "opinion"), ("physician", doc), ("date", iso(d)),
             ("percent", str(pct))]
    return lines, facts


def form7801(rng):
    d = date(rng)
    lines = [("h2", "טופס סינתטי לבדיקה — במבנה דומה לטופס 7801"),
             ("h1", "תביעה לקצבת נכות כללית"),
             ("table", [("שדה", "ערך"), ("שם משפחה ושם פרטי", person(rng)),
                        ("מספר זהות", fake_id(rng)), ("תאריך הגשה", d),
                        ("סיבת התביעה", "מגבלה בתפקוד הגפה העליונה")]),
             ("p", "אני מצהיר/ה כי כל הפרטים שמסרתי בטופס זה נכונים ומלאים."),
             ("p", "חתימת התובע/ת: ________")]
    facts = [("doc_type", "form7801"), ("date", iso(d))]
    return lines, facts


def committee(rng):
    d, frm = date(rng), date(rng)
    pct = rng.choice([20, 25, 30, 40])
    doc = person(rng)
    lines = [("h1", "פרוטוקול ועדה רפואית"), ("p", "תאריך הוועדה: %s" % d),
             ("p", "שם הנבדק: %s    ת.ז.: %s" % (person(rng), fake_id(rng))),
             ("h3", "החלטה"),
             ("p", "הוועדה קבעה נכות רפואית משוקללת בשיעור %d%% החל מיום %s." % (pct, frm)),
             ("p", "ניתן להגיש ערר על החלטה זו תוך 60 יום."),
             ("sig", 'יו"ר הוועדה: ד"ר %s' % doc)]
    facts = [("doc_type", "committee"), ("physician", doc), ("date", iso(d)),
             ("date", iso(frm)), ("percent", str(pct))]
    return lines, facts


TEMPLATES = {"discharge": discharge, "emg": emg, "mri": mri,
             "opinion": opinion, "form7801": form7801, "committee": committee}


def plain_text(lines):
    out = []
    for style, content in lines:
        if style == "table":
            out.extend("  ".join(row) for row in content)
        else:
            out.append(content)
    out.append(STAMP)
    return "\n".join(out)


def to_html(lines, visual=False):
    def esc(s):
        # סדר ויזואלי: כל שורה הפוכה ומוצגת משמאל לימין בלי bidi -
        # כך נבנים PDF-ים ממערכות ישנות, ושכבת הטקסט יוצאת הפוכה.
        return html.escape(s[::-1] if visual else s)

    body = []
    for style, content in lines:
        if style == "table":
            rows = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % esc(c) for c in r)
                           for r in content)
            body.append("<table>%s</table>" % rows)
        elif style in ("h1", "h2", "h3"):
            body.append("<%s>%s</%s>" % (style, esc(content), style))
        else:
            body.append("<p class='%s'>%s</p>" % (style, esc(content)))
    direction = ("direction:ltr; unicode-bidi:bidi-override; text-align:right"
                 if visual else "direction:rtl")
    return """<!doctype html><html lang="he"><meta charset="utf-8"><style>
body{font-family:David,Arial,sans-serif;font-size:15pt;margin:2cm;%s}
h1{font-size:22pt;margin:.3em 0} h2{font-size:17pt} h3{font-size:16pt;margin-top:1em}
table{border-collapse:collapse;margin:.6em 0} td{border:1px solid #444;padding:4px 10px}
.sig{margin-top:2em} .stamp{position:fixed;bottom:1cm;font-size:10pt;color:#777}
</style><body>%s<p class="stamp">%s</p></body></html>""" % (
        direction, "\n".join(body), esc(STAMP))


def print_pdf(html_path, pdf_path):
    subprocess.run([EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    "--print-to-pdf=%s" % pdf_path, html_path.as_uri()],
                   check=True, capture_output=True, timeout=60)


def render(pdf_path, dpi):
    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[0]
    img = page.render(scale=dpi / 72).to_pil().convert("L")
    pdf.close()
    return img


def degrade(img, rng):
    img = img.resize((img.width * 2 // 3, img.height * 2 // 3))      # 300 -> 200dpi
    # הטיה של 1-2.5 מעלות לפחות - הטיה אפסית הייתה מחמיאה ל-OCR.
    img = img.rotate(rng.choice([-1, 1]) * rng.uniform(1.0, 2.5), expand=True,
                     fillcolor=255, resample=Image.Resampling.BILINEAR)
    img = img.filter(ImageFilter.GaussianBlur(1.0))
    noise = Image.effect_noise(img.size, 40)
    img = Image.blend(img, noise, 0.18)
    # "דהייה" של טונר: חלק מהדיו מתבהר
    img = img.point(lambda v: min(255, int(v * 1.25 + 20)))
    return img


def phone(img, rng):
    w, h = img.size
    img = img.resize((w // 2, h // 2))                                # ~150dpi
    w, h = img.size
    d = int(w * 0.06)
    # קואורדינטות המקור לכל פינה - "צילום מזווית"
    quad = (rng.randint(0, d), rng.randint(0, d), rng.randint(0, d), h - rng.randint(0, d),
            w - rng.randint(0, d), h - rng.randint(0, d), w - rng.randint(0, d), rng.randint(0, d))
    img = img.transform((w, h), Image.Transform.QUAD, quad, fillcolor=200)
    gradient = Image.linear_gradient("L").resize((w, h)).point(lambda v: 150 + v * 105 // 255)
    img = Image.composite(img, Image.new("L", (w, h), 0), gradient)
    img = ImageOps.autocontrast(img.filter(ImageFilter.GaussianBlur(1.1)), cutoff=1)
    return img


def main():
    CORPUS.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261002)
    count = 0
    for kind, make in TEMPLATES.items():
        for n in range(3):
            doc_id = "%s-%d" % (kind, n + 1)
            lines, facts = make(rng)
            base = CORPUS / doc_id

            (base.with_suffix(".html")).write_text(to_html(lines), encoding="utf-8")
            print_pdf(base.with_suffix(".html"), base.with_suffix(".pdf"))
            vis = CORPUS / (doc_id + ".visual.html")
            vis.write_text(to_html(lines, visual=True), encoding="utf-8")
            print_pdf(vis, CORPUS / (doc_id + ".visual.pdf"))

            scan = render(base.with_suffix(".pdf"), 300)
            scan.save(CORPUS / (doc_id + ".scan.png"))
            degrade(scan, rng).save(CORPUS / (doc_id + ".degraded.jpg"), quality=55)
            phone(scan, rng).save(CORPUS / (doc_id + ".phone.jpg"), quality=70)

            truth = {"id": doc_id, "type": kind, "synthetic": True,
                     "text": plain_text(lines),
                     "facts": [{"kind": k, "value": v} for k, v in facts]}
            (CORPUS / (doc_id + ".truth.json")).write_text(
                json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
            count += 1
            print("generated", doc_id)
    print("%d synthetic documents in %s" % (count, CORPUS))


if __name__ == "__main__":
    main()
