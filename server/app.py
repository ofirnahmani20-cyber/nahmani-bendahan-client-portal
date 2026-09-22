"""
app.py - שרת אזור הלקוחות Nahmani Ben-Dahan.

אחריות:
  1. הגשת קובצי האתר - ברשימת היתר מפורשת בלבד.
  2. נקודת קצה שמדברת עם Claude, כדי שמפתח ה-API יישאר בשרת.

מצב נוכחי: אין כאן עדיין מסד נתונים ואין אימות בצד שרת. אלה מנה ב'
בתוכנית ההקשחה. עד אז נקודות ה-AI מושבתות כברירת מחדל.

הפעלה:
    pip install -r requirements.txt
    uvicorn server.app:app --host 127.0.0.1 --port 8777
"""

import contextlib
import ipaddress
import json
import os
import pathlib
import time
from collections import deque

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import api_auth, api_client, api_office, audit
from .auth import require_csrf, require_staff
from .db.pool import healthy as db_healthy
from .policy import POLICY_MODE, assert_clean, build_context

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ============================================================
#  מצב ההרצה
# ------------------------------------------------------------
#  demo       - נתוני הדגמה, נוחות פיתוח, ה-AI מותר מ-localhost.
#  production - שער שנכשל סגור. ראה _require_production_ready.
#
#  ברירת המחדל היא demo בכוונה: מי ששכח להגדיר לא מקבל בטעות
#  מערכת שמתנהגת כאילו היא בייצור.
# ============================================================
PORTAL_MODE = os.environ.get("PORTAL_MODE", "demo").strip().lower()
IS_PRODUCTION = PORTAL_MODE == "production"

app = FastAPI(title="Nahmani Ben-Dahan portal")

# ============================================================
#  כותרות אבטחה
# ------------------------------------------------------------
#  CSP מוגדרת כאן ולא ב-meta כדי שתחול גם על תשובות API.
#  'unsafe-inline' לסגנונות נדרש כי הקוד הקיים מציב style
#  ישירות (רוחב סרגל התקדמות למשל); לסקריפטים אין היתר כזה.
# ============================================================

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(self)"
    # מידע רפואי ומשפטי - לא בקאש של הדפדפן או של proxy.
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    # דפי ה-HTML מחזיקים את חותמי הגרסה של ה-CSS וה-JS. בלי
    # הנחיית קאש הדפדפן מגיש דף ישן, הדף מפנה לחותמים ישנים,
    # וקידום החותם לעולם אינו מגיע למשתמש. no-cache מחייב
    # אימות מול השרת בכל טעינה; הנכסים עצמם ממשיכים להיות
    # ניתנים לקאש לנצח, כי החותם מבדיל ביניהם.
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] =             "max-age=31536000; includeSubDomains"
    return response


app.include_router(api_auth.router)
app.include_router(api_client.router)
app.include_router(api_office.router)


# ============================================================
#  ההנחיה למודל
# ------------------------------------------------------------
#  הגבולות כאן אינם קישוט. הכלי משמש עורך דין בתיק אמיתי,
#  ולכן מוטב שיאמר "אין לי מספיק מידע" מאשר שינחש.
# ============================================================
SYSTEM_PROMPT = """אתה עוזר מקצועי לעורך דין ישראלי המתמחה בתביעות ביטוח לאומי.
הפונה אליך הוא עורך דין מוסמך, לא לקוח.

תפקידך:
- לנתח את מצב התיק ולהציע כיווני פעולה להמשך.
- לזהות מה חסר כדי להתקדם בשלב הנוכחי.
- לנסח, לפי בקשה, טיוטת הודעה ללקוח בשפה פשוטה ונגישה.

גבולות מחייבים:
1. הפלט שלך הוא חומר לשיקול דעתו של עורך הדין. הוא אינו ייעוץ משפטי
   ואינו ייעוץ רפואי, ואינו מחליף את שיקול דעתו המקצועי.
2. כשאתה מתייחס לבדיקות או לתיעוד רפואי, נסח זאת כשאלות להעלות מול
   הרופא המטפל או כתיעוד שכדאי לאסוף - לעולם לא כהמלצה קלינית.
3. אל תמציא עובדות. אין להמציא תאריכים, מועדי התיישנות, סעיפי חוק,
   תקנות, אחוזי נכות או ממצאים רפואיים. אם מידע חסר לך - אמור זאת
   במפורש וציין איזה מידע היה עוזר.
4. אתה רואה מטא-דאטה של התיק בלבד: סוג התביעה, השלב, ורשימת שמות
   וסטטוסים של מסמכים. אינך רואה את תוכן המסמכים ואינך רואה פרטים
   מזהים של הלקוח. אל תתייחס ללקוח בשם - הוא אינו ידוע לך.
5. אינך שולח דבר ללקוח. אם ביקשו ממך לנסח הודעה, זו טיוטה בלבד
   שעורך הדין יערוך וישלח בעצמו.

ענה בעברית, בקצרה ולעניין, במבנה מסודר עם כותרות קצרות ורשימות.
כשאתה ממליץ על פעולה, הסבר בקצרה מה הנימוק המקצועי מאחוריה."""


PRESET_QUESTIONS = {
    "next": "מה השלב הבא בתיק הזה, ומה נדרש כדי להגיע אליו?",
    "say": ("מה כדאי לומר ללקוח בשלב הזה? נסח טיוטת הודעה קצרה "
            "בשפה פשוטה, לאדם שאינו משפטן."),
    "medical": ("אילו השלמות תיעוד רפואי חסרות כדי לחזק את התיק בשלב הזה? "
                "נסח אותן כשאלות להעלות מול הרופא המטפל."),
}


class AssistRequest(BaseModel):
    case: dict
    question: str = ""
    preset: str = ""
    history: list = []


def _client():
    """מחזיר לקוח Anthropic, או None אם אין מפתח מוגדר."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    return anthropic.Anthropic()



# ============================================================
#  שער הייצור - נכשל סגור
# ------------------------------------------------------------
#  PORTAL_MODE=production אינו "אותו קוד עם דגל". זהו שער
#  שמסרב לעלות כשחסר לו משהו, במקום ליפול חזרה להתנהגות demo.
#
#  הבדיקה על חשבונות ההדגמה היא הסעיף החשוב כאן: משתמש
#  nahmani@... עם הסיסמה office2026 ששרד לייצור הוא בדיוק
#  התרחיש שהדגל הזה נועד למנוע.
# ============================================================

NEWLINE_BULLET = chr(10) + "  - "

DEMO_EMAIL_MARKERS = ("nahmani@nahmani-bendahan.co.il",
                      "bendahan@nahmani-bendahan.co.il")


def _assert_production_ready():
    problems = []

    for key in ("PORTAL_ID_HMAC_KEY", "PORTAL_ID_ENC_KEY", "DATABASE_URL"):
        if not os.environ.get(key):
            problems.append("משתנה הסביבה %s חסר" % key)

    if not os.environ.get("PORTAL_ALLOWED_ORIGINS"):
        problems.append("PORTAL_ALLOWED_ORIGINS חסר - בדיקת CSRF תחסום הכול")

    try:
        from .db.pool import cursor as _cur
        with _cur() as cur:
            cur.execute("select email from users where lower(email) = any(%s)",
                        (list(DEMO_EMAIL_MARKERS),))
            found = [r["email"] for r in cur.fetchall()]
            if found:
                problems.append(
                    "חשבונות הדגמה פעילים במסד: %s" % ", ".join(found))
    except Exception as exc:
        problems.append("אין חיבור למסד: %s" % exc)

    if problems:
        raise RuntimeError(
            "עלייה במצב production נחסמה:" + NEWLINE_BULLET
            + NEWLINE_BULLET.join(problems)
        )


@contextlib.asynccontextmanager
async def _lifespan(_app):
    """נבדק בעלייה, לפני שהשרת מקבל בקשה ראשונה."""
    if IS_PRODUCTION:
        _assert_production_ready()
    yield


app.router.lifespan_context = _lifespan


# ============================================================
#  שער ה-AI
# ------------------------------------------------------------
#  זו נקודת הקצה היחידה שעולה כסף, והיא הייתה פתוחה לחלוטין.
#
#  במכוון אין כאן "סוד זמני" שה-JavaScript שולח: כל סוד שהדפדפן
#  צריך להחזיק הוא סוד חשוף, וזו בדיוק התקלה שאנחנו סוגרים.
#  במקום זאת שלושה תנאים שאף אחד מהם אינו סוד:
#
#    1. PORTAL_MODE=demo
#    2. הבקשה הגיעה מ-loopback
#    3. Origin/Referer מקומי, אם נשלחו
#
#  ⚠️ זהו היתר פיתוח, לא אימות. במנה ב' הוא מוחלף ב-staff
#     session אמיתי מה-DB, והבלוק הזה יורד.
# ============================================================

AI_DISABLED_MESSAGE = (
    "הניתוח המקצועי מושבת. הוא ייפתח כשאימות הצוות בצד השרת יושלם "
    "(מנה ב' בתוכנית ההקשחה)."
)


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin_is_local(request: Request) -> bool:
    """Origin/Referer זר נדחה. היעדרם מותר - בקשות same-origin רבות
    אינן שולחות Origin, ובדיקת loopback כבר הגבילה את המקור."""
    raw = request.headers.get("origin") or request.headers.get("referer")
    if not raw:
        return True
    from urllib.parse import urlparse
    return _is_loopback(urlparse(raw).hostname)


def require_ai_allowed(request: Request):
    """
    נכשל סגור, ומחייב זהות אמיתית.

    עד מנה ב' זה היה היתר פיתוח מבוסס loopback. עכשיו זה staff
    session מה-DB, בדיוק כמו כל נקודת office אחרת. נשמר גם תנאי
    ה-loopback בפרודקשן, כי הפיצ'ר עדיין לא אושר לייצור.
    """
    if IS_PRODUCTION:
        raise HTTPException(status_code=503, detail=AI_DISABLED_MESSAGE)
    identity = require_staff(request)
    if not _origin_is_local(request):
        raise HTTPException(status_code=403, detail="מקור הבקשה אינו מורשה.")
    return identity


# ---- הגבלת קצב -------------------------------------------------
#  לפי IP במנה א'; יעבור ל-session במנה ב'. בזיכרון בלבד - מספיק
#  לתהליך יחיד, ואינו תלות חדשה.
AI_MAX_CALLS = int(os.environ.get("PORTAL_AI_RATE_LIMIT", "20"))
AI_WINDOW_SECONDS = 300
_ai_calls: dict[str, deque] = {}


def enforce_rate_limit(request: Request) -> None:
    key = (request.client.host if request.client else "unknown")
    now = time.monotonic()
    hits = _ai_calls.setdefault(key, deque())
    while hits and now - hits[0] > AI_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= AI_MAX_CALLS:
        raise HTTPException(
            status_code=429,
            detail="יותר מדי בקשות ניתוח. נסה שוב בעוד מספר דקות.",
        )
    hits.append(now)


@app.get("/api/health")
def health(request: Request):
    """ליבנס בלבד. שם המודל ומצב המדיניות הוסרו - הם מידע על
    המערכת הפנימית ואין לממשק צורך בהם."""
    return {"ok": True, "db": db_healthy()}


@app.post("/api/office/assist/preview")
def assist_preview(req: AssistRequest, request: Request):
    require_ai_allowed(request)
    require_csrf(request)
    """
    מחזיר בדיוק את מה שהיה נשלח לניתוח - בלי לשלוח דבר.

    זו נקודת הביקורת של המשרד: אפשר לפתוח אותה בכל רגע ולראות
    בעיניים איזה מידע יוצא החוצה, במקום להאמין לתיעוד.
    """
    try:
        context = assert_clean(build_context(req.case))
    except ValueError as exc:
        return {"blocked": True, "reason": str(exc)}

    return {
        "blocked": False,
        "policy": POLICY_MODE,
        "question": PRESET_QUESTIONS.get(req.preset) or req.question,
        "context": context,
    }


@app.post("/api/office/assist")
def assist(req: AssistRequest, request: Request):
    identity = require_ai_allowed(request)
    require_csrf(request)
    enforce_rate_limit(request)

    audit.record(identity, "office.ai_invoked", request=request,
                 metadata={"preset": req.preset or "free-text"})

    client = _client()
    if client is None:
        return StreamingResponse(
            iter(["לא הוגדר מפתח API בשרת. יש להגדיר ANTHROPIC_API_KEY ולהפעיל מחדש."]),
            media_type="text/plain; charset=utf-8",
            status_code=503,
        )

    question = PRESET_QUESTIONS.get(req.preset) or req.question.strip()
    if not question:
        return StreamingResponse(
            iter(["לא התקבלה שאלה."]),
            media_type="text/plain; charset=utf-8",
            status_code=400,
        )

    # שכבת המדיניות: מצמצמת, ואז מוודאת שלא נשאר פרט מזהה.
    try:
        context = assert_clean(build_context(req.case))
    except ValueError as exc:
        return StreamingResponse(
            iter(["הבקשה נחסמה על ידי מדיניות הפרטיות: %s" % exc]),
            media_type="text/plain; charset=utf-8",
            status_code=400,
        )

    context_text = json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)

    messages = []
    for turn in req.history[-6:]:            # הקשר קצר, לא כל ההיסטוריה
        role = turn.get("role")
        text = (turn.get("text") or "").strip()
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})

    messages.append({
        "role": "user",
        "content": "נתוני התיק:\n%s\n\nהשאלה: %s" % (context_text, question),
    })

    def stream():
        try:
            with client.messages.stream(
                model="claude-opus-5",
                max_tokens=8000,
                output_config={"effort": "high"},
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # ההנחיה זהה בכל תור - שמירתה במטמון חוסכת עלות
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=messages,
            ) as response:
                for chunk in response.text_stream:
                    yield chunk

                final = response.get_final_message()
                if final.stop_reason == "refusal":
                    yield "\n\n[הבקשה נדחתה על ידי מנגנוני הבטיחות של המודל.]"
        except anthropic.APIStatusError as exc:
            yield "\n\n[שגיאה מהשרת (%s). נסה שוב.]" % exc.status_code
        except anthropic.APIConnectionError:
            yield "\n\n[אין חיבור לשירות הניתוח. בדוק את החיבור לרשת.]"

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


# ============================================================
#  הגשת האתר
# ------------------------------------------------------------
#  עד 13.09 היה כאן StaticFiles על שורש הפרויקט. lookup_path של
#  Starlette חוסם רק directory traversal - הוא אינו מסנן קבצי
#  נקודה ואינו מסנן לפי סיומת. כלומר /.env, /server/policy.py
#  ו-/docs/* היו כולם ניתנים להגשה.
#
#  התיקון אינו "להסתיר" אלא להוציא מהתחום: רק assets/ נמצא
#  ב-static root, ודפי ה-HTML מוגשים מרשימה קשיחה. server/,
#  docs/, scripts/, .git ו-.env אינם בתוך שום תיקייה מוגשת,
#  ולכן אין על מה לסמוך שיסנן אותם.
# ============================================================

app.mount("/assets", StaticFiles(directory=str(ROOT / "assets")), name="assets")

PAGES = {
    "":               "index.html",
    "index.html":     "index.html",
    "dashboard.html": "dashboard.html",
    "admin.html":     "admin.html",
    "info.html":      "info.html",
}


@app.get("/{page:path}")
def serve_page(page: str):
    """מגיש דף מהרשימה הקשיחה בלבד. כל שאר הנתיבים הם 404."""
    name = PAGES.get(page)
    if name is None:
        raise HTTPException(status_code=404)
    return FileResponse(ROOT / name, media_type="text/html; charset=utf-8")
