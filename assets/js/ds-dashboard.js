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

  /** מציג הודעת מצב.

      state.className = '...' היה מוחק את המחלקה שנכתבה
      ב-HTML, ולכן שורת הטעינה איבדה את העיצוב שלה בציור
      הראשון. כאן המחלקה הבסיסית נשמרת תמיד. */
  function setState(el, kind, text) {
    if (!el) return;
    el.hidden = false;
    el.className = kind;
    el.textContent = text;
  }

  function init(deps) {
    d = deps;
    wireCommandBar();
    heroMark();
    /* הקטלוג נטען בשימוש הראשון ולא באתחול. באתחול מסך
       הכניסה עדיין מוצג, ובקשה לשרת שם היא גם מיותרת וגם
       הייתה מעיפה את הדף. */
  }

  /** תג ה-placeholder של ה-Hero.

      הוא נועד להיראות כל עוד אין תמונה, כדי שלא יישכח
      בייצור. ברגע ש---ds-hero-image מצביע על קובץ הוא מסיר
      את עצמו - בלי שמישהו יצטרך לזכור למחוק אותו מה-HTML. */
  function heroMark() {
    var hero = $('dashHero');
    var mark = $('dashHeroMark');
    if (!hero || !mark) return;
    var img = getComputedStyle(hero).backgroundImage || '';
    /* המשטח עצמו בנוי מ-gradients, ולכן "יש תמונה" פירושו
       שמופיע בו url() ולא רק שהוא אינו none. */
    if (img.indexOf('url(') !== -1) mark.remove();
  }


  /* ==========================================================
     טעינה
     ========================================================== */

  function load() {
    if (!d) return Promise.resolve();

    paintGreeting();

    return Promise.all([
      d.Api.officeTasks().catch(function () { return null; }),
      d.Api.officeCases().catch(function () { return null; }),
      d.Api.auditLog(null, 200).catch(function () { return null; })
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
      setState(state, 'ds-error', 'לא הצלחנו לטעון את הנתונים.');
      return;
    }

    var items = collectAttention(tasks, cases);

    $('dashAttnCount').textContent = items.length
      ? (items.length === 1 ? 'פריט אחד' : items.length + ' פריטים')
      : '';

    if (!items.length) {
      setState(state, 'ds-empty', 'אין כרגע דבר שדורש את תשומת לבך. כל המועדים והמסמכים מטופלים.');
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

  /** שורה בפיד, בשפת הכרטיסים.

      סדר הסריקה: פס דחיפות -> גלולת סטטוס -> הפעולה ->
      לקוח ותיק -> מועד. שלוש עמודות בשורה אחת, צפוף מספיק
      כדי לסרוק חמש שורות בשניות.

      המונה 01/02/03 ירד: הוא תפס עמודה שלמה ולא נשא מידע. */
  function row(it, n) {
    var li = d.el('li');
    var btn = d.el('button', 'ds-row ds-row-' + it.tone);
    btn.type = 'button';

    /* פס הדחיפות. דקורטיבי - הגלולה נושאת את המשמעות. */
    btn.appendChild(d.el('span', 'ds-bar', null, true));

    var what = d.el('div', 'ds-c-what');
    what.appendChild(pill(it.tone, it.mark, it.badge));
    what.appendChild(d.el('p', 'ds-row-title', it.title));

    var meta = d.el('p', 'ds-meta');
    if (it.who) meta.appendChild(d.el('span', null, it.who));
    if (it.num) meta.appendChild(d.el('span', 'ds-num', it.num));
    if (it.why) meta.appendChild(d.el('span', null, it.why));
    what.appendChild(meta);
    btn.appendChild(what);

    var side = d.el('div', 'ds-c-side');
    if (it.stamp) side.appendChild(d.el('span', 'ds-when-t', it.stamp));
    side.appendChild(d.el('span', 'ds-row-go', it.go + ' ←'));
    btn.appendChild(side);

    btn.addEventListener('click', function () { d.openCase(it.caseId, it.tab); });
    li.appendChild(btn);
    return li;
  }

  /** גלולת סטטוס. הסמל דקורטיבי; המילים נושאות את המשמעות,
      ולכן הצבע לעולם אינו הערוץ היחיד. */
  function pill(tone, mark, text) {
    var p = d.el('span', 'ds-pill ds-pill-' + tone);
    p.appendChild(d.el('span', null, mark, true));
    p.appendChild(d.el('span', null, text));
    return p;
  }


  /* ==========================================================
     ג. סדר היום שלי
     ========================================================== */

  function paintToday(tasks) {
    var list = $('dashTodayList');
    var state = $('dashTodayState');
    list.textContent = '';

    if (!tasks) {
      setState(state, 'ds-error', 'לא הצלחנו לטעון את המשימות.');
      return;
    }

    /* בדיוק אותו תנאי של השבב "להיום" בניהול משימות. */
    var today = (tasks.tasks || []).filter(function (t) {
      return t.bucket !== 'closed' && t.daysLeft === 0;
    });

    $('dashTodayCount').textContent = today.length
      ? (today.length === 1 ? 'פריט אחד' : today.length + ' פריטים') : '';

    if (!today.length) {
      setState(state, 'ds-empty', 'אין דבר שמועדו היום.');
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
  /* אירועי גישה. אמיתיים וחשובים, אבל הם אינם "מה קרה בתיק".
     כשהם מעורבים בפיד הראשי הם מציפים אותו - בצילום נראו
     ארבע כניסות זהות שתפסו כמעט את כולו. */
  var ACCESS = {
    'staff.login_success': true, 'staff.login_failed': true,
    'staff.logout': true, 'client.logout': true,
    'client.login_success': true, 'client.login_failed': true,
    'client.otp_requested': true
  };
  /* צפייה בתיק נרשמת בכל פתיחה. זו רשומת אבטחה, לא פעילות. */
  var NOISE = {
    'office.case_viewed': true, 'client.case_viewed': true
  };

  /* לאיזו לשונית קופצים מכל סוג פעולה. רק נתיבים שכבר
     קיימים - אין כאן route חדש. */
  var ACTION_TAB = {
    'document':     'docs',
    'task':         'tasks',
    'requirement':  'comms',
    'conversation': 'comms',
    'reminder':     'comms',
    'decision':     'committees',
    'stage':        'committees'
  };

  function tabFor(e) {
    var a = String(e.action || '');
    var keys = Object.keys(ACTION_TAB);
    for (var i = 0; i < keys.length; i++) {
      if (a.indexOf(keys[i]) !== -1) return ACTION_TAB[keys[i]];
    }
    return 'state';
  }

  /** "לפני 12 דקות". נופל בחזרה לתאריך כשזה כבר לא רלוונטי. */
  function ago(iso) {
    var t = new Date(iso);
    if (isNaN(t.getTime())) return '';
    var sec = Math.round((Date.now() - t.getTime()) / 1000);
    if (sec < 60)    return 'הרגע';
    var min = Math.round(sec / 60);
    if (min < 60)    return 'לפני ' + min + (min === 1 ? ' דקה' : ' דקות');
    var hr = Math.round(min / 60);
    if (hr < 24)     return 'לפני ' + hr + (hr === 1 ? ' שעה' : ' שעות');
    return d.dayLabel(iso) + ' ' + d.clockOf(iso);
  }

  /** מספר התיק וסוג התביעה מגיעים מרשימת התיקים שכבר נטענה.
      תשובת האודיט נושאת caseId ושם לקוח, אך לא מספר תיק -
      ולכן זהו חיבור בצד הלקוח על נתונים שכבר בידינו, ולא
      קריאה נוספת ולא ניחוש. */
  function caseOf(id) {
    if (!id) return null;
    for (var i = 0; i < caseIndex.length; i++) {
      if (caseIndex[i].id === id) return caseIndex[i];
    }
    return null;
  }

  function paintActivity(log) {
    var list = $('dashActList');
    var state = $('dashActState');
    list.textContent = '';

    if (!log) {
      setState(state, 'ds-error', 'לא הצלחנו לטעון את הפעילות.');
      paintAccess(null);
      return;
    }

    var all = log.entries || [];

    /* הפעולות המשמעותיות קודמות, והן נשלפות מתוך חלון גדול
       כדי שכניסות תכופות לא ידחקו אותן החוצה. הסדר בתוך
       הקבוצה נשאר כרונולוגי, כפי שהשרת החזיר. */
    var rows = all.filter(function (e) {
      return !NOISE[e.action] && !ACCESS[e.action];
    }).slice(0, 8);

    if (!rows.length) {
      setState(state, 'ds-empty', 'עוד לא בוצעה פעולה בתיקים.');
    } else {
      state.hidden = true;
      rows.forEach(function (e) { list.appendChild(activityRow(e)); });
    }

    paintAccess(all);
  }

  /** שורת פעילות בתיק, בשלוש רמות:
        הפעולה
        לקוח · מספר תיק · סוג תביעה
        מי ביצע · מתי
      לחיצה פותחת את התיק בלשונית המתאימה, כשיש caseId. */
  function activityRow(e) {
    var c = caseOf(e.caseId);
    var li = d.el('li', 'ds-ev');

    var box = e.caseId ? d.el('button', 'ds-ev-in ds-ev-go') : d.el('div', 'ds-ev-in');
    if (e.caseId) {
      box.type = 'button';
      box.addEventListener('click', function () { d.openCase(e.caseId, tabFor(e)); });
    }

    box.appendChild(d.el('p', 'ds-ev-what', d.describe(e)));

    var where = d.el('p', 'ds-ev-where');
    if (e.client) where.appendChild(d.el('span', null, e.client));
    if (c && c.caseNumber) where.appendChild(d.el('span', 'ds-num', c.caseNumber));
    if (c && c.claimType) where.appendChild(d.el('span', null, c.claimType));
    if (where.childNodes.length) box.appendChild(where);

    var who = d.el('p', 'ds-ev-who');
    if (e.actor) who.appendChild(d.el('span', null, e.actor));
    who.appendChild(d.el('span', 'ds-ev-when', ago(e.at)));
    box.appendChild(who);

    li.appendChild(box);
    return li;
  }

  /** שם פרטי בלבד, לסיכום קצר. */
  function firstName(full) {
    return String(full || '').replace(/^עו"ד\s+/, '').split(' ')[0] || full || '';
  }

  /** סיכום הגישה למערכת.

      עד כה זה היה בלוק כהה בתחתית העמוד, והוא תפס משקל
      ויזואלי שאינו מגיע לו: 83% משורות האודיט הן כניסות,
      והן אינן "מה קרה בתיק".

      עכשיו זו שורה שקטה אחת בתחתית כרטיס הפעילות. האודיט
      עצמו אינו משתנה - אף אירוע לא נמחק וסמנטיקת הרישום
      זהה. זהו סיכום לתצוגה בלבד.

      ניסיון כניסה שנכשל מקבל שורה נפרדת, כי זו אמירה
      ביטחונית ולא רעש - אבל גם הוא שורה, לא בלוק. */
  function paintAccess(all) {
    var box = $('dashSysList');
    var state = $('dashSysState');
    if (!box) return;
    box.textContent = '';

    if (!all) { state.hidden = false; state.textContent = 'אירועי הגישה לא נטענו.'; return; }

    var today = d.dayLabel(new Date().toISOString());
    var ok = [], failed = [];
    all.forEach(function (e) {
      if (!ACCESS[e.action]) return;
      (/_failed$/.test(e.action) ? failed : ok).push(e);
    });

    if (!ok.length && !failed.length) {
      state.hidden = false;
      state.textContent = 'אין אירועי גישה.';
      return;
    }
    state.hidden = true;

    /* מי נכנס הכי הרבה היום, וכמה נשארו מעבר לו. */
    var byWho = {}, order = [];
    ok.forEach(function (e) {
      if (d.dayLabel(e.at) !== today) return;
      var who = firstName(e.actor || e.client) || 'לא מזוהה';
      if (!byWho[who]) { byWho[who] = 0; order.push(who); }
      byWho[who] += 1;
    });
    order.sort(function (a, b) { return byWho[b] - byWho[a]; });

    var bits = ['גישה למערכת'];
    if (order.length) {
      var top = order[0];
      bits.push(top + ' · ' + byWho[top] +
                (byWho[top] === 1 ? ' כניסה היום' : ' כניסות היום'));
      var rest = 0;
      for (var i = 1; i < order.length; i++) rest += byWho[order[i]];
      if (rest) bits.push(rest + (rest === 1 ? ' כניסה נוספת' : ' כניסות נוספות'));
    } else {
      bits.push('אין כניסות היום');
    }
    box.appendChild(d.el('p', 'ds-access-line', bits.join(' · ')));

    if (failed.length) {
      var who = firstName(failed[0].actor) || 'לא מזוהה';
      box.appendChild(d.el('p', 'ds-access-line ds-access-warn',
        (failed.length === 1 ? 'ניסיון כניסה שנכשל' : failed.length + ' ניסיונות כניסה שנכשלו')
        + ' · ' + who));
    }
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

  /* החיפוש רץ מול השרת ולא על הרשימה שבזיכרון, ולכן הוא מוצא
     גם מסמך, משימה ודרישה - לא רק תיק שכבר נטען.

     ההשהיה קיימת כי הקלדה מהירה הייתה יורה בקשה לכל תו.
     seq מונע מתשובה איטית לדרוס תשובה חדשה ממנה, וזו תקלה
     שקל מאוד לפספס: בלעדיו הקלדת "EMG" יכולה להציג את
     תוצאות "EM" אם הן חזרו אחרונות. */
  var timer = null;
  var seq = 0;

  function search(q) {
    q = String(q || '').trim();
    clearTimeout(timer);

    /* האזורים הם ניווט מקומי ומיידי, בלי רשת. */
    var local = AREAS.filter(function (a) {
      return !q || a[1].toLowerCase().indexOf(q.toLowerCase()) !== -1;
    }).map(function (a) {
      return { kind: 'area', label: a[1], sub: 'מעבר לאזור', view: a[0] };
    });

    if (q.length < 2) {
      hits = local.slice(0, 12);
      cursor = -1;
      paintPanel(q);
      return;
    }

    /* הצגה מיידית של האזורים, והתוצאות מהשרת מצטרפות אליהן */
    hits = local;
    cursor = -1;
    paintPanel(q, true);

    loadKinds();
    var mine = ++seq;
    timer = setTimeout(function () {
      d.Api.search(q).then(function (data) {
        if (mine !== seq) return;        /* תשובה שאיחרה */
        var remote = (data.results || []).map(function (r) {
          var bits = [];
          if (r.clientName) bits.push(r.clientName);
          if (r.caseNumber && r.kind !== 'case') bits.push(r.caseNumber);
          if (r.subtitle) bits.push(r.subtitle);
          return {
            kind: r.kind,
            label: r.title,
            sub: bits.join(' · '),
            caseId: r.caseId,
            tab: r.tab
          };
        });
        hits = remote.concat(local).slice(0, 20);
        cursor = -1;
        paintPanel(q);
      }).catch(function () {
        if (mine !== seq) return;
        hits = local;
        paintPanel(q);
      });
    }, 220);
  }

  /* התוויות מגיעות מהשרת, כדי שהוספת מקור חיפוש בעתיד -
     תוכן מסמך, למשל - תופיע כאן בלי שינוי בצד הלקוח. */
  var KIND_LABEL = { area: 'אזורים במערכת' };

  var kindsLoaded = false;

  function loadKinds() {
    if (kindsLoaded || !d || !d.Api.searchKinds) return;
    kindsLoaded = true;
    d.Api.searchKinds().then(function (data) {
      (data.kinds || []).forEach(function (k) { KIND_LABEL[k.kind] = k.label; });
    }).catch(function () { /* התוויות נשארות כברירת מחדל */ });
  }

  function paintPanel(q, loading) {
    var panel = $('cmdPanel');
    var input = $('cmdInput');
    panel.textContent = '';

    if (!hits.length) {
      panel.appendChild(d.el('p', 'ds-cmd-empty', loading
        ? 'מחפש…'
        : 'לא נמצא דבר שמתאים ל"' + q + '".'));
      open();
      return;
    }

    var group = null;
    var list = null;
    hits.forEach(function (h, i) {
      var name = KIND_LABEL[h.kind] || h.kind;
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

    if (loading) panel.appendChild(d.el('p', 'ds-cmd-empty', 'מחפש…'));

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
    /* כל תוצאה מהשרת נושאת caseId ו-tab, ולכן היא קופצת
       ישירות ללשונית שבה הפריט באמת יושב. */
    if (hit.kind === 'area') d.topNav.show(hit.view, true);
    else if (hit.caseId)     d.openCase(hit.caseId, hit.tab || 'state');
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

  /** נשמר כדי ש-admin.js לא ישבר. החיפוש עצמו עבר לשרת ואינו
      נשען עוד על הרשימה שבזיכרון. */
  function setCases(list) { caseIndex = list || []; }

  return { init: init, load: load, setCases: setCases };
})();
