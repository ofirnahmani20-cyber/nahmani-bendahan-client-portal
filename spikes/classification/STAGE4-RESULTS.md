# תוצאות סיווג - שלב 4 (קורפוס סינתטי, צנרת הייצור)

> נוצר על ידי `spikes/classification/measure.py`. 92 מסמכים סינתטיים, כל אחד כ-PDF דיגיטלי וכסריקה (OCR fast). כללים: `2026.10.3`, טקסונומיה: `2026.10.1`.

**PDF דיגיטלי:** 85 מסמכים שיש להם סוג · סווגו נכון 100% · **נותרו ללא סיווג 0%** · **סווגו שגוי בביטחון: 0**

**סריקה:** 85 מסמכים שיש להם סוג · סווגו נכון 99% · **נותרו ללא סיווג 1%** · **סווגו שגוי בביטחון: 0**

## לפי סוג מסמך

precision = מתוך המסמכים שסווגו בביטחון לסוג הזה, כמה באמת מהסוג. recall = מתוך המסמכים מהסוג הזה, כמה סווגו אליו בביטחון. "ללא סיווג" = דו-משמעי / לא ידוע / לא קריא - הועברו לאדם, לא שגו.

| קטגוריה | סוג | מסמכים | precision | recall | ללא סיווג | תת-סוג נכון / לא נקבע / שגוי |
|---|---|---|---|---|---|---|
| העסקה והכנסה | אישור על קצבה / גמלה אחרת | 4 | 100% | 100% | 0% | - |
| העסקה והכנסה | אישור מעסיק על תאונה | 4 | 100% | 100% | 0% | - |
| העסקה והכנסה | אישור העסקה | 4 | 100% | 100% | 0% | - |
| העסקה והכנסה | תלוש שכר | 6 | 100% | 100% | 0% | - |
| ביטוח ופנסיה | פוליסת ביטוח | 4 | 100% | 100% | 0% | - |
| ביטוח ופנסיה | החלטת חברת ביטוח | 6 | 100% | 100% | 0% | 4 / 0 / 0 |
| ביטוח ופנסיה | מסמך קרן פנסיה | 4 | 100% | 100% | 0% | - |
| משפטי | תצהיר | 4 | 100% | 100% | 0% | - |
| משפטי | כתב ערר / ערעור | 6 | 100% | 100% | 0% | - |
| משפטי | החלטה / פסק דין | 4 | 100% | 100% | 0% | - |
| משפטי | ייפוי כוח | 4 | 100% | 100% | 0% | - |
| משפטי | כתב תביעה | 6 | 100% | 100% | 0% | - |
| מסמכים רפואיים | תיעוד חדר מיון | 4 | 100% | 100% | 0% | - |
| מסמכים רפואיים | דוח תפקוד | 4 | 100% | 100% | 0% | - |
| מסמכים רפואיים | מסמך קופת חולים | 8 | 100% | 100% | 0% | 8 / 0 / 0 |
| מסמכים רפואיים | סיכום אשפוז | 8 | 100% | 88% | 12% | - |
| מסמכים רפואיים | בדיקת דימות | 12 | 100% | 100% | 0% | 12 / 0 / 0 |
| מסמכים רפואיים | בדיקות מעבדה | 4 | 100% | 100% | 0% | - |
| מסמכים רפואיים | EMG / הולכה עצבית | 4 | 100% | 100% | 0% | - |
| מסמכים רפואיים | מסמך רופא תעסוקתי | 4 | 100% | 100% | 0% | - |
| מסמכים רפואיים | סיכום ביקור אצל רופא | 6 | 100% | 100% | 0% | - |
| מסמכים רפואיים | תיעוד פיזיותרפיה | 4 | 100% | 100% | 0% | - |
| מסמכים רפואיים | מסמך פסיכיאטרי | 4 | 100% | 100% | 0% | - |
| מסמכים רפואיים | אבחון / דוח פסיכולוגי | 4 | 100% | 100% | 0% | - |
| מסמכים רפואיים | אישור מחלה | 4 | 100% | 100% | 0% | - |
| ביטוח לאומי | פרוטוקול ועדה רפואית | 10 | 100% | 100% | 0% | 8 / 0 / 0 |
| ביטוח לאומי | מכתב החלטה מביטוח לאומי | 8 | 100% | 100% | 0% | 8 / 0 / 0 |
| ביטוח לאומי | טופס ביטוח לאומי | 12 | 100% | 100% | 0% | 12 / 0 / 0 |
| חוות דעת | חוות דעת מומחה | 6 | 100% | 100% | 0% | - |
| חוות דעת | חוות דעת / מכתב רופא מטפל | 4 | 100% | 100% | 0% | - |
| מסמכים אישיים | תעודת זהות וספח | 4 | 100% | 100% | 0% | - |

## קבוצות מיוחדות

| קבוצה | נדרש | גרסה | תוצאות |
|---|---|---|---|
| confuser | הסוג הנכון, או דו-משמעי | pdf | ✓ appeal_filing_about_committee=appeal_filing · ✓ claim_mentions_committee=statement_of_claim · ✓ committee_quotes_opinion=nii_committee · ✓ discharge_mentions_mri=hospital_discharge · ✓ insurer_vs_nii=insurer_decision.rejection · ✓ opinion_cites_committee=expert_opinion · ✓ payslip_mentions_nii=payslip · ✓ visit_mentions_sickleave=physician_visit |
| confuser | הסוג הנכון, או דו-משמעי | scan | ✓ appeal_filing_about_committee=appeal_filing · ✓ claim_mentions_committee=statement_of_claim · ✓ committee_quotes_opinion=nii_committee · ✗ discharge_mentions_mri=unknown · ✓ insurer_vs_nii=insurer_decision.rejection · ✓ opinion_cites_committee=expert_opinion · ✓ payslip_mentions_nii=payslip · ✓ visit_mentions_sickleave=physician_visit |
| continuation | ברור (לא מעורב) | pdf | ✓ discharge=hospital_discharge |
| continuation | ברור (לא מעורב) | scan | ✓ discharge=hospital_discharge |
| mixed | מעורב, עם סוג נכון לכל עמוד | pdf | ✓ discharge-emg=mixed · ✓ poa-id=mixed |
| mixed | מעורב, עם סוג נכון לכל עמוד | scan | ✓ discharge-emg=mixed · ✓ poa-id=mixed |
| unknown | לא ידוע | pdf | ✓ electricity_bill=unknown · ✓ general_letter=unknown · ✓ shop_receipt=unknown |
| unknown | לא ידוע | scan | ✓ electricity_bill=unknown · ✓ general_letter=unknown · ✓ shop_receipt=unknown |
| unreadable | לא קריא | scan | ✓ blank=unreadable · ✓ noise=unreadable |

## סיווגים שגויים בביטחון

- אין.

## "לא ידוע" / "לא קריא" שסווגו בכל זאת

- אין.

## מסמכים שנותרו ללא סיווג (הועברו לאדם)

- `confuser-discharge_mentions_mri` (scan): unknown, מועמדים: ['hospital_discharge', 'imaging']

## כשלים

- אין. כל 182 הריצות הסתיימו.
