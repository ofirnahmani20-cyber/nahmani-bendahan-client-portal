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
      return loginFail('צריך להזין שם משתמש וסיסמה.');
    }
    if (!StaffAuth.login(u, p)) {
      return loginFail('שם המשתמש או הסיסמה אינם נכונים.');
    }
    start();
  });

  function loginFail(msg) {
    loginErr.textContent = msg;
    loginErr.hidden = false;
    $('staffUser').focus();
  }

  $('staffLogout').addEventListener('click', function () { StaffAuth.logout(); });

  /* ================= הפעלה ================= */

  function start() {
    staff = StaffAuth.current();
    if (!staff) return;

    $('loginView').hidden = true;
    $('appView').hidden   = false;
    $('staffName').textContent = staff.name + ' · ' + staff.role;

    showList();
  }

  /* ================= רשימת התיקים ================= */

  function showList() {
    openId = null;
    $('listView').hidden = false;
    $('caseView').hidden = true;
    $('backBtn').hidden  = true;

    var cases   = CaseStore.list();
    var waiting = cases.reduce(function (n, c) { return n + c.waiting; }, 0);

    $('listSummary').textContent = waiting === 0
      ? cases.length + ' תיקים פעילים. אין מסמכים שממתינים לבדיקה.'
      : cases.length + ' תיקים פעילים · ' +
        (waiting === 1 ? 'מסמך אחד ממתין' : waiting + ' מסמכים ממתינים') + ' לבדיקת המשרד.';

    var rows = $('caseRows');
    rows.textContent = '';

    cases.forEach(function (c) {
      var tr = document.createElement('tr');

      var who = document.createElement('td');
      who.appendChild(el('div', 'client', c.name));
      who.appendChild(el('div', 'sub num', 'ת״ז ' + c.idNumber + ' · ' + c.phone));
      tr.appendChild(who);

      tr.appendChild(cell(c.caseNumber, 'num'));
      tr.appendChild(cell(c.claimType));

      var stage = document.createElement('td');
      stage.appendChild(el('div', null, c.currentStage + ' מתוך ' + CLAIM_STAGES.length));
      stage.appendChild(el('div', 'sub', c.stageTitle));
      tr.appendChild(stage);

      tr.appendChild(countCell(c.waiting, c.waiting > 0 ? 'hot' : 'calm'));
      tr.appendChild(countCell(c.open,    c.open    > 0 ? 'hot' : 'calm'));

      var act = document.createElement('td');
      var btn = el('button', 'btn btn-outline btn-sm', 'פתיחת התיק');
      btn.type = 'button';
      btn.setAttribute('aria-label', 'פתיחת התיק של ' + c.name);
      btn.addEventListener('click', function () { showCase(c.idNumber); });
      act.appendChild(btn);
      tr.appendChild(act);

      rows.appendChild(tr);
    });

    renderLog();
  }

  function cell(text, cls) {
    var td = document.createElement('td');
    if (cls) td.className = cls;
    td.textContent = text;
    return td;
  }

  function countCell(n, tone) {
    var td = document.createElement('td');
    td.appendChild(el('span', 'count ' + tone, String(n)));
    return td;
  }

  /* ================= יומן פעולות ================= */

  function renderLog() {
    var log  = CaseStore.log();
    var list = $('logList');
    list.textContent = '';

    $('logIntro').textContent = log.length === 0
      ? ''
      : 'כל שינוי שהצוות מבצע נרשם כאן. מוצגות ' + Math.min(log.length, 20) + ' הפעולות האחרונות.';

    if (log.length === 0) {
      list.appendChild(el('li', 'empty', 'עוד לא בוצעו פעולות במערכת.'));
      return;
    }

    log.slice(0, 20).forEach(function (entry) {
      var li = el('li');
      li.appendChild(el('div', 'log-when', stamp(entry.at)));

      var line = el('p', 'log-text');
      line.appendChild(el('b', null, entry.staff));
      line.appendChild(document.createTextNode(
        ' ' + entry.text + ' · בתיק של ' + entry.client));
      li.appendChild(line);

      list.appendChild(li);
    });
  }

  /* ================= תיק בודד ================= */

  function showCase(idNumber) {
    openId = idNumber;
    $('listView').hidden = true;
    $('caseView').hidden = false;
    $('backBtn').hidden  = false;

    renderCase();
    $('caseClient').focus();
  }

  $('backBtn').addEventListener('click', showList);

  function renderCase() {
    var file   = CaseStore.load(openId);
    var client = DEMO_CLIENTS[openId].profile;

    var head = $('caseClient');
    head.textContent = client.name;
    head.tabIndex = -1;

    $('caseMeta').textContent =
      'תיק ' + file.caseNumber + ' · ' + file.claimType + ' · ' + file.branch +
      ' · נפתח ב-' + formatDate(file.openedAt) + ' · מטפל: ' + file.lawyer.name;

    renderStage(file);
    renderReview(file);
    renderSent(file);
  }

  /* ---- שלב התביעה ---- */

  function renderStage(file) {
    $('stageNow').textContent =
      'כרגע: שלב ' + file.currentStage + ' מתוך ' + CLAIM_STAGES.length +
      ' - ' + CLAIM_STAGES[file.currentStage - 1].title +
      ' (מאז ' + formatDate(file.stageEnteredAt) + ').';

    var sel = $('stageSelect');
    sel.textContent = '';

    CLAIM_STAGES.forEach(function (stage) {
      var opt = document.createElement('option');
      opt.value = String(stage.id);
      opt.textContent = stage.id + '. ' + stage.title;
      if (stage.id === file.currentStage) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  $('stageSave').addEventListener('click', function () {
    var stage = parseInt($('stageSelect').value, 10);
    var file  = CaseStore.load(openId);

    if (stage === file.currentStage) {
      return toast('התיק כבר נמצא בשלב הזה.');
    }

    CaseStore.setStage(openId, stage, staff.name);
    renderCase();
    toast('התיק עודכן לשלב ' + stage + ' - ' + CLAIM_STAGES[stage - 1].title + '.');
  });

  /* ---- בדיקת מסמכים ---- */

  var REVIEW_STATUS = {
    'approved':       { tag: 'tag-ok',   mark: '✓', text: 'אושר' },
    'pending-review': { tag: 'tag-warn', mark: '●', text: 'ממתין לבדיקה' },
    'missing':        { tag: 'tag-wait', mark: '!', text: 'הלקוח עוד לא העלה' },
    'rejected':       { tag: 'tag-stop', mark: '✗', text: 'נדחה - הלקוח התבקש להעלות מחדש' }
  };

  function renderReview(file) {
    // מה שממתין לבדיקה קודם - זו העבודה הפתוחה של הצוות
    var order = { 'pending-review': 0, 'rejected': 1, 'missing': 2, 'approved': 3 };
    var docs  = file.documents.slice().sort(function (a, b) {
      return order[a.status] - order[b.status];
    });

    var waiting = docs.filter(function (d) { return d.status === 'pending-review'; }).length;
    $('reviewIntro').textContent = waiting === 0
      ? 'אין מסמכים שממתינים לבדיקה בתיק הזה.'
      : (waiting === 1 ? 'מסמך אחד ממתין' : waiting + ' מסמכים ממתינים') + ' לבדיקה שלך.';

    var list = $('reviewList');
    list.textContent = '';

    docs.forEach(function (doc) {
      var s  = REVIEW_STATUS[doc.status];
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
      if (doc.status === 'pending-review') {
        li.appendChild(reviewControls(doc));
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
      CaseStore.reviewDocument(openId, doc.id, 'approved', null, staff.name);
      renderCase();
      toast('המסמך "' + doc.name + '" אושר.');
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
      CaseStore.reviewDocument(openId, doc.id, 'rejected', reason, staff.name);
      renderCase();
      toast('המסמך "' + doc.name + '" נדחה והסיבה נשלחה ללקוח.');
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

  /* ---- שליחת עדכון ללקוח ---- */

  var msgForm = $('msgForm');
  var msgErr  = $('msgError');

  msgForm.addEventListener('submit', function (e) {
    e.preventDefault();

    var title = $('msgTitle').value.trim();
    var body  = $('msgBody').value.trim();

    msgErr.hidden = true;

    if (!title)           return msgFail('צריך כותרת להודעה.', $('msgTitle'));
    if (body.length < 10) return msgFail('תוכן ההודעה קצר מדי.', $('msgBody'));

    CaseStore.addMessage(openId, {
      title:     title,
      body:      body,
      important: $('msgImportant').checked
    }, staff.name);

    msgForm.reset();
    renderCase();
    toast('העדכון נשלח ללקוח.');
  });

  function msgFail(text, focusOn) {
    msgErr.textContent = text;
    msgErr.hidden = false;
    focusOn.focus();
  }

  function renderSent(file) {
    var list = $('sentList');
    list.textContent = '';

    if (!file.messages.length) {
      list.appendChild(el('li', 'empty', 'עוד לא נשלחו עדכונים בתיק הזה.'));
      return;
    }

    file.messages.forEach(function (msg) {
      var li = el('li');
      if (msg.important) {
        var tag = el('span', 'tag tag-warn');
        tag.appendChild(el('span', null, '!', true));
        tag.appendChild(document.createTextNode('עדכון חשוב'));
        li.appendChild(tag);
      }
      li.appendChild(el('h3', null, msg.title));
      li.appendChild(el('p', 'item-note', msg.body));
      li.appendChild(el('p', 'item-when',
        formatDate(msg.date) + (msg.from ? ' · מאת ' + msg.from : '')));
      list.appendChild(li);
    });
  }

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

  /* ---- מי שכבר מחובר נכנס ישירות ---- */
  if (StaffAuth.current()) start();
})();
