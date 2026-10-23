"""
scan.py - סריקת קבצים שהועלו.

מנוע: ClamAV (clamd), דרך PORTAL_CLAMD_ADDR. בלי הכתובת - NoScanner.

הכלל היחיד שאסור לשבור
-----------------------
`clean` נכתב אך ורק על ידי סורק אמיתי שהחזיר תוצאה. אין בקובץ
הזה - ובשום מקום אחר - נתיב שמסמן קובץ כנקי בלי סריקה.

מה קורה בפועל
--------------
demo       - NoScanner. הקובץ נשאר pending לנצח, ואינו ניתן
             להורדה. זה מצב גלוי ולא תקלה.
production - בלי סורק מוגדר, ההעלאה עצמה נדחית ב-503. מוטב
             לסרב לקבל מסמך מאשר לקבל אותו ולהשאיר אותו תקוע.

clamd    - ClamdScanner. הקובץ נשלח מהזיכרון בפרוטוקול INSTREAM,
           בלי קובץ זמני. OK -> clean, FOUND -> infected, וכל
           תקלה (חיבור, timeout, תשובה לא מוכרת) -> failed.
           failed אינו clean: הקובץ נשמר מוצפן ונסרק שוב
           (python -m server.maintenance rescan).

השער היחיד
-----------
usable() הוא הבדיקה היחידה שקובעת אם מותר לגעת בתוכן קובץ:
הורדה, צפייה, ובשלבים הבאים גם עיבוד. כל נתיב חדש שמחזיר
תוכן קובץ חייב לעבור בו.
"""

import datetime
import os
import socket
import struct


class ScannerNotConfigured(RuntimeError):
    """אין סורק. נזרק בייצור במקום להעמיד פנים."""


class Result:
    """תוצאת סריקה. status חייב להיות אחד מערכי scan_status בסכמה."""

    def __init__(self, status, detail=None):
        assert status in ("pending", "clean", "infected", "failed")
        self.status = status
        self.detail = detail


class Scanner:
    """הממשק שמנוע אמיתי יצטרך לממש."""

    #: האם מותר להסתמך על הסורק הזה בייצור
    production_safe = False

    def scan(self, data: bytes) -> Result:
        raise NotImplementedError


class NoScanner(Scanner):
    """
    אין מנוע. הקובץ נשאר pending.

    במכוון אינו מחזיר clean ואינו מחזיר failed: הקובץ לא נסרק,
    וזה בדיוק מה ש-pending אומר.
    """

    production_safe = False

    def scan(self, data: bytes) -> Result:
        return Result("pending", "לא הוגדר מנוע סריקה; הקובץ לא נסרק.")


class ClamdScanner(Scanner):
    """
    לקוח clamd מינימלי. stdlib בלבד - אין תלות חדשה.

    addr: "tcp://host:port", "host:port", או "unix:///path/clamd.sock".
    """

    production_safe = True
    CHUNK = 64 * 1024

    def __init__(self, addr: str, timeout: float = 30.0):
        self.addr = addr
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        if self.addr.startswith("unix://"):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect(self.addr[len("unix://"):])
            return sock
        hostport = self.addr.removeprefix("tcp://")
        host, _, port = hostport.rpartition(":")
        return socket.create_connection((host, int(port)), timeout=self.timeout)

    @staticmethod
    def _reply(sock) -> str:
        buf = b""
        while not buf.endswith(b"\0"):
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.rstrip(b"\0").decode("utf-8", "replace").strip()

    def _command(self, cmd: bytes) -> str:
        with self._connect() as sock:
            sock.sendall(b"z" + cmd + b"\0")
            return self._reply(sock)

    def ping(self) -> bool:
        try:
            return self._command(b"PING") == "PONG"
        except OSError:
            return False

    def signature_date(self):
        """תאריך מאגר החתימות, מתוך VERSION. None אם לא ידוע."""
        try:
            # "ClamAV 1.4.1/27412/Wed Oct  1 08:21:00 2026"
            parts = self._command(b"VERSION").split("/")
            if len(parts) < 3:
                return None
            return datetime.datetime.strptime(" ".join(parts[2].split()),
                                              "%a %b %d %H:%M:%S %Y")
        except (OSError, ValueError):
            return None

    def scan(self, data: bytes) -> Result:
        try:
            with self._connect() as sock:
                sock.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), self.CHUNK):
                    chunk = data[i:i + self.CHUNK]
                    sock.sendall(struct.pack(">I", len(chunk)) + chunk)
                sock.sendall(struct.pack(">I", 0))
                reply = self._reply(sock)
        except OSError as exc:
            return Result("failed", "סורק לא זמין (%s)." % type(exc).__name__)

        # "stream: OK" | "stream: Eicar-Signature FOUND" | "... ERROR"
        if reply.endswith(" OK"):
            return Result("clean")
        if reply.endswith(" FOUND"):
            signature = reply.split(":", 1)[-1].strip()[:-len(" FOUND")].strip()
            return Result("infected", signature[:120])
        return Result("failed", "תשובת סורק לא צפויה.")


def get_scanner() -> Scanner:
    addr = os.environ.get("PORTAL_CLAMD_ADDR", "").strip()
    if addr:
        return ClamdScanner(addr)
    return NoScanner()


def is_production() -> bool:
    return os.environ.get("PORTAL_MODE", "demo").strip().lower() == "production"


def assert_upload_allowed() -> None:
    """
    נקרא לפני קבלת קובץ.

    בייצור בלי סורק בטוח - סירוב. אין fallback ל-NoScanner.
    """
    if is_production() and not get_scanner().production_safe:
        raise ScannerNotConfigured(
            "לא הוגדר מנוע סריקת קבצים. העלאת מסמכים מושבתת במצב ייצור."
        )


def usable(scan_status: str) -> bool:
    """
    רק קובץ שנסרק ונמצא נקי ניתן להורדה, לצפייה או לעיבוד.

    pending, failed ו-infected - כולם לא. אין חריג לצוות ואין
    חריג למצב demo.
    """
    return scan_status == "clean"


# השם הישן נשמר לקריאות קיימות; usable הוא השם הנכון, כי השער
# חל על כל שימוש בתוכן ולא רק על הורדה.
downloadable = usable


SIGNATURE_MAX_AGE = datetime.timedelta(hours=48)


def production_problems() -> list:
    """מה חסר לסורק כדי שמותר יהיה לעלות בייצור. ריק = תקין."""
    scanner = get_scanner()
    if not scanner.production_safe:
        return ["לא הוגדר סורק קבצים (PORTAL_CLAMD_ADDR)"]
    problems = []
    if not scanner.ping():
        problems.append("סורק הקבצים אינו מגיב (%s)" % scanner.addr)
        return problems
    date = scanner.signature_date()
    if date is None:
        problems.append("לא ניתן לקרוא את תאריך חתימות הסורק")
    elif datetime.datetime.now() - date > SIGNATURE_MAX_AGE:
        problems.append("חתימות הסורק ישנות מ-48 שעות (%s)" % date.isoformat())
    return problems
