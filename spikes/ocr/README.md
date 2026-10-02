# spike: OCR וחילוץ מקומי (Layer 2, שלב 1)

בדיקת היתכנות של **חלופה A** — עיבוד מקומי בלבד (Tesseract + כללים), בלי שום שירות חיצוני — על **קורפוס סינתטי בלבד**.
זה אינו קוד שרת. הוא קיים כדי למדוד, והתוצאות ב-[`RESULTS.md`](RESULTS.md).

## הרצה (Windows, פיתוח)

```powershell
scoop install tesseract
# מודלים heb/eng (fast + best) ל-%LOCALAPPDATA%\nahmani-ocr-spike\tessdata\{fast,best}
#   https://github.com/tesseract-ocr/tessdata_fast  /  tessdata_best
python -m venv spikes\ocr\.venv
spikes\ocr\.venv\Scripts\pip install pypdfium2 Pillow numpy psutil rapidfuzz python-bidi

spikes\ocr\.venv\Scripts\python spikes\ocr\make_corpus.py   # 18 מסמכים × 5 גרסאות
spikes\ocr\.venv\Scripts\python spikes\ocr\run_spike.py     # מדדים -> out\report.md
spikes\ocr\.venv\Scripts\python spikes\ocr\robustness.py    # קבצים עוינים -> out\robustness.md
```

הקורפוס, המודלים והתוצרים יושבים ב-`%LOCALAPPDATA%\nahmani-ocr-spike` — נתיב ASCII, כי Tesseract/Leptonica ב-Windows אינם אמינים על נתיב עברי (אותה סיבה כמו ב-`pg-local.ps1`).

## קבצים

| קובץ | תפקיד |
|---|---|
| `make_corpus.py` | מסמכים סינתטיים: HTML ← PDF (Edge headless) ← סריקה/סריקה גרועה/צילום טלפון. ת"ז עם ספרת ביקורת **שגויה** בכוונה |
| `pipeline.py` | אב-טיפוס הצנרת: מגבלות משאבים, שכבת טקסט, תיקון סדר bidi, OCR, נרמול, סיווג וחילוץ בכללים |
| `run_spike.py` | CER/WER, סיווג, precision/recall של עובדות, זמן וזיכרון |
| `robustness.py` | PDF bomb, עמוד ענק, PNG bomb, PDF קטוע — כל אחד בתהליך ילד עם timeout |

## גבולות

- אין כאן חיבור למסד או לשרת, ואין שום מסמך אמיתי.
- התלויות כאן (`numpy`, `python-bidi`, `rapidfuzz`…) **אינן** תלויות של השרת. מה שיעבור לשלב 3 יוחלט בנפרד.
- אין בדיקה של כתב יד: אי אפשר לייצר כתב יד סינתטי אמין, ו-Tesseract אינו מיועד לו.
