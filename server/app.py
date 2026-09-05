"""
app.py - שרת מינימלי לאזור הלקוחות NAHMANI-BENDAHAN.

שתי אחריות בלבד:
  1. הגשת האתר הסטטי הקיים (אותו origin, בלי CORS).
  2. נקודת קצה אחת שמדברת עם Claude - כדי שמפתח ה-API יישאר בשרת
     ולא ייחשף ב-JavaScript בצד הלקוח.

זהו שלב ביניים מכוון: אין כאן מסד נתונים ואין אימות בצד שרת.
מסד הנתונים והאימות הם שלב 1 באפיון (ראה 06 - מודל נתונים ו-API).

הפעלה:
    pip install -r requirements.txt
    set ANTHROPIC_API_KEY=...
    uvicorn server.app:app --host 127.0.0.1 --port 8777
"""

import json
import os
import pathlib

import anthropic
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .policy import POLICY_MODE, assert_clean, build_context

ROOT = pathlib.Path(__file__).resolve().parent.parent

app = FastAPI(title="NAHMANI-BENDAHAN portal")


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


@app.get("/api/health")
def health():
    """מאפשר לממשק לדעת מראש אם הניתוח זמין, במקום להיכשל באמצע."""
    return {
        "ready": _client() is not None,
        "policy": POLICY_MODE,
        "model": "claude-opus-5",
    }


@app.post("/api/office/assist/preview")
def assist_preview(req: AssistRequest):
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
def assist(req: AssistRequest):
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


# האתר הסטטי נרשם אחרון, כדי שלא יבלע את נתיבי ה-API.
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="site")
