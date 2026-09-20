/* ==========================================================
   admin.js - ממשק הניהול הפנימי
   ----------------------------------------------------------
   שלושה דברים שהצוות עושה כאן:
     1. מעדכן את השלב שבו נמצא התיק
     2. מאשר או דוחה מסמכים שהלקוח העלה (דחייה מחייבת סיבה)
     3. שולח עדכון ללקוח
   כל פעולה נרשמת ביומן הפעולות.
   ========================================================== */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var staff  = null;   // הצוות המחובר
  var openId = null;   // ת״ז הלקוח שהתיק שלו פתוח כרגע

  /* ================= כניסת צוות ================= */

  var loginForm = $('staffLoginForm');
  var loginErr  = $('loginError');

  loginForm.addEventListener('submit', function (e) {
    e.preventDefault();

    var u = $('staffUser').value.trim();
    var p = $('staffPass').value;

    loginErr.hidden = true;

    if (!u || !p) {
      return loginFail('צריך להזין אימייל וסיסמה.');
    }

    /* ההשוואה בשרת, מול bcrypt. הדפדפן אינו מחזיק סיסמאות. */
    Api.staffLogin(u, p)
       .then(function () { start(); })
       .catch(function (err) {
         loginFail(err.status === 429
           ? 'יותר מדי ניסיונות. נסה שוב מאוחר יותר.'
           : 'האימייל או הסיסמה אינם נכונים.');
       });
  });

  function loginFail(msg) {
    loginErr.textContent = msg;
    loginErr.hidden = false;
    $('staffUser').focus();
  }

  $('staffLogout').addEventListener('click', function () {
    Api.staffLogout().then(reloadPage, reloadPage);
  });

  function reloadPage() { location.reload(); }

  /* ================= הפעלה ================= */

  /* התיק הפתוח, כפי שהתקבל מהשרת. אין מצב מקומי שנכתב אליו -
     כל שינוי נשלח לשרת ואז נטען מחדש ממנו. */
  var currentCase = null;

  function start() {
    return Api.me()
      .then(function (who) {
        if (who.type !== 'user') { location.replace('index.html'); return; }
        staff = who;
        $('loginView').hidden = true;
        $('appView').hidden   = false;
        /* הפוטר שייך למסך הכניסה. עד 18.09 הוא נשאר גלוי גם
           אחרי התחברות והופיע מעל הבאנר. */
        var foot = document.querySelector('.sitefoot');
        if (foot) foot.hidden = true;
        $('staffName').textContent = who.name + (who.role ? ' · ' + who.role : '');
        return route(location.hash.slice(1));
      });
  }


  /* ================= ניווט =================
     שני מפלסים על אותו מנוע: שמונת האזורים, ושש הלשוניות
     שבתוך תיק. הוספת אזור או לשונית בעתיד היא שורה במפה. */

  var TOP_VIEWS = {
    dashboard: { el: 'view-dashboard', title: 'דשבורד' },
    tasks:     { el: 'view-tasks',     title: 'ניהול משימות' },
    cases:     { el: 'view-cases',     title: 'התיקים בטיפולי' },
    closed:    { el: 'view-closed',    title: 'תיקים שהושלמו ושכר טרחה' },
    documents: { el: 'view-documents', title: 'מסמכים' },
    clients:   { el: 'view-clients',   title: 'לקוחות' },
    reports:   { el: 'view-reports',   title: 'דוחות' },
    settings:  { el: 'view-settings',  title: 'הגדרות' }
  };

  var CASE_VIEWS = {
    state:      { el: 'case-view-state',      title: 'מצב התיק' },
    comms:      { el: 'case-view-comms',      title: 'דרישות ותקשורת עם הלקוח' },
    docs:       { el: 'case-view-docs',       title: 'מסמכי התיק' },
    committees: { el: 'case-view-committees', title: 'ועדות, החלטות ומועדים' },
    tasks:      { el: 'case-view-tasks',      title: 'משימות' },
    history:    { el: 'case-view-history',    title: 'היסטוריה' }
  };

  var drawer = ViewNav.drawer({
    button: 'menuBtn', panel: 'drawer',
    overlay: 'drawerOverlay', close: 'drawerClose',
    inert: ['#main', '.topbar']
  });

  var topNav = ViewNav.create({
    views: TOP_VIEWS,
    links: '.sitenav a[data-view], .drawer-link[data-view], #view-dashboard a[data-view]',
    announce: 'adminAnnounce',
    titleSel: '.view-title',
    fallback: 'cases',
    onLink: function () { drawer.close(false); },
    onShow: function (name) {
      /* תיק פתוח ובחרו אזור עליון - יוצאים מסביבת התיק. */
      if (openId) {
        openId = null;
        currentCase = null;
        $('caseWorkspace').hidden = true;
        $('topLevel').hidden      = false;
        $('backBtn').hidden       = true;
      }
      setHash(name);
      loadTopView(name);
    }
  });

  var caseNav = ViewNav.create({
    views: CASE_VIEWS,
    links: '.case-tabs a[data-view]',
    announce: 'adminAnnounce',
    /* הכותרת בתוך לשונית מוסתרת חזותית, ולכן המיקוד נשאר על
       הלשונית שנלחצה. ההכרזה היא מה שמוסר את המידע לקורא מסך. */
    focusTitle: false,
    fallback: 'state',
    onShow: function (name) {
      if (openId) setHash('cases/' + openId + '/' + name);
    }
  });

  /** כל אזור טוען את מה ששייך לו, ורק כשנכנסים אליו.
      הבאנר הוא היוצא מן הכלל: הוא נטען בכל אזור, כי מועד
      קריטי לא אמור להיעלם רק כי עברת למסך אחר. */
  function loadTopView(name) {
    if (name !== 'tasks') loadBanner();
    if (name === 'cases')   return showList();
    if (name === 'tasks')   return loadAttention();
    if (name === 'reports') return renderLog(null, 'firmLogList');
    return Promise.resolve();
  }

  var settingHash = false;
  function setHash(value) {
    if (location.hash.slice(1) === value) return;
    settingHash = true;
    history.replaceState(null, '', '#' + value);
    settingHash = false;
  }

  /** #cases/<id>/<tab> פותח תיק ולשונית; אחרת אזור עליון. */
  function route(hash) {
    var parts = (hash || '').split('/');

    if (parts[0] === 'cases' && parts[1]) {
      topNav.show('cases', false);
      return openCase(parts[1], caseNav.has(parts[2]) ? parts[2] : 'state');
    }

    var name = topNav.has(parts[0]) ? parts[0] : 'cases';
    topNav.show(name, false);
    return loadTopView(name);
  }

  window.addEventListener('hashchange', function () {
    if (settingHash) return;
    route(location.hash.slice(1));
  });

  var drawerOut = $('drawerLogout');
  if (drawerOut) {
    drawerOut.addEventListener('click', function () { $('staffLogout').click(); });
  }


  /* ================= החלטת הוועדה ================= */

  var OUTCOMES = [
    ['below-threshold', 'נקבעו אחוזים מתחת לסף הזכאות'],
    ['grant',           'מענק חד-פעמי'],
    ['pension',         'קצבה חודשית'],
    ['rejected',        'התביעה נדחתה'],
  ];

  var decForm = $('decForm');
  var decErr  = $('decError');

  function renderDecisionForm(file) {
    var sel = $('decOutcome');
    if (!sel.options.length) {
      OUTCOMES.forEach(function (pair) {
        var opt = document.createElement('option');
        opt.value = pair[0];
        opt.textContent = pair[1];
        sel.appendChild(opt);
      });
    }
    var d = file.decision;
    $('decisionNow').textContent = d
      ? 'נרשמה החלטה מ-' + formatDate(d.date) +
        (d.percent != null ? ' · ' + d.percent + '% נכות' : '') +
        '. רישום חדש יתווסף לצידה ולא ידרוס אותה.'
      : 'טרם נרשמה החלטה בתיק.';
  }

  decForm.addEventListener('submit', function (e) {
    e.preventDefault();
    decErr.hidden = true;

    var date = $('decDate').value;
    if (!date) return decFail('צריך תאריך החלטה.', $('decDate'));

    var percent = $('decPercent').value;
    if (percent === '') return decFail('צריך להזין אחוזי נכות.', $('decPercent'));

    Api.recordDecision(openId, {
      decided_at:      date,
      outcome:         $('decOutcome').value,
      percent:         parseInt(percent, 10),
      is_permanent:    $('decPermanent').checked,
      appeal_deadline: $('decDeadline').value || null,
      office_note:     $('decNote').value.trim() || null,
    }).then(function () { return renderCase(); })
      .then(function () {
        decForm.reset();
        toast('ההחלטה נרשמה והלקוח יראה אותה.');
      })
      .catch(function (err) { decFail(err.message || 'רישום ההחלטה נכשל.'); });
  });

  function decFail(text, focusOn) {
    decErr.textContent = text;
    decErr.hidden = false;
    if (focusOn) focusOn.focus();
  }

  /* ================= קטלוג המסמכים ================= */

  /* הקטלוג מגיע מ-required_document_templates במסד. עד 13.09 הוא
     היה קבוע ב-data.js, כלומר עותק בדפדפן שיכול לסטות מהמסד. */
  var templates = [];

  function renderCatalog(file) {
    var sel = $('reqPick');
    sel.textContent = '';
    var first = document.createElement('option');
    first.value = '';
    first.textContent = 'בחירה מהקטלוג…';
    sel.appendChild(first);

    return Api.documentTemplates(file.id).then(function (data) {
      templates = data.templates;
      var have = {};
      (file.documents || []).forEach(function (d) { have[d.name] = true; });

      templates.forEach(function (t, i) {
        var opt = document.createElement('option');
        opt.value = String(i);
        opt.textContent = t.name + (have[t.name] ? ' (כבר בתיק)' : '');
        opt.disabled = !!have[t.name];
        sel.appendChild(opt);
      });
    }).catch(function () {
      first.textContent = 'לא הצלחנו לטעון את הקטלוג';
    });
  }

  $('reqPick').addEventListener('change', function () {
    var t = templates[parseInt(this.value, 10)];
    if (!t) return;
    $('reqName').value = t.name;
    $('reqNote').value = t.guidance || '';
    $('reqRequired').checked = !!t.required;
  });

  /* ================= יומן הפעולות ================= */

  var ACTION_LABELS = {
    'office.stage_changed':      'עודכן שלב',
    'office.document_reviewed':  'נבדק מסמך',
    'office.document_requested': 'נדרש מסמך',
    'office.document_cancelled': 'נסגרה דרישה',
    'office.decision_recorded':  'נרשמה החלטה',
    'office.message_sent':       'נשלח עדכון',
    'office.case_viewed':        'צפייה בתיק',
    'office.ai_invoked':         'הופעל ניתוח',
    'client.file_uploaded':      'הלקוח העלה מסמך',
    'client.document_replied':   'הלקוח הגיב',
    'client.case_viewed':        'הלקוח צפה בתיק',
    'client.file_downloaded':    'הלקוח הוריד מסמך',
    'office.task_created':          'נוצרה משימה',
    'office.task_updated':          'עודכנה משימה',
    'office.task_reassigned':       'הוחלף אחראי',
    'office.task_completed':        'הושלמה משימה',
    'office.task_reopened':         'נפתחה משימה מחדש',
    'office.task_deadline_changed': 'הוזז מועד יעד',
  };

  /** caseId=null מביא את יומן כל המשרד, לאזור "דוחות". */
  function renderLog(caseId, listId) {
    var list = $(listId || 'logList');
    if (!list) return Promise.resolve();
    return Api.auditLog(caseId).then(function (data) {
      list.textContent = '';
      if (!data.entries.length) {
        list.appendChild(el('li', 'item-note', 'אין עדיין פעולות רשומות.'));
        return;
      }
      data.entries.forEach(function (entry) {
        var li = el('li');
        li.appendChild(el('h3', null,
          ACTION_LABELS[entry.action] || entry.action));
        li.appendChild(el('p', 'item-note',
          (entry.actor || 'המערכת') + ' · ' + stamp(entry.at)));
        list.appendChild(li);
      });
    }).catch(function () {
      list.textContent = '';
      list.appendChild(el('li', 'item-note', 'לא הצלחנו לטעון את היומן.'));
    });
  }


  /* ================= רשימת התיקים ================= */

  function showList() {
    return Api.officeCases().then(function (data) {
      var cases   = data.cases;
      var waiting = cases.reduce(function (n, c) { return n + c.awaitingReview; }, 0);

      $('listSummary').textContent = waiting === 0
        ? cases.length + ' תיקים פעילים. אין מסמכים שממתינים לבדיקה.'
        : cases.length + ' תיקים פעילים · ' +
          (waiting === 1 ? 'מסמך אחד ממתין' : waiting + ' מסמכים ממתינים') +
          ' לבדיקת המשרד.';

      var rows = $('caseRows');
      rows.textContent = '';

      cases.forEach(function (c) {
        var tr = document.createElement('tr');

        var who = document.createElement('td');
        who.appendChild(el('div', 'client', c.clientName));
        who.appendChild(el('div', 'sub num', c.caseNumber));
        tr.appendChild(who);

        tr.appendChild(cell(c.caseNumber, 'num'));
        tr.appendChild(cell(c.claimType));

        var stage = document.createElement('td');
        stage.appendChild(el('div', null, (c.currentStage || '-') + ''));
        stage.appendChild(el('div', 'sub', c.stageTitle || ''));
        tr.appendChild(stage);

        tr.appendChild(countCell(c.awaitingReview, 'hot'));
        tr.appendChild(countCell(c.openForClient, 'calm'));

        var act = document.createElement('td');
        var open = el('button', 'btn btn-outline btn-sm', 'פתיחת התיק');
        open.type = 'button';
        open.addEventListener('click', function () { openCase(c.id); });
        act.appendChild(open);
        tr.appendChild(act);

        rows.appendChild(tr);
      });
    }).catch(function (err) {
      $('listSummary').textContent =
        err.message || 'לא הצלחנו לטעון את רשימת התיקים.';
    });
  }

  function cell(text, cls) {
    var td = document.createElement('td');
    if (cls) td.className = cls;
    td.textContent = text == null ? '' : text;
    return td;
  }

  function countCell(n, tone) {
    var td = document.createElement('td');
    var box = el('span', 'count' + (n ? ' ' + tone : ''), String(n || 0));
    td.appendChild(box);
    return td;
  }

  /* ================= תיק בודד ================= */

  function openCase(caseId, tab) {
    openId = caseId;

    /* הניתוח שייך לתיק - אסור שיישאר על המסך כשעוברים לתיק אחר */
    assistHistory = [];
    $('assistLog').textContent = '';
    $('assistInput').value = '';

    $('topLevel').hidden     = true;
    $('caseWorkspace').hidden = false;
    $('backBtn').hidden      = false;

    caseNav.show(tab || 'state', false);
    setHash('cases/' + caseId + '/' + caseNav.current());

    return renderCase().then(function () { $('caseClient').focus(); });
  }

  /** יוצא מהתיק וחוזר לרשימה. */
  function closeCase() {
    openId = null;
    currentCase = null;
    $('caseWorkspace').hidden = true;
    $('topLevel').hidden      = false;
    $('backBtn').hidden       = true;
    topNav.show('cases', true);
    setHash('cases');
    return showList();
  }

  $('backBtn').addEventListener('click', closeCase);

  /** הכותרת הקבועה של התיק - נשארת זהה בכל שש הלשוניות. */
  function renderCaseHead(file) {
    var facts = [
      ['מספר תיק',  file.caseNumber, true],
      ['סוג התביעה', file.claimType],
      ['שלב נוכחי',  (file.currentStage || '-') +
                     (file.stageTitle ? ' · ' + file.stageTitle : '')],
      ['סטטוס',      file.status || 'פעיל'],
      ['אחראי',      file.assignee || 'לא שויך']
    ];
    /* הסניף נשאר מחוץ לכותרת הקבועה במכוון: הוא הוסיף שורה
       שלישית לפרטים ואינו נדרש בכל לשונית. הוא מופיע בפרטי
       התיק עצמם. */
    var dl = $('caseFacts');
    dl.textContent = '';
    facts.forEach(function (f) {
      var wrap = el('div', 'case-fact');
      wrap.appendChild(el('dt', null, f[0]));
      wrap.appendChild(el('dd', f[2] ? 'num' : null, f[1] || '-'));
      dl.appendChild(wrap);
    });
  }

  /** טוען את התיק מהשרת ומצייר. מוחזר Promise כדי שפעולות
      יוכלו להמתין לרענון לפני שהן מודיעות שהצליחו. */
  function renderCase() {
    return Api.officeCase(openId).then(function (file) {
      currentCase = file;

      $('caseClient').textContent = file.clientName;
      renderCaseHead(file);

      /* הפרטים יושבים בכותרת הקבועה. #caseMeta נשאר ריק בהצלחה
         ומשמש רק להודעת שגיאה, ולכן הוא מוסתר כשהוא ריק. */
      $('caseMeta').textContent = '';

      renderStage(file);
      renderDecisionForm(file);
      renderCatalog(file);
      renderReview(file);
      renderCaseTasks();
      renderLog(openId);
      /* הבאנר חייב להישאר גם כאן: מועד קריטי בתיק אחר לא
         אמור להיעלם רק כי נפתח תיק. */
      loadBanner();
    }).catch(function (err) {
      $('caseMeta').textContent = err.message || 'לא הצלחנו לטעון את התיק.';
    });
  }

  /* ---- מצב טכני: מה חוסם ומה חסר ---- */


  /* השלבים מגיעים מהשרת (stage_templates של סוג התביעה של התיק)
     ולא מקבוע מקומי. כך תיק נכות מעבודה מקבל את המסלול שלו ולא
     את זה של נכות כללית - הפער שתועד ב-"05 - אפיון מוצר". */
  function renderStage(file) {
    var stages = file.stageOptions || [];

    $('stageNow').textContent = file.currentStage
      ? 'כרגע: שלב ' + file.currentStage + ' מתוך ' + stages.length +
        (file.stageTitle ? ' - ' + file.stageTitle : '') + '.'
      : 'טרם נרשם שלב לתיק.';

    var sel = $('stageSelect');
    sel.textContent = '';

    stages.forEach(function (stage) {
      var opt = document.createElement('option');
      /* הערך הוא המזהה מהמסד, לא מספר סידורי. */
      opt.value = stage.id;
      opt.textContent = stage.position + '. ' + stage.title;
      if (stage.position === file.currentStage) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  $('stageSave').addEventListener('click', function () {
    var select = $('stageSelect');
    var stage  = select.options[select.selectedIndex];
    if (!stage || !stage.value) return;

    /* המזהה הוא stage_template_id מהשרת, לא מספר סידורי מקומי.
       השרת מוודא שהשלב שייך למסלול של התיק. */
    Api.setStage(openId, stage.value, null)
       .then(function () { return renderCase(); })
       .then(function () { toast('התיק עודכן ל' + stage.textContent + '.'); })
       .catch(function (err) { toast(err.message || 'עדכון השלב נכשל.'); });
  });


  var reqForm = $('reqForm');
  var reqErr  = $('reqError');

  reqForm.addEventListener('submit', function (e) {
    e.preventDefault();

    var name = $('reqName').value.trim();
    var note = $('reqNote').value.trim();

    reqErr.hidden = true;

    if (!name) return reqFail('צריך שם למסמך.', $('reqName'));
    if (note.length < 5) {
      return reqFail('ההנחיה קצרה מדי. בלי הנחיה ברורה הלקוח יעלה את המסמך הלא נכון.', $('reqNote'));
    }

    if (currentCase && currentCase.documents.some(function (d) { return d.name === name; })) {
      return reqFail('המסמך "' + name + '" כבר קיים בתיק.', $('reqName'));
    }

    Api.addDocument(openId, name, note, $('reqRequired').checked)
       .then(function () { return renderCase(); })
       .then(function () { toast('הדרישה נוספה והלקוח יראה אותה.'); })
       .catch(function (err) { toast(err.message || 'הוספת הדרישה נכשלה.'); });

    reqForm.reset();
    $('reqRequired').checked = true;
  });

  function reqFail(text, focusOn) {
    reqErr.textContent = text;
    reqErr.hidden = false;
    focusOn.focus();
  }

  /* ---- בדיקת מסמכים ---- */

  /* המפתחות הם ערכי המסד (pending_review עם קו תחתון), כפי
     שממשק הניהול מקבל אותם מ-/api/office. עד 13.09 הם היו
     הערכים של data.js עם מקף. */
  var REVIEW_STATUS = {
    'approved':       { tag: 'tag-ok',   mark: '✓', text: 'אושר' },
    'pending_review': { tag: 'tag-warn', mark: '●', text: 'ממתין לבדיקה' },
    'missing':        { tag: 'tag-wait', mark: '!', text: 'הלקוח עוד לא העלה' },
    'rejected':       { tag: 'tag-stop', mark: '✗', text: 'נדחה - הלקוח התבקש להעלות מחדש' },
    'cancelled':      { tag: 'tag-wait', mark: '–', text: 'הדרישה נסגרה' }
  };

  function reviewStatus(value) {
    return REVIEW_STATUS[value] ||
           { tag: 'tag-wait', mark: '?', text: value || 'לא ידוע' };
  }

  function renderReview(file) {
    // מה שממתין לבדיקה קודם - זו העבודה הפתוחה של הצוות
    var order = { 'pending_review': 0, 'rejected': 1, 'missing': 2,
                  'approved': 3, 'cancelled': 4 };
    var docs  = file.documents.slice().sort(function (a, b) {
      return order[a.status] - order[b.status];
    });

    var waiting = docs.filter(function (d) { return d.status === 'pending_review'; }).length;
    $('reviewIntro').textContent = waiting === 0
      ? 'אין מסמכים שממתינים לבדיקה בתיק הזה.'
      : (waiting === 1 ? 'מסמך אחד ממתין' : waiting + ' מסמכים ממתינים') + ' לבדיקה שלך.';

    var list = $('reviewList');
    list.textContent = '';

    docs.forEach(function (doc) {
      var s  = reviewStatus(doc.status);
      var li = el('li');

      var tag = el('span', 'tag ' + s.tag);
      tag.appendChild(el('span', null, s.mark, true));
      tag.appendChild(document.createTextNode(s.text));
      li.appendChild(tag);

      li.appendChild(el('h3', null, doc.name + (doc.required ? '' : ' (לא חובה)')));
      li.appendChild(el('p', 'item-note', doc.note));

      if (doc.file) {
        li.appendChild(el('span', 'file-name',
          'הקובץ שהלקוח העלה: ' + doc.file + ' · ' + formatDate(doc.date)));
      }
      if (doc.status === 'rejected' && doc.rejectReason) {
        li.appendChild(el('p', 'reject-note', 'סיבת הדחייה שנמסרה ללקוח: ' + doc.rejectReason));
      }
      if (doc.status === 'pending_review') {
        li.appendChild(reviewControls(doc));
      }

      /* סגירת דרישה שהלקוח טרם מילא. השורה נשארת במסד עם סטטוס
         cancelled - מחיקה הייתה מוחקת בקסקייד גם קבצים שכבר
         הועלו תחתיה. */
      if (doc.status === 'missing' || doc.status === 'rejected') {
        var cancel = el('button', 'btn btn-outline btn-sm doc-actions', 'סגירת הדרישה');
        cancel.type = 'button';
        cancel.setAttribute('aria-label', 'סגירת הדרישה למסמך ' + doc.name);
        cancel.addEventListener('click', function () {
          cancel.disabled = true;
          Api.cancelDocument(doc.id)
             .then(function () { return renderCase(); })
             .then(function () { toast('הדרישה למסמך "' + doc.name + '" נסגרה.'); })
             .catch(function (err) {
               cancel.disabled = false;
               toast(err.message || 'סגירת הדרישה נכשלה.');
             });
        });
        li.appendChild(cancel);
      }

      list.appendChild(li);
    });
  }

  /** כפתורי אישור/דחייה + טופס סיבת הדחייה שנפתח מתחתיהם */
  function reviewControls(doc) {
    var wrap    = el('div');
    var actions = el('div', 'review-actions');

    var ok = el('button', 'btn btn-ok btn-sm', 'אישור המסמך');
    ok.type = 'button';
    ok.setAttribute('aria-label', 'אישור המסמך ' + doc.name);
    ok.addEventListener('click', function () {
      Api.reviewDocument(doc.id, 'approve', null)
         .then(function () { return renderCase(); })
         .then(function () { toast('המסמך "' + doc.name + '" אושר.'); })
         .catch(function (err) { toast(err.message || 'האישור נכשל.'); });
    });

    var no = el('button', 'btn btn-stop btn-sm', 'דחייה');
    no.type = 'button';
    no.setAttribute('aria-expanded', 'false');
    no.setAttribute('aria-label', 'דחיית המסמך ' + doc.name);

    actions.appendChild(ok);
    actions.appendChild(no);
    wrap.appendChild(actions);

    // הדחייה מחייבת סיבה - הלקוח צריך לדעת מה לתקן
    var form = el('div', 'reject-form');
    form.hidden = true;

    var fieldId = 'reason-' + doc.id;
    var field   = el('div', 'field');
    var label   = el('label', null, 'סיבת הדחייה - תוצג ללקוח כפי שהיא');
    label.setAttribute('for', fieldId);

    var input = document.createElement('input');
    input.type        = 'text';
    input.id          = fieldId;
    input.maxLength   = 140;
    input.placeholder = 'לדוגמה: הצילום לא קריא, נא לצלם שוב באור טוב';

    field.appendChild(label);
    field.appendChild(input);
    form.appendChild(field);

    var send = el('button', 'btn btn-stop btn-sm', 'דחייה ושליחת הסיבה ללקוח');
    send.type = 'button';
    send.addEventListener('click', function () {
      var reason = input.value.trim();
      if (reason.length < 5) {
        input.setAttribute('aria-invalid', 'true');
        input.focus();
        return toast('צריך לכתוב סיבת דחייה ברורה - הלקוח רואה אותה.');
      }
      Api.reviewDocument(doc.id, 'reject', reason)
         .then(function () { return renderCase(); })
         .then(function () { toast('המסמך "' + doc.name + '" נדחה והלקוח יעודכן.'); })
         .catch(function (err) { toast(err.message || 'הדחייה נכשלה.'); });
    });
    form.appendChild(send);
    wrap.appendChild(form);

    no.addEventListener('click', function () {
      var opening = form.hidden;
      form.hidden = !opening;
      no.setAttribute('aria-expanded', String(opening));
      if (opening) input.focus();
    });

    return wrap;
  }

  /* ---- פניות מהלקוח ---- */

  var msgForm = $('msgForm');
  var msgErr  = $('msgError');

  msgForm.addEventListener('submit', function (e) {
    e.preventDefault();

    var title = $('msgTitle').value.trim();
    var body  = $('msgBody').value.trim();

    msgErr.hidden = true;

    if (!title)           return msgFail('צריך כותרת להודעה.', $('msgTitle'));
    if (body.length < 10) return msgFail('תוכן ההודעה קצר מדי.', $('msgBody'));

    Api.sendMessage(openId, title, body, $('msgImportant').checked)
       .then(function () { return renderCase(); })
       .then(function () { toast('העדכון נשלח ללקוח.'); })
       .catch(function (err) { toast(err.message || 'שליחת העדכון נכשלה.'); });

    msgForm.reset();
  });

  function msgFail(text, focusOn) {
    msgErr.textContent = text;
    msgErr.hidden = false;
    focusOn.focus();
  }


  /* ================= ניתוח מקצועי ================= */

  var assistHistory = [];
  var assistBusy    = false;

  /**
   * בונה את הקשר התיק לשליחה.
   * נשלחת מטא-דאטה בלבד - בלי שם, תעודת זהות, טלפון או שם קובץ.
   * השרת מצמצם ובודק שוב (server/policy.py); הצמצום כאן הוא
   * השכבה הראשונה, לא היחידה.
   */
  function assistContext() {
    var file = currentCase || {};

    return {
      claimType:      file.claimType,
      branch:         file.branch,
      currentStage:   file.currentStage,
      totalStages:    (file.stageOptions || []).length,
      stageTitle:     file.stageTitle,
      stageEnteredAt: null,
      openedAt:       null,
      nextHearing:    file.nextHearing,
      documents:      file.documents.map(function (d) {
        return {
          name:         d.name,
          note:         d.note,
          required:     d.required,
          status:       d.status,
          rejectReason: d.rejectReason || null,
          file:         d.file            // השרת ממיר ל-hasFile בלבד
        };
      }),
    };
  }

  /** נועל את הכפתורים בזמן ניתוח, כדי שברור למה לחיצה לא עושה דבר */
  function setAssistBusy(busy) {
    assistBusy = busy;
    var controls = document.querySelectorAll(
      '.assist-actions [data-ask], #assistSend, #assistInput');
    Array.prototype.forEach.call(controls, function (c) { c.disabled = busy; });
  }

  function askAssist(preset, question) {
    if (assistBusy) return;
    setAssistBusy(true);

    var log  = $('assistLog');
    var turn = el('div', 'assist-turn');

    var label = preset === 'next'    ? 'מה השלב הבא בתיק'
              : preset === 'say'     ? 'מה כדאי לומר ללקוח'
              : preset === 'medical' ? 'אילו השלמות רפואיות חסרות'
              : question;

    turn.appendChild(el('p', 'assist-q', label));
    var answer = el('div', 'assist-a pending', 'מנתח את התיק...');
    turn.appendChild(answer);
    log.appendChild(turn);
    turn.scrollIntoView({ block: 'nearest' });

    // בקשה שנתקעת חייבת להשתחרר מעצמה - אחרת הצ'אט מת בשקט
    var abort = new AbortController();
    var timer = setTimeout(function () { abort.abort(); }, 120000);

    /* הבקשה הזו אינה עוברת דרך Api.request כי היא קוראת את
       התשובה כזרם, ולכן טוקן ה-CSRF חייב להיצרף כאן ידנית.
       בלעדיו השרת מחזיר 403 וכל הפאנל לא עבד. */
    fetch('/api/office/assist', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': Api.csrfToken() || ''
      },
      signal: abort.signal,
      body: JSON.stringify({
        case:     assistContext(),
        preset:   preset || '',
        question: question || '',
        history:  assistHistory
      })
    }).then(function (res) {
      var reader  = res.body.getReader();
      var decoder = new TextDecoder();
      var text    = '';
      answer.className = 'assist-a' + (res.ok ? '' : ' error');
      answer.textContent = '';

      function pump() {
        return reader.read().then(function (chunk) {
          if (chunk.done) {
            clearTimeout(timer);
            finishAssist(turn, answer, label, text, res.ok);
            return;
          }
          text += decoder.decode(chunk.value, { stream: true });
          answer.textContent = text;
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      clearTimeout(timer);
      answer.className = 'assist-a error';
      answer.textContent = err && err.name === 'AbortError'
        ? 'הניתוח לקח יותר מדי זמן והופסק. אפשר לנסות שוב.'
        : 'לא ניתן להגיע לשרת הניתוח. ודא שהשרת פועל (uvicorn server.app:app).';
      setAssistBusy(false);
    });
  }

  function finishAssist(turn, answer, label, text, ok) {
    setAssistBusy(false);
    if (!ok || !text) return;

    assistHistory.push({ role: 'user',      text: label });
    assistHistory.push({ role: 'assistant', text: text });

    // המודל לעולם לא שולח ללקוח. הוא רק ממלא טיוטה שעורך הדין עורך.
    var copy = el('button', 'btn btn-outline btn-sm assist-copy', 'העתקה לטיוטת הודעה');
    copy.type = 'button';
    copy.addEventListener('click', function () {
      $('msgBody').value = text;
      if (!$('msgTitle').value) $('msgTitle').value = 'עדכון בתיק שלך';
      $('msgTitle').focus();
      $('msgTitle').scrollIntoView({ block: 'center' });
      toast('הטיוטה הועתקה. יש לערוך ולשלוח ידנית.');
    });
    turn.appendChild(copy);
  }

  Array.prototype.forEach.call(
    document.querySelectorAll('.assist-actions [data-ask]'),
    function (btn) {
      btn.addEventListener('click', function () {
        askAssist(btn.getAttribute('data-ask'), '');
      });
    }
  );

  $('assistForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var q = $('assistInput').value.trim();
    if (!q) return;
    $('assistInput').value = '';
    askAssist('', q);
  });

  /* ================= משימות ומועדי גג =================
     המיון, המדרגות והמונים מגיעים מהשרת. הדפדפן אינו מחשב
     דחיפות ואינו ממיין מחדש - אחרת מסך "דורש טיפול" ומסך
     התיק היו יכולים לסטות זה מזה.

     כל מדרגה וכל דחיפות נושאות סמל *וטקסט*. הסמל aria-hidden,
     והמילים הן המידע. ==================================== */

  var BUCKETS = {
    'overdue':  { tag: 'tag-overdue', mark: '✗', text: 'באיחור' },
    'critical': { tag: 'tag-stop',    mark: '!', text: 'קריטי' },
    'warning':  { tag: 'tag-warn',    mark: '●', text: 'אזהרה' },
    'normal':   { tag: 'tag-wait',    mark: '●', text: 'רגיל' },
    'later':    { tag: 'tag-wait',    mark: '○', text: 'בהמשך' },
    'closed':   { tag: 'tag-ok',      mark: '✓', text: 'נסגרה' }
  };

  var PRIORITIES = {
    'critical': { tag: 'tag-stop', mark: '!', text: 'דחיפות קריטית' },
    'high':     { tag: 'tag-warn', mark: '▲', text: 'דחיפות גבוהה' },
    'normal':   { tag: 'tag-wait', mark: '●', text: 'דחיפות רגילה' },
    'low':      { tag: 'tag-wait', mark: '▽', text: 'דחיפות נמוכה' }
  };

  var TASK_STATUS = {
    'open':           'פתוחה',
    'in_progress':    'בטיפול',
    'waiting_client': 'ממתין ללקוח',
    'done':           'הושלמה',
    'cancelled':      'בוטלה'
  };

  /* חמשת השבבים. count הוא המפתח במונים שהשרת מחזיר, ו-match
     מסנן את המערך שכבר נטען - בלי קריאה חדשה לשרת. */
  var CHIPS = [
    { key: 'critical', label: 'קריטי', count: 'critical',
      match: function (t) {
        return t.bucket !== 'closed' &&
               (t.priority === 'critical' || t.daysLeft <= 3);
      } },
    { key: 'today', label: 'להיום', count: 'today',
      match: function (t) { return t.bucket !== 'closed' && t.daysLeft === 0; } },
    { key: 'week', label: 'השבוע', count: 'week',
      match: function (t) {
        return t.bucket !== 'closed' && t.daysLeft >= 0 && t.daysLeft <= 7;
      } },
    { key: 'waiting', label: 'ממתין ללקוח', count: 'waitingClient',
      match: function (t) { return t.status === 'waiting_client'; } },
    { key: 'overdue', label: 'באיחור', count: 'overdue',
      match: function (t) { return t.isOverdue; } }
  ];

  var bannerOpen  = false;  /* הבאנר נפתח בלחיצה ונשאר פתוח */
  var attention   = null;   /* התשובה האחרונה מהשרת */
  var chipFilter  = null;   /* השבב הנבחר, או null */
  var taskCatalog = null;   /* סוגי המשימות, נטענים פעם אחת */
  var staffList   = null;   /* אנשי הצוות, לבחירת אחראי */
  var editingTask = null;   /* המשימה שבעריכה, או null ליצירה */

  function bucketOf(value) {
    return BUCKETS[value] || { tag: 'tag-wait', mark: '?', text: value || 'לא ידוע' };
  }

  function priorityOf(value) {
    return PRIORITIES[value] ||
           { tag: 'tag-wait', mark: '?', text: value || 'לא ידוע' };
  }

  /** תג סטטוס: סמל aria-hidden ולצידו טקסט גלוי. */
  function statusTag(spec) {
    var tag = el('span', 'tag ' + spec.tag);
    tag.appendChild(el('span', null, spec.mark, true));
    tag.appendChild(document.createTextNode(spec.text));
    return tag;
  }

  /** כמה זמן נותר, במילים. שלילי = המועד חלף. */
  function timeLeft(t) {
    var d = t.daysLeft;
    if (t.isOverdue) {
      if (d === 0)  return 'המועד חלף היום';
      if (d === -1) return 'באיחור של יום אחד';
      return 'באיחור של ' + (-d) + ' ימים';
    }
    if (d === 0) return 'המועד היום';
    if (d === 1) return 'נותר יום אחד';
    return 'נותרו ' + d + ' ימים';
  }

  function loadLookups() {
    if (taskCatalog && staffList) return Promise.resolve();
    return Promise.all([Api.taskTypes(), Api.officeStaff()])
      .then(function (res) {
        taskCatalog = res[0].taskTypes;
        staffList   = res[1].staff;

        var type = $('taskType');
        type.textContent = '';
        taskCatalog.forEach(function (tt) {
          var o = el('option', null, tt.name);
          o.value = tt.id;
          type.appendChild(o);
        });

        [$('taskAssignee'), $('attentionAssignee')].forEach(function (sel) {
          /* האפשרות הראשונה נשמרת מה-HTML: "ללא אחראי" בטופס,
             "כל המשרד" בסינון. */
          while (sel.options.length > 1) sel.remove(1);
          staffList.forEach(function (s) {
            var o = el('option', null, s.name);
            o.value = s.id;
            sel.appendChild(o);
          });
        });
      });
  }

  /* ---- "דורש טיפול" ---- */

  function loadAttention() {
    return loadLookups().then(function () {
      return Api.officeTasks({
        assignee: $('attentionAssignee').value || null,
        includeDone: $('attentionDone').checked
      });
    }).then(function (data) {
      attention = data;
      renderChips(data.counts);
      renderAttention();
      renderBanner(data.banner);
    }).catch(function (err) {
      attention = null;
      var list = $('attentionList');
      list.textContent = '';
      list.appendChild(el('li', 'item-note',
        err.message || 'לא הצלחנו לטעון את המשימות.'));
      $('attentionScope').textContent = '';
    });
  }

  function renderChips(counts) {
    var box = $('attentionChips');
    box.textContent = '';
    CHIPS.forEach(function (chip) {
      var n = counts[chip.count] || 0;
      var btn = el('button', 'chip' + (n ? '' : ' chip-zero'));
      btn.type = 'button';
      btn.setAttribute('data-chip', chip.key);
      btn.appendChild(document.createTextNode(chip.label));
      btn.appendChild(el('span', 'chip-n', String(n)));
      btn.addEventListener('click', function () {
        chipFilter = (chipFilter === chip.key) ? null : chip.key;
        syncChips();
        renderAttention();
      });
      box.appendChild(btn);
    });
    syncChips();
  }

  /** מסמן את השבב הנבחר בלי לבנות את הכפתורים מחדש.
      בנייה מחדש הייתה מוחקת את הכפתור שנלחץ, וכל הפעלה
      מהמקלדת הייתה מאבדת את המיקוד לתוך ה-body. */
  function syncChips() {
    var chips = $('attentionChips').querySelectorAll('.chip');
    Array.prototype.forEach.call(chips, function (btn) {
      btn.setAttribute('aria-pressed',
        String(btn.getAttribute('data-chip') === chipFilter));
    });
  }

  function renderAttention() {
    var list = $('attentionList');
    list.textContent = '';
    if (!attention) return;

    var chip  = null;
    var i;
    for (i = 0; i < CHIPS.length; i++) {
      if (CHIPS[i].key === chipFilter) chip = CHIPS[i];
    }
    var shown = chip ? attention.tasks.filter(chip.match) : attention.tasks;

    var who = $('attentionAssignee');
    var scope = who.value
      ? 'האחראי: ' + who.options[who.selectedIndex].text
      : 'כל המשרד';
    $('attentionScope').textContent = shown.length === 0
      ? scope + ' · אין משימות בתצוגה'
      : scope + ' · ' + (shown.length === 1 ? 'משימה אחת' :
                         shown.length + ' משימות') +
        (chip ? ' בסינון "' + chip.label + '"' : '');

    if (!shown.length) {
      list.appendChild(el('li', 'item-note', chip
        ? 'אין משימות בקטגוריה "' + chip.label + '".'
        : 'אין משימות פתוחות. כל המועדים מטופלים.'));
      announce('אין משימות בתצוגה.');
      return;
    }

    shown.forEach(function (t) { list.appendChild(taskRow(t, false)); });
    announce((chip ? 'סינון ' + chip.label + ': ' : '') +
             shown.length + ' משימות בתצוגה.');
  }

  /* ---- הבאנר ---- */

  function loadBanner() {
    return Api.officeTasks().then(function (data) {
      renderBanner(data.banner);
    }).catch(function () { /* הבאנר אינו חוסם את תצוגת התיק */ });
  }

  function renderBanner(items) {
    var box  = $('deadlineBanner');
    var body = $('bannerBody');
    body.textContent = '';

    if (!items || !items.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;

    /* הראשון הוא הדחוף ביותר - המיון כבר נעשה בשרת.
       החלקים מופרדים ב-"·" ולא נתפרים למשפט אחד, כי כותרת
       משימה היא טקסט חופשי שהצוות מקליד ולא בהכרח נסמכת. */
    var top = items[0];

    /* השורה הסגורה: מה הכי דחוף וכמה יש. הבאנר מופיע בכל מסך,
       ולכן במצב סגור הוא שורה ולא בלוק. */
    var bar  = el('div', 'banner-bar');
    var lead = el('p', 'banner-lead');
    lead.appendChild(el('span', 'banner-mark',
                        top.isOverdue ? '✗' : '!', true));
    lead.appendChild(document.createTextNode(
      top.isOverdue ? 'באיחור' : 'דחוף'));
    lead.appendChild(el('span', 'banner-when', '— ' + timeLeft(top)));
    lead.appendChild(el('span', 'banner-text', '· ' + top.title));
    lead.appendChild(el('span', 'banner-text', '· ' + top.clientName));
    bar.appendChild(lead);

    var details = el('div', 'banner-details');
    details.id = 'bannerDetails';
    details.hidden = !bannerOpen;

    var toggle = el('button', 'banner-toggle');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', String(bannerOpen));
    toggle.setAttribute('aria-controls', 'bannerDetails');
    var label = items.length === 1
      ? 'פרטים'
      : 'עוד ' + (items.length - 1);
    toggle.appendChild(document.createTextNode(
      (bannerOpen ? 'סגירה' : label)));
    toggle.appendChild(el('span', 'caret', '▾', true));
    toggle.addEventListener('click', function () {
      bannerOpen = !bannerOpen;
      renderBanner(items);
      /* הכפתור נבנה מחדש, ולכן המיקוד מוחזר אליו במפורש -
         אחרת הפעלה מהמקלדת הייתה נזרקת ל-body. */
      var again = document.querySelector('.banner-toggle');
      if (again) again.focus();
      announce(bannerOpen ? 'פרטי המועדים נפתחו.' : 'פרטי המועדים נסגרו.');
    });
    bar.appendChild(toggle);
    body.appendChild(bar);

    if (top.isLegalDeadline) {
      details.appendChild(el('p', 'task-legal-note',
        'מועד משפטי מחייב · מקור: ' + (top.deadlineSource || '-')));
    }

    if (items.length > 1) {
      var more = el('div', 'banner-more');
      more.appendChild(el('p', null, items.length === 2
        ? 'מועד קריטי נוסף:'
        : 'עוד ' + (items.length - 1) + ' מועדים קריטיים:'));
      var ul = document.createElement('ul');
      items.slice(1).forEach(function (t) {
        ul.appendChild(el('li', null,
          timeLeft(t) + ' · ' + t.title + ' · ' + t.clientName));
      });
      more.appendChild(ul);
      details.appendChild(more);
    }

    var go = el('div', 'banner-go');
    var btn = el('button', 'btn btn-primary btn-sm', 'פתיחת התיק הדחוף');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'פתיחת התיק של ' + top.clientName);
    btn.addEventListener('click', function () { openCase(top.caseId); });
    go.appendChild(btn);
    details.appendChild(go);

    body.appendChild(details);
  }

  /* ---- שורת משימה ---- */

  function taskRow(t, allowEdit) {
    var li = el('li');
    var bar = el('div', 'task-bar b-' + t.bucket);
    var row = el('div', 'task-row');

    var main = el('div', 'task-main');
    var tags = el('div', 'task-tags');
    tags.appendChild(statusTag(bucketOf(t.bucket)));
    tags.appendChild(statusTag(priorityOf(t.priority)));
    if (t.isLegalDeadline) {
      tags.appendChild(statusTag({ tag: 'tag-legal', mark: '§',
                                   text: 'מועד משפטי' }));
    }
    main.appendChild(tags);

    main.appendChild(el('p', 'task-title' +
      (t.bucket === 'closed' ? ' task-closed' : ''), t.title));

    var where = el('p', 'task-where');
    where.appendChild(document.createTextNode(t.taskType + ' · ' +
                                              t.clientName + ' · '));
    where.appendChild(el('span', 'num', t.caseNumber));
    where.appendChild(document.createTextNode(' · ' +
      (TASK_STATUS[t.status] || t.status) +
      ' · ' + (t.assignee || 'ללא אחראי')));
    main.appendChild(where);

    if (t.description) {
      main.appendChild(el('p', 'task-desc', t.description));
    }

    var when = el('p', 'task-when');
    when.appendChild(el('span', 'num', stamp(t.dueAt)));
    when.appendChild(document.createTextNode(' · ' + timeLeft(t)));
    main.appendChild(when);

    if (t.isLegalDeadline && t.deadlineSource) {
      main.appendChild(el('p', 'task-legal-note',
        'מקור המועד: ' + t.deadlineSource +
        (t.confirmedBy ? ' · אושר בידי ' + t.confirmedBy : '')));
    }
    if (t.completedAt) {
      main.appendChild(el('p', 'task-legal-note',
        'הושלמה ' + stamp(t.completedAt) +
        (t.completedBy ? ' בידי ' + t.completedBy : '')));
    }

    row.appendChild(main);

    var side = el('div', 'task-side');
    var actions = el('div', 'task-actions');

    if (t.status === 'done' || t.status === 'cancelled') {
      actions.appendChild(taskButton('פתיחה מחדש', 'btn-outline', t,
        'פתיחה מחדש של המשימה ' + t.title,
        function () { return Api.reopenTask(t.id); },
        'המשימה נפתחה מחדש.'));
    } else {
      actions.appendChild(taskButton('סימון הושלמה', 'btn-ok', t,
        'סימון המשימה ' + t.title + ' כהושלמה',
        function () { return Api.completeTask(t.id); },
        'המשימה סומנה כהושלמה ונשמרה בהיסטוריה.'));
    }

    if (allowEdit) {
      var edit = el('button', 'btn btn-outline btn-sm', 'עריכה');
      edit.type = 'button';
      edit.setAttribute('aria-label', 'עריכת המשימה ' + t.title);
      edit.addEventListener('click', function () { openTaskForm(t); });
      actions.appendChild(edit);
    } else {
      var open = el('button', 'btn btn-outline btn-sm', 'פתיחת התיק');
      open.type = 'button';
      open.setAttribute('aria-label', 'פתיחת התיק של ' + t.clientName);
      open.addEventListener('click', function () { openCase(t.caseId); });
      actions.appendChild(open);
    }

    side.appendChild(actions);
    row.appendChild(side);
    bar.appendChild(row);
    li.appendChild(bar);
    return li;
  }

  /** כפתור פעולה שמרענן את המסך שממנו נלחץ. */
  function taskButton(label, kind, t, aria, action, okMessage) {
    var btn = el('button', 'btn ' + kind + ' btn-sm', label);
    btn.type = 'button';
    btn.setAttribute('aria-label', aria);
    btn.addEventListener('click', function () {
      btn.disabled = true;
      action()
        .then(function () { return refreshTasks(); })
        .then(function () { toast(okMessage); })
        .catch(function (err) {
          btn.disabled = false;
          toast(err.message || 'הפעולה נכשלה.');
        });
    });
    return btn;
  }

  /** מרענן את מה שמוצג כרגע: רשימת התיק, או "דורש טיפול".
      גם היומן מתרענן - פעולת משימה נרשמת בו, ואם הוא לא ייטען
      מחדש הוא יציג מצב שכבר אינו נכון. */
  function refreshTasks() {
    if (openId) {
      return renderCaseTasks()
        .then(loadBanner)
        .then(function () { return renderLog(openId); });
    }
    return loadAttention();
  }

  /* ---- משימות התיק ---- */

  function renderCaseTasks() {
    var list = $('taskList');
    if (!openId) return Promise.resolve();
    return loadLookups().then(function () {
      return Api.caseTasks(openId);
    }).then(function (data) {
      list.textContent = '';
      var open = data.tasks.filter(function (t) {
        return t.status !== 'done' && t.status !== 'cancelled';
      }).length;

      $('taskIntro').textContent = data.tasks.length === 0
        ? 'אין משימות בתיק הזה.'
        : (open === 0 ? 'כל המשימות בתיק טופלו. '
                      : (open === 1 ? 'משימה אחת פתוחה. '
                                    : open + ' משימות פתוחות. ')) +
          'ההיסטוריה נשמרת במלואה.';

      data.tasks.forEach(function (t) {
        list.appendChild(taskRow(t, true));
      });
    }).catch(function (err) {
      list.textContent = '';
      list.appendChild(el('li', 'item-note',
        err.message || 'לא הצלחנו לטעון את משימות התיק.'));
    });
  }

  /* ---- הטופס ---- */

  var taskForm = $('taskForm');

  function taskFail(text, focusOn) {
    var box = $('taskError');
    box.textContent = text;
    box.hidden = false;
    if (focusOn) focusOn.focus();
  }

  function setLegalPanel(on) {
    $('taskLegalPanel').hidden = !on;
    $('taskLegal').setAttribute('aria-expanded', String(on));
  }

  function openTaskForm(t) {
    editingTask = t || null;
    $('taskError').hidden = true;
    taskForm.hidden = false;
    $('taskNew').setAttribute('aria-expanded', 'true');
    $('taskSave').textContent = t ? 'שמירת השינויים' : 'יצירת המשימה';

    $('taskTitle').value    = t ? t.title : '';
    $('taskDesc').value     = t && t.description ? t.description : '';
    /* dueAt חוזר עם אזור זמן; datetime-local רוצה זמן מקומי בלי
       אזור, וזה בדיוק 16 התווים הראשונים. */
    $('taskDue').value      = t ? t.dueAt.slice(0, 16) : '';
    $('taskPriority').value = t ? t.priority : 'normal';
    $('taskStatus').value   = t && t.status !== 'done' ? t.status : 'open';
    $('taskType').value     = t ? t.taskTypeId :
                              (taskCatalog.length ? taskCatalog[0].id : '');
    $('taskAssignee').value = t && t.assigneeId ? t.assigneeId : '';

    $('taskLegal').checked  = !!(t && t.isLegalDeadline);
    $('taskSource').value   = t && t.deadlineSource ? t.deadlineSource : '';
    /* אישור לעולם אינו מסומן מראש. מועד משפטי נשמר רק כשאדם
       מסמן את התיבה באותו מסך, גם בעריכה של מועד שאושר קודם. */
    $('taskConfirm').checked = false;
    setLegalPanel($('taskLegal').checked);

    $('taskTitle').focus();
  }

  function closeTaskForm() {
    editingTask = null;
    taskForm.reset();
    taskForm.hidden = true;
    setLegalPanel(false);
    $('taskError').hidden = true;
    $('taskNew').setAttribute('aria-expanded', 'false');
    $('taskNew').focus();
  }

  $('taskNew').addEventListener('click', function () {
    if (taskForm.hidden) openTaskForm(null);
    else closeTaskForm();
  });

  $('taskCancel').addEventListener('click', closeTaskForm);

  $('taskLegal').addEventListener('change', function () {
    setLegalPanel($('taskLegal').checked);
    if ($('taskLegal').checked) $('taskSource').focus();
  });

  taskForm.addEventListener('submit', function (e) {
    e.preventDefault();
    $('taskError').hidden = true;

    var title = $('taskTitle').value.trim();
    var due   = $('taskDue').value;
    var legal = $('taskLegal').checked;

    if (title.length < 2) return taskFail('צריך כותרת למשימה.', $('taskTitle'));
    if (!due)             return taskFail('צריך תאריך ושעת יעד.', $('taskDue'));
    if (!$('taskType').value) {
      return taskFail('צריך לבחור סוג משימה.', $('taskType'));
    }
    /* אותן שתי בדיקות נאכפות גם בשרת וגם באילוץ במסד. כאן הן
       רק כדי שההודעה תגיע מיד ובלי סבב לשרת. */
    if (legal && $('taskSource').value.trim().length < 5) {
      return taskFail('מועד משפטי מחייב ציון מקור - מאיזה מכתב או ' +
                      'החלטה נגזר התאריך.', $('taskSource'));
    }
    if (legal && !$('taskConfirm').checked) {
      return taskFail('מועד משפטי מחייב אישור מפורש. המערכת אינה ' +
                      'קובעת מועדים משפטיים בעצמה.', $('taskConfirm'));
    }

    var body = {
      task_type_id: $('taskType').value,
      title: title,
      description: $('taskDesc').value.trim() || null,
      due_at: due,
      priority: $('taskPriority').value,
      status: $('taskStatus').value,
      assignee_user_id: $('taskAssignee').value || null,
      is_legal_deadline: legal,
      deadline_source: legal ? $('taskSource').value.trim() : null,
      confirm_legal_deadline: legal && $('taskConfirm').checked
    };

    var saving = editingTask
      ? Api.updateTask(editingTask.id, body)
      : Api.createTask(openId, body);
    var isEdit = !!editingTask;

    saving
      .then(function (res) {
        closeTaskForm();
        return refreshTasks().then(function () {
          toast(isEdit
            ? (res && res.dueChanged
                ? 'המשימה עודכנה. הזזת המועד נרשמה ביומן.'
                : 'המשימה עודכנה.')
            : 'המשימה נוצרה.');
        });
      })
      .catch(function (err) {
        taskFail(err.message || 'שמירת המשימה נכשלה.', $('taskTitle'));
      });
  });

  $('attentionAssignee').addEventListener('change', loadAttention);
  $('attentionDone').addEventListener('change', loadAttention);


  /* ================= עזרים ================= */

  /** ממיר 2026-09-22 ל-22.09.2026 */
  function formatDate(iso) {
    if (!iso) return '';
    var p = iso.split('-');
    return p[2] + '.' + p[1] + '.' + p[0];
  }

  /** חותמת זמן מלאה ליומן הפעולות */
  function stamp(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    var pad = function (n) { return n < 10 ? '0' + n : String(n); };
    return pad(d.getDate()) + '.' + pad(d.getMonth() + 1) + '.' + d.getFullYear() +
           ' בשעה ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  /** הכרזה לקורא מסך, בדפוס #viewAnnounce שבאזור הלקוח.
      שינוי סינון אינו מזיז מיקוד, ולכן בלעדיה הוא היה שקט. */
  function announce(msg) {
    var box = $('adminAnnounce');
    if (box) box.textContent = msg;
  }

  var toastTimer = null;
  function toast(msg) {
    var box = $('toast');
    box.textContent = msg;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { box.hidden = true; }, 5000);
  }

  function el(tag, className, text, decorative) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    if (decorative) node.setAttribute('aria-hidden', 'true');
    return node;
  }

  /* ---- מי שכבר מחובר נכנס ישירות ----
     השרת מחליט, לא הדפדפן. 401 פשוט משאיר את מסך הכניסה. */
  Api.me({ allowUnauthorized: true })
     .then(function (who) { if (who && who.type === 'user') start(); })
     .catch(function () { /* לא מחובר */ });
})();
