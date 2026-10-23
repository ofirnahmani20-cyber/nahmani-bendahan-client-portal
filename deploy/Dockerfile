# Dockerfile - שרת הפורטל לפריסה ב-Linux.
#
# ⚠️ נכתב ב-2026-10-02 ועדיין לא נבנה ולא הורץ: בסביבת הפיתוח
#    (Windows) אין Docker. לפני שימוש: docker compose build והרצת
#    הבדיקות בתוך הקונטיינר.

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORTAL_STORAGE_DIR=/data/uploads \
    PORTAL_SECRETS_DIR=/run/secrets/nahmani

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# OCR (Layer 2, שלב 3). Tesseract מהמאגר של Debian; המודלים מורדים
# בגרסה מקובעת ונבדקים מול SHA-256 - מודל שהוחלף הוא קוד שרץ על
# מסמכים רפואיים. ראה docs/08 - רישיונות צד שלישי.md.
ENV PORTAL_TESSERACT=/usr/bin/tesseract \
    PORTAL_TESSDATA=/opt/tessdata
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr curl ca-certificates \
 && mkdir -p /opt/tessdata/fast /opt/tessdata/best \
 && cd /opt/tessdata \
 && curl -fsSL -o fast/heb.traineddata https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/heb.traineddata \
 && curl -fsSL -o fast/eng.traineddata https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata \
 && curl -fsSL -o best/heb.traineddata https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/4.1.0/heb.traineddata \
 && curl -fsSL -o best/eng.traineddata https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/4.1.0/eng.traineddata \
 && printf '%s\n' \
    "11f9e43ab227f786352a50f75c94c2e9906f1baba86d93276da19da7ce0904db  fast/heb.traineddata" \
    "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2  fast/eng.traineddata" \
    "dbaa827aea6bc21215638447f17783a1004987c2d0bf5573d111fee397abdae5  best/heb.traineddata" \
    "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba  best/eng.traineddata" \
    | sha256sum -c - \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# רק מה שהשרת מגיש או מריץ. לא docs, לא tests, לא .env.
COPY server/ server/
COPY assets/ assets/
COPY index.html dashboard.html admin.html info.html ./

# משתמש לא-root קבוע, כדי שבעלות על קבצי הסוד במארח תתאים.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin portal \
 && mkdir -p /data/uploads && chown portal:portal /data/uploads
USER 10001

EXPOSE 8777
CMD ["python", "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8777", "--proxy-headers"]
