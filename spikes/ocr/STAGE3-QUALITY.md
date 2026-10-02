# מדדי איכות - שלב 3 (צנרת הייצור, קורפוס סינתטי)

> נוצר על ידי `spikes/ocr/measure_stage3.py` · 18 מסמכים סינתטיים · Tesseract 5.5.3 · Windows (פיתוח). הקוד שנמדד הוא ה-sandbox של הייצור.
> CER = שיעור שגיאות תווים. עובדות = תאריכים, ICD, אחוזים, רופא, מוסד, סוג בדיקה (כללי ה-spike, למדידה בלבד - שלב 3 אינו שומר עובדות).
> תצורת ברירת המחדל שנבחרה: **`fast`**.

## ההחלטה

ההנחיה הייתה fast כברירת מחדל ו-best לעמודים עם ביטחון נמוך, **בתנאי שהמעבר אינו יוצר שגיאות חדשות**. הטבלה "האם המעבר ל-best מוסיף שגיאות" למטה מראה שהתנאי אינו מתקיים באף צורת מעבר שנבדקה:

- `best` קורא 2026 כ-**2076** - שגיאה מספרית שנראית סבירה.
- מעבר ברמת שורה (`hybrid-replace`) הוסיף 2 תאריכים שגויים ואיבד 6 עובדות נכונות.
- מעבר ברמת עמוד (`hybrid-page`) לא הוסיף שגויות, אבל איבד 2 עובדות נכונות (קוד ICD ושם רופא).
- קריאה חלופית בלי החלפה (`fast+alt`) לא תפסה אף אחד מ-9 המספרים השגויים, והוסיפה 3 התרעות שווא.

לכן ברירת המחדל היא **fast בלבד**. ביטחון (confidence) מודד כמה Tesseract בטוח, לא האם הוא צודק. התצורות האחרות זמינות בהגדרה, למדידה חוזרת.

## לפי סוג מסמך (ברירת המחדל: fast לסריקות, שכבת טקסט ל-PDF)

| סוג | PDF דיגיטלי | סריקה 300dpi | סריקה גרועה | צילום טלפון | PDF הפוך (ישן) | recall עובדות (סריקות) | precision (סריקות) |
|---|---|---|---|---|---|---|---|
| סיכום אשפוז | 1.1% | 18.7% | 19.4% | 15.9% | 23.0% | 93.3% | 100.0% |
| EMG | 0.5% | 14.2% | 19.5% | 20.0% | 41.7% | 91.1% | 100.0% |
| MRI | 0.0% | 8.2% | 19.8% | 21.9% | 44.8% | 93.3% | 84.0% |
| חוות דעת | 1.3% | 5.0% | 14.7% | 11.5% | 64.6% | 96.3% | 100.0% |
| טופס תביעה | 0.0% | 10.2% | 27.0% | 36.5% | 58.0% | 77.8% | 100.0% |
| החלטת ועדה | 0.0% | 12.0% | 30.5% | 7.2% | 58.2% | 94.4% | 97.1% |

## השוואת תצורות (סריקות וצילומים)

| גרסה | תצורה | CER | recall | precision | שניות לעמוד |
|---|---|---|---|---|---|
| scan-300dpi | fast | 11.4% | 89.9% | 95.4% | 1.1 |
| scan-300dpi | best | 8.3% | 94.2% | 94.2% | 1.6 |
| scan-300dpi | hybrid-replace | 10.9% | 94.2% | 95.6% | 1.5 |
| scan-300dpi | hybrid-page | 11.4% | 89.9% | 95.4% | 1.0 |
| scan-300dpi | fast+alt | 11.4% | 89.9% | 95.4% | 1.3 |
| scan-degraded | fast | 21.8% | 97.1% | 95.7% | 1.0 |
| scan-degraded | best | 20.4% | 75.4% | 83.9% | 1.4 |
| scan-degraded | hybrid-replace | 21.3% | 95.7% | 94.3% | 1.2 |
| scan-degraded | hybrid-page | 21.8% | 97.1% | 95.7% | 0.9 |
| scan-degraded | fast+alt | 21.8% | 97.1% | 95.7% | 1.1 |
| phone-photo | fast | 18.9% | 91.3% | 95.5% | 0.9 |
| phone-photo | best | 16.6% | 84.1% | 90.6% | 1.2 |
| phone-photo | hybrid-replace | 18.6% | 87.0% | 93.8% | 1.3 |
| phone-photo | hybrid-page | 18.8% | 88.4% | 95.3% | 1.0 |
| phone-photo | fast+alt | 18.9% | 91.3% | 95.5% | 1.1 |
| pdf-text | layer | 0.5% | 100.0% | 100.0% | 0.40 |
| pdf-visual | layer | 48.4% | 78.3% | 100.0% | 0.36 |

## האם המעבר ל-best מוסיף שגיאות? (כל תצורה מול fast, אותו עמוד)

| תצורה | עמודים שעברו ל-best | CER טוב יותר | ללא שינוי | CER גרוע יותר | עובדות נכונות שאבדו | **עובדות שגויות חדשות** | עובדות נכונות שנוספו |
|---|---|---|---|---|---|---|---|
| hybrid-replace | 33/54 | 15 | 37 | 2 | 6 | **2** | 5 |
| hybrid-page | 3/54 | 2 | 51 | 1 | 2 | **0** | 0 |
| best | 54/54 | 38 | 4 | 12 | 25 | **12** | 8 |
| fast+alt | 0/54 | 0 | 54 | 0 | 0 | **0** | 0 |

**hybrid-replace**
- אבדה: committee-3/scan-degraded: date=2026-04-24
- אבדה: discharge-1/scan-300dpi: icd10=S82.1
- אבדה: discharge-3/phone-photo: icd10=S42.2
- אבדה: discharge-3/phone-photo: physician=נועה דגני
- אבדה: discharge-3/scan-degraded: date=2026-06-09
- אבדה: mri-3/phone-photo: date=2026-01-20
- שגויה חדשה: committee-3/scan-degraded: date=2076-04-24
- שגויה חדשה: mri-3/phone-photo: date=2076-01-20

**hybrid-page**
- אבדה: discharge-3/phone-photo: icd10=S42.2
- אבדה: discharge-3/phone-photo: physician=נועה דגני

**best**
- אבדה: committee-2/scan-degraded: date=2026-08-03
- אבדה: committee-2/scan-degraded: physician=תמר תבור
- אבדה: committee-3/phone-photo: date=2026-04-24
- אבדה: committee-3/scan-degraded: date=2026-04-24
- אבדה: committee-3/scan-degraded: physician=איתי תבור
- אבדה: discharge-1/scan-300dpi: icd10=S82.1
- אבדה: discharge-1/scan-degraded: date=2026-07-12
- אבדה: discharge-1/scan-degraded: icd10=S82.1
- אבדה: discharge-2/scan-300dpi: icd10=S42.2
- אבדה: discharge-2/scan-degraded: date=2026-06-09
- אבדה: discharge-2/scan-degraded: icd10=S42.2
- אבדה: discharge-3/phone-photo: icd10=S42.2
- אבדה: discharge-3/phone-photo: physician=נועה דגני
- אבדה: discharge-3/scan-300dpi: icd10=S42.2
- אבדה: discharge-3/scan-degraded: date=2026-06-09
- אבדה: emg-1/scan-degraded: date=2026-01-10
- אבדה: emg-1/scan-degraded: physician=רוני גפני
- אבדה: mri-1/phone-photo: date=2026-11-23
- אבדה: mri-1/scan-300dpi: date=2026-11-23
- אבדה: mri-2/scan-degraded: physician=רוני לביא
- אבדה: mri-3/phone-photo: date=2026-01-20
- אבדה: mri-3/scan-degraded: date=2026-01-20
- אבדה: opinion-1/scan-degraded: date=2026-12-27
- אבדה: opinion-1/scan-degraded: physician=שירה גפני
- אבדה: opinion-2/scan-degraded: date=2026-12-01
- שגויה חדשה: committee-3/phone-photo: date=2076-04-24
- שגויה חדשה: committee-3/scan-degraded: date=7076-04-24
- שגויה חדשה: discharge-2/scan-degraded: date=2076-06-09
- שגויה חדשה: discharge-3/scan-degraded: date=2076-06-09
- שגויה חדשה: emg-1/scan-degraded: date=2076-01-10
- שגויה חדשה: mri-1/phone-photo: date=2076-11-23
- שגויה חדשה: mri-1/scan-300dpi: date=2026-11-22
- שגויה חדשה: mri-3/phone-photo: date=2076-01-20
- שגויה חדשה: mri-3/phone-photo: date=2081-05-01
- שגויה חדשה: mri-3/scan-degraded: date=2076-01-20
- שגויה חדשה: opinion-1/scan-degraded: date=2076-12-27
- שגויה חדשה: opinion-2/scan-degraded: date=2076-12-01

## הקריאה החלופית כאות לבדיקה (fast+alt)

שורה חלשה נקראת שוב ב-best, **והטקסט אינו מוחלף**. עובדה ששתי הקריאות חלוקות עליה מסומנת "לבדיקה" בשלב 5.

- שורות עם קריאה חלופית: 58, ב-34 מתוך 54 עמודים
- מספרים שגויים שהפיק fast: **9** · מתוכם סומנו כחלוקים: **0**
- מספרים נכונים שסומנו כחלוקים (התרעת שווא - מוסיפה בדיקה, לא שגיאה): 3 מתוך 110

> גם מספר שלא סומן **אינו** נכנס לתיק בלי אישור אדם. הסימון קובע מה יוצג ראשון ובולט, לא מה מותר לדלג עליו.

## שגיאות מספריות שה-OCR הפיק (fast)

**אף אחת מהן אינה נכנסת לתיק בלי אישור אדם** (`fact_review_has_reviewer`, `requiresHumanVerification`).

- `committee-3` / phone-photo: date=2026-09-26
- `mri-1` / phone-photo: date=2051-05-01
- `mri-1` / scan-300dpi: date=2051-05-01
- `mri-1` / scan-degraded: date=2051-05-01
- `mri-2` / phone-photo: date=2051-05-01
- `mri-2` / scan-300dpi: date=2051-05-01
- `mri-2` / scan-degraded: date=2051-05-01
- `mri-3` / scan-300dpi: date=2051-05-01
- `mri-3` / scan-degraded: date=2051-05-01

## כשלים

- אין. כל 306 הריצות הסתיימו.
