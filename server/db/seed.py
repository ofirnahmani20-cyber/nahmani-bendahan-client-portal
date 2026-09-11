"""
seed.py - נתוני ההדגמה, מומרים משני התיקים שב-assets/js/data.js.

המטרה אינה "למלא את המסד" אלא לאפשר השוואה: מה שהמסד מחזיר
צריך להתאים למה שהאתר מציג היום.

⚠️ אלה נתוני הדגמה בדויים. הסיסמאות כאן הן סיסמאות ההדגמה
   מ-data.js ואין להשתמש בהן בסביבה אמיתית.
"""

import bcrypt

from .connect import id_lookup, id_encrypt


FIRM_SLUG = "nahmani-ben-dahan"

# ------------------------------------------------------------------
#  שמונת השלבים.
#
#  ב-data.js זהו קבוע אחד (CLAIM_STAGES) שמשרת את שני סוגי
#  התביעה - וזה היה באג: תיק נכות מעבודה הוצג לפי שלבי נכות
#  כללית. כאן כל סוג תביעה מקבל שורות משלו, ולכן המשרד יכול
#  לפצל את המסלולים בלי לגעת בקוד.
#
#  התוכן עדיין זהה לשניהם כי זה מה שקיים היום. **על המשרד
#  לעבור על מסלול נכות מעבודה ולהתאים אותו** - הוא אינו זהה
#  למסלול נכות כללית במציאות.
# ------------------------------------------------------------------
STAGES = [
    ("פתיחת תיק ואיסוף מסמכים",   "חתימה על ייפוי כוח, איסוף מסמכים רפואיים ותלושי שכר."),
    ("הגשת התביעה לביטוח לאומי",  "הגשת טופס התביעה בצירוף כלל האסמכתאות לסניף."),
    ("בדיקת התביעה בסניף",        "פקיד התביעות בוחן זכאות עקרונית ומבקש השלמות במידת הצורך."),
    ("זימון לוועדה רפואית",       "קבלת זימון לוועדה רפואית מדרג ראשון והכנה מקדימה."),
    ("ועדה רפואית מדרג ראשון",    "התייצבות בוועדה, הצגת התיק הרפואי וקביעת אחוזי נכות."),
    ("קבלת החלטת הוועדה",         "פרוטוקול הוועדה מתקבל ונבחן על ידי המשרד."),
    ("הגשת ערר / ועדת עררים",     "ככל שנדרש - הגשת ערר מנומק ודיון בוועדה לעררים."),
    ("סיום התיק ותשלום הגמלה",    "קבלת ההחלטה הסופית, חישוב רטרו ותשלום הגמלה."),
]

CLAIM_TYPES = [
    ("נכות כללית",  "general-disability", 1),
    ("נכות מעבודה", "work-injury",        2),
]

STAFF = [
    # username לשעבר ב-StaffAuth -> email אמיתי
    ("עו\"ד אופיר נחמני", "nahmani@nahmani-bendahan.co.il",  "office2026",
     "admin", "שותפה, מחלקת ביטוח לאומי", "03-5551234"),
    ("עו\"ד בן דהן",      "bendahan@nahmani-bendahan.co.il", "office2026",
     "staff", "ראש מחלקת נפגעי עבודה",     "03-5551235"),
]

STATUS_MAP = {
    "approved":       "approved",
    "pending-review": "pending_review",
    "missing":        "missing",
    "rejected":       "rejected",
}

CASES = [
    {
        "national_id": "123456782",
        "client_name": "ישראל ישראלי",
        "phone": "050-1234567",
        "email": "israel@example.com",
        "case_number": "BL-2026-0417",
        "claim_code": "general-disability",
        "opened_at": "2026-03-11",
        "branch": "סניף תל אביב",
        "next_hearing": "2026-09-22 10:30",
        "lawyer_email": "nahmani@nahmani-bendahan.co.il",
        # stage_entries[i] = מתי נכנס התיק לשלב i+1.
        # נגזר מ-stageDates שב-data.js, שם התאריך הוא סיום השלב:
        # סיום שלב N הוא בדיוק הכניסה לשלב N+1.
        "stage_entries": ["2026-03-11", "2026-03-11", "2026-04-02",
                          "2026-05-20", "2026-08-19"],
        "decision": None,
        "documents": [
            ("צילום תעודת זהות + ספח", "קריא, כולל הספח המלא", True, "approved", "teudat_zehut.pdf", "2026-03-11"),
            ("ייפוי כוח חתום", "חתום בפני עורך דין", True, "approved", "yipuy_koach.pdf", "2026-03-11"),
            ("טופס ויתור על סודיות רפואית", "טופס 1811 של המוסד לביטוח לאומי", True, "approved", "vitur_sodiyut.pdf", "2026-03-14"),
            ("סיכומי אשפוז", "כל האשפוזים משנת 2023 ואילך", True, "pending-review", "sikum_ishpuz.pdf", "2026-08-28"),
            ("חוות דעת רפואית עדכנית", "מרופא מומחה בתחום הרלוונטי, עד 6 חודשים אחורה", True, "missing", None, None),
            ("תלושי שכר - 12 חודשים אחרונים", "לחישוב בסיס הגמלה", True, "missing", None, None),
            ("אישורי מחלה (טופס 100)", "מקופת החולים, לתקופת אי הכושר", True, "missing", None, None),
            ("תוצאות בדיקות הדמיה", "MRI / CT / רנטגן - דיסק או קובץ סרוק", False, "missing", None, None),
            ("אישור על קצבאות אחרות", "ככל שמתקבלות קצבאות ממקור אחר", False, "missing", None, None),
        ],
        "next_steps": [
            ("התייצבות בוועדה רפואית", "הוועדה תתקיים בסניף תל אביב. יש להגיע 20 דקות מראש עם תעודת זהות וכל המסמכים הרפואיים המקוריים.", "ב-22.09.2026, בשעה 10:30"),
            ("פגישת הכנה עם עורכת הדין", "שיחת הכנה לקראת הוועדה - סקירת התיק הרפואי ותרגול מענה לשאלות הוועדה.", "ייקבע לאחר השלמת המסמכים החסרים"),
            ("קבלת פרוטוקול הוועדה", "הפרוטוקול מתקבל בדרך כלל תוך 30-45 יום. המשרד יבחן אותו וימליץ אם להגיש ערר.", "צפוי בנובמבר 2026"),
        ],
        "messages": [
            ("התקבל זימון לוועדה רפואית", "התקבל זימון לוועדה רפואית מדרג ראשון ליום 22.09.2026 בשעה 10:30, בסניף תל אביב. נא להשלים את המסמכים החסרים לכל המאוחר עד 15.09.2026.", True, "2026-08-19"),
            ("סיכומי האשפוז התקבלו", "סיכומי האשפוז שהעלית התקבלו ונמצאים בבדיקת המשרד. נעדכן תוך 3 ימי עסקים.", False, "2026-08-28"),
            ("התביעה הוגשה לביטוח לאומי", "התביעה הוגשה לסניף תל אביב וקיבלה מספר אסמכתא. זמן הטיפול הממוצע בשלב זה הוא 60-90 יום.", False, "2026-04-02"),
        ],
    },
    {
        "national_id": "987654321",
        "client_name": "שרה כהן",
        "phone": "052-9876543",
        "email": "sara@example.com",
        "case_number": "BL-2026-0388",
        "claim_code": "work-injury",
        "opened_at": "2026-01-08",
        "branch": "סניף חיפה",
        "next_hearing": "2026-10-14 09:00",
        "lawyer_email": "bendahan@nahmani-bendahan.co.il",
        "stage_entries": ["2026-01-08", "2026-01-08", "2026-01-29",
                          "2026-03-15", "2026-05-06", "2026-06-18", "2026-07-22"],
        "decision": {
            "decided_at": "2026-07-22",
            "outcome": "below-threshold",
            "percent": 10,
            "is_permanent": False,
            "appeal_deadline": "2026-09-20",
            "office_note": "הגשנו ערר. אנו טוענים לאחוזים גבוהים משמעותית בהתאם לתיעוד הרפואי.",
        },
        "documents": [
            ("צילום תעודת זהות + ספח", "קריא, כולל הספח המלא", True, "approved", "id_sara.pdf", "2026-01-08"),
            ("ייפוי כוח חתום", "חתום בפני עורך דין", True, "approved", "poa_sara.pdf", "2026-01-08"),
            ("הודעה על פגיעה בעבודה (ב.ל 250)", "חתום על ידי המעסיק", True, "approved", "bl250.pdf", "2026-01-12"),
            ("פרוטוקול ועדה מדרג ראשון", "התקבל מהמוסד לביטוח לאומי", True, "approved", "protocol_1.pdf", "2026-07-22"),
            ("חוות דעת מומחה מטעמנו", "לצורך הדיון בוועדת העררים", True, "missing", None, None),
            ("תיעוד טיפולים פיזיותרפיים", "מ-2026 ואילך", False, "pending-review", "physio.pdf", "2026-08-30"),
        ],
        "next_steps": [
            ("דיון בוועדה הרפואית לעררים", "הוועדה תדון בערר שהוגש על קביעת 10% הנכות. עו\"ד בן דהן ילווה אותך לדיון.", "ב-14.10.2026, בשעה 09:00"),
            ("הגשת חוות דעת נגדית", "חוות דעת מומחה מטעמנו תוגש לוועדה עד 14 יום לפני מועד הדיון.", "עד 30.09.2026"),
            ("בחינת פנייה לבית הדין לעבודה", "ככל שהערר יידחה, נבחן הגשת ערעור לבית הדין האזורי לעבודה.", "לאחר קבלת החלטת העררים"),
        ],
        "messages": [
            ("התקבלה החלטת הוועדה", "הוועדה קבעה 10% נכות זמנית. המשרד בחן את הפרוטוקול והגיש ערר.", True, "2026-07-22"),
        ],
    },
]


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def run(conn) -> dict:
    """זורע את המסד. מנקה קודם, כדי שההרצה תחזור על עצמה."""
    counts = {}
    with conn.cursor() as cur:

        # ניקוי. ON DELETE CASCADE עושה את רוב העבודה; הסדר כאן
        # הוא לפי התלות ההפוכה עבור מה שמוגדר RESTRICT.
        cur.execute("""
            TRUNCATE audit_log, sessions, otp_challenges, notifications,
                     notification_preferences, messages, document_replies,
                     document_files, case_documents, case_decisions,
                     case_next_steps, case_stage_events, cases,
                     required_document_templates, stage_templates,
                     claim_types, clients, users, firms
            RESTART IDENTITY CASCADE
        """)

        cur.execute(
            "INSERT INTO firms (name, slug, timezone) VALUES (%s, %s, %s) RETURNING id",
            ("משרד עורכי הדין Nahmani ben-dahan", FIRM_SLUG, "Asia/Jerusalem"),
        )
        firm_id = cur.fetchone()[0]

        users = {}
        for full_name, email, password, role, title, phone in STAFF:
            cur.execute(
                """INSERT INTO users (firm_id, full_name, email, password_hash,
                                      role, title, phone)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (firm_id, full_name, email, _hash(password), role, title, phone),
            )
            users[email] = cur.fetchone()[0]
        counts["users"] = len(users)

        claim_types = {}
        stages = {}
        for name, code, position in CLAIM_TYPES:
            cur.execute(
                """INSERT INTO claim_types (firm_id, name, code, position)
                   VALUES (%s,%s,%s,%s) RETURNING id""",
                (firm_id, name, code, position),
            )
            ct_id = cur.fetchone()[0]
            claim_types[code] = ct_id
            stages[code] = []
            for i, (title, desc) in enumerate(STAGES, start=1):
                cur.execute(
                    """INSERT INTO stage_templates
                         (firm_id, claim_type_id, position, title, description, is_terminal)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (firm_id, ct_id, i, title, desc, i == len(STAGES)),
                )
                stages[code].append(cur.fetchone()[0])
        counts["claim_types"] = len(claim_types)
        counts["stage_templates"] = sum(len(v) for v in stages.values())

        counts["cases"] = 0
        counts["documents"] = 0
        counts["stage_events"] = 0
        for c in CASES:
            cur.execute(
                """INSERT INTO clients (firm_id, full_name, national_id_lookup,
                                        national_id_enc, phone, email,
                                        privacy_accepted_at)
                   VALUES (%s,%s,%s,%s,%s,%s, now()) RETURNING id""",
                (firm_id, c["client_name"],
                 id_lookup(c["national_id"]), id_encrypt(c["national_id"]),
                 c["phone"], c["email"]),
            )
            client_id = cur.fetchone()[0]

            ct_id = claim_types[c["claim_code"]]
            cur.execute(
                """INSERT INTO cases (firm_id, client_id, claim_type_id, case_number,
                                      branch, opened_at, assigned_user_id, next_hearing_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (firm_id, client_id, ct_id, c["case_number"], c["branch"],
                 c["opened_at"], users[c["lawyer_email"]], c["next_hearing"]),
            )
            case_id = cur.fetchone()[0]
            counts["cases"] += 1

            for i, entered_at in enumerate(c["stage_entries"]):
                cur.execute(
                    """INSERT INTO case_stage_events
                         (firm_id, case_id, stage_template_id, claim_type_id,
                          occurred_at, created_by_user_id)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (firm_id, case_id, stages[c["claim_code"]][i], ct_id,
                     entered_at, users[c["lawyer_email"]]),
                )
                counts["stage_events"] += 1

            for pos, (name, guidance, required, status, filename, date) in enumerate(c["documents"], start=1):
                cur.execute(
                    """INSERT INTO case_documents
                         (firm_id, case_id, name, guidance, is_required,
                          status, position, reviewed_by_user_id, reviewed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (firm_id, case_id, name, guidance, required,
                     STATUS_MAP[status], pos,
                     users[c["lawyer_email"]] if status == "approved" else None,
                     date if status == "approved" else None),
                )
                doc_id = cur.fetchone()[0]
                counts["documents"] += 1

                # שם קובץ בלבד קיים בנתוני ההדגמה. אין קובץ אמיתי,
                # ולכן scan_status נשאר pending - קובץ נגיש להורדה
                # רק כשהוא clean.
                if filename:
                    cur.execute(
                        """INSERT INTO document_files
                             (firm_id, document_id, storage_key, original_filename,
                              uploaded_at, uploaded_by_client_id)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (firm_id, doc_id,
                         f"firms/{firm_id}/cases/{case_id}/{doc_id}",
                         filename, date, client_id),
                    )

            for pos, (title, desc, eta) in enumerate(c["next_steps"], start=1):
                cur.execute(
                    """INSERT INTO case_next_steps
                         (firm_id, case_id, title, description, eta_text, position)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (firm_id, case_id, title, desc, eta, pos),
                )

            for title, body, important, sent_at in c["messages"]:
                cur.execute(
                    """INSERT INTO messages
                         (firm_id, case_id, title, body, is_important,
                          sent_by_user_id, sent_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (firm_id, case_id, title, body, important,
                     users[c["lawyer_email"]], sent_at),
                )

            d = c["decision"]
            if d:
                cur.execute(
                    """INSERT INTO case_decisions
                         (firm_id, case_id, decided_at, outcome, percent,
                          is_permanent, appeal_deadline, office_note,
                          recorded_by_user_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (firm_id, case_id, d["decided_at"], d["outcome"], d["percent"],
                     d["is_permanent"], d["appeal_deadline"], d["office_note"],
                     users[c["lawyer_email"]]),
                )

    conn.commit()
    return counts
