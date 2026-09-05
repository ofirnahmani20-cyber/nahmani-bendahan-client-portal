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
      tr.appendChild(countCell(c.unread,  c.unread  > 0 ? 'hot' : 'calm'));
      tr.appendChild(countCell(c.blocking, c.blocking > 0 ? 'hot' : 'calm'));

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

    // הניתוח שייך לתיק - אסור שישאר על המסך כשעוברים לתיק אחר
    assistHistory = [];
    $('assistLog').textContent = '';
    $('assistInput').value = '';

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

    renderGap(file);
    renderStage(file);
    renderCatalog(file);
    renderReview(file);
    renderReplies();
    renderSent(file);
  }

  /* ---- מצב טכני: מה חוסם ומה חסר ---- */

  function renderGap(file) {
    var gap  = analyzeGap(file);
    var body = $('gapBody');
    body.textContent = '';

    if (!gap.hasRules) {
      body.appendChild(el('p', 'empty',
        'לא הוגדרו דרישות מסמכים לסוג התביעה "' + file.claimType + '".'));
      return;
    }

    var head = el('p', 'gap-head');
    if (gap.canAdvance) {
      head.className += ' ok';
      head.textContent = 'כל המסמכים הנדרשים לשלב ' + gap.stage + ' התקבלו. אין חסם טכני להתקדמות.';
    } else {
      head.className += ' stop';
      head.textContent = gap.blocking.length === 1
        ? 'מסמך אחד חוסם את המעבר מהשלב הנוכחי.'
        : gap.blocking.length + ' מסמכים חוסמים את המעבר מהשלב הנוכחי.';
    }
    body.appendChild(head);

    if (gap.blocking.length) {
      body.appendChild(gapList('חוסם עכשיו', gap.blocking, 'stop'));
    }
    if (gap.inReview.length) {
      body.appendChild(gapList('נדרש, ואצלנו בבדיקה', gap.inReview, 'wait'));
    }
    if (gap.upcoming.length) {
      body.appendChild(gapList('ייחסם בשלבים הבאים', gap.upcoming, 'soon'));
    }
  }

  function gapList(title, items, tone) {
    var wrap = el('div', 'gap-group ' + tone);
    wrap.appendChild(el('h3', null, title));

    var ul = el('ul', 'gap-items');
    items.forEach(function (item) {
      var li = el('li');
      li.appendChild(el('span', 'gap-name', item.name));

      var why = item.state === 'not-requested' ? 'עוד לא נדרש מהלקוח'
              : item.state === 'in-review'     ? 'הועלה וממתין לבדיקה'
              : item.doc && item.doc.status === 'rejected' ? 'נדחה - הלקוח התבקש להעלות מחדש'
              : 'הלקוח עוד לא העלה';
      if (item.stage) why += ' · נדרש בשלב ' + item.stage;
      li.appendChild(el('span', 'gap-why', why));

      // מסמך שכלל לא נדרש - קיצור דרך לדרוש אותו
      if (item.state === 'not-requested') {
        var btn = el('button', 'btn btn-outline btn-sm gap-req', 'דרישה מהלקוח');
        btn.type = 'button';
        btn.addEventListener('click', function () { prefillRequest(item.name); });
        li.appendChild(btn);
      }
      ul.appendChild(li);
    });

    wrap.appendChild(ul);
    return wrap;
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

  /* ---- דרישת מסמך מהלקוח ---- */

  /** ממלא את הקטלוג של סוג התביעה, בלי מסמכים שכבר קיימים בתיק */
  function renderCatalog(file) {
    var catalog = REQUIRED_DOC_CATALOG[file.claimType] || [];
    var taken   = {};
    file.documents.forEach(function (d) { taken[d.name] = true; });

    var sel = $('reqPick');
    sel.textContent = '';

    var blank = document.createElement('option');
    blank.value = '';
    blank.textContent = catalog.length ? '— בחירה מהקטלוג או מילוי ידני —' : '— אין קטלוג לסוג תביעה זה —';
    sel.appendChild(blank);

    catalog.forEach(function (entry, i) {
      if (taken[entry.name]) return;          // כבר בתיק - אין טעם לדרוש שוב
      var opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = entry.name;
      sel.appendChild(opt);
    });
  }

  // בחירה מהקטלוג ממלאת את השדות, ואפשר עדיין לערוך אותם
  $('reqPick').addEventListener('change', function () {
    var file = CaseStore.load(openId);
    var entry = (REQUIRED_DOC_CATALOG[file.claimType] || [])[parseInt(this.value, 10)];
    if (!entry) return;
    $('reqName').value     = entry.name;
    $('reqNote').value     = entry.note;
    $('reqRequired').checked = entry.required;
  });

  /** ממלא את הטופס משם מסמך שהגיע מניתוח הפער */
  function prefillRequest(name) {
    var file  = CaseStore.load(openId);
    var entry = (REQUIRED_DOC_CATALOG[file.claimType] || [])
                  .find(function (e) { return e.name === name; });

    $('reqName').value       = name;
    $('reqNote').value       = entry ? entry.note : '';
    $('reqRequired').checked = entry ? entry.required : true;
    $('reqName').focus();
    $('reqName').scrollIntoView({ block: 'center' });
  }

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

    var file = CaseStore.load(openId);
    if (file.documents.some(function (d) { return d.name === name; })) {
      return reqFail('המסמך "' + name + '" כבר קיים בתיק.', $('reqName'));
    }

    CaseStore.addDocument(openId, {
      name:     name,
      note:     note,
      required: $('reqRequired').checked
    }, staff.name);

    reqForm.reset();
    $('reqRequired').checked = true;
    renderCase();
    toast('הדרישה נשלחה. המסמך מופיע כעת אצל הלקוח.');
  });

  function reqFail(text, focusOn) {
    reqErr.textContent = text;
    reqErr.hidden = false;
    focusOn.focus();
  }

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

      // דרישה שהמשרד הוסיף וטרם נענתה - אפשר לבטל אותה
      if (doc.id.indexOf('req-') === 0 && doc.status === 'missing') {
        var cancel = el('button', 'btn btn-outline btn-sm doc-actions', 'ביטול הדרישה');
        cancel.type = 'button';
        cancel.setAttribute('aria-label', 'ביטול הדרישה למסמך ' + doc.name);
        cancel.addEventListener('click', function () {
          CaseStore.removeDocument(openId, doc.id, staff.name);
          renderCase();
          toast('הדרישה למסמך "' + doc.name + '" בוטלה.');
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

  /* ---- פניות מהלקוח ---- */

  var REPLY_KINDS = {
    'no-document':  'אין לי את המסמך',
    'need-help':    'צריך עזרה בהשגתו',
    'sent-by-mail': 'שלחתי בדואר',
    'already-gave': 'כבר מסרתי למשרד',
    'other':        'הודעה מהלקוח'
  };

  function renderReplies() {
    var replies = CaseStore.clientReplies(openId);
    var list    = $('replyList');
    list.textContent = '';

    if (!replies.length) {
      list.appendChild(el('li', 'empty', 'הלקוח עוד לא פנה בתיק הזה.'));
      return;
    }

    replies.forEach(function (reply) {
      var li = el('li', reply.readAt ? 'reply read' : 'reply unread');

      if (!reply.readAt) {
        var tag = el('span', 'tag tag-warn');
        tag.appendChild(el('span', null, '!', true));
        tag.appendChild(document.createTextNode('חדש'));
        li.appendChild(tag);
      }

      li.appendChild(el('h3', null, REPLY_KINDS[reply.kind] || reply.kind));
      if (reply.docName) {
        li.appendChild(el('p', 'item-note', 'בנוגע ל: ' + reply.docName));
      }
      if (reply.text) {
        li.appendChild(el('p', 'reply-text', reply.text));
      }
      li.appendChild(el('p', 'item-when', formatDate(reply.date)));

      if (!reply.readAt) {
        var done = el('button', 'btn btn-outline btn-sm', 'סימון כטופל');
        done.type = 'button';
        done.addEventListener('click', function () {
          CaseStore.markReplyRead(openId, reply.id);
          renderCase();
        });
        li.appendChild(done);
      }

      list.appendChild(li);
    });
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
    var file = CaseStore.load(openId);
    var gap  = analyzeGap(file);

    return {
      claimType:      file.claimType,
      branch:         file.branch,
      currentStage:   file.currentStage,
      totalStages:    CLAIM_STAGES.length,
      stageTitle:     CLAIM_STAGES[file.currentStage - 1].title,
      stageEnteredAt: file.stageEnteredAt,
      openedAt:       file.openedAt,
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
      gap: {
        blocking: gap.blocking.map(function (i) { return i.name; }),
        inReview: gap.inReview.map(function (i) { return i.name; }),
        upcoming: gap.upcoming.map(function (i) { return i.name + ' (שלב ' + i.stage + ')'; })
      }
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

    fetch('/api/office/assist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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
