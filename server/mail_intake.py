"""
mail_intake.py - קליטת מסמכים מתיבת מייל של המשרד (docs/10, v1 סעיף 4).

    python -m server.mail_intake        סבב אחד (גם רץ בתוך notifier --loop)

לקוח ששולח מסמך במייל - או מעביר אליו מווטסאפ - לא צריך לדעת דבר
על הפורטל. כל קובץ מצורף עובר את אותו מסלול של העלאה בפורטל: בדיקת
סוג לפי תוכן (storage.validate), סריקה (scan), הצפנה. קובץ נגוע אינו
נשמר בכלל.

מה המערכת *לא* עושה לבד
------------------------
היא אינה מכניסה את הקובץ לתיק. כתובת שולח ניתנת לזיוף בקלות, ולכן
השולח והתיק הם הצעה בלבד (matched_*), ואיש צוות משייך כל פריט
לדרישה (api_mailbox.py). רק אז הקובץ הופך ל-document_files ונכנס
לעיבוד - וגם להחלטה האוטומטית.

מה נשמר מההודעה: כתובת השולח, שם הקובץ, ומתי הגיע. לא נושא ולא גוף:
שורת נושא כמו "בדיקת MRI של רוחמה" היא מידע רפואי מזוהה, ואינה
נחוצה כדי לשייך קובץ.

הגדרה (סביבה)
-------------
    PORTAL_INTAKE_IMAP_HOST       למשל imap.gmail.com. בלעדיו - כבוי.
    PORTAL_INTAKE_IMAP_USER
    PORTAL_INTAKE_IMAP_PASSWORD_FILE   קובץ סוד (עדיף), או
    PORTAL_INTAKE_IMAP_PASSWORD
    PORTAL_INTAKE_FOLDER          ברירת מחדל INBOX
    PORTAL_INTAKE_DONE_FOLDER     לשם מועברת הודעה שטופלה. ברירת מחדל Processed
"""

import email
import email.policy
import email.utils
import hashlib
import imaplib
import os
import re
import sys
import uuid

from . import crypto, scan, storage
from .db.pool import cursor

MAX_MESSAGES_PER_RUN = 50
MAX_ATTACHMENTS_PER_MESSAGE = 20

CASE_NUMBER = re.compile(r"\b[A-Z]{2,}-\d{4}-\d{4}\b")


# ================================================================
#  פענוח הודעה - בלי רשת ובלי מסד
# ================================================================

def parse(raw: bytes):
    """
    (כתובת שולח, Message-ID, מספרי תיק מהנושא, [(שם, bytes)]).
    הנושא נקרא רק כדי למצוא בו מספר תיק, ואינו מוחזר.
    """
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    _, addr = email.utils.parseaddr(str(msg.get("From", "")))
    message_id = str(msg.get("Message-ID", "")).strip() or hashlib.sha256(raw).hexdigest()
    subject = str(msg.get("Subject", ""))
    cases = CASE_NUMBER.findall(subject.upper())

    files = []
    for part in msg.iter_attachments():
        if len(files) >= MAX_ATTACHMENTS_PER_MESSAGE:
            break
        data = part.get_payload(decode=True)
        if data:
            files.append((part.get_filename() or "attachment", data))
    # תמונה שהודבקה בגוף ההודעה (טלפונים עושים את זה) אינה "מצורף"
    if not files:
        for part in msg.walk():
            if part.get_content_maintype() == "image" and len(files) < MAX_ATTACHMENTS_PER_MESSAGE:
                data = part.get_payload(decode=True)
                if data:
                    files.append((part.get_filename() or "image", data))
    return addr.strip().lower(), message_id, cases, files


def _match(cur, firm_id, sender, case_numbers):
    """(client_id, case_id) - הצעה בלבד. כל עמימות: אין התאמה."""
    if not sender:
        return None, None
    cur.execute("""select id from clients
                    where firm_id = %s and status = 'active' and lower(email) = %s""",
                (firm_id, sender))
    clients = cur.fetchall()
    if len(clients) != 1:
        return None, None
    client_id = clients[0]["id"]
    cur.execute("""select id, case_number from cases
                    where client_id = %s and firm_id = %s and status in ('active', 'frozen')""",
                (client_id, firm_id))
    cases = cur.fetchall()
    named = [c for c in cases if c["case_number"] in case_numbers]
    if len(named) == 1:
        return client_id, named[0]["id"]
    if len(cases) == 1:
        return client_id, cases[0]["id"]
    return client_id, None


def ingest(raw: bytes, firm_id):
    """
    הודעה אחת -> שורות intake_items. מחזיר ספירות:
    {stored, duplicate, rejected, infected}.
    """
    sender, message_id, case_numbers, files = parse(raw)
    counts = {"stored": 0, "duplicate": 0, "rejected": 0, "infected": 0}
    with cursor() as cur:
        client_id, case_id = _match(cur, firm_id, sender, case_numbers)

    for index, (name, data) in enumerate(files):
        dedupe = hashlib.sha256(("%s|%d" % (message_id, index)).encode()).hexdigest()
        with cursor() as cur:
            cur.execute("select 1 from intake_items where firm_id = %s and dedupe_hash = %s",
                        (firm_id, dedupe))
            if cur.fetchone():
                counts["duplicate"] += 1
                continue
        try:
            mime = storage.validate(data)
        except storage.RejectedFile:
            counts["rejected"] += 1          # חתימות מייל, קבצי Word וכדומה
            continue
        result = scan.get_scanner().scan(data)
        if result.status == "infected":
            counts["infected"] += 1          # לא נשמר בכלל, כמו בפורטל
            continue

        key = "firms/%s/intake/%s" % (firm_id, uuid.uuid4())
        storage.store_encrypted(data, key)
        try:
            with cursor(commit=True) as cur:
                cur.execute(
                    """insert into intake_items
                         (firm_id, from_address, dedupe_hash, matched_client_id,
                          matched_case_id, storage_key, original_filename, mime_type,
                          size_bytes, checksum, scan_status)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (firm_id, sender or None, dedupe, client_id, case_id, key,
                     storage.safe_filename(name), mime, len(data),
                     hashlib.sha256(data).hexdigest(), result.status))
        except BaseException:
            storage.delete(key)
            raise
        counts["stored"] += 1
    return counts


# ================================================================
#  IMAP
# ================================================================

def configured() -> bool:
    return bool(os.environ.get("PORTAL_INTAKE_IMAP_HOST"))


def _password():
    path = os.environ.get("PORTAL_INTAKE_IMAP_PASSWORD_FILE")
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get("PORTAL_INTAKE_IMAP_PASSWORD", "")


def _firm_id():
    with cursor() as cur:
        cur.execute("select id from firms order by created_at limit 1")
        row = cur.fetchone()
    return row["id"] if row else None


def poll():
    """
    סבב אחד מול התיבה. הודעה מועברת לתיקיית "טופל" רק אחרי שכל
    הקבצים שלה נשמרו; כישלון באמצע משאיר אותה בתיבה לסבב הבא,
    ו-dedupe_hash מונע כפילות של מה שכבר נשמר.
    """
    if not configured():
        return None
    try:
        crypto.provider()
    except crypto.KeysUnavailable:
        return {"error": "keys_unavailable"}
    firm_id = _firm_id()
    folder = os.environ.get("PORTAL_INTAKE_FOLDER", "INBOX")
    done = os.environ.get("PORTAL_INTAKE_DONE_FOLDER", "Processed")
    totals = {"messages": 0, "stored": 0, "duplicate": 0, "rejected": 0, "infected": 0}

    imap = imaplib.IMAP4_SSL(os.environ["PORTAL_INTAKE_IMAP_HOST"])
    try:
        imap.login(os.environ.get("PORTAL_INTAKE_IMAP_USER", ""), _password())
        imap.select(folder)
        status, data = imap.uid("search", None, "UNDELETED")
        uids = (data[0] or b"").split()[:MAX_MESSAGES_PER_RUN] if status == "OK" else []
        imap.create(done)                     # כבר קיימת? השרת מחזיר NO, וזה בסדר
        for uid in uids:
            status, parts = imap.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            counts = ingest(parts[0][1], firm_id)
            for k, v in counts.items():
                totals[k] += v
            totals["messages"] += 1
            if imap.uid("copy", uid, done)[0] == "OK":
                imap.uid("store", uid, "+FLAGS", r"(\Deleted)")
        imap.expunge()
    finally:
        try:
            imap.logout()
        except Exception:                                # noqa: BLE001
            pass
    return totals


if __name__ == "__main__":
    print("[mail_intake] %s" % poll(), flush=True)
    sys.exit(0)
