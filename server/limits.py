"""
limits.py - מגבלות משאבים קשיחות לתהליך העיבוד.

למה
----
ב-spike, PDF של 3MB התנפח ל-3.9GB זיכרון לפני שה-timeout הרג
אותו (spikes/ocr/RESULTS.md, ממצא 4). timeout מגביל זמן, לא
זיכרון - ובזמן הזה השרת כולו יכול לקרוס.

לכן התהליך שמפענח קבצים מטיל על *עצמו* תקרה קשיחה, לפני שקרא
בייט אחד של קלט. אחרי זה הוא אינו יכול להסיר אותה.

  Linux/POSIX  setrlimit: RLIMIT_AS (זיכרון), RLIMIT_CPU, RLIMIT_CORE=0
               (אין core dump עם מסמך בזיכרון), RLIMIT_FSIZE=0 (אין
               כתיבת קבצים בכלל - ולכן גם לא קובץ זמני גלוי).
  Windows      Job Object: תקרת זיכרון לתהליך ולג'וב, מספר תהליכים
               פעילים, ו-KILL_ON_JOB_CLOSE - כשהתהליך מת, כל צאצא מת
               איתו.

אם התקרה לא הוטלה - התהליך מסרב לעבוד. אין מצב "בלי הגבלה".

בייצור יש שכבה נוספת מחוץ לתהליך: mem_limit של הקונטיינר
(deploy/docker-compose.yml).
"""

import os
import sys


class LimitsUnavailable(RuntimeError):
    pass


def apply(memory_mb: int, cpu_seconds: int, max_processes: int = 1) -> dict:
    if os.name == "posix":
        return _apply_posix(memory_mb, cpu_seconds)
    if sys.platform == "win32":
        return _apply_windows(memory_mb, cpu_seconds, max_processes)
    raise LimitsUnavailable("אין מנגנון הגבלה לפלטפורמה %s" % sys.platform)


def _apply_posix(memory_mb, cpu_seconds):
    import resource

    mem = memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if resource.getrlimit(resource.RLIMIT_AS)[0] != mem:
        raise LimitsUnavailable("RLIMIT_AS לא הוחל")
    return {"mechanism": "rlimit", "memory_mb": memory_mb, "cpu_seconds": cpu_seconds}


# ----------------------------------------------------------------
#  Windows Job Object (ctypes, בלי תלות חיצונית)
# ----------------------------------------------------------------

_JOB = None   # הידית חייבת לחיות כל עוד התהליך חי - סגירתה הורגת את הג'וב


def _apply_windows(memory_mb, cpu_seconds, max_processes):
    import ctypes
    from ctypes import wintypes

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in
                    ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    LIMIT_PROCESS_TIME = 0x0002
    LIMIT_ACTIVE_PROCESS = 0x0008
    LIMIT_PROCESS_MEMORY = 0x0100
    LIMIT_JOB_MEMORY = 0x0200
    LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x0400
    LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    EXTENDED_LIMIT_INFORMATION = 9

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE

    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise LimitsUnavailable("CreateJobObject נכשל (%d)" % ctypes.get_last_error())

    mem = memory_mb * 1024 * 1024
    info = EXTENDED()
    info.Basic.LimitFlags = (LIMIT_PROCESS_MEMORY | LIMIT_JOB_MEMORY | LIMIT_PROCESS_TIME
                             | LIMIT_ACTIVE_PROCESS | LIMIT_DIE_ON_UNHANDLED_EXCEPTION
                             | LIMIT_KILL_ON_JOB_CLOSE)
    info.Basic.PerProcessUserTimeLimit = cpu_seconds * 10_000_000   # יחידות של 100ns
    info.Basic.ActiveProcessLimit = max_processes
    info.ProcessMemoryLimit = mem
    info.JobMemoryLimit = mem

    if not k32.SetInformationJobObject(wintypes.HANDLE(job), EXTENDED_LIMIT_INFORMATION,
                                       ctypes.byref(info), ctypes.sizeof(info)):
        raise LimitsUnavailable("SetInformationJobObject נכשל (%d)" % ctypes.get_last_error())
    if not k32.AssignProcessToJobObject(wintypes.HANDLE(job),
                                        wintypes.HANDLE(k32.GetCurrentProcess())):
        raise LimitsUnavailable("AssignProcessToJobObject נכשל (%d)" % ctypes.get_last_error())

    global _JOB
    _JOB = job
    return {"mechanism": "job_object", "memory_mb": memory_mb, "cpu_seconds": cpu_seconds}
