---
project: אתר לקוחות NAHMANI-BENDAHAN
type: אפיון טכני
status: טיוטה לדיון
created: 2026-09-05
audience: מפתח / חברת פיתוח שתבנה את גרסת הייצור
tags: [אפיון, API, מודל-נתונים, אבטחה]
---

# מודל נתונים ו-API

> נספח טכני ל-[[05 - אפיון מוצר]]. המסמך הזה מגדיר את החוזה; ההנמקה המוצרית נמצאת שם.
> טיוטה — נגזרת מהחלטות שחלקן עדיין פתוחות (סעיף 12 באפיון).

---

## 1. עקרונות שהסכמה מיישמת

| עיקרון | איך זה בא לידי ביטוי |
|---|---|
| **`firm_id` בכל טבלה מהיום הראשון** | ההחלטה החד-כיוונית היחידה. להוסיף אותה אחר כך זו מיגרציה כואבת על נתונים חיים |
| **לקוחות וצוות בטבלאות נפרדות** | מסלולי אימות שונים לחלוטין. טבלה משותפת עם שדה `role` היא הדרך הקצרה להסלמת הרשאות בגלל באג אחד |
| **מסלול השלבים הוא נתון, לא קוד** | לכל סוג תביעה תבנית משלו, נערכת בממשק |
| **מצב התיק הוא יומן אירועים** | `case_stage_events` ולא `current_stage`. נותן היסטוריה ומסלול ביקורת בחינם |
| **הדרישה למסמך נפרדת מהקובץ** | לקוח שדחו לו מסמך מעלה קובץ חדש; שניהם נשמרים |
| **תיק אחד ללקוח זו הנחה שגויה** | לקוח יכול לנהל נכות כללית ונכות מעבודה במקביל |

---

## 2. סכמת נתונים

טיפוסים בכתיב ניטרלי. מפתחות זרים מרומזים משמות השדות.

### 2.1 משרד ומשתמשים

**`firms`** — הכנה ל-SaaS. בגרסה 1 יש שורה אחת.
`id` · `name` · `slug` · `branding` (JSON: לוגו, צבעים, פרטי קשר) · `timezone` · `created_at`

**`users`** — צוות המשרד בלבד.
`id` · `firm_id` · `full_name` · `email` (ייחודי בתוך המשרד) · `password_hash` (argon2id או bcrypt) · `totp_secret` · `role` (`staff` / `admin`) · `title` · `phone` · `status` (`active` / `disabled`) · `last_login_at` · `created_at`

**`clients`** — לקוחות. אין להם סיסמה.
`id` · `firm_id` · `full_name` · `national_id_lookup` · `national_id_enc` · `phone` · `email` · `status` · `privacy_accepted_at` · `created_at`

> **על תעודת הזהות.** היא גם מזהה כניסה, ולכן חייבת להיות ניתנת לחיפוש — מה שמונע hash עם מלח פר-שורה.
> הפתרון: `national_id_lookup` = HMAC עם מפתח שרת (ניתן לחיפוש, לא הפיך בלי המפתח) לאינדוקס ולכניסה, ובנפרד `national_id_enc` מוצפן לצורכי תצוגה. **אין לשמור תעודת זהות כטקסט גלוי.**

### 2.2 סוגי תביעה ומסלולים

**`claim_types`**
`id` · `firm_id` · `name` (למשל "נכות כללית") · `code` · `is_active` · `position`

**`stage_templates`** — שלבי המסלול של סוג תביעה.
`id` · `firm_id` · `claim_type_id` · `position` · `title` · `description` · `is_terminal` · `typical_duration_days`

- `position` ייחודי בתוך `claim_type_id`.
- `is_terminal` מסמן שלב שבו תיק יכול להסתיים באופן לגיטימי — כך "הסתיים בשלב 6 בלי ערר" אינו נראה כתקוע.
- **התוכן נכתב על ידי המשרד**, לא על ידי המפתח.

**`required_document_templates`** — ברירת מחדל של מסמכים לפי סוג תביעה.
`id` · `firm_id` · `claim_type_id` · `name` · `guidance` · `is_required` · `position`

### 2.3 תיקים

**`cases`**
`id` · `firm_id` · `client_id` · `claim_type_id` · `case_number` · `branch` · `opened_at` · `closed_at` · `status` (`active` / `closed_accepted` / `closed_rejected` / `frozen`) · `assigned_user_id` · `next_hearing_at` · `created_at`

**`case_stage_events`** — מחליף את `current_stage`.
`id` · `firm_id` · `case_id` · `stage_template_id` · `occurred_at` · `note` · `created_by_user_id` · `created_at`

- השלב הנוכחי = האירוע בעל `occurred_at` המאוחר ביותר.
- "מאז מתי בשלב" = `occurred_at` של אותו אירוע. אין שדה נפרד לתחזק.
- חזרה אחורה = פשוט אירוע נוסף. אין מקרה קצה.
- **רשומות אלה אינן נמחקות.** תיקון נעשה באירוע מבטל.

**`case_next_steps`** — "מה יקרה בהמשך".
`id` · `firm_id` · `case_id` · `title` · `description` · `eta_text` · `position`

> `eta_text` הוא טקסט חופשי במכוון ("צפוי בנובמבר 2026"), ולא תאריך. תאריך מדויק נקרא כהתחייבות.

### 2.4 מסמכים

**`case_documents`** — הדרישה למסמך.
`id` · `firm_id` · `case_id` · `name` · `guidance` · `is_required` · `status` (`missing` / `pending_review` / `approved` / `rejected`) · `reject_reason` · `reviewed_by_user_id` · `reviewed_at` · `position` · `created_at`

- `reject_reason` חובה כאשר `status = rejected` — נאכף באילוץ ברמת המסד, לא רק בקוד.

**`document_files`** — הקבצים בפועל. יחס אחד-לרבים כדי לשמר היסטוריית העלאות.
`id` · `firm_id` · `document_id` · `storage_key` · `original_filename` · `mime_type` (מזוהה בשרת) · `size_bytes` · `checksum` · `scan_status` (`pending` / `clean` / `infected` / `failed`) · `is_current` · `uploaded_at` · `uploaded_by_client_id`

- **קובץ נגיש להורדה רק כאשר `scan_status = clean`.**
- `storage_key` מפוצל לפי משרד: `firms/{firm_id}/cases/{case_id}/{uuid}` — ללא שם הקובץ המקורי בנתיב.

### 2.5 הודעות והתראות

**`messages`**
`id` · `firm_id` · `case_id` · `title` · `body` · `is_important` · `sent_by_user_id` · `sent_at`

**`notification_preferences`**
`id` · `firm_id` · `client_id` · `event_type` · `is_enabled`
`document_rejected` אינו ניתן לכיבוי (סעיף 6.2 באפיון).

**`notifications`** — יומן שליחה.
`id` · `firm_id` · `client_id` · `case_id` · `event_type` · `channel` · `status` (`queued` / `sent` / `delivered` / `failed`) · `provider_message_id` · `error` · `sent_at` · `delivered_at`

> **הטבלה הזו אינה שומרת את גוף ההודעה או פרט כלשהו מהתיק** — רק את סוג האירוע. עקבי עם כלל הברזל בסעיף 6.1 באפיון.

### 2.6 אימות וביקורת

**`otp_challenges`**
`id` · `client_id` · `code_hash` · `expires_at` · `attempts` · `consumed_at` · `requested_ip` · `created_at`

- הקוד נשמר כ-hash. תפוגה קצרה, מספר ניסיונות מוגבל, וקצב בקשות מוגבל לפי לקוח ולפי כתובת מקור.

**`sessions`**
`id` · `firm_id` · `subject_type` (`client` / `user`) · `subject_id` · `token_hash` · `expires_at` · `ip` · `user_agent` · `revoked_at`

**`audit_log`** — לא ניתן לעדכון ולא למחיקה.
`id` · `firm_id` · `actor_type` · `actor_id` · `action` · `entity_type` · `entity_id` · `case_id` · `ip` · `user_agent` · `metadata` (JSON) · `created_at`

**חובה לרשום גם צפייה ולא רק שינוי** — נדרש לחובת החיסיון (סעיף 8.2 באפיון). לכל הפחות: `case.viewed` · `document.downloaded` · `stage.updated` · `document.reviewed` · `message.sent` · `client.phone_changed` · `auth.login` · `auth.failed`.

---

## 3. חוזה ה-API

**הפרדה ברמת הנתיב.** `/api/client/*` ו-`/api/office/*` הן שתי מערכות שמקרה לא יכול לדלוף ביניהן בגלל בדיקה שנשכחה.

**כלל חוצה:** בכל נקודת קצה בצד הלקוח, זהות הלקוח נלקחת **מההפעלה בלבד**. מזהה שמגיע מהדפדפן משמש לניווט, לעולם לא לקביעת הרשאה. כל שאילתה מסננת גם לפי `firm_id`.

### 3.1 לקוח — אימות

```
POST /api/client/auth/request-otp     { national_id }
  → 200 תמיד, גם אם תעודת הזהות אינה קיימת (מניעת מניית משתמשים)
  → מוגבל בקצב לפי תעודת זהות ולפי IP

POST /api/client/auth/verify-otp      { national_id, code }
  → 200 + עוגיית הפעלה HttpOnly/Secure/SameSite
  → 401 בכשל; מונה הניסיונות עולה

POST /api/client/auth/logout
```

### 3.2 לקוח — נתונים

```
GET  /api/client/me
     → { full_name, phone_masked, firm: { name, branding } }

GET  /api/client/cases
     → רשימה. לקוח יכול לנהל יותר מתיק אחד

GET  /api/client/cases/:caseId
     → { case_number, claim_type, branch, status, opened_at, next_hearing_at,
         current_stage: { position, total, title, description, since },
         stages: [ ... כל המסלול, כל שלב עם done/current/pending + תאריך ],
         next_steps: [...], documents: [...], messages: [...],
         assigned: { name, title, phone, email } }

POST /api/client/documents/:documentId/files      multipart/form-data
     → ולידציה בשרת לפי תוכן הקובץ; מעביר את המסמך ל-pending_review
     → 413 גודל · 415 סוג · 422 המסמך אינו במצב שמאפשר העלאה

GET  /api/client/documents/:documentId/files/:fileId
     → 302 לכתובת חתומה קצרת-מועד; רק כאשר scan_status = clean

GET  /api/client/notification-preferences
PUT  /api/client/notification-preferences
```

### 3.3 משרד — אימות

```
POST /api/office/auth/login       { email, password }   → דורש שלב שני
POST /api/office/auth/verify-2fa  { code }              → עוגיית הפעלה
POST /api/office/auth/logout
```

### 3.4 משרד — עבודה שוטפת

```
GET  /api/office/cases?filter=needs_review|stale|mine&q=
     → שורות עם המונים שממשק הניהול מציג היום:
       pending_review_count, open_required_count, days_since_stage_change

GET  /api/office/cases/:caseId

POST /api/office/cases/:caseId/stage-events
     { stage_template_id, occurred_at?, note? }
     → 422 אם השלב אינו שייך למסלול של סוג התביעה של התיק
     → מפעיל התראה

POST /api/office/documents/:documentId/review
     { decision: "approve" | "reject", reject_reason? }
     → 422 בדחייה בלי סיבה
     → מפעיל התראה

POST   /api/office/cases/:caseId/messages     { title, body, is_important }
GET    /api/office/cases/:caseId/documents
POST   /api/office/cases/:caseId/documents    { name, guidance, is_required }
PATCH  /api/office/documents/:documentId
DELETE /api/office/documents/:documentId
```

### 3.5 משרד — ניהול

```
GET/POST/PATCH  /api/office/clients
POST            /api/office/clients/:id/phone    { phone }
                → פעולה רגישה: נרשמת ביומן ומתריעה למספר הישן (7.2 באפיון)
GET/POST/PATCH  /api/office/cases
GET/POST/PATCH  /api/office/claim-types
GET/POST/PATCH  /api/office/claim-types/:id/stages
GET             /api/office/audit-log?case_id=&user_id=&from=&to=     [admin]
GET/POST/PATCH  /api/office/users                                     [admin]
```

---

## 4. מטריצת הרשאות

| פעולה | לקוח | צוות | מנהל |
|---|---|---|---|
| צפייה בתיק שלו | ✅ | — | — |
| צפייה בתיק של אחר | ❌ **לעולם** | לפי 12.4 | ✅ |
| העלאת מסמך | ✅ לתיק שלו | ❌ | ❌ |
| אישור / דחיית מסמך | ❌ | ✅ | ✅ |
| עדכון שלב | ❌ | ✅ | ✅ |
| שליחת הודעה ללקוח | ❌ | ✅ | ✅ |
| שינוי טלפון של לקוח | ❌ **לעולם** | ✅ | ✅ |
| יצירת לקוח / תיק | ❌ | ✅ | ✅ |
| עריכת סוגי תביעה ומסלולים | ❌ | ❌ | ✅ |
| ניהול משתמשי צוות | ❌ | ❌ | ✅ |
| צפייה ביומן הביקורת | ❌ | ❌ | ✅ |

עמודת "צוות" תלויה בהחלטה 12.4 באפיון. **המלצה: רק תיקים משויכים**, עקבי עם חובת החיסיון.

---

## 5. אחסון קבצים

| דרישה | פירוט |
|---|---|
| מיקום | אחסון אובייקטים, **לא בתוך שורה במסד ולא בתיקיית האתר** |
| דומיין | נפרד מהאפליקציה — מונע הרצת קובץ שהועלה בהקשר האתר |
| גישה | הדלי סגור לחלוטין; הגשה בכתובת חתומה קצרת-מועד בלבד |
| הרשאה | נבדקת **בכל הורדה**, לא רק בהעלאה |
| הצפנה | במנוחה ובתעבורה |
| זיהוי סוג | לפי תוכן הקובץ. הסיומת וכותרת `Content-Type` הן קלט מהמשתמש ואינן ראיה |
| סריקה | לפני שהקובץ זמין להורדה |
| כותרות הגשה | `Content-Disposition: attachment` + `X-Content-Type-Options: nosniff` |
| שמירה | לפי מדיניות השמירה — החלטה 12.6 באפיון |

---

## 6. מיפוי מהקוד הקיים

ה-MVP כבר מרכז את כל הגישה לנתונים בשכבה אחת, ולכן ההחלפה ממוקדת. **גבולות השכבות נכונים — מה שמאחוריהם צריך להשתנות.**

| קיים ב-`assets/js/data.js` | מוחלף ב | הערה |
|---|---|---|
| `Auth.login()` | `POST /api/client/auth/request-otp` + `verify-otp` | מסיסמה ל-OTP — שני שלבים במקום אחד |
| `Auth.current()` / `requireLogin()` | `GET /api/client/me` | מקור האמת עובר לעוגיית ההפעלה |
| `CaseStore.load(id)` | `GET /api/client/cases/:id` | השרת מזהה את הלקוח מההפעלה; `id` לניווט בלבד |
| `CaseStore.list()` | `GET /api/office/cases` | המונים מחושבים בשרת |
| `CaseStore.setStage()` | `POST /api/office/cases/:id/stage-events` | משדה בודד לאירוע |
| `CaseStore.reviewDocument()` | `POST /api/office/documents/:id/review` | האילוץ "דחייה מחייבת סיבה" עובר למסד |
| `CaseStore.addMessage()` | `POST /api/office/cases/:id/messages` | |
| `CaseStore.saveDocument()` | `POST /api/client/documents/:id/files` | היום נשמר שם הקובץ בלבד |
| `CaseStore.log()` | `GET /api/office/audit-log` | מ-`localStorage` ל-audit log בשרת |
| `StaffAuth` | `POST /api/office/auth/*` | + אימות דו-שלבי |
| `CLAIM_STAGES` (קבוע) | `claim_types` + `stage_templates` | **השינוי המבני הגדול** |
| `DEMO_CLIENTS` | `clients` + `cases` | |
| `DOC_NEEDS_UPLOAD()` | נשאר בצד הלקוח | לוגיקת תצוגה טהורה |

**מה שנשאר כפי שהוא:** כל שכבת התצוגה — `dashboard.js`, `admin.js`, `style.css`, `admin.css`. הרינדור, מצבי הסטטוס, הנגישות וזרימות המסך נבדקו ועובדים. יש להחליף את מקור הנתונים, לא את הממשק.

---

## 7. סדר בנייה מוצע

| # | שלב | תוצאה |
|---|---|---|
| 1 | סכמה + `firm_id` + סוגי תביעה ומסלולים | הבסיס. **12.1 חייבת להיות סגורה קודם** |
| 2 | אימות לקוח ב-OTP + הפעלות + יומן ביקורת | מכאן קיים מושג "משתמש אמיתי" |
| 3 | חיבור אזור הלקוחות ל-API לקריאה | הפורטל מציג נתונים אמיתיים |
| 4 | ממשק ניהול: שלבים, הודעות, מסמכים | המשרד מזין לראשונה |
| 5 | העלאת קבצים: אחסון, סריקה, הגשה חתומה | **החלק המסוכן ביותר** — לתת לו זמן |
| 6 | התראות SMS | הערך המלא של המוצר |
| 7 | ביקורת נגישות + הצהרת נגישות + בדיקת חדירה | לפני לקוח אמיתי ראשון |

> אין להעלות לאוויר עם נתוני לקוחות אמיתיים לפני שלב 7.

---

## קשור
[[05 - אפיון מוצר]] · [[00 - סקירת הפרויקט]] · [[03 - צעדים הבאים ופריסה]]
