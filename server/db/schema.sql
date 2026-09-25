-- =============================================================
--  schema.sql - אזור הלקוחות Nahmani ben-dahan
--  PostgreSQL 14+
--
--  מיישם את סעיף 2 ב"06 - מודל נתונים ו-API".
--  ההרצה חוזרת על עצמה: אפשר להריץ שוב ושוב על אותו מסד.
--
--  שני עקרונות שקובעים את כל השאר:
--    1. firm_id בכל טבלה מהיום הראשון. להוסיף אותו אחר כך על
--       נתונים חיים זו מיגרציה כואבת - זו ההחלטה החד-כיוונית.
--    2. מה שחייב להיות נכון נאכף כאן ולא רק בקוד. אילוץ שיושב
--       רק בשרת נשבר ברגע שמישהו כותב סקריפט תחזוקה אחד.
-- =============================================================

BEGIN;

-- gen_random_uuid() הוא חלק מהליבה מ-PG13. בגרסה ישנה יותר
-- יש להפעיל את pgcrypto.
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- -------------------------------------------------------------
--  1. משרד ומשתמשים
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS firms (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text        NOT NULL,
  slug        text        NOT NULL UNIQUE,
  branding    jsonb       NOT NULL DEFAULT '{}'::jsonb,
  timezone    text        NOT NULL DEFAULT 'Asia/Jerusalem',
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- צוות המשרד בלבד. לקוחות נמצאים בטבלה נפרדת לחלוטין:
-- טבלה משותפת עם שדה role היא הדרך הקצרה להסלמת הרשאות בגלל באג אחד.
CREATE TABLE IF NOT EXISTS users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id       uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  full_name     text NOT NULL,
  email         text NOT NULL,
  password_hash text NOT NULL,
  totp_secret   text,
  role          text NOT NULL DEFAULT 'staff'
                CHECK (role IN ('staff', 'admin')),
  title         text,
  phone         text,
  status        text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'disabled')),
  last_login_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (firm_id, email)
);

-- ללקוחות אין סיסמה. הכניסה היא בקוד חד-פעמי.
--
-- על תעודת הזהות: היא גם מזהה כניסה ולכן חייבת להיות ניתנת
-- לחיפוש, מה שמונע hash עם מלח פר-שורה.
--   national_id_lookup - HMAC עם מפתח שרת. ניתן לאינדוקס,
--                        ולא הפיך בלי המפתח.
--   national_id_enc    - מוצפן, לתצוגה בלבד.
-- אין לשמור תעודת זהות כטקסט גלוי בשום מקום.
CREATE TABLE IF NOT EXISTS clients (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id            uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  full_name          text NOT NULL,
  national_id_lookup bytea NOT NULL,
  national_id_enc    bytea NOT NULL,
  phone              text,
  email              text,
  status             text NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'disabled')),
  privacy_accepted_at timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (firm_id, national_id_lookup)
);


-- -------------------------------------------------------------
--  2. סוגי תביעה ומסלולים
--  המסלול הוא נתון ולא קוד: כל סוג תביעה והשלבים שלו.
--  מחליף את הקבוע CLAIM_STAGES שב-data.js.
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS claim_types (
  id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id   uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  name      text NOT NULL,
  code      text NOT NULL,
  is_active boolean NOT NULL DEFAULT true,
  position  integer NOT NULL,
  UNIQUE (firm_id, code),
  -- נדרש כמפתח יעד לאילוץ המורכב שמונע ערבוב מסלולים
  UNIQUE (id, firm_id)
);

CREATE TABLE IF NOT EXISTS stage_templates (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id               uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  claim_type_id         uuid NOT NULL REFERENCES claim_types(id) ON DELETE CASCADE,
  position              integer NOT NULL,
  title                 text NOT NULL,
  description           text,
  -- שלב שבו תיק יכול להסתיים באופן לגיטימי. בלי זה "הסתיים
  -- בשלב 6 בלי ערר" נראה כמו תיק תקוע.
  is_terminal           boolean NOT NULL DEFAULT false,
  typical_duration_days integer,
  UNIQUE (claim_type_id, position),
  UNIQUE (id, claim_type_id)
);

CREATE TABLE IF NOT EXISTS required_document_templates (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id       uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  claim_type_id uuid NOT NULL REFERENCES claim_types(id) ON DELETE CASCADE,
  name          text NOT NULL,
  guidance      text,
  is_required   boolean NOT NULL DEFAULT true,
  position      integer NOT NULL,
  UNIQUE (claim_type_id, position)
);


-- -------------------------------------------------------------
--  3. תיקים
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cases (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id          uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  client_id        uuid NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
  claim_type_id    uuid NOT NULL,
  case_number      text NOT NULL,
  branch           text,
  opened_at        date NOT NULL,
  closed_at        date,
  status           text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'closed_accepted',
                                     'closed_rejected', 'frozen')),
  assigned_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  next_hearing_at  timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (firm_id, case_number),
  -- סוג התביעה חייב להיות של אותו משרד
  FOREIGN KEY (claim_type_id, firm_id) REFERENCES claim_types(id, firm_id),
  -- נדרש כמפתח יעד לאילוץ שמוודא שהשלב שייך למסלול של התיק
  UNIQUE (id, claim_type_id)
);

-- מצב התיק הוא יומן אירועים ולא שדה current_stage.
--   השלב הנוכחי  = האירוע בעל occurred_at המאוחר ביותר.
--   "מאז מתי"    = occurred_at של אותו אירוע. אין שדה לתחזק.
--   חזרה אחורה   = פשוט אירוע נוסף. אין מקרה קצה.
-- הרשומות האלה אינן נמחקות; תיקון נעשה באירוע מבטל.
CREATE TABLE IF NOT EXISTS case_stage_events (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id            uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  case_id            uuid NOT NULL,
  stage_template_id  uuid NOT NULL,
  -- מוחזק כאן כדי שהמסד יוכל לאכוף ששלב שייך למסלול של התיק
  claim_type_id      uuid NOT NULL,
  occurred_at        timestamptz NOT NULL DEFAULT now(),
  note               text,
  created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (case_id, claim_type_id)           REFERENCES cases(id, claim_type_id),
  FOREIGN KEY (stage_template_id, claim_type_id) REFERENCES stage_templates(id, claim_type_id)
);

CREATE INDEX IF NOT EXISTS idx_stage_events_case
  ON case_stage_events (case_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS case_next_steps (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  case_id     uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  title       text NOT NULL,
  description text,
  -- טקסט חופשי במכוון ("צפוי בנובמבר 2026") ולא תאריך.
  -- תאריך מדויק נקרא אצל הלקוח כהתחייבות.
  eta_text    text,
  position    integer NOT NULL,
  UNIQUE (case_id, position)
);

-- החלטת הוועדה. אינה באפיון המקורי - נוספה כשהפיצ'ר נבנה.
-- שמורה בטבלה נפרדת ולא בשדה על cases, כי ייתכן יותר מהחלטה
-- אחת בתיק אחד (ועדה, ואז ועדת עררים).
CREATE TABLE IF NOT EXISTS case_decisions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id            uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  case_id            uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  decided_at         date NOT NULL,
  outcome            text NOT NULL
                     CHECK (outcome IN ('below-threshold', 'grant',
                                        'pension', 'rejected')),
  percent            integer CHECK (percent BETWEEN 0 AND 100),
  is_permanent       boolean NOT NULL DEFAULT false,
  -- מוזן ידנית על ידי המשרד ולא מחושב. מועדי ערר תלויים
  -- בנסיבות ואסור לגזור אותם אוטומטית.
  appeal_deadline    date,
  office_note        text,
  recorded_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now()
);


-- -------------------------------------------------------------
--  4. מסמכים
--  הדרישה למסמך נפרדת מהקובץ: לקוח שדחו לו מסמך מעלה קובץ חדש,
--  ושניהם נשמרים.
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS case_documents (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id            uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  case_id            uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  name               text NOT NULL,
  guidance           text,
  is_required        boolean NOT NULL DEFAULT true,
  -- cancelled: המשרד סגר את הדרישה. השורה נשארת, ולכן ההיסטוריה
  -- ומי שכבר הועלה תחתיה נשמרים. מחיקה הייתה מוחקת גם אותם.
  status             text NOT NULL DEFAULT 'missing'
                     CHECK (status IN ('missing', 'pending_review',
                                       'approved', 'rejected', 'cancelled')),
  reject_reason      text,
  reviewed_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at        timestamptz,
  position           integer NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, position),
  -- דחייה מחייבת סיבה. הלקוח חייב לדעת למה המסמך לא התקבל,
  -- ולכן זה אילוץ במסד ולא בדיקה בקוד שאפשר לעקוף.
  CONSTRAINT reject_needs_reason
    CHECK (status <> 'rejected' OR (reject_reason IS NOT NULL
                                    AND length(btrim(reject_reason)) >= 5))
);

CREATE TABLE IF NOT EXISTS document_files (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id             uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  document_id         uuid NOT NULL REFERENCES case_documents(id) ON DELETE CASCADE,
  -- מפוצל לפי משרד, בלי שם הקובץ המקורי בנתיב:
  -- firms/{firm_id}/cases/{case_id}/{uuid}
  storage_key         text NOT NULL UNIQUE,
  original_filename   text,
  -- מזוהה בשרת לפי תוכן הקובץ. הסיומת וכותרת Content-Type
  -- הן קלט מהמשתמש ואינן ראיה.
  mime_type           text,
  size_bytes          bigint CHECK (size_bytes >= 0),
  checksum            text,
  scan_status         text NOT NULL DEFAULT 'pending'
                      CHECK (scan_status IN ('pending', 'clean',
                                             'infected', 'failed')),
  is_current          boolean NOT NULL DEFAULT true,
  uploaded_at         timestamptz NOT NULL DEFAULT now(),
  uploaded_by_client_id uuid REFERENCES clients(id) ON DELETE SET NULL
);

-- קובץ נגיש להורדה רק כאשר scan_status = 'clean'.
CREATE INDEX IF NOT EXISTS idx_files_document_current
  ON document_files (document_id) WHERE is_current;

-- תגובת הלקוח על מסמך חסר ("אין לי את המסמך" וכדומה).
CREATE TABLE IF NOT EXISTS document_replies (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  document_id uuid NOT NULL REFERENCES case_documents(id) ON DELETE CASCADE,
  kind        text NOT NULL,
  text        text,
  is_read     boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now()
);


-- -------------------------------------------------------------
--  5. הודעות והתראות
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS messages (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id          uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  case_id          uuid NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
  title            text NOT NULL,
  body             text NOT NULL,
  is_important     boolean NOT NULL DEFAULT false,
  sent_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
  sent_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notification_preferences (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id    uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  client_id  uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  event_type text NOT NULL,
  is_enabled boolean NOT NULL DEFAULT true,
  UNIQUE (client_id, event_type)
);

-- יומן שליחה בלבד. הטבלה הזו אינה שומרת את גוף ההודעה ואף
-- לא פרט כלשהו מהתיק - רק את סוג האירוע.
CREATE TABLE IF NOT EXISTS notifications (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id             uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  client_id           uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  case_id             uuid REFERENCES cases(id) ON DELETE SET NULL,
  event_type          text NOT NULL,
  channel             text NOT NULL CHECK (channel IN ('sms', 'email')),
  status              text NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'sent', 'delivered', 'failed')),
  provider_message_id text,
  error               text,
  sent_at             timestamptz,
  delivered_at        timestamptz
);


-- -------------------------------------------------------------
--  6. אימות וביקורת
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS otp_challenges (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id    uuid NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  code_hash    text NOT NULL,
  expires_at   timestamptz NOT NULL,
  attempts     integer NOT NULL DEFAULT 0,
  consumed_at  timestamptz,
  requested_ip inet,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_otp_client_live
  ON otp_challenges (client_id, expires_at DESC) WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS sessions (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id      uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  subject_type text NOT NULL CHECK (subject_type IN ('client', 'user')),
  subject_id   uuid NOT NULL,
  token_hash   text NOT NULL UNIQUE,
  expires_at   timestamptz NOT NULL,
  ip           inet,
  user_agent   text,
  revoked_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- יומן ביקורת. חובה לרשום גם צפייה ולא רק שינוי - נדרש
-- לחובת החיסיון המקצועית.
-- אין UPDATE ואין DELETE על הטבלה הזו.
-- ⚠️ נכון ל-13.09 זו עדיין הסכמה שלא לגעת ולא אילוץ: הקוד כותב
--    בלבד (server/audit.py), אך אין REVOKE ברמת המסד. grants.sql
--    טרם נכתב - ראה "מצב הפרויקט" ב-README.
CREATE TABLE IF NOT EXISTS audit_log (
  id          bigserial PRIMARY KEY,
  firm_id     uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  actor_type  text NOT NULL CHECK (actor_type IN ('client', 'user', 'system')),
  actor_id    uuid,
  action      text NOT NULL,
  entity_type text,
  entity_id   uuid,
  case_id     uuid,
  ip          inet,
  user_agent  text,
  metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log (case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor_type, actor_id, created_at DESC);


-- -------------------------------------------------------------
--  7. תצוגת עזר: השלב הנוכחי של כל תיק
--  מרכזת את "האירוע האחרון" במקום אחד, כדי ששאילתות לא יחזרו
--  על אותה לוגיקה ויסטו זו מזו.
-- -------------------------------------------------------------

CREATE OR REPLACE VIEW case_current_stage AS
SELECT DISTINCT ON (e.case_id)
       e.case_id,
       e.firm_id,
       e.stage_template_id,
       s.position    AS stage_position,
       s.title       AS stage_title,
       s.description AS stage_description,
       s.is_terminal,
       e.occurred_at AS in_stage_since
FROM   case_stage_events e
JOIN   stage_templates  s ON s.id = e.stage_template_id
ORDER  BY e.case_id, e.occurred_at DESC, e.created_at DESC;


-- -------------------------------------------------------------
--  8. משימות ומועדי גג
--  המשרד עובד לפי זמן, והטבלה הזו היא המקום היחיד שיודע מתי.
--  שתי הערות לפני הקוד:
--
--  א. מועד גג משפטי אינו נגזר כאן ולא בשום מקום אחר. הוא מוזן
--     על ידי איש צוות, עם מקור ועם אישור מפורש, ואילוץ
--     legal_deadline_needs_human למטה אוכף זאת גם על insert
--     ישיר במסד. המערכת מנהלת התראות על מועד - היא אינה
--     ממציאה אותו.
--
--  ב. מועד היעד אינו מוצג ללקוח. ראה ההערה על
--     case_next_steps.eta_text: תאריך מדויק שמוצג ללקוח נקרא
--     כהתחייבות, ולכן המועדים כאן משרתים את המשרד בלבד.
-- -------------------------------------------------------------

-- הטבלאות למטה תולות מפתח זר מורכב ב-cases(id, firm_id), כדי
-- שהמסד עצמו ימנע זיווג של משימה לתיק של משרד אחר. האילוץ
-- המתאים לא היה קיים על cases, וכאן הוא נוסף.
-- הקובץ כולו הוא CREATE ... IF NOT EXISTS ולכן אידמפוטנטי;
-- ALTER אינו כזה, ולכן הוא עטוף בבדיקת קטלוג.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'cases_id_firm_key') THEN
    ALTER TABLE cases ADD CONSTRAINT cases_id_firm_key UNIQUE (id, firm_id);
  END IF;
END $$;

-- קטלוג סוגי המשימות, בדפוס required_document_templates: רשימה
-- סגורה במסד ולא קבוע בקוד, כדי שהמשרד יוכל להרחיב בלי פריסה.
CREATE TABLE IF NOT EXISTS task_types (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id    uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  name       text NOT NULL,
  code       text NOT NULL,
  position   integer NOT NULL,
  is_active  boolean NOT NULL DEFAULT true,
  UNIQUE (firm_id, code),
  -- נדרש כמפתח יעד לאילוץ שמוודא שהסוג שייך לאותו משרד
  UNIQUE (id, firm_id)
);

CREATE TABLE IF NOT EXISTS case_tasks (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id              uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  case_id              uuid NOT NULL,
  task_type_id         uuid NOT NULL,
  title                text NOT NULL,
  description          text,
  assignee_user_id     uuid REFERENCES users(id) ON DELETE SET NULL,
  -- timestamptz ולא date, בשונה מ-appeal_deadline: למשימה יש
  -- שעת יעד ("ועדה ב-10:30"), והשעה היא חלק מהמועד.
  due_at               timestamptz NOT NULL,
  -- cancelled: המשימה בוטלה. השורה נשארת, כמו ב-case_documents.
  -- אין DELETE על משימות באף נתיב קוד.
  status               text NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open', 'in_progress',
                                         'waiting_client', 'done',
                                         'cancelled')),
  priority             text NOT NULL DEFAULT 'normal'
                       CHECK (priority IN ('critical', 'high',
                                           'normal', 'low')),
  is_legal_deadline    boolean NOT NULL DEFAULT false,
  deadline_source      text,
  confirmed_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  confirmed_at         timestamptz,
  completed_at         timestamptz,
  completed_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_by_user_id   uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  -- הזוג הגנרי היחיד בסכמה. בכל מקום אחר זמן השינוי נושא שם
  -- דומיין (reviewed_at, decided_at), אבל משימה נערכת בכמה
  -- אופנים שונים והדרישה היא לדעת מי עדכן לאחרונה.
  updated_by_user_id   uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at           timestamptz,
  -- מועד משפטי מחייב אישור אנושי, מקור מתועד, ושם המאשר.
  -- זהו הגב של הכלל: גם insert שמדלג על ה-API אינו יכול
  -- ליצור מועד משפטי שאיש לא אישר.
  CONSTRAINT legal_deadline_needs_human
    CHECK (NOT is_legal_deadline
           OR (deadline_source IS NOT NULL
               AND length(btrim(deadline_source)) >= 5
               AND confirmed_by_user_id IS NOT NULL
               AND confirmed_at IS NOT NULL)),
  -- "הושלמה" מחייב חתימה. בלעדיה אי אפשר לדעת מי סגר מועד גג.
  CONSTRAINT done_needs_completion
    CHECK (status <> 'done'
           OR (completed_at IS NOT NULL
               AND completed_by_user_id IS NOT NULL)),
  FOREIGN KEY (case_id, firm_id)
    REFERENCES cases(id, firm_id) ON DELETE CASCADE,
  FOREIGN KEY (task_type_id, firm_id)
    REFERENCES task_types(id, firm_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_case
  ON case_tasks (case_id, due_at);
-- המסך הראשי שואל תמיד "מה פתוח במשרד", ולכן האינדקס חלקי.
CREATE INDEX IF NOT EXISTS idx_tasks_open
  ON case_tasks (firm_id, due_at) WHERE completed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_assignee
  ON case_tasks (assignee_user_id, due_at) WHERE completed_at IS NULL;


-- -------------------------------------------------------------
--  תצוגת עזר: דחיפות המשימה
--  מדרגות המועד והמיון מוגדרים פה פעם אחת, כדי ששתי נקודות
--  קצה לא יחשבו "קריטי" בשתי דרכים שונות ויסטו זו מזו.
--  המדרגות: עבר · 3-1 ימים · 7-4 · 14-8 · מעבר לכך.
-- -------------------------------------------------------------

CREATE OR REPLACE VIEW case_task_urgency AS
SELECT t.id                            AS task_id,
       t.firm_id,
       t.case_id,
       t.due_at,
       t.status,
       t.priority,
       t.is_legal_deadline,
       -- ימים שלמים עד המועד. שלילי = המועד חלף.
       (t.due_at::date - current_date)  AS days_left,
       t.status IN ('done', 'cancelled')                      AS is_closed,
       t.status NOT IN ('done', 'cancelled') AND t.due_at < now()
                                                              AS is_overdue,
       CASE
         WHEN t.status IN ('done', 'cancelled')    THEN 'closed'
         WHEN t.due_at < now()                     THEN 'overdue'
         WHEN t.due_at::date - current_date <= 3   THEN 'critical'
         WHEN t.due_at::date - current_date <= 7   THEN 'warning'
         WHEN t.due_at::date - current_date <= 14  THEN 'normal'
         ELSE 'later'
       END                             AS bucket,
       -- המיון שאושר: באיחור תחילה, אחריו העדיפות שהצוות קבע,
       -- ובתוך אותה עדיפות המועד הקרוב ראשון.
       CASE WHEN t.status NOT IN ('done', 'cancelled')
                 AND t.due_at < now() THEN 0 ELSE 1 END       AS overdue_rank,
       CASE t.priority WHEN 'critical' THEN 0
                       WHEN 'high'     THEN 1
                       WHEN 'normal'   THEN 2
                       ELSE 3 END                             AS priority_rank
FROM   case_tasks t;


-- -------------------------------------------------------------
--  9. דרישות מהלקוח, משלוח ותקשורת
--  ארבע הערות לפני הקוד:
--
--  א. דרישה ≠ משלוח. "מה הלקוח צריך לעשות" הוא דבר אחד, ו"ניסיון
--     לשלוח לו הודעה בערוץ מסוים" הוא דבר אחר. אותה דרישה יכולה
--     להישלח כמה פעמים ובכמה ערוצים בלי ליצור דרישות כפולות.
--
--  ב. דרישת מסמך אינה טבלה מקבילה. כשסוג הדרישה הוא מסמך היא
--     מצביעה על שורת case_documents הקיימת, וכך הקובץ שהועלה
--     הוא אותו קובץ שבקלסר. אין עותק שני.
--
--  ג. גוף ההודעה אינו נשמר ב-message_deliveries - רק template_key.
--     זהו אותו כלל שכבר כתוב על notifications בסעיף 5.
--
--  ד. אין כאן scheduler. הכללים נשמרים והשליחה ידנית, עד
--     שייבחר ספק ותיבנה הרצה מתוזמנת.
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS case_requirements (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id            uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  case_id            uuid NOT NULL,
  kind               text NOT NULL
                     CHECK (kind IN ('document', 'info', 'signature',
                                     'form', 'contact', 'action', 'other')),
  title              text NOT NULL,
  guidance           text,
  due_at             timestamptz,
  priority           text NOT NULL DEFAULT 'normal'
                     CHECK (priority IN ('critical', 'high', 'normal', 'low')),
  -- cancelled ולא מחיקה, כמו בכל שאר המערכת.
  status             text NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'sent', 'completed',
                                       'cancelled')),
  -- כשהסוג מסמך, זו הדרישה הקיימת ב-case_documents ולא חדשה.
  document_id        uuid REFERENCES case_documents(id) ON DELETE SET NULL,
  completed_at       timestamptz,
  completed_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at         timestamptz,
  -- דרישת מסמך בלי מסמך היא דרישה שאי אפשר למלא.
  CONSTRAINT requirement_needs_document
    CHECK (kind <> 'document' OR document_id IS NOT NULL),
  CONSTRAINT requirement_done_needs_signature
    CHECK (status <> 'completed'
           OR (completed_at IS NOT NULL AND completed_by_user_id IS NOT NULL)),
  FOREIGN KEY (case_id, firm_id)
    REFERENCES cases(id, firm_id) ON DELETE CASCADE,
  UNIQUE (id, firm_id)
);

CREATE INDEX IF NOT EXISTS idx_requirements_case
  ON case_requirements (case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_requirements_open
  ON case_requirements (firm_id, due_at)
  WHERE status IN ('open', 'sent');


CREATE TABLE IF NOT EXISTS reminder_rules (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id          uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  requirement_id   uuid NOT NULL,
  every_days       integer NOT NULL CHECK (every_days BETWEEN 1 AND 90),
  start_at         timestamptz NOT NULL DEFAULT now(),
  end_at           timestamptz,
  channel          text NOT NULL
                   CHECK (channel IN ('sms', 'whatsapp', 'email', 'portal')),
  -- חלון שעות וימים מותרים לשליחה. ימי אי-שליחה (חגים) ייכנסו
  -- בעתיד כשורות בטבלת חריגים ולא כשינוי מבני כאן.
  hours_from       smallint NOT NULL DEFAULT 9
                   CHECK (hours_from BETWEEN 0 AND 23),
  hours_to         smallint NOT NULL DEFAULT 20
                   CHECK (hours_to BETWEEN 0 AND 23),
  weekdays         text NOT NULL DEFAULT '0,1,2,3,4',
  is_paused        boolean NOT NULL DEFAULT false,
  -- הדרישה הושלמה או בוטלה: התזכורות נעצרות. זו אינה בחירה.
  stop_on_complete boolean NOT NULL DEFAULT true,
  created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT reminder_window_is_ordered CHECK (hours_from < hours_to),
  FOREIGN KEY (requirement_id, firm_id)
    REFERENCES case_requirements(id, firm_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reminders_live
  ON reminder_rules (requirement_id) WHERE NOT is_paused;


CREATE TABLE IF NOT EXISTS message_deliveries (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id             uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  requirement_id      uuid,
  -- פולימורפי כמו sessions: notifications מוגבלת ל-client_id
  -- NOT NULL ולכן אינה יכולה לרשום משלוח לאיש צוות.
  subject_type        text NOT NULL CHECK (subject_type IN ('client', 'user')),
  subject_id          uuid NOT NULL,
  channel             text NOT NULL
                      CHECK (channel IN ('sms', 'whatsapp', 'email', 'portal')),
  -- לאן זה באמת נשלח. בלי זה אי אפשר לשחזר משלוח אחרי שהטלפון
  -- של הלקוח השתנה.
  to_address          text,
  -- מפתח תבנית בלבד. גוף ההודעה אינו נשמר כאן, לעולם.
  template_key        text NOT NULL,
  status              text NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued', 'sent', 'delivered',
                                        'failed', 'skipped')),
  provider_message_id text,
  error               text,
  attempts            integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  scheduled_for       timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  sent_at             timestamptz,
  delivered_at        timestamptz,
  created_by_user_id  uuid REFERENCES users(id) ON DELETE SET NULL,
  FOREIGN KEY (requirement_id, firm_id)
    REFERENCES case_requirements(id, firm_id) ON DELETE CASCADE,
  -- מניעת הודעה כפולה: אותה דרישה, אותו ערוץ, אותו מועד מתוכנן -
  -- שורה אחת. זה מה שיאפשר ל-worker עתידי לרוץ שוב בבטחה.
  UNIQUE (requirement_id, channel, scheduled_for)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_requirement
  ON message_deliveries (requirement_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deliveries_pending
  ON message_deliveries (scheduled_for) WHERE status = 'queued';


-- שיחה אגנוסטית לערוץ. תגובה שקשורה לדרישה נושאת requirement_id
-- או document_id; פנייה כללית נושאת case_id בלבד. זו ההפרדה
-- שנדרשה, והיא נאכפת במודל ולא במוסכמה.
CREATE TABLE IF NOT EXISTS case_conversation (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  firm_id        uuid NOT NULL REFERENCES firms(id) ON DELETE RESTRICT,
  case_id        uuid NOT NULL,
  direction      text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  channel        text NOT NULL DEFAULT 'portal'
                 CHECK (channel IN ('sms', 'whatsapp', 'email', 'portal')),
  body           text NOT NULL,
  requirement_id uuid,
  document_id    uuid REFERENCES case_documents(id) ON DELETE SET NULL,
  sent_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  read_at        timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  -- הודעה יוצאת נשלחת בידי איש צוות; נכנסת מגיעה מהלקוח.
  CONSTRAINT outbound_has_a_sender
    CHECK (direction <> 'outbound' OR sent_by_user_id IS NOT NULL),
  FOREIGN KEY (case_id, firm_id)
    REFERENCES cases(id, firm_id) ON DELETE CASCADE,
  FOREIGN KEY (requirement_id, firm_id)
    REFERENCES case_requirements(id, firm_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_conversation_case
  ON case_conversation (case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_unread
  ON case_conversation (case_id) WHERE direction = 'inbound' AND read_at IS NULL;


-- קשר דו-כיווני בין דרישת מסמך לשורת המסמך, כדי שאפשר יהיה
-- להגיע משם לכאן בלי לסרוק. האילוץ נוסף ב-ALTER מוגן כי
-- case_documents כבר קיימת.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_name = 'case_documents'
                    AND column_name = 'requirement_id') THEN
    ALTER TABLE case_documents ADD COLUMN requirement_id uuid;
  END IF;
END $$;

COMMIT;
