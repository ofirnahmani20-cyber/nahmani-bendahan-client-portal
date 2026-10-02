"""
upload_stream.py - קריאת העלאה לזיכרון בלבד.

למה לא UploadFile
------------------
UploadFile של Starlette שומר כל קובץ מעל 1MB בקובץ זמני בדיסק
(SpooledTemporaryFile), עוד לפני שהקוד שלנו רץ. במסמך רפואי זה
אומר עותק גלוי ב-/tmp, ואם התהליך קורס באמצע - עותק שנשאר שם.

כאן גוף הבקשה נקרא כזרם ומפוענח ב-python-multipart (שכבר מותקן)
אל bytearray בזיכרון. אין spool, אין קובץ זמני, והקריאה נעצרת
ברגע שהקובץ חורג מהתקרה - לא אחרי שכולו נקרא.
"""

from python_multipart.multipart import MultipartParser, parse_options_header

# שוליים לכותרות ה-multipart ולשדות שאינם הקובץ.
ENVELOPE_BYTES = 64 * 1024


class UploadError(ValueError):
    """ההעלאה אינה תקינה. ההודעה מיועדת להצגה ללקוח."""


class TooLarge(UploadError):
    pass


async def read_single_file(request, *, field: str, max_bytes: int):
    """
    קורא את חלק ה-multipart בשם field. מחזיר (bytes, filename).

    חלקים אחרים נקראים ונזרקים, אך נספרים מול התקרה הכוללת.
    """
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    if ctype != b"multipart/form-data" or b"boundary" not in params:
        raise UploadError("הבקשה אינה העלאת קובץ.")

    limit = max_bytes + ENVELOPE_BYTES
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        # נדחה לפני קריאת בייט אחד מהגוף.
        raise TooLarge("הקובץ גדול מדי.")

    state = {"headers": {}, "field": b"", "value": b"", "name": None,
             "filename": None, "target": None}
    found = {}
    buf = bytearray()

    def on_part_begin():
        state["headers"] = {}

    def on_header_field(data, start, end):
        state["field"] += data[start:end]

    def on_header_value(data, start, end):
        state["value"] += data[start:end]

    def on_header_end():
        state["headers"][state["field"].lower()] = state["value"]
        state["field"] = state["value"] = b""

    def on_headers_finished():
        _, disp = parse_options_header(state["headers"].get(b"content-disposition", b""))
        name = disp.get(b"name", b"").decode("utf-8", "replace")
        state["target"] = None
        if name == field and "data" not in found:
            state["target"] = buf
            raw = disp.get(b"filename", b"")
            found["filename"] = raw.decode("utf-8", "replace") if raw else None

    def on_part_data(data, start, end):
        if state["target"] is not None:
            state["target"] += data[start:end]
            if len(state["target"]) > max_bytes:
                raise TooLarge("הקובץ גדול מדי.")

    def on_part_end():
        if state["target"] is not None:
            found["data"] = True
        state["target"] = None

    parser = MultipartParser(params[b"boundary"], {
        "on_part_begin": on_part_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
    })

    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise TooLarge("הקובץ גדול מדי.")
        parser.write(chunk)
    parser.finalize()

    if "data" not in found:
        raise UploadError("לא נמצא קובץ בבקשה.")
    return bytes(buf), found.get("filename")
