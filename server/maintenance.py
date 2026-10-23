"""
maintenance.py - משימות תחזוקה של האחסון.

    python -m server.maintenance rescan [--limit N]
        סריקה חוזרת לקבצים במצב pending/failed. הקובץ מפוענח
        בזיכרון ונשלח לסורק מהזיכרון - אינו נכתב גלוי לשום מקום.
        נגוע: מסומן infected והצופן נמחק. מיועד ל-timer מחזורי.

    python -m server.maintenance sweep [--apply]
        ניקוי .part שנשארו מכתיבה שנקטעה, וקבצים מוצפנים שאין להם
        שורה במסד. ברירת המחדל היא דיווח בלבד.

    python -m server.maintenance orphan-report [--out FILE]
        דוח קריאה-בלבד על קבצים בלי שורה במסד: מקור, שיוך, תוכן (סוג
        בלבד). ל-var/reports, שאינו נכנס ל-git.

    python -m server.maintenance encrypt-legacy [--apply]
        הצפנה חד-פעמית של קבצים שנשמרו גלויים לפני שההצפנה נוספה.
        ברירת המחדל היא דיווח בלבד.

הפלט ASCII בלבד וספירות בלבד: אין שמות קבצים, כי שם קובץ שלקוח
העלה עלול לזהות אותו.
"""

import argparse
import pathlib
import sys

from . import audit, crypto, processing, scan, storage
from .db.pool import cursor


def rescan(limit=200):
    scanner = scan.get_scanner()
    if not scanner.production_safe:
        return {"skipped": "no scanner configured"}

    with cursor() as cur:
        cur.execute(
            """select id, firm_id, storage_key from document_files
                where scan_status in ('pending', 'failed')
                order by uploaded_at limit %s""", (limit,))
        rows = cur.fetchall()

    counts = {"clean": 0, "infected": 0, "failed": 0, "pending": 0,
              "missing": 0, "unreadable": 0}
    for row in rows:
        try:
            data = storage.read(row["storage_key"])
        except FileNotFoundError:
            counts["missing"] += 1
            continue
        except (crypto.DecryptionFailed, crypto.KeysUnavailable):
            # קובץ גלוי מלפני ההצפנה, או מפתח חסר. לא נסרק עד
            # שיטופל (encrypt-legacy / שחזור מפתח) - ולכן גם לא usable.
            counts["unreadable"] += 1
            continue
        result = scanner.scan(data)
        del data
        counts[result.status] += 1
        with cursor(commit=True) as cur:
            cur.execute("update document_files set scan_status = %s where id = %s",
                        (result.status, row["id"]))
            if scan.usable(result.status):
                processing.enqueue(cur, row["id"], row["firm_id"])
        if result.status == "infected":
            storage.delete(row["storage_key"])
        if result.status in ("clean", "infected"):
            audit.record_actor(row["firm_id"], "system", None, "system.file_rescanned",
                               metadata={"file_id": str(row["id"]),
                                         "scan_status": result.status,
                                         "scan_signature": result.detail})
    return counts


def _known_keys():
    with cursor() as cur:
        cur.execute("select storage_key from document_files")
        return {r["storage_key"] for r in cur.fetchall()}


def sweep(apply=False):
    return storage.sweep(_known_keys(), dry_run=not apply)


def sweep_parts_only():
    """בעלייה: רק .part. אינו נוגע במסד ולכן בטוח גם כשהמסד איטי."""
    return storage.sweep(None)


def orphan_report():
    """
    דוח על קבצים באחסון שאין להם שורה ב-document_files. קריאה בלבד -
    אינו מוחק, אינו משנה ואינו כותב ליומן.

    לכל קובץ נבדק: האם קיים המשרד, האם קיים התיק, האם יש מסמך או
    שורת קובץ שמפנים אליו, ומה היומן יודע על התיק. התוכן מסווג לפי
    הבתים הראשונים בלבד ("שלד בדיקה" / "תוכן אחר") - שום טקסט אינו
    נכנס לדוח.
    """
    import datetime

    with cursor() as cur:
        cur.execute("select storage_key from document_files")
        known = {r["storage_key"] for r in cur.fetchall()}

    rows = []
    for key, path in storage.iter_files():
        if key in known:
            continue
        parts = key.split("/")
        firm_id, case_id, file_uuid = parts[1], parts[3], parts[4]
        stat = path.stat()
        blob = path.read_bytes()
        header = crypto.parse_header(blob)
        try:
            plain = storage.read(key)
            # שלד הבדיקות: PDF של עשרות בתים בלי עמוד אחד. מסמך אמיתי
            # אינו קטן מ-200 בתים.
            content = ("synthetic test stub" if plain.startswith(b"%PDF-1.4\n")
                       and len(plain) < 200 else "other content")
            del plain
            readable = True
        except (crypto.DecryptionFailed, crypto.KeysUnavailable):
            content, readable = "unreadable", False
        rows.append({"key": key, "firm_id": firm_id, "case_id": case_id,
                     "file_uuid": file_uuid, "size": stat.st_size,
                     "mtime": datetime.datetime.fromtimestamp(stat.st_mtime),
                     "encrypted": header is not None, "readable": readable,
                     "content": content})

    with cursor() as cur:
        for r in rows:
            cur.execute("select 1 from firms where id = %s", (r["firm_id"],))
            r["firm_exists"] = cur.fetchone() is not None
            cur.execute("select case_number from cases where id = %s", (r["case_id"],))
            case = cur.fetchone()
            r["case_exists"] = case is not None
            cur.execute("select count(*) as n from case_documents where case_id = %s",
                        (r["case_id"],))
            r["documents_in_case"] = cur.fetchone()["n"]
            cur.execute("select count(*) as n from document_files"
                        " where storage_key like %s", ("%" + r["file_uuid"] + "%",))
            r["file_rows"] = cur.fetchone()["n"]
            cur.execute("""select count(*) as n, min(created_at) as first,
                                  string_agg(distinct action, ',') as actions
                             from audit_log where case_id = %s""", (r["case_id"],))
            a = cur.fetchone()
            r["audit_rows"], r["audit_actions"] = a["n"], a["actions"] or ""
    return rows


def rows_without_files():
    """
    הכיוון ההפוך: שורות document_files שהקובץ שלהן אינו באחסון.

    origin: "seed" - נתוני ההדגמה יוצרים שורה בלי קובץ במכוון
    (seed.py), ומפתח האחסון שלהן מסתיים במזהה המסמך. כל השאר -
    "other".
    """
    with cursor() as cur:
        cur.execute("""select f.id, f.storage_key, f.scan_status, f.uploaded_at,
                              f.document_id, d.case_id
                         from document_files f
                         join case_documents d on d.id = f.document_id""")
        rows = cur.fetchall()
    out = []
    for r in rows:
        try:
            exists = storage._path_for(r["storage_key"]).exists()
        except storage.RejectedFile:
            exists = False
        if not exists:
            seed = r["storage_key"].rsplit("/", 1)[-1] == str(r["document_id"])
            out.append(dict(r, origin="seed" if seed else "other"))
    return out


def write_orphan_report(rows, path, missing=None):
    from collections import defaultdict

    by_case = defaultdict(list)
    for r in rows:
        by_case[r["case_id"]].append(r)
    attached = [r for r in rows if r["case_exists"] or r["file_rows"] or r["documents_in_case"]]

    lines = ["# דוח קבצים יתומים - %s" % __import__("datetime").date.today(), "",
             "> קריאה בלבד. לא נמחק ולא שונה דבר. תוכן מסווג לפי בתים ראשונים בלבד.", "",
             "**סה\"כ:** %d קבצים · **משויכים לתיק או למסמך קיים:** %d" % (len(rows), len(attached)),
             "", "| תיק (case_id) | קבצים | התיק קיים | המשרד קיים | מסמכים בתיק | שורות קובץ |"
             " מוצפנים | תוכן | נוצרו | שורות ביומן |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for case_id, rs in sorted(by_case.items(), key=lambda kv: -len(kv[1])):
        times = sorted(r["mtime"] for r in rs)
        contents = sorted({r["content"] for r in rs})
        lines.append("| `%s` | %d | %s | %s | %d | %d | %d/%d | %s | %s – %s | %d |" % (
            case_id, len(rs), "כן" if rs[0]["case_exists"] else "**לא**",
            "כן" if rs[0]["firm_exists"] else "לא", rs[0]["documents_in_case"],
            sum(r["file_rows"] for r in rs), sum(r["encrypted"] for r in rs), len(rs),
            ", ".join(contents), times[0].strftime("%Y-%m-%d %H:%M"),
            times[-1].strftime("%Y-%m-%d %H:%M"), rs[0]["audit_rows"]))
    stubs = all(r["content"] == "synthetic test stub" for r in rows)
    lines += ["", "## מסקנה", ""]
    if rows and not attached and stubs:
        lines += [
            "- **אף קובץ אינו משויך** לתיק, למסמך או לשורת קובץ קיימים, ואין להם שורות ביומן.",
            "- **כולם שלדי בדיקה סינתטיים** (PDF של עשרות בתים) - לא מסמכי לקוח.",
            "- **מקור משוער:** ריצות pytest לפני 2026-10-02, כשהבדיקות כתבו לתיקיית האחסון של",
            "  הפיתוח: תיק זמני (`temp_case`) ומשרד בדיקה (`second_firm`) נמחקו מהמסד בסוף",
            "  הבדיקה, והקבצים נשארו. מאז שלב 0 הבדיקות כותבות לתיקייה זמנית משלהן, כך",
            "  שהמקור הזה נסגר.",
            "- ⚠️ זמן העדכון של כל הקבצים זהה, כי `encrypt-legacy` כתב אותם מחדש. זמן",
            "  ההעלאה המקורי אבד, ולכן אין לו משקל בדוח.",
        ]
    else:
        lines += ["- נמצאו קבצים משויכים או עם תוכן שאינו שלד בדיקה - "
                  "**לבדיקה ידנית לפני כל מחיקה**."]
    if missing is not None:
        from collections import Counter
        lines += ["", "## הכיוון ההפוך: שורות במסד בלי קובץ", "",
                  "**%d שורות** ב-`document_files` מפנות לקובץ שאינו באחסון. אינן "
                  "חושפות דבר (אין קובץ להוריד), אבל ה-worker יסמן אותן `file_missing`."
                  % len(missing), "",
                  "| מקור | שורות | סטטוס | טווח העלאה | הסבר |", "|---|---|---|---|---|"]
        notes = {"seed": "נתוני ההדגמה (`seed.py`) - שם קובץ בלי קובץ, במכוון. תקין.",
                 "other": "ריצות pytest שהעלו למסמכי הזרע; הקובץ נשמר בתיקייה "
                          "זמנית שנמחקה. מאז 2026-10-02 הבדיקות מוחקות את השורות שלהן."}
        for origin in ("seed", "other"):
            rs = [r for r in missing if r["origin"] == origin]
            if not rs:
                continue
            status = ", ".join("%s=%d" % kv for kv in sorted(
                Counter(r["scan_status"] for r in rs).items()))
            lines.append("| %s | %d | %s | %s – %s | %s |" % (
                origin, len(rs), status,
                min(r["uploaded_at"] for r in rs).strftime("%Y-%m-%d"),
                max(r["uploaded_at"] for r in rs).strftime("%Y-%m-%d"), notes[origin]))
    lines += ["", "## קבצים", "", "| storage_key | גודל | נוצר | תוכן |", "|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: r["mtime"]):
        lines.append("| `%s` | %d | %s | %s |" % (r["key"], r["size"],
                                                    r["mtime"].strftime("%Y-%m-%d %H:%M"),
                                                    r["content"]))
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows), len(attached)


def encrypt_legacy(apply=False):
    counts = {"plaintext": 0, "encrypted": 0, "already": 0}
    for key, path in list(storage.iter_files()):
        blob = path.read_bytes()
        if crypto.is_encrypted(blob):
            counts["already"] += 1
            continue
        counts["plaintext"] += 1
        if apply:
            storage.store_encrypted(blob, key)
            counts["encrypted"] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m server.maintenance")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("rescan")
    p.add_argument("--limit", type=int, default=200)
    p = sub.add_parser("sweep")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("encrypt-legacy")
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("orphan-report")
    p.add_argument("--out", default=str(storage.ROOT / "var" / "reports" / "orphans.md"))
    args = parser.parse_args(argv)

    if args.cmd == "orphan-report":
        missing = rows_without_files()
        total, attached = write_orphan_report(orphan_report(), args.out, missing)
        print("orphans=%d attached_to_existing_case_or_document=%d rows_without_file=%d"
              % (total, attached, len(missing)))
        print("report: %s" % args.out)
        return 0

    if args.cmd == "rescan":
        result = rescan(args.limit)
    elif args.cmd == "sweep":
        result = sweep(args.apply)
    else:
        result = encrypt_legacy(args.apply)
    print(" ".join("%s=%s" % kv for kv in result.items()))
    if getattr(args, "apply", True) is False:
        print("(dry run - nothing changed; add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
