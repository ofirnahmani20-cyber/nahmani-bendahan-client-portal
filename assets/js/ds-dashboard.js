/* ==========================================================
   DS Dashboard - מרכז הפיקוד של עורך הדין
   ----------------------------------------------------------
   המסך עונה על שאלה אחת: מה דורש את תשומת לבי עכשיו.
   הוא אינו לוח מחוונים ואינו אוסף מונים.

   שלוש קריאות בלבד, כולן קיימות וכולן ברמת המשרד:
     Api.officeTasks()  משימות, מועדים משפטיים ומונים
     Api.officeCases()  מסמכים לבדיקה וממתין ללקוח
     Api.auditLog(null) פעילות המערכת

   שני כללים שהמודול מקפיד עליהם:

   1. הדחיפות מגיעה מהשרת. bucket, daysLeft ו-isOverdue
      מחושבים ב-case_task_urgency, והסדר מגיע מ-TASK_ORDER.
      כאן מסננים ומציגים - לא ממיינים מחדש ולא מחשבים דחיפות.

   2. מה שאין לו מקור אמיתי אינו מוצג. אין כאן נתון מומצא,
      אין אומדן ואין AI. הפערים נאמרים במפורש למשתמש.

   התלויות מוזרקות ב-init ולא נלקחות מה-global, בדפוס של
   viewnav.js. כך admin.js אינו צריך לחשוף את פנימיותו.
   ========================================================== */

window.DsDashboard = (function () {
  'use strict';

  var d = null;                 /* התלויות שהוזרקו */
  var caseIndex = [];           /* רשימת התיקים, לשורת הפקודה */

  /* כמה פריטים מוצגים לפני "ועוד N". חמישה הוא התקדים
     שנקבע ב"דורש תשומת לב" שבתוך תיק: מעבר לכך המסך כבר
     אינו נקרא בשניות. */
  var CAP = 5;

  function $(id) { return document.getElementById(id); }

  function init(deps) { d = deps; wireCommandBar(); }


  /* ==========================================================
     טעינה
     ========================================================== */

  function load() {
    if (!d) return Promise.resolve();

    paintGreeting();

    return Promise.all([
      d.Api.officeTasks().catch(function () { return null; }),
      d.Api.officeCases().catch(function () { return null; }),
      d.Api.auditLog(null).catch(function () { return null; })
    ]).then(function (res) {
      var tasks = res[0], cases = res[1], log = res[2];

      caseIndex = (cases && cases.cases) || [];

      paintAttention(tasks, cases);
      paintToday(tasks);
      paintActivity(log);
      paintStats(tasks, cases);
      paintContext(tasks, cases);
      paintGaps();
    });
  }


  /* ==========================================================
     א. ברכה
     ========================================================== */

  var DAYS = ['ראשון', 'שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת'];
  var MONTHS = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי',
                'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר'];

  function paintGreeting() {
    var now = new Date();
    $('dashDate').textContent =
      'יום ' + DAYS[now.getDay()] + ' · ' +
      now.getDate() + ' ב' + MONTHS[now.getMonth()] + ' ' + now.getFullYear();

    /* השם המלא נושא תואר ושם משפחה. בברכה מספיק השם הפרטי,
       והוא נלקח מהשרת ולא מנוסח כאן. staffInfo היא פונקציה
       ולא ערך, כי הזהות מגיעה אחרי ה-init. */
    var who = d.staffInfo ? d.staffInfo() : null;
    var full = (who && who.name) || '';
    var parts = full.replace(/^עו"ד\s+/, '').split(' ');
    $('dashGreeting').textContent = parts[0] ? 'שלום, ' + parts[0] : 'שלום';
  }

  function paintContext(tasks, cases) {
    var box = $('dashContext');
    if (!tasks && !cases) {
      box.textContent = 'לא הצלחנו לטעון את תמונת היום. אפשר לרענן את הדף.';
      return;
    }
    var c = (tasks && tasks.counts) || {};
    var bits = [];
    if (c.overdue) bits.push(c.overdue === 1 ? 'משימה אחת באיחור'
                                             : c.overdue + ' משימות באיחור');
    if (c.today)   bits.push(c.today === 1 ? 'אחת למועד היום'
                                           : c.today + ' למועד היום');

    box.textContent = bits.length
      ? bits.join(' · ') + '. להלן מה שדורש החלטה שלך.'
      : 'אין משימות באיחור ואין מועד להיום. להלן מה שעדיין פתוח.';
  }


  /* ==========================================================
     ב. דורש את תשומת לבי
     ----------------------------------------------------------
     כל שורה אומרת: מה קרה · באיזה תיק · למה זה דורש תשומת לב ·
     מה הפעולה. שורה שאינה יכולה לומר את ארבעתם אינה נכנסת.

     הדירוג: סיכון קודם לתשומת לב, ותשומת לב למידע. אדום שמור
     למועד שחלף בלבד - לא לכל דבר שדורש טיפול.
     ========================================================== */

  function paintAttention(tasks, cases) {
    var list = $('dashAttnList');
    var state = $('dashAttnState');
    var more = $('dashAttnMore');
    list.textContent = '';
    more.textContent = '';

    if (!tasks && !cases) {
      state.hidden = false;
      state.className = 'ds-error';
      state.textContent = 'לא הצלחנו לטעון את הנתונים.';
      return;
    }

    var items = collectAttention(tasks, cases);

    $('dashAttnCount').textContent = items.length
      ? (items.length === 1 ? 'פריט אחד' : items.length + ' פריטים')
      : '';

    if (!items.length) {
      state.hidden = false;
      state.className = 'ds-empty';
      state.textContent =
        'אין כרגע דבר שדורש את תשומת לבך. כל המועדים והמסמכים מטופלים.';
      return;
    }
    state.hidden = true;

    items.slice(0, CAP).forEach(function (it, i) { list.appendChild(row(it, i + 1)); });

    if (items.length > CAP) {
      var rest = items.length - CAP;
      var btn = d.el('button', 'ds-btn ds-btn-quiet',
                     'הצגת ' + rest + ' נוספים');
      btn.type = 'button';
      btn.addEventListener('click', function () {
        items.slice(CAP).forEach(function (it, i) { list.appendChild(row(it, CAP + i + 1)); });
        more.textContent = '';
        d.announce('נוספו ' + rest + ' פריטים לרשימה.');
      });
      more.appendChild(btn);
    }
  }

  /** אוסף את הפריטים משני המקורות ומדרג אותם. */
  function collectAttention(tasks, cases) {
    var out = [];

    /* --- משימות ומועדים משפטיים ---
       ארבע רמות, ולא שתיים. "באיחור" ו"קריטי" אינם אותו דבר:
       באיחור פירושו שהמועד כבר חלף, וקריטי פירושו שהוא מתקרב.
       המועד המשפטי הוא הרמה העליונה, כי הוא היחיד שאי אפשר
       לתקן בדיעבד.

       הרמה משפיעה על עובי הקו, על הסימן, על משקל המונה ועל
       צבע הכותרת - ולא רק על הצבע. */
    ((tasks && tasks.tasks) || []).forEach(function (t) {
      var urgent = t.isOverdue || t.bucket === 'critical';
      if (!urgent) return;

      var legal = t.isLegalDeadline;
      var level, badge, mark;

      if (t.isOverdue && legal) { level = 1; badge = 'מועד משפטי באיחור'; mark = '✗'; }
      else if (t.isOverdue)     { level = 1; badge = 'באיחור';            mark = '✗'; }
      else if (legal)           { level = 2; badge = 'מועד משפטי קרוב';   mark = '§'; }
      else                      { level = 2; badge = 'דחוף';              mark = '!'; }

      out.push({
        /* מועד משפטי קודם למשימה רגילה באותה רמה */
        rank:  (t.isOverdue ? 0 : 2) + (legal ? 0 : 1),
        tone:  'u' + level,
        badge: badge,
        mark:  mark,
        title: t.title,
        why:   d.timeLeft(t) + (legal ? ' · מועד משפטי מחייב' : ''),
        who:   t.clientName,
        num:   t.caseNumber,
        go:    'פתיחת התיק',
        caseId: t.caseId,
        tab:   'tasks'
      });
    });

    /* --- מסמכים שממתינים לבדיקה שלי ---
       ברמת המשרד יש מונה לכל תיק, ולא רשימת מסמכים. לכן
       השורה מדברת במונה ואינה מתיימרת לנקוב בשם המסמך. */
    ((cases && cases.cases) || []).forEach(function (c) {
      if (c.awaitingReview > 0) {
        out.push({
          rank: 4, tone: 'u3', mark: '●',
          badge: 'ממתין לבדיקה',
          title: c.awaitingReview === 1
            ? 'מסמך אחד ממתין לבדיקה שלך'
            : c.awaitingReview + ' מסמכים ממתינים לבדיקה שלך',
          why: 'הלקוח העלה, והמסמך אינו מאושר עד שתבדוק אותו.',
          who: c.clientName, num: c.caseNumber,
          go: 'מעבר למסמכי התיק',
          caseId: c.id, tab: 'docs'
        });
      }
    });

    /* --- מסמכים שהלקוח עדיין לא השלים --- */
    ((cases && cases.cases) || []).forEach(function (c) {
      if (c.openForClient > 0) {
        out.push({
          rank: 5, tone: 'u4', mark: '○',
          badge: 'ממתין ללקוח',
          title: c.openForClient === 1
            ? 'מסמך אחד חסר מהלקוח'
            : c.openForClient + ' מסמכים חסרים מהלקוח',
          why: 'המסמכים התבקשו ועדיין לא הגיעו. אפשר לשלוח תזכורת.',
          who: c.clientName, num: c.caseNumber,
          go: 'מעבר לדרישות ותקשורת',
          caseId: c.id, tab: 'comms'
        });
      }
    });

    /* יציב: הדירוג בלבד. בתוך אותו דירוג נשמר סדר השרת. */
    return stableSort(out, function (a, b) { return a.rank - b.rank; });
  }

  /** Array.prototype.sort אינו יציב בכל מנוע ES5. */
  function stableSort(arr, cmp) {
    return arr.map(function (v, i) { return { v: v, i: i }; })
      .sort(function (a, b) { return cmp(a.v, b.v) || (a.i - b.i); })
      .map(function (x) { return x.v; });
  }

  /** שורה בפיד.

      המבנה הוא מונה תלוי בעמודה משלו ולידו קו אנכי דק, ואז
      כותרת, נימוק ושורת מטא. אין כאן תיבה, מסגרת או מילוי -
      ההיררכיה כולה טיפוגרפית.

      כפתור, כי השורה מבצעת פעולה. */
  function row(it, n) {
    var li = d.el('li');
    var btn = d.el('button', 'ds-row ds-row-' + it.tone);
    btn.type = 'button';

    /* המונה דקורטיבי: הוא מיקום ברשימה, לא מידע. המשמעות
       יושבת בתג שלצד שורת המטא.

       השורות הדחופות מקבלות גם נקודה קטנה מעל המונה, כדי
       שאפשר יהיה לסרוק אותן בעין אחת. היא בתוך ה-aria-hidden
       ולכן היא חיזוק בלבד - הצבע לעולם אינו הערוץ היחיד. */
    var enu = d.el('span', 'ds-enum', null, true);
    if (it.tone !== 'u4') enu.appendChild(d.el('span', 'ds-enum-mark'));
    enu.appendChild(d.el('span', 'ds-enum-n', n < 10 ? '0' + n : String(n)));
    enu.appendChild(d.el('span', 'ds-enum-rule'));
    btn.appendChild(enu);

    btn.appendChild(d.el('p', 'ds-row-title', it.title));
    btn.appendChild(d.el('p', 'ds-row-why', it.why));

    var foot = d.el('div', 'ds-row-foot');
    foot.appendChild(badge(it.tone, it.mark, it.badge));

    var meta = d.el('p', 'ds-meta');
    if (it.who) meta.appendChild(d.el('span', null, it.who));
    if (it.num) meta.appendChild(d.el('span', 'ds-num', it.num));
    foot.appendChild(meta);

    foot.appendChild(d.el('p', 'ds-row-go', it.go + ' ←'));
    btn.appendChild(foot);

    btn.addEventListener('click', function () { d.openCase(it.caseId, it.tab); });
    li.appendChild(btn);
    return li;
  }

  /** הסמל דקורטיבי; המילים נושאות את המשמעות. */
  function badge(tone, mark, text) {
    var b = d.el('span', 'ds-badge ds-badge-' + tone);
    b.appendChild(d.el('span', null, mark, true));
    b.appendChild(d.el('span', null, text));
    return b;
  }


  /* ==========================================================
     ג. סדר היום שלי
     ========================================================== */

  function paintToday(tasks) {
    var list = $('dashTodayList');
    var state = $('dashTodayState');
    list.textContent = '';

    if (!tasks) {
      state.hidden = false;
      state.className = 'ds-error';
      state.textContent = 'לא הצלחנו לטעון את המשימות.';
      return;
    }

    /* בדיוק אותו תנאי של השבב "להיום" בניהול משימות. */
    var today = (tasks.tasks || []).filter(function (t) {
      return t.bucket !== 'closed' && t.daysLeft === 0;
    });

    $('dashTodayCount').textContent = today.length
      ? (today.length === 1 ? 'פריט אחד' : today.length + ' פריטים') : '';

    if (!today.length) {
      state.hidden = false;
      state.className = 'ds-empty';
      state.textContent = 'אין דבר שמועדו היום.';
      return;
    }
    state.hidden = true;

    today.forEach(function (t, i) {
      list.appendChild(row({
        tone: t.isOverdue ? 'u1' : (t.isLegalDeadline ? 'u2' : 'u3'),
        mark: t.isOverdue ? '✗' : (t.isLegalDeadline ? '§' : '●'),
        badge: t.isLegalDeadline ? 'מועד משפטי' : 'להיום',
        title: t.title,
        why: d.timeLeft(t) + (t.assignee ? ' · באחריות ' + t.assignee : ''),
        who: t.clientName, num: t.caseNumber,
        go: 'פתיחת התיק',
        caseId: t.caseId, tab: 'tasks'
      }, i + 1));
    });
  }


  /* ==========================================================
     ד. מה קרה במערכת
     ----------------------------------------------------------
     שימוש חוזר ב-describe של admin.js, ולכן אין כאן אף
     מחרוזת תיאור חדשה ואף מפתח טכני לא יגיע למסך.
     ========================================================== */

  /* אירועי גישה ואבטחה. הם אמיתיים וחשובים, אבל הם אינם
     "מה קרה בתיק" - וכשהם מעורבים בפיד הראשי הם מציפים אותו
     ומאבדים לו את המשמעות. לכן הם יורדים לחלון צר משלהם.

     צפייה בתיק נרשמת בכל פתיחה, ולכן היא מוצאת מהשניים. */
  var ACCESS = {
    'staff.login_success': true, 'staff.login_failed': true,
    'client.login_success': true, 'client.login_failed': true,
    'client.otp_requested': true
  };
  var NOISE = {
    'office.case_viewed': true, 'client.case_viewed': true
  };

  function paintActivity(log) {
    var list = $('dashActList');
    var state = $('dashActState');
    list.textContent = '';

    if (!log) {
      state.hidden = false;
      state.className = 'ds-error';
      state.textContent = 'לא הצלחנו לטעון את הפעילות.';
      paintAccess(null);
      return;
    }

    var all = log.entries || [];

    var rows = all.filter(function (e) {
      return !NOISE[e.action] && !ACCESS[e.action];
    }).slice(0, 14);

    if (!rows.length) {
      state.hidden = false;
      state.className = 'ds-empty';
      state.textContent = 'עוד לא בוצעה פעולה בתיקים.';
    } else {
      state.hidden = true;
      var day = null;
      rows.forEach(function (e) {
        var label = d.dayLabel(e.at);
        if (label !== day) {
          day = label;
          list.appendChild(d.el('li', 'ds-act-day', label));
        }
        var li = d.el('li', 'ds-act-row');
        li.appendChild(d.el('span', 'ds-act-time', d.clockOf(e.at)));
        li.appendChild(d.el('span', 'ds-act-what', d.describe(e)));
        /* שם הלקוח הוא ההקשר החשוב: "הלקוח העלה מסמך" בלי
           לומר איזה לקוח אינו אומר דבר. actor הוא איש הצוות,
           והוא ריק בפעולת לקוח. */
        li.appendChild(d.el('span', 'ds-act-who', e.client || e.actor || ''));
        list.appendChild(li);
      });
    }

    paintAccess(all);
  }

  /** החלון הצר: כניסות וקודי אימות בלבד. */
  function paintAccess(all) {
    var list = $('dashSysList');
    var state = $('dashSysState');
    if (!list) return;
    list.textContent = '';

    if (!all) {
      state.hidden = false;
      state.textContent = 'לא נטען.';
      return;
    }

    var rows = all.filter(function (e) { return ACCESS[e.action]; }).slice(0, 8);

    if (!rows.length) {
      state.hidden = false;
      state.textContent = 'אין אירועי גישה.';
      return;
    }
    state.hidden = true;

    rows.forEach(function (e) {
      var li = d.el('li', 'ds-sys-row');
      li.appendChild(d.el('span', 'ds-sys-time',
        d.dayLabel(e.at) + ' · ' + d.clockOf(e.at)));
      li.appendChild(d.el('span', 'ds-sys-what', d.describe(e)));
      list.appendChild(li);
    });
  }


  /* ==========================================================
     ה. תמונת מצב
     ----------------------------------------------------------
     משנית בכוונה. ארבעה מספרים שיש להם מקור, ולא יותר.
     ========================================================== */

  function paintStats(tasks, cases) {
    var box = $('dashStats');
    box.textContent = '';

    var c = (tasks && tasks.counts) || {};
    var rows = (cases && cases.cases) || [];

    var review = 0, waiting = 0;
    rows.forEach(function (r) {
      review += r.awaitingReview || 0;
      waiting += r.openForClient || 0;
    });

    var stats = [
      ['תיקים במערכת', rows.length],
      ['מסמכים לבדיקה שלי', review],
      ['מסמכים שממתינים ללקוח', waiting],
      ['משימות השבוע', c.week || 0]
    ];

    stats.forEach(function (s) {
      var wrap = d.el('div', 'ds-stat');
      wrap.appendChild(d.el('dt', null, s[0]));
      wrap.appendChild(d.el('dd', null, String(s[1])));
      box.appendChild(wrap);
    });
  }

  /** נאמר במפורש, כדי שלא ייווצר רושם שהפיד מציג הכול. */
  function paintGaps() {
    $('dashGaps').textContent =
      'המסך מציג את מה שיש לו מקור נתונים ברמת המשרד. הודעות ' +
      'שלא נקראו, דרישות פתוחות, דיונים קרובים ותיקים שלא ' +
      'התקדמו נשמרים כיום ברמת התיק בלבד, ולכן אינם מופיעים כאן.';
  }


  /* ==========================================================
     שורת הפקודה
     ----------------------------------------------------------
     מסננת את רשימת התיקים שכבר נטענה, ומנווטת לשמונת האזורים.
     אין כאן קריאה חדשה לשרת, אין AI, ואין פעולה משפטית.
     כשאין תוצאה - נאמר שאין, ולא מוצג דבר שאינו קיים.
     ========================================================== */

  var AREAS = [
    ['dashboard', 'דשבורד'], ['tasks', 'ניהול משימות'],
    ['cases', 'התיקים בטיפולי'], ['closed', 'תיקים שהושלמו ושכר טרחה'],
    ['documents', 'מסמכים'], ['clients', 'לקוחות'],
    ['reports', 'דוחות'], ['settings', 'הגדרות']
  ];

  var hits = [];      /* התוצאות המוצגות */
  var cursor = -1;    /* הפריט המסומן במקלדת */

  function wireCommandBar() {
    var input = $('cmdInput');
    var panel = $('cmdPanel');
    if (!input || !panel) return;

    input.addEventListener('input', function () { search(input.value); });
    input.addEventListener('focus', function () { search(input.value); });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { close(); input.blur(); return; }
      if (!hits.length) return;

      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        run(hits[cursor < 0 ? 0 : cursor]);
      }
    });

    /* לחיצה מחוץ לשורה סוגרת. blur לבדו היה סוגר לפני
       שהלחיצה על תוצאה הספיקה להירשם. */
    document.addEventListener('click', function (e) {
      if (!panel.hidden && !panel.contains(e.target) && e.target !== input) close();
    });

    /* קיצור המקלדת. לא נתפס בזמן הקלדה בשדה אחר. */
    document.addEventListener('keydown', function (e) {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
      var t = e.target;
      var tag = t && t.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
          || (t && t.isContentEditable)) return;
      e.preventDefault();
      input.focus();
      input.select();
    });
  }

  function search(q) {
    q = String(q || '').trim().toLowerCase();
    hits = [];
    cursor = -1;

    if (q) {
      caseIndex.forEach(function (c) {
        var hay = [c.clientName, c.caseNumber, c.claimType]
                  .join(' ').toLowerCase();
        if (hay.indexOf(q) !== -1) {
          hits.push({
            kind: 'case', label: c.clientName,
            sub: c.caseNumber + ' · ' + (c.claimType || ''),
            caseId: c.id
          });
        }
      });
    }

    AREAS.forEach(function (a) {
      if (!q || a[1].toLowerCase().indexOf(q) !== -1) {
        hits.push({ kind: 'area', label: a[1], sub: 'מעבר לאזור', view: a[0] });
      }
    });

    hits = hits.slice(0, 12);
    paintPanel(q);
  }

  function paintPanel(q) {
    var panel = $('cmdPanel');
    var input = $('cmdInput');
    panel.textContent = '';

    if (!hits.length) {
      var none = d.el('p', 'ds-cmd-empty',
        'לא נמצא תיק או אזור שמתאים ל"' + q + '".');
      panel.appendChild(none);
      open();
      return;
    }

    var group = null;
    var list = null;
    hits.forEach(function (h, i) {
      var name = h.kind === 'case' ? 'תיקים' : 'אזורים במערכת';
      if (name !== group) {
        group = name;
        panel.appendChild(d.el('p', 'ds-cmd-group', name));
        list = d.el('ul', 'ds-cmd-list');
        panel.appendChild(list);
      }
      var li = d.el('li');
      var btn = d.el('button', 'ds-cmd-opt');
      btn.type = 'button';
      btn.id = 'cmdOpt' + i;
      btn.setAttribute('role', 'option');
      btn.setAttribute('aria-selected', 'false');
      btn.appendChild(d.el('span', null, h.label));
      if (h.sub) btn.appendChild(d.el('span', 'ds-cmd-sub', h.sub));
      btn.addEventListener('click', function () { run(h); });
      li.appendChild(btn);
      list.appendChild(li);
    });

    open();
    input.setAttribute('aria-activedescendant', '');
  }

  function move(step) {
    cursor += step;
    if (cursor < 0) cursor = hits.length - 1;
    if (cursor >= hits.length) cursor = 0;

    var input = $('cmdInput');
    for (var i = 0; i < hits.length; i++) {
      var b = $('cmdOpt' + i);
      if (b) b.setAttribute('aria-selected', i === cursor ? 'true' : 'false');
    }
    var active = $('cmdOpt' + cursor);
    if (active) {
      input.setAttribute('aria-activedescendant', active.id);
      active.scrollIntoView({ block: 'nearest' });
    }
  }

  function run(hit) {
    if (!hit) return;
    close();
    $('cmdInput').value = '';
    if (hit.kind === 'case') d.openCase(hit.caseId, 'state');
    else d.topNav.show(hit.view, true);
  }

  function open() {
    $('cmdPanel').hidden = false;
    $('cmdInput').setAttribute('aria-expanded', 'true');
  }

  function close() {
    $('cmdPanel').hidden = true;
    $('cmdInput').setAttribute('aria-expanded', 'false');
    $('cmdInput').removeAttribute('aria-activedescendant');
    cursor = -1;
  }

  /** admin.js מעדכן את המפתח אחרי שרשימת התיקים נטענת ממילא. */
  function setCases(list) { caseIndex = list || []; }

  return { init: init, load: load, setCases: setCases };
})();
