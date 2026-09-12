/* ==========================================================
   data.js - שכבת הנתונים של אזור הלקוחות
   ----------------------------------------------------------
   בגרסת ההדגמה הנתונים מוגדרים כאן ונשמרים ב-localStorage.
   בעת חיבור לשרת אמיתי יש להחליף את CaseStore.load()
   בקריאת fetch לנקודת קצה מאובטחת (ראה docs/).
   ========================================================== */

/* ---- קטלוג שלבי תביעת ביטוח לאומי ---- */
const CLAIM_STAGES = [
  { id: 1, title: 'פתיחת תיק ואיסוף מסמכים',      desc: 'חתימה על ייפוי כוח, איסוף מסמכים רפואיים ותלושי שכר.' },
  { id: 2, title: 'הגשת התביעה לביטוח לאומי',      desc: 'הגשת טופס התביעה בצירוף כלל האסמכתאות לסניף.' },
  { id: 3, title: 'בדיקת התביעה בסניף',            desc: 'פקיד התביעות בוחן זכאות עקרונית ומבקש השלמות במידת הצורך.' },
  { id: 4, title: 'זימון לוועדה רפואית',           desc: 'קבלת זימון לוועדה רפואית מדרג ראשון והכנה מקדימה.' },
  { id: 5, title: 'ועדה רפואית מדרג ראשון',        desc: 'התייצבות בוועדה, הצגת התיק הרפואי וקביעת אחוזי נכות.' },
  { id: 6, title: 'קבלת החלטת הוועדה',             desc: 'פרוטוקול הוועדה מתקבל ונבחן על ידי המשרד.' },
  { id: 7, title: 'הגשת ערר / ועדת עררים',         desc: 'ככל שנדרש - הגשת ערר מנומק ודיון בוועדה לעררים.' },
  { id: 8, title: 'סיום התיק ותשלום הגמלה',        desc: 'קבלת ההחלטה הסופית, חישוב רטרו ותשלום הגמלה.' }
];

/* ==========================================================
   קטלוג מסמכים לפי סוג תביעה
   ----------------------------------------------------------
   רוב הבקשות חוזרות על עצמן, ולכן הן מנוסחות פעם אחת כאן
   במקום שכל פרליגל ינסח אחרת. ההנחיה (note) היא החלק החשוב -
   היא זו שמונעת מהלקוח להעלות את המסמך הלא נכון.

   ⚠️ התוכן הוא ידע מקצועי של המשרד. זו טיוטה להשלמה.
   ========================================================== */
const REQUIRED_DOC_CATALOG = {
  'נכות כללית': [
    { name: 'צילום תעודת זהות + ספח',          note: 'קריא, כולל הספח המלא',                             required: true },
    { name: 'ייפוי כוח חתום',                   note: 'חתום בפני עורך דין',                                required: true },
    { name: 'טופס ויתור על סודיות רפואית',      note: 'טופס 1811 של המוסד לביטוח לאומי',                   required: true },
    { name: 'סיכומי אשפוז',                     note: 'כל האשפוזים משנת 2023 ואילך',                       required: true },
    { name: 'חוות דעת רפואית עדכנית',           note: 'מרופא מומחה בתחום הרלוונטי, עד 6 חודשים אחורה',     required: true },
    { name: 'תלושי שכר - 12 חודשים אחרונים',    note: 'לחישוב בסיס הגמלה',                                 required: true },
    { name: 'אישורי מחלה (טופס 100)',           note: 'מקופת החולים, לתקופת אי הכושר',                     required: true },
    { name: 'תוצאות בדיקות הדמיה',              note: 'MRI / CT / רנטגן - דיסק או קובץ סרוק',              required: false },
    { name: 'אישור על קצבאות אחרות',            note: 'ככל שמתקבלות קצבאות ממקור אחר',                     required: false },
    { name: 'פרוטוקול ועדה רפואית',             note: 'מתקבל מהמוסד לביטוח לאומי לאחר הוועדה',             required: false }
  ],
  'נכות מעבודה': [
    { name: 'צילום תעודת זהות + ספח',           note: 'קריא, כולל הספח המלא',                              required: true },
    { name: 'ייפוי כוח חתום',                    note: 'חתום בפני עורך דין',                                 required: true },
    { name: 'הודעה על פגיעה בעבודה (ב.ל 250)',  note: 'חתום על ידי המעסיק',                                 required: true },
    { name: 'טופס ויתור על סודיות רפואית',       note: 'טופס 1811 של המוסד לביטוח לאומי',                    required: true },
    { name: 'אישור על תאונת עבודה מהמעסיק',      note: 'כולל תיאור נסיבות הפגיעה ומועדה',                    required: true },
    { name: 'תיעוד חדר מיון',                    note: 'מהפנייה הראשונה לאחר הפגיעה',                        required: true },
    { name: 'פרוטוקול ועדה מדרג ראשון',          note: 'התקבל מהמוסד לביטוח לאומי',                          required: true },
    { name: 'חוות דעת מומחה מטעמנו',             note: 'לצורך הדיון בוועדת העררים',                          required: true },
    { name: 'תיעוד טיפולים פיזיותרפיים',         note: 'מ-2026 ואילך',                                       required: false },
    { name: 'תצהירי עדים לפגיעה',                note: 'ככל שהיו עדים לאירוע',                               required: false }
  ]
};

/* ==========================================================
   מה נדרש כדי לעבור כל שלב, לפי סוג תביעה
   ----------------------------------------------------------
   זהו הבסיס לניתוח הפער הטכני. הוא מכוון להיות דטרמיניסטי
   לחלוטין - בלי מודל ובלי אי-ודאות. כל מפתח (שם מסמך) חייב
   להופיע בקטלוג של אותו סוג תביעה.
   ========================================================== */
const STAGE_DOC_REQUIREMENTS = {
  'נכות כללית': {
    1: ['צילום תעודת זהות + ספח', 'ייפוי כוח חתום', 'טופס ויתור על סודיות רפואית'],
    2: ['תלושי שכר - 12 חודשים אחרונים', 'אישורי מחלה (טופס 100)'],
    3: ['סיכומי אשפוז'],
    4: ['חוות דעת רפואית עדכנית'],
    5: ['חוות דעת רפואית עדכנית', 'סיכומי אשפוז'],
    6: [],
    7: ['פרוטוקול ועדה רפואית'],
    8: []
  },
  'נכות מעבודה': {
    1: ['צילום תעודת זהות + ספח', 'ייפוי כוח חתום', 'הודעה על פגיעה בעבודה (ב.ל 250)'],
    2: ['טופס ויתור על סודיות רפואית', 'אישור על תאונת עבודה מהמעסיק'],
    3: ['תיעוד חדר מיון'],
    4: [],
    5: [],
    6: ['פרוטוקול ועדה מדרג ראשון'],
    7: ['חוות דעת מומחה מטעמנו'],
    8: []
  }
};

/* ---- לקוחות הדגמה ---- */
const DEMO_CLIENTS = {
  '123456782': {
    password: '1234',
    profile: {
      name: 'ישראל ישראלי',
      idNumber: '123456782',
      phone: '050-1234567',
      email: 'israel@example.com'
    },
    caseFile: {
      caseNumber: 'BL-2026-0417',
      claimType: 'נכות כללית',
      openedAt: '2026-03-11',
      branch: 'סניף תל אביב',
      currentStage: 5,
      stageEnteredAt: '2026-08-19',
      nextHearing: '2026-09-22',
      lawyer: {
        name: 'עו"ד אופיר נחמני',
        role: 'שותפה, מחלקת ביטוח לאומי',
        phone: '03-5551234',
        email: 'nahmani@nahmani-bendahan.co.il'
      },
      stageDates: {
        1: '2026-03-11',
        2: '2026-04-02',
        3: '2026-05-20',
        4: '2026-08-19'
      },
      nextSteps: [
        {
          title: 'התייצבות בוועדה רפואית',
          desc: 'הוועדה תתקיים בסניף תל אביב. יש להגיע 20 דקות מראש עם תעודת זהות וכל המסמכים הרפואיים המקוריים.',
          eta: 'ב-22.09.2026, בשעה 10:30'
        },
        {
          title: 'פגישת הכנה עם עורכת הדין',
          desc: 'שיחת הכנה לקראת הוועדה - סקירת התיק הרפואי ותרגול מענה לשאלות הוועדה.',
          eta: 'ייקבע לאחר השלמת המסמכים החסרים'
        },
        {
          title: 'קבלת פרוטוקול הוועדה',
          desc: 'הפרוטוקול מתקבל בדרך כלל תוך 30-45 יום. המשרד יבחן אותו וימליץ אם להגיש ערר.',
          eta: 'צפוי בנובמבר 2026'
        }
      ],
      documents: [
        { id: 'd1', name: 'צילום תעודת זהות + ספח',   note: 'קריא, כולל הספח המלא',                          required: true,  status: 'approved',       file: 'teudat_zehut.pdf', date: '2026-03-11' },
        { id: 'd2', name: 'ייפוי כוח חתום',            note: 'חתום בפני עורך דין',                            required: true,  status: 'approved',       file: 'yipuy_koach.pdf',  date: '2026-03-11' },
        { id: 'd3', name: 'טופס ויתור על סודיות רפואית', note: 'טופס 1811 של המוסד לביטוח לאומי',            required: true,  status: 'approved',       file: 'vitur_sodiyut.pdf',date: '2026-03-14' },
        { id: 'd4', name: 'סיכומי אשפוז',              note: 'כל האשפוזים משנת 2023 ואילך',                   required: true,  status: 'pending-review', file: 'sikum_ishpuz.pdf', date: '2026-08-28' },
        { id: 'd5', name: 'חוות דעת רפואית עדכנית',    note: 'מרופא מומחה בתחום הרלוונטי, עד 6 חודשים אחורה', required: true,  status: 'missing',        file: null, date: null },
        { id: 'd6', name: 'תלושי שכר - 12 חודשים אחרונים', note: 'לחישוב בסיס הגמלה',                        required: true,  status: 'missing',        file: null, date: null },
        { id: 'd7', name: 'אישורי מחלה (טופס 100)',    note: 'מקופת החולים, לתקופת אי הכושר',                 required: true,  status: 'missing',        file: null, date: null },
        { id: 'd8', name: 'תוצאות בדיקות הדמיה',       note: 'MRI / CT / רנטגן - דיסק או קובץ סרוק',          required: false, status: 'missing',        file: null, date: null },
        { id: 'd9', name: 'אישור על קצבאות אחרות',     note: 'ככל שמתקבלות קצבאות ממקור אחר',                 required: false, status: 'missing',        file: null, date: null }
      ],
      messages: [
        { title: 'התקבל זימון לוועדה רפואית', date: '2026-08-19', important: true,
          body: 'התקבל זימון לוועדה רפואית מדרג ראשון ליום 22.09.2026 בשעה 10:30, בסניף תל אביב. נא להשלים את המסמכים החסרים לכל המאוחר עד 15.09.2026.' },
        { title: 'סיכומי האשפוז התקבלו', date: '2026-08-28', important: false,
          body: 'סיכומי האשפוז שהעלית התקבלו ונמצאים בבדיקת המשרד. נעדכן תוך 3 ימי עסקים.' },
        { title: 'התביעה הוגשה לביטוח לאומי', date: '2026-04-02', important: false,
          body: 'התביעה הוגשה לסניף תל אביב וקיבלה מספר אסמכתא. זמן הטיפול הממוצע בשלב זה הוא 60-90 יום.' }
      ]
    }
  },

  '987654321': {
    password: '1234',
    profile: {
      name: 'שרה כהן',
      idNumber: '987654321',
      phone: '052-9876543',
      email: 'sara@example.com'
    },
    caseFile: {
      caseNumber: 'BL-2026-0388',
      claimType: 'נכות מעבודה',
      openedAt: '2026-01-08',
      branch: 'סניף חיפה',
      currentStage: 7,
      stageEnteredAt: '2026-08-05',
      nextHearing: '2026-10-14',
      // החלטת הוועדה. appealDeadline מוזן ידנית על ידי המשרד ולא
      // מחושב על ידי המערכת - מועדי ערר הם קריטיים ותלויים בנסיבות.
      decision: {
        date:           '2026-07-22',
        outcome:        'below-threshold',
        percent:        10,
        permanent:      false,
        appealDeadline: '2026-09-20',
        officeNote:     'הגשנו ערר. אנו טוענים לאחוזים גבוהים משמעותית בהתאם לתיעוד הרפואי.'
      },
      lawyer: {
        name: 'עו"ד בן דהן',
        role: 'ראש מחלקת נפגעי עבודה',
        phone: '03-5551235',
        email: 'bendahan@nahmani-bendahan.co.il'
      },
      stageDates: {
        1: '2026-01-08', 2: '2026-01-29', 3: '2026-03-15',
        4: '2026-05-06', 5: '2026-06-18', 6: '2026-07-22'
      },
      nextSteps: [
        { title: 'דיון בוועדה הרפואית לעררים', desc: 'הוועדה תדון בערר שהוגש על קביעת 10% הנכות. עו"ד בן דהן ילווה אותך לדיון.', eta: 'ב-14.10.2026, בשעה 09:00' },
        { title: 'הגשת חוות דעת נגדית', desc: 'חוות דעת מומחה מטעמנו תוגש לוועדה עד 14 יום לפני מועד הדיון.', eta: 'עד 30.09.2026' },
        { title: 'בחינת פנייה לבית הדין לעבודה', desc: 'ככל שהערר יידחה, נבחן הגשת ערעור לבית הדין האזורי לעבודה.', eta: 'לאחר קבלת החלטת העררים' }
      ],
      documents: [
        { id: 'd1', name: 'צילום תעודת זהות + ספח', note: 'קריא, כולל הספח המלא', required: true, status: 'approved', file: 'id_sara.pdf', date: '2026-01-08' },
        { id: 'd2', name: 'ייפוי כוח חתום', note: 'חתום בפני עורך דין', required: true, status: 'approved', file: 'poa_sara.pdf', date: '2026-01-08' },
        { id: 'd3', name: 'הודעה על פגיעה בעבודה (ב.ל 250)', note: 'חתום על ידי המעסיק', required: true, status: 'approved', file: 'bl250.pdf', date: '2026-01-12' },
        { id: 'd4', name: 'פרוטוקול ועדה מדרג ראשון', note: 'התקבל מהמוסד לביטוח לאומי', required: true, status: 'approved', file: 'protocol_1.pdf', date: '2026-07-22' },
        { id: 'd5', name: 'חוות דעת מומחה מטעמנו', note: 'לצורך הדיון בוועדת העררים', required: true, status: 'missing', file: null, date: null },
        { id: 'd6', name: 'תיעוד טיפולים פיזיותרפיים', note: 'מ-2026 ואילך', required: false, status: 'pending-review', file: 'physio.pdf', date: '2026-08-30' }
      ],
      messages: [
        { title: 'נקבע מועד לוועדת עררים', date: '2026-08-05', important: true,
          body: 'הערר התקבל לדיון. מועד הדיון: 14.10.2026 בשעה 09:00 בסניף חיפה. נא להשלים את חוות הדעת הנגדית בהקדם.' },
        { title: 'הוגש ערר על החלטת הוועדה', date: '2026-08-01', important: false,
          body: 'הוגש ערר מנומק על קביעת 10% נכות בלבד. אנו טוענים לאחוזי נכות גבוהים משמעותית בהתאם לתיעוד הרפואי.' }
      ]
    }
  }
};

/* ==========================================================
   הסבר זכויות אחרי החלטת ועדה
   ----------------------------------------------------------
   🔴 קריטי: התוכן כאן הוא **טיוטה** ואינו תוכן משפטי מאושר.
   הזכויות, הסכומים והמועדים בתביעות ביטוח לאומי תלויים
   בנסיבות אישיות ובספים משתנים, וניסוח שגוי עלול לעלות
   ללקוח בזכות.

   לכן כל ערך כאן נושא `approved`. **כל עוד approved אינו true,
   ההסבר אינו מוצג ללקוח כלל** - במקומו הוא מקבל הודעה לפנות
   למשרד. זו התנהגות ברירת המחדל והיא מכוונת.

   על המשרד לכתוב את התוכן, לאמת אותו, ורק אז לסמן approved.
   ========================================================== */
const RIGHTS_EXPLAINER = {

  /** תוצאה שבה נקבעו אחוזי נכות נמוכים מהסף המזכה */
  'below-threshold': {
    approved: false,
    title: 'נקבעו אחוזים נמוכים מהסף המזכה',
    meaning: 'הוועדה קבעה אחוזי נכות, אך הם נמוכים מהשיעור שמזכה בתשלום.',
    entitlements: [
      'טיוטה - על המשרד למלא את הזכויות הרלוונטיות.'
    ],
    whatNow: [
      'אפשר להגיש ערר על קביעת הוועדה.',
      'עורך הדין שלך יבחן אם התיעוד הרפואי תומך באחוזים גבוהים יותר.'
    ]
  },

  /** תוצאה שמזכה בתשלום חד-פעמי */
  'grant': {
    approved: false,
    title: 'נקבעה זכאות למענק חד-פעמי',
    meaning: 'הוועדה קבעה אחוזי נכות המזכים בתשלום חד-פעמי ולא בקצבה חודשית.',
    entitlements: [
      'טיוטה - על המשרד למלא את הזכויות הרלוונטיות.'
    ],
    whatNow: [
      'אפשר להגיש ערר אם לדעתך האחוזים אינם משקפים את מצבך.'
    ]
  },

  /** תוצאה שמזכה בקצבה חודשית */
  'pension': {
    approved: false,
    title: 'נקבעה זכאות לקצבה חודשית',
    meaning: 'הוועדה קבעה אחוזי נכות המזכים בתשלום חודשי קבוע.',
    entitlements: [
      'טיוטה - על המשרד למלא את הזכויות הרלוונטיות.'
    ],
    whatNow: [
      'כדאי לבדוק מול המשרד אם מגיעות זכויות נוספות מעבר לקצבה.'
    ]
  },

  /** התביעה נדחתה */
  'rejected': {
    approved: false,
    title: 'התביעה נדחתה',
    meaning: 'הוועדה לא הכירה בזכאות בשלב זה.',
    entitlements: [],
    whatNow: [
      'אפשר להגיש ערר על ההחלטה.',
      'עורך הדין שלך יסביר לך מה הסיכוי ומה נדרש.'
    ]
  }
};

/* ---- צוות המשרד (הדגמה בלבד - ראה docs/) ---- */
const DEMO_STAFF = {
  'nahmani':  { password: 'office2026', name: 'עו"ד אופיר נחמני', role: 'שותפה, מחלקת ביטוח לאומי' },
  'bendahan': { password: 'office2026', name: 'עו"ד בן דהן',      role: 'ראש מחלקת נפגעי עבודה' }
};

/* ==========================================================
   שכבת אחסון: מיזוג נתוני הדגמה עם שינויים מקומיים
   ----------------------------------------------------------
   מבנה ה-override לכל לקוח:
     { documents: { <docId>: patch },
       caseInfo:  { currentStage, stageEnteredAt, stageDates },
       messages:  [ עדכונים שהמשרד שלח ] }
   ========================================================== */
const CaseStore = {
  KEY:     'bl_case_overrides_v2',
  LOG_KEY: 'bl_office_log_v1',

  /** טוען את תיק הלקוח כולל כל השינויים שנשמרו מקומית */
  load(idNumber) {
    const base = DEMO_CLIENTS[idNumber];
    if (!base) return null;

    const data = JSON.parse(JSON.stringify(base.caseFile));
    const mine = this._overrides()[idNumber];
    if (!mine) return data;

    // מסמכים שהמשרד דרש מצטרפים לרשימה לפני מיזוג הסטטוסים,
    // כדי שגם הם יוכלו לעבור אישור/דחייה כמו כל מסמך אחר.
    if (mine.addedDocuments && mine.addedDocuments.length) {
      data.documents = data.documents.concat(
        JSON.parse(JSON.stringify(mine.addedDocuments))
      );
    }
    if (mine.documents) {
      data.documents = data.documents.map(doc =>
        mine.documents[doc.id] ? Object.assign({}, doc, mine.documents[doc.id]) : doc
      );
    }
    if (mine.caseInfo) {
      const dates = Object.assign({}, data.stageDates, mine.caseInfo.stageDates);
      Object.assign(data, mine.caseInfo);
      data.stageDates = dates;
    }
    if (mine.messages && mine.messages.length) {
      data.messages = mine.messages.concat(data.messages);
    }
    return data;
  },

  /** סיכום כל התיקים - עבור מסך הניהול */
  list() {
    return Object.keys(DEMO_CLIENTS).map(id => {
      const client = DEMO_CLIENTS[id];
      const file   = this.load(id);
      return {
        idNumber:    id,
        name:        client.profile.name,
        phone:       client.profile.phone,
        caseNumber:  file.caseNumber,
        claimType:   file.claimType,
        branch:      file.branch,
        lawyer:      file.lawyer,
        currentStage: file.currentStage,
        stageTitle:  CLAIM_STAGES[file.currentStage - 1].title,
        nextHearing: file.nextHearing,
        waiting:     file.documents.filter(d => d.status === 'pending-review').length,
        open:        file.documents.filter(d => DOC_NEEDS_UPLOAD(d) && d.required).length,
        unread:      this.clientReplies(id).filter(r => !r.readAt).length,
        blocking:    analyzeGap(file).blocking.length
      };
    });
  },

  /** מעדכן את השלב הנוכחי ומתעד תאריכי השלמה לשלבים שמאחור */
  setStage(idNumber, stage, staffName) {
    const base = DEMO_CLIENTS[idNumber];
    if (!base) return;
    const today = this._today();

    this._patch(idNumber, mine => {
      const info  = mine.caseInfo || (mine.caseInfo = {});
      const dates = info.stageDates || (info.stageDates = {});
      const known = Object.assign({}, base.caseFile.stageDates, dates);

      // שלב שהושלם ואין לו תאריך מתועד - מקבל את תאריך העדכון
      for (let s = 1; s < stage; s++) {
        if (!known[s]) dates[s] = today;
      }
      info.currentStage   = stage;
      info.stageEnteredAt = today;
    });

    this._addLog(idNumber, staffName,
      'עדכן את התיק לשלב ' + stage + ' - ' + CLAIM_STAGES[stage - 1].title);
  },

  /** אישור או דחייה של מסמך שהלקוח העלה */
  reviewDocument(idNumber, docId, decision, reason, staffName) {
    const file = this.load(idNumber);
    const doc  = file && file.documents.find(d => d.id === docId);
    const name = doc ? doc.name : docId;

    this.saveDocument(idNumber, docId, decision === 'approved'
      ? { status: 'approved', rejectReason: null }
      : { status: 'rejected',  rejectReason: reason });

    this._addLog(idNumber, staffName, decision === 'approved'
      ? 'אישר את המסמך "' + name + '"'
      : 'דחה את המסמך "' + name + '" - ' + reason);
  },

  /** רושם את החלטת הוועדה. appealDeadline מוזן על ידי המשרד. */
  saveDecision(idNumber, decision, staffName) {
    this._patch(idNumber, mine => {
      mine.caseInfo = mine.caseInfo || {};
      mine.caseInfo.decision = {
        date:           decision.date,
        outcome:        decision.outcome,
        percent:        decision.percent,
        permanent:      !!decision.permanent,
        appealDeadline: decision.appealDeadline || null,
        officeNote:     decision.officeNote || ''
      };
    });
    this._addLog(idNumber, staffName,
      'רשם את החלטת הוועדה: ' + decision.percent + '% נכות');
  },

  /** דורש מסמך חדש מהלקוח - מופיע אצלו מיד כ"צריך להעלות" */
  addDocument(idNumber, doc, staffName) {
    const id = 'req-' + Date.now().toString(36);
    this._patch(idNumber, mine => {
      const added = mine.addedDocuments || (mine.addedDocuments = []);
      added.push({
        id:       id,
        name:     doc.name,
        note:     doc.note,
        required: !!doc.required,
        status:   'missing',
        file:     null,
        date:     null,
        requestedAt: this._today(),
        requestedBy: staffName
      });
    });
    this._addLog(idNumber, staffName, 'דרש מהלקוח את המסמך "' + doc.name + '"');
    return id;
  },

  /** מבטל דרישת מסמך. אפשר להסיר רק מסמך שהמשרד הוסיף ידנית. */
  removeDocument(idNumber, docId, staffName) {
    let removed = null;
    this._patch(idNumber, mine => {
      const added = mine.addedDocuments || [];
      const i = added.findIndex(d => d.id === docId);
      if (i !== -1) removed = added.splice(i, 1)[0];
    });
    if (removed) {
      this._addLog(idNumber, staffName, 'ביטל את דרישת המסמך "' + removed.name + '"');
    }
    return !!removed;
  },

  /** תגובת לקוח על מסמך - תגובה מובנית ואופציונלית גם טקסט חופשי */
  addClientReply(idNumber, docId, kind, text) {
    const file = this.load(idNumber);
    const doc  = file && file.documents.find(d => d.id === docId);

    this._patch(idNumber, mine => {
      const list = mine.clientReplies || (mine.clientReplies = []);
      list.unshift({
        id:       'rep-' + Date.now().toString(36),
        docId:    docId,
        docName:  doc ? doc.name : '',
        kind:     kind,
        text:     text || '',
        date:     this._today(),
        readAt:   null
      });
    });
  },

  /** כל פניות הלקוח בתיק, החדשות ראשונות */
  clientReplies(idNumber) {
    const mine = this._overrides()[idNumber];
    return (mine && mine.clientReplies) ? mine.clientReplies : [];
  },

  /** מסמן פנייה כנקראה על ידי המשרד */
  markReplyRead(idNumber, replyId) {
    this._patch(idNumber, mine => {
      const reply = (mine.clientReplies || []).find(r => r.id === replyId);
      if (reply && !reply.readAt) reply.readAt = this._today();
    });
  },

  /** שומר סטטוס/קובץ עבור מסמך מסוים */
  saveDocument(idNumber, docId, patch) {
    this._patch(idNumber, mine => {
      const docs = mine.documents || (mine.documents = {});
      docs[docId] = Object.assign({}, docs[docId], patch);
    });
  },

  /** שולח עדכון ללקוח - מופיע בראש "עדכונים מהמשרד" */
  addMessage(idNumber, msg, staffName) {
    this._patch(idNumber, mine => {
      const list = mine.messages || (mine.messages = []);
      list.unshift({
        title:     msg.title,
        body:      msg.body,
        important: !!msg.important,
        date:      this._today(),
        from:      staffName
      });
    });
    this._addLog(idNumber, staffName, 'שלח עדכון ללקוח: "' + msg.title + '"');
  },

  /** יומן פעולות הצוות - מי עשה מה ומתי */
  log() {
    try { return JSON.parse(localStorage.getItem(this.LOG_KEY)) || []; }
    catch (e) { return []; }
  },

  _addLog(idNumber, staffName, text) {
    const client = DEMO_CLIENTS[idNumber];
    const log = this.log();
    log.unshift({
      at:     new Date().toISOString(),
      staff:  staffName || 'לא ידוע',
      client: client ? client.profile.name : idNumber,
      text:   text
    });
    try { localStorage.setItem(this.LOG_KEY, JSON.stringify(log.slice(0, 100))); }
    catch (e) {}
  },

  _patch(idNumber, fn) {
    const all = this._overrides();
    all[idNumber] = all[idNumber] || {};
    fn(all[idNumber]);
    try { localStorage.setItem(this.KEY, JSON.stringify(all)); } catch (e) {}
  },

  _overrides() {
    try { return JSON.parse(localStorage.getItem(this.KEY)) || {}; }
    catch (e) { return {}; }
  },

  _today() {
    return new Date().toISOString().slice(0, 10);
  }
};

/** מסמך שהלקוח עדיין צריך להעלות - חסר, או שנדחה וצריך העלאה מחדש */
function DOC_NEEDS_UPLOAD(doc) {
  return doc.status === 'missing' || doc.status === 'rejected';
}

/* ==========================================================
   ניתוח הפער הטכני - דטרמיניסטי
   ----------------------------------------------------------
   מחזיר מה חוסם את התיק מלהתקדם, מה חסר בהמשך הדרך,
   ומה נדרש אך כבר ממתין לבדיקת המשרד.
   אין כאן מודל ואין הערכה - רק השוואה מול STAGE_DOC_REQUIREMENTS.
   ========================================================== */
function analyzeGap(caseFile) {
  const byType  = STAGE_DOC_REQUIREMENTS[caseFile.claimType] || {};
  const stage   = caseFile.currentStage;
  const byName  = {};
  caseFile.documents.forEach(d => { byName[d.name] = d; });

  /** ממפה שם מסמך למצבו בתיק */
  const resolve = (name) => {
    const doc = byName[name];
    if (!doc)                            return { name, state: 'not-requested', doc: null };
    if (doc.status === 'approved')       return { name, state: 'ready',         doc };
    if (doc.status === 'pending-review') return { name, state: 'in-review',     doc };
    return { name, state: 'outstanding', doc };
  };

  const current = (byType[stage] || []).map(resolve);

  // מה שנדרש בשלבים שעוד לפנינו
  const upcoming = [];
  Object.keys(byType).forEach(key => {
    const s = parseInt(key, 10);
    if (s <= stage) return;
    byType[key].forEach(name => {
      const item = resolve(name);
      if (item.state === 'ready' || item.state === 'in-review') return;
      upcoming.push(Object.assign({ stage: s }, item));
    });
  });
  upcoming.sort((a, b) => a.stage - b.stage);

  const blocking = current.filter(i => i.state === 'outstanding' || i.state === 'not-requested');

  return {
    hasRules:  Object.keys(byType).length > 0,
    stage:     stage,
    blocking:  blocking,                                        // חוסם את השלב הנוכחי
    inReview:  current.filter(i => i.state === 'in-review'),     // נדרש, ואצלנו בבדיקה
    ready:     current.filter(i => i.state === 'ready'),
    upcoming:  upcoming,                                        // ייחסם בהמשך
    canAdvance: blocking.length === 0
  };
}

/* ---- אימות לקוח (הדגמה בלבד - ראה docs/ לגבי מימוש שרת) ---- */
const Auth = {
  KEY: 'bl_session_v1',

  login(idNumber, password) {
    const c = DEMO_CLIENTS[idNumber];
    if (!c || c.password !== password) return false;
    sessionStorage.setItem(this.KEY, JSON.stringify(c.profile));
    return true;
  },

  current() {
    try { return JSON.parse(sessionStorage.getItem(this.KEY)); }
    catch (e) { return null; }
  },

  logout() {
    sessionStorage.removeItem(this.KEY);
    location.href = 'index.html';
  },

  /** מגן על עמוד פנימי - מפנה למסך התחברות אם אין הרשאה */
  requireLogin() {
    const u = this.current();
    if (!u) { location.replace('index.html'); return null; }
    return u;
  }
};

/* ---- אימות צוות המשרד (הדגמה בלבד) ---- */
const StaffAuth = {
  KEY: 'bl_staff_session_v1',

  login(username, password) {
    const s = DEMO_STAFF[String(username).trim().toLowerCase()];
    if (!s || s.password !== password) return false;
    sessionStorage.setItem(this.KEY, JSON.stringify({ name: s.name, role: s.role }));
    return true;
  },

  current() {
    try { return JSON.parse(sessionStorage.getItem(this.KEY)); }
    catch (e) { return null; }
  },

  logout() {
    sessionStorage.removeItem(this.KEY);
    location.reload();
  }
};

/* ==========================================================
   משכי טיפול ממוצעים

   🔴🔴 אזהרה: המספרים כאן הם הערכה בלבד ואינם נתונים מאומתים.
   הם נכתבו כדי שהמסך יעבוד, לפי סדרי גודל כלליים - לא לפי
   סטטיסטיקה של המוסד לביטוח לאומי ולא לפי נתוני המשרד.

   לקוח שרואה "ממוצע 11 חודשים" בונה על זה ציפיות. לכן:
     1. כל ערך נושא verified: false.
     2. כל עוד verified אינו true, המסך מציג סייג גלוי ללקוח
        שמדובר בהערכה כללית ולא בהבטחה לגבי תיקו.
     3. אין להעלות לאוויר עם לקוחות אמיתיים לפני שהמשרד מחליף
        את המספרים ומסמן verified: true.
        רשום כחסם ב-docs/03 - צעדים הבאים ופריסה.
   ========================================================== */
const CLAIM_DURATION_ESTIMATES = {
  'נכות כללית': {
    verified: false,
    typicalMonths: 11,        // מפתיחת התיק ועד החלטה
    rangeMonths: [8, 16],
    note: 'התהליך מתארך כשנדרשת ועדה נוספת או השלמת מסמכים רפואיים.'
  },
  'נכות מעבודה': {
    verified: false,
    typicalMonths: 9,
    rangeMonths: [6, 14],
    note: 'הכרה בפגיעה כתאונת עבודה נבחנת לפני קביעת האחוזים, וזה מוסיף זמן.'
  },
  /* ברירת מחדל לסוג תביעה שאינו ברשימה */
  '_default': {
    verified: false,
    typicalMonths: 10,
    rangeMonths: [6, 18],
    note: 'משך הטיפול משתנה מאוד בין תיק לתיק.'
  }
};

/** מחזיר את ההערכה לסוג התביעה, תמיד עם אובייקט תקין */
function durationEstimate(claimType) {
  return CLAIM_DURATION_ESTIMATES[claimType] || CLAIM_DURATION_ESTIMATES._default;
}

/* ==========================================================
   תוכן הדף הציבורי

   ⚠️ טיוטה. התקצירים כאן נכתבו כשלד למבנה העמוד ואינם תוכן
   משפטי שנבדק. המשרד כותב את התוכן המלא.
   ========================================================== */
const ARTICLES = [
  {
    id: 'a1',
    category: 'ועדה רפואית',
    title: 'מה קורה בוועדה רפואית ואיך מתכוננים אליה',
    summary: 'מי יושב בוועדה, מה היא בודקת, אילו מסמכים כדאי להביא, ומה חשוב לומר ומה לא.',
    minutes: 6
  },
  {
    id: 'a2',
    category: 'ערר',
    title: 'נדחיתם? כך מגישים ערר ומה הסיכוי שיתקבל',
    summary: 'לוחות הזמנים להגשת ערר, מה צריך לכלול בנימוק, ומתי עדיף לפנות לבית הדין לעבודה.',
    minutes: 7
  },
  {
    id: 'a3',
    category: 'זכאות',
    title: 'נכות כללית מול נכות מעבודה - מה ההבדל',
    summary: 'שני מסלולים שונים עם תנאי זכאות שונים, סכומים שונים ומסמכים שונים.',
    minutes: 5
  },
  {
    id: 'a4',
    category: 'מסמכים',
    title: 'אילו מסמכים רפואיים באמת משנים את התוצאה',
    summary: 'לא כל מסמך שווה. מה מחזק תביעה, ומה כדאי לבקש מהרופא לפני שמגישים.',
    minutes: 4
  }
];

const FAQ = [
  {
    q: 'כמה זמן לוקח התהליך?',
    a: 'זה משתנה מאוד לפי סוג התביעה והאם נדרשת ועדה נוספת. באזור האישי שלכם, תחת "מפת הדרכים", אפשר לראות כמה זמן התיק שלכם כבר רץ.'
  },
  {
    q: 'האם אני צריך להגיע לוועדה לבד?',
    a: 'לא. אפשר להגיע עם מלווה, ובמקרים רבים עורך הדין מלווה אתכם. נתאם זאת מראש.'
  },
  {
    q: 'מה קורה אם דחו את התביעה שלי?',
    a: 'דחייה אינה סוף הדרך. יש מועד להגשת ערר, והוא קצר - לכן חשוב לפנות אלינו מיד כשמתקבלת ההחלטה.'
  },
  {
    q: 'למה ביקשו ממני להעלות מסמך שוב?',
    a: 'בדרך כלל בגלל שהצילום לא היה קריא. באזור האישי תראו את הסיבה המדויקת שהמשרד רשם, ליד המסמך.'
  },
  {
    q: 'מי רואה את המסמכים שאני מעלה?',
    a: 'רק הצוות שמטפל בתיק שלכם. המסמכים אינם נשלחים לגורם חיצוני.'
  },
  {
    q: 'כמה עולה הייצוג?',
    a: 'תנאי שכר הטרחה נקבעים מראש ובכתב בפגישת הייעוץ. אין עלויות שלא סוכמו איתכם.'
  }
];
