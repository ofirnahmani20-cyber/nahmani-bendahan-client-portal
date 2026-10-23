"""
fake_clamd.py - שרת clamd מזויף לבדיקות.

מדבר את אותו פרוטוקול (zPING, zVERSION, zINSTREAM) כדי שהלקוח
האמיתי ב-scan.py ייבדק מקצה לקצה, בלי ClamAV מותקן. "נגוע" =
התוכן מכיל את מחרוזת EICAR.
"""

import socket
import struct
import threading

EICAR = (b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
         b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")


class FakeClamd:
    def __init__(self, version="ClamAV 1.4.1/27412/Wed Oct  1 08:21:00 2026",
                 broken=False):
        self.version = version
        self.broken = broken
        self.scanned = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.addr = "tcp://127.0.0.1:%d" % self.sock.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _read_cmd(self, conn):
        buf = b""
        while not buf.endswith(b"\0"):
            chunk = conn.recv(1)
            if not chunk:
                return None
            buf += chunk
        return buf[:-1]

    def _recv_exact(self, conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError
            buf += chunk
        return buf

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                try:
                    cmd = self._read_cmd(conn)
                    if self.broken:
                        conn.sendall(b"garbage\0")
                    elif cmd == b"zPING":
                        conn.sendall(b"PONG\0")
                    elif cmd == b"zVERSION":
                        conn.sendall(self.version.encode() + b"\0")
                    elif cmd == b"zINSTREAM":
                        data = b""
                        while True:
                            (n,) = struct.unpack(">I", self._recv_exact(conn, 4))
                            if n == 0:
                                break
                            data += self._recv_exact(conn, n)
                        self.scanned += 1
                        if EICAR in data:
                            conn.sendall(b"stream: Eicar-Signature FOUND\0")
                        else:
                            conn.sendall(b"stream: OK\0")
                except (ConnectionError, OSError):
                    pass

    def close(self):
        self._stop = True
        self.sock.close()
