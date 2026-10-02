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
