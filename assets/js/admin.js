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
        $('staffName').textContent = who.name + (who.role ? ' · ' + who.role : '');
        return showList();
      });
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
  };

  function renderLog(caseId) {
    var list = $('logList');
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
    openId = null;
    currentCase = null;
    $('listView').hidden = false;
    $('caseView').hidden = true;
    $('backBtn').hidden  = true;

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

        tr.appendChild(countCell(c.awaitingReview, 'wait'));
        tr.appendChild(countCell(c.openForClient, 'warn'));

        var act = document.createElement('td');
        var open = el('button', 'btn btn-outline btn-small', 'פתיחת התיק');
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
    var box = el('span', 'count' + (n ? ' count-' + tone : ''), String(n || 0));
    td.appendChild(box);
    return td;
  }

  /* ================= תיק בודד ================= */

  function openCase(caseId) {
    openId = caseId;
    $('listView').hidden = true;
    $('caseView').hidden = false;
    $('backBtn').hidden  = false;

    /* הניתוח שייך לתיק - אסור שיישאר על המסך כשעוברים לתיק אחר */
    assistHistory = [];
    $('assistLog').textContent = '';
    $('assistInput').value = '';

    renderCase().then(function () { $('caseClient').focus(); });
  }

  $('backBtn').addEventListener('click', showList);

  /** טוען את התיק מהשרת ומצייר. מוחזר Promise כדי שפעולות
      יוכלו להמתין לרענון לפני שהן מודיעות שהצליחו. */
  function renderCase() {
    return Api.officeCase(openId).then(function (file) {
      currentCase = file;

      var head = $('caseClient');
      head.textContent = file.clientName;
      head.tabIndex = -1;

      $('caseMeta').textContent =
        'תיק ' + file.caseNumber + ' · ' + file.claimType +
        (file.branch ? ' · ' + file.branch : '') +
        ' · שלב ' + (file.currentStage || '-') +
        (file.stageTitle ? ' - ' + file.stageTitle : '');

      renderStage(file);
      renderDecisionForm(file);
      renderCatalog(file);
      renderReview(file);
      renderLog(openId);
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

    Api.addDocument(openId, name, note, required)
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
      return;
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
      Api.reviewDocument(doc.id, 'reject', reason)
         .then(function () { return renderCase(); })
         .then(function () { toast('המסמך "' + doc.name + '" נדחה והלקוח יעודכן.'); })
         .catch(function (err) { toast(err.message || 'הדחייה נכשלה.'); });
      return;
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

  var msgForm = $('msgForm');
  var msgErr  = $('msgError');

  msgForm.addEventListener('submit', function (e) {
    e.preventDefault();

    var title = $('msgTitle').value.trim();
    var body  = $('msgBody').value.trim();

    msgErr.hidden = true;

    if (!title)           return msgFail('צריך כותרת להודעה.', $('msgTitle'));
    if (body.length < 10) return msgFail('תוכן ההודעה קצר מדי.', $('msgBody'));

    Api.sendMessage(openId, title, body, important)
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

  /* ---- מי שכבר מחובר נכנס ישירות ----
     השרת מחליט, לא הדפדפן. 401 פשוט משאיר את מסך הכניסה. */
  Api.me({ allowUnauthorized: true })
     .then(function (who) { if (who && who.type === 'user') start(); })
     .catch(function () { /* לא מחובר */ });
})();
