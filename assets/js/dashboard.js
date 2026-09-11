/* ==========================================================
   dashboard.js - רינדור האזור האישי
   ----------------------------------------------------------
   מבנה: טור אחד, לפי סדר החשיבות ללקוח -
   איפה התיק עומד -> מה עליי לעשות -> מסמכים -> המשך -> קשר.
   ========================================================== */

(function () {
  'use strict';

  var user = Auth.requireLogin();
  if (!user) return;

  var caseFile = CaseStore.load(user.idNumber);
  if (!caseFile) { Auth.logout(); return; }

  var $ = function (id) { return document.getElementById(id); };

  /* ---- עזרי טקסט ---- */

  /** ממיר 2026-09-22 ל-22.09.2026 */
  function formatDate(iso) {
    if (!iso) return '';
    var p = iso.split('-');
    return p[2] + '.' + p[1] + '.' + p[0];
  }

  /** "מסמך אחד" / "3 מסמכים" - עברית תקינה גם ביחיד */
  function countDocs(n) {
    return n === 1 ? 'מסמך אחד' : n + ' מסמכים';
  }

  var STATUS = {
    'approved':       { tag: 'tag-ok',   mark: '✓', text: 'התקבל ואושר' },
    'pending-review': { tag: 'tag-wait', mark: '●', text: 'אצלנו בבדיקה' },
    'missing':        { tag: 'tag-warn', mark: '!', text: 'צריך להעלות' },
    'rejected':       { tag: 'tag-stop', mark: '✗', text: 'צריך להעלות מחדש' }
  };

  /** תווית הכפתור משתנה לפי הסיבה שהמסמך חסר */
  function uploadLabel(doc) {
    return doc.status === 'rejected' ? 'העלאת מסמך מתוקן' : 'העלאת המסמך';
  }

  /* ---- תגובה למשרד על מסמך ---- */

  var REPLY_OPTIONS = [
    { kind: 'no-document',  label: 'אין לי את המסמך' },
    { kind: 'need-help',    label: 'צריך עזרה בהשגתו' },
    { kind: 'sent-by-mail', label: 'שלחתי בדואר' },
    { kind: 'already-gave', label: 'כבר מסרתי למשרד' }
  ];

  /**
   * בונה את אזור התגובה: כפתורי תגובה מהירה, ומתחתיהם
   * שדה טקסט חופשי שנפתח רק אחרי בחירה - כדי שהמסך לא יתמלא
   * בתיבות טקסט שאיש לא ימלא.
   */
  function replyBox(doc) {
    var wrap = el('div', 'reply-box');
    wrap.appendChild(el('p', 'reply-lead', 'לא מצליחים להעלות? אפשר לעדכן אותנו:'));

    var row     = el('div', 'reply-options');
    var chosen  = null;
    var buttons = [];

    var form = el('div', 'reply-detail');
    form.hidden = true;

    var areaId = 'reply-text-' + doc.id;
    var label  = el('label', 'reply-label', 'רוצים להוסיף פרטים? (לא חובה)');
    label.setAttribute('for', areaId);

    var area = document.createElement('textarea');
    area.id = areaId;
    area.rows = 3;
    area.maxLength = 400;
    area.className = 'reply-text';

    var send = el('button', 'btn btn-primary', 'שליחה למשרד');
    send.type = 'button';

    var small = el('p', 'reply-small',
      'זו אינה פנייה דחופה. לעניין דחוף יש להתקשר למשרד.');

    form.appendChild(label);
    form.appendChild(area);
    form.appendChild(send);
    form.appendChild(small);

    REPLY_OPTIONS.forEach(function (opt) {
      var btn = el('button', 'btn btn-outline reply-pick', opt.label);
      btn.type = 'button';
      btn.setAttribute('aria-pressed', 'false');
      btn.addEventListener('click', function () {
        chosen = opt.kind;
        buttons.forEach(function (b) {
          var on = b === btn;
          b.classList.toggle('on', on);
          b.setAttribute('aria-pressed', String(on));
        });
        form.hidden = false;
        area.focus();
      });
      buttons.push(btn);
      row.appendChild(btn);
    });

    send.addEventListener('click', function () {
      if (!chosen) return;
      CaseStore.addClientReply(user.idNumber, doc.id, chosen, area.value.trim());
      toast('העדכון נשלח למשרד. ניצור קשר בהקדם.');
      caseFile = CaseStore.load(user.idNumber);
      renderTodo();
      renderDocs();
    });

    wrap.appendChild(row);
    wrap.appendChild(form);
    return wrap;
  }

  /* ---- 1. כותרת ומצב התביעה ---- */

  function renderStatus() {
    var stage = CLAIM_STAGES[caseFile.currentStage - 1];
    var total = CLAIM_STAGES.length;
    var pct   = Math.round((caseFile.currentStage / total) * 100);

    $('userName').textContent = user.name;
    $('greeting').textContent = 'שלום ' + user.name.split(' ')[0];
    $('caseSummary').textContent =
      'תביעת ' + caseFile.claimType + ' · תיק מספר ' + caseFile.caseNumber;

    $('stepOf').textContent   = 'שלב ' + caseFile.currentStage + ' מתוך ' + total;
    $('stepName').textContent = stage.title;
    $('stepWhat').textContent = stage.desc;

    $('progressFill').style.width = pct + '%';
    $('progressBar').setAttribute('aria-label',
      'התקדמות התביעה: ' + pct + ' אחוז, שלב ' + caseFile.currentStage + ' מתוך ' + total);
    $('progressNote').textContent =
      'בשלב הזה מאז ' + formatDate(caseFile.stageEnteredAt) + ' · ' + pct + '% מהדרך';
  }

  /* ---- 1ב. ההחלטה שהתקבלה ומה היא אומרת ---- */

  /** מספר הימים מהיום עד תאריך. שלילי = התאריך חלף. */
  function daysUntil(iso) {
    if (!iso) return null;
    var parts = iso.split('-');
    var target = new Date(parts[0], parts[1] - 1, parts[2]);
    var today  = new Date();
    today.setHours(0, 0, 0, 0);
    return Math.round((target - today) / 86400000);
  }

  function renderDecision() {
    var block = $('decisionBlock');
    var body  = $('decisionBody');
    var d     = caseFile.decision;

    if (!d) { block.hidden = true; return; }

    block.hidden = false;
    body.textContent = '';

    // מה נקבע - בשורה אחת גדולה וברורה
    var head = el('p', 'decision-headline',
      d.percent != null
        ? 'הוועדה קבעה ' + d.percent + '% נכות' + (d.permanent ? ' צמיתה' : ' זמנית')
        : 'התקבלה החלטה בעניינך');
    body.appendChild(head);
    body.appendChild(el('p', 'decision-when', 'ההחלטה התקבלה ב-' + formatDate(d.date)));

    var info = RIGHTS_EXPLAINER[d.outcome];

    if (info && info.approved) {
      body.appendChild(el('h3', null, 'מה זה אומר'));
      body.appendChild(el('p', 'decision-meaning', info.meaning));

      if (info.entitlements && info.entitlements.length) {
        body.appendChild(el('h3', null, 'מה מגיע לך'));
        var ul = el('ul', 'decision-list');
        info.entitlements.forEach(function (t) { ul.appendChild(el('li', null, t)); });
        body.appendChild(ul);
      }

      if (info.whatNow && info.whatNow.length) {
        body.appendChild(el('h3', null, 'מה אפשר לעשות עכשיו'));
        var ol = el('ul', 'decision-list');
        info.whatNow.forEach(function (t) { ol.appendChild(el('li', null, t)); });
        body.appendChild(ol);
      }
    } else {
      // ההסבר טרם אושר על ידי המשרד - לא מציגים תוכן משפטי לא מאושר
      body.appendChild(el('p', 'decision-pending',
        'עורך הדין שלך יסביר לך בדיוק מה ההחלטה אומרת ומה מגיע לך. ' +
        'אפשר להתקשר למשרד בכל שאלה.'));
    }

    if (d.officeNote) {
      var note = el('div', 'decision-note');
      note.appendChild(el('strong', null, 'מהמשרד המטפל:'));
      note.appendChild(el('p', null, d.officeNote));
      body.appendChild(note);
    }

    // מועד הערר - הדבר הכי קריטי במסך הזה
    var left = daysUntil(d.appealDeadline);
    if (left !== null) {
      var dl = el('div', 'decision-deadline');
      if (left > 0) {
        dl.className += left <= 14 ? ' urgent' : '';
        dl.appendChild(el('strong', null,
          'המועד האחרון להגשת ערר: ' + formatDate(d.appealDeadline)));
        dl.appendChild(el('p', null,
          left === 1 ? 'נותר יום אחד.' : 'נותרו ' + left + ' ימים.'));
      } else {
        dl.className += ' passed';
        dl.appendChild(el('strong', null,
          'המועד להגשת ערר (' + formatDate(d.appealDeadline) + ') חלף.'));
        dl.appendChild(el('p', null,
          'אם לא הוגש ערר, יש לפנות למשרד בהקדם כדי לבחון מה אפשר לעשות.'));
      }
      body.appendChild(dl);
    }

    var call = el('a', 'btn btn-primary decision-call',
      'התקשרות למשרד: ' + caseFile.lawyer.phone);
    call.href = 'tel:' + caseFile.lawyer.phone.replace(/[^0-9+]/g, '');
    body.appendChild(call);

    body.appendChild(el('p', 'decision-legal',
      'ההסבר כאן נועד לעזור להבין את ההחלטה ואינו מהווה ייעוץ משפטי. ' +
      'הזכויות המדויקות תלויות בנסיבות האישיות שלך.'));
  }

  /* ---- 2. מה עליי לעשות עכשיו ---- */

  function renderTodo() {
    var required = caseFile.documents.filter(function (d) {
      return d.required && DOC_NEEDS_UPLOAD(d);
    });
    var block = $('todoBlock');
    var body  = $('todoBody');

    body.textContent = '';

    if (required.length === 0) {
      block.classList.add('done');
      body.appendChild(el('p', null,
        'אין כרגע משימות פתוחות. כל המסמכים הדרושים התקבלו - המשרד ממשיך לטפל בתיק ויעדכן אותך.'));
      return;
    }

    block.classList.remove('done');
    body.appendChild(el('p', null,
      'צריך להשלים ' + countDocs(required.length) + ' כדי שנוכל להמשיך בתביעה. אפשר להעלות אותם כאן באתר.'));

    var list = el('ul', 'plain-list');
    required.forEach(function (doc) {
      var li = el('li');
      li.appendChild(el('h3', null, doc.name));
      li.appendChild(el('p', 'item-note', doc.note));
      if (doc.status === 'rejected' && doc.rejectReason) {
        li.appendChild(el('p', 'reject-note', 'המשרד ביקש להעלות מחדש: ' + doc.rejectReason));
      }
      li.appendChild(uploadButton(doc, uploadLabel(doc) + ': ' + doc.name));
      li.appendChild(qualitySlot(doc));
      li.appendChild(replyBox(doc));
      list.appendChild(li);
    });
    body.appendChild(list);

    if (caseFile.nextHearing) {
      body.appendChild(el('p', 'item-when',
        'חשוב להשלים לפני הדיון הקרוב ב-' + formatDate(caseFile.nextHearing) + '.'));
    }
  }

  /* ---- 3. המסמכים שלי ---- */

  function renderDocs() {
    var docs = caseFile.documents.slice().sort(function (a, b) {
      // הנדחים למעלה, אחריהם החסרים, אחר כך בבדיקה, ולבסוף המאושרים
      var order = { 'rejected': 0, 'missing': 1, 'pending-review': 2, 'approved': 3 };
      return order[a.status] - order[b.status];
    });

    var approved = docs.filter(function (d) { return d.status === 'approved'; }).length;
    $('docsIntro').textContent =
      approved + ' מתוך ' + docs.length + ' המסמכים כבר אצלנו. המסמכים שצריך להשלים מופיעים ראשונים.';

    var list = $('docList');
    list.textContent = '';

    docs.forEach(function (doc) {
      var s  = STATUS[doc.status];
      var li = el('li');

      var tag = el('span', 'tag ' + s.tag);
      tag.appendChild(el('span', null, s.mark, true));
      tag.appendChild(document.createTextNode(s.text));
      li.appendChild(tag);

      li.appendChild(el('h3', null, doc.name + (doc.required ? '' : ' (לא חובה)')));
      li.appendChild(el('p', 'item-note', doc.note));

      if (doc.file) {
        li.appendChild(el('span', 'file-name',
          'הקובץ שהתקבל: ' + doc.file + ' · ' + formatDate(doc.date)));
      }

      if (doc.status === 'rejected' && doc.rejectReason) {
        li.appendChild(el('p', 'reject-note', 'סיבת הדחייה: ' + doc.rejectReason));
      }

      if (DOC_NEEDS_UPLOAD(doc)) {
        li.appendChild(uploadButton(doc, uploadLabel(doc) + ': ' + doc.name));
        li.appendChild(qualitySlot(doc));
      }

      list.appendChild(li);
    });
  }

  /* ---- 4. מה יקרה בהמשך ---- */

  function renderNextSteps() {
    var list = $('nextSteps');
    list.textContent = '';

    caseFile.nextSteps.forEach(function (step) {
      var li = el('li');
      li.appendChild(el('h3', null, step.title));
      li.appendChild(el('p', 'item-note', step.desc));
      li.appendChild(el('p', 'item-when', 'מתי: ' + step.eta));
      list.appendChild(li);
    });
  }

  /* ---- 5. כל שלבי התביעה ---- */

  function renderStages() {
    var list = $('stepsList');
    list.textContent = '';

    CLAIM_STAGES.forEach(function (stage) {
      var state = stage.id < caseFile.currentStage ? 'done'
                : stage.id === caseFile.currentStage ? 'now'
                : 'wait';

      var li = el('li', state);

      var mark = el('span', 'mark', state === 'done' ? '✓' : String(stage.id), true);
      li.appendChild(mark);

      var body = el('div');
      body.appendChild(el('span', 'name', stage.title));

      var doneOn = caseFile.stageDates[stage.id];
      var label = state === 'done' ? (doneOn ? 'הושלם ב-' + formatDate(doneOn) : 'הושלם')
                : state === 'now'  ? 'כאן נמצא התיק עכשיו'
                : 'עוד לא התחיל';
      body.appendChild(el('div', 'when', label));

      li.appendChild(body);
      list.appendChild(li);
    });
  }

  /* ---- 6. עדכונים ---- */

  function renderMessages() {
    var list = $('messages');
    list.textContent = '';

    caseFile.messages.forEach(function (msg) {
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

  /* ---- 7. יצירת קשר ---- */

  function renderContact() {
    var l = caseFile.lawyer;
    $('lawyerName').textContent = l.name;
    $('lawyerRole').textContent = l.role;

    var phone = $('lawyerPhone');
    phone.textContent = 'התקשרות למשרד: ' + l.phone;
    phone.href = 'tel:' + l.phone.replace(/[^0-9+]/g, '');

    $('lawyerEmail').href = 'mailto:' + l.email;
  }

  /* ---- העלאת מסמכים ---- */

  var fileInput   = $('fileInput');
  var cameraInput = $('cameraInput');
  var pendingDoc  = null;

  /* אחרי כמה ניסיונות כושלים ברציפות נפתחת דרך המשך.
     הסיבה: קהל היעד כולל אנשים מבוגרים ופגועי תנועה. חסימה
     מוחלטת פירושה לקוח שלא יכול להגיש מסמך כלל, והתיק שלו נתקע -
     וזה גרוע יותר מצילום בינוני שהמשרד יבדוק ויחליט לגביו.
     לחסימה קשיחה לחלוטין: ALLOW_OVERRIDE_AFTER = 0 */
  var ALLOW_OVERRIDE_AFTER = 3;
  var attempts = {};

  /** אזור שבו יוצג משוב איכות הצילום עבור המסמך הזה */
  function qualitySlot(doc) {
    var slot = el('div', 'quality-slot');
    slot.setAttribute('data-quality-for', doc.id);
    slot.setAttribute('aria-live', 'polite');
    return slot;
  }

  /** שתי דרכים להגיש: צילום ישיר, או קובץ שכבר קיים במכשיר */
  function uploadButton(doc, label) {
    var wrap = el('div', 'upload-actions');

    var cam = el('button', 'btn btn-primary', 'צילום המסמך');
    cam.type = 'button';
    cam.setAttribute('aria-label', 'צילום המסמך ' + doc.name);
    cam.addEventListener('click', function () {
      pendingDoc = doc;
      cameraInput.click();
    });

    var pick = el('button', 'btn btn-outline', 'בחירת קובץ');
    pick.type = 'button';
    pick.setAttribute('aria-label', label);
    pick.addEventListener('click', function () {
      pendingDoc = doc;
      fileInput.click();
    });

    wrap.appendChild(cam);
    wrap.appendChild(pick);
    return wrap;
  }

  fileInput.addEventListener('change', function () { handlePick(fileInput); });
  cameraInput.addEventListener('change', function () { handlePick(cameraInput); });

  function handlePick(input) {
    var file = input.files[0];
    var doc  = pendingDoc;
    input.value = '';
    if (!file || !doc) return;

    showChecking(doc);

    DocQuality.check(file).then(function (result) {
      if (result.verdict === 'ok' || result.verdict === 'skipped') {
        attempts[doc.id] = 0;
        acceptFile(doc, file);
        return;
      }

      attempts[doc.id] = (attempts[doc.id] || 0) + 1;
      var mayOverride = ALLOW_OVERRIDE_AFTER > 0 &&
                        attempts[doc.id] >= ALLOW_OVERRIDE_AFTER &&
                        result.verdict !== 'too-big' &&
                        result.verdict !== 'bad-type';

      showProblem(doc, file, result, mayOverride);
    });
  }

  function acceptFile(doc, file) {
    CaseStore.saveDocument(user.idNumber, doc.id, {
      status: 'pending-review',
      file: file.name,
      date: new Date().toISOString().slice(0, 10)
    });

    toast('המסמך "' + doc.name + '" נשלח למשרד.');

    caseFile = CaseStore.load(user.idNumber);
    pendingDoc = null;
    renderTodo();
    renderDocs();
  }

  /* ---- משוב על איכות הצילום ---- */

  /** מאתר את כל אזורי המשוב של מסמך - הוא מופיע גם במשימות וגם ברשימה */
  function feedbackSlots(docId) {
    return document.querySelectorAll('[data-quality-for="' + docId + '"]');
  }

  function showChecking(doc) {
    Array.prototype.forEach.call(feedbackSlots(doc.id), function (slot) {
      slot.textContent = '';
      slot.appendChild(el('p', 'quality-checking', 'בודקים את איכות הצילום...'));
    });
  }

  function showProblem(doc, file, result, mayOverride) {
    Array.prototype.forEach.call(feedbackSlots(doc.id), function (slot) {
      slot.textContent = '';

      var box = el('div', 'quality-box');
      box.setAttribute('role', 'alert');

      box.appendChild(el('h4', 'quality-title',
        result.verdict === 'unreadable' || result.verdict === 'too-big' ||
        result.verdict === 'bad-type'
          ? 'המסמך לא נשלח - הצילום אינו קריא'
          : 'המסמך לא נשלח - כדאי לצלם שוב'));

      var list = el('ul', 'quality-reasons');
      result.reasons.forEach(function (r) {
        var li = el('li');
        li.appendChild(el('strong', null, r.title));
        li.appendChild(el('span', 'quality-fix', r.fix));
        list.appendChild(li);
      });
      box.appendChild(list);

      var again = el('button', 'btn btn-primary', 'צילום מחדש');
      again.type = 'button';
      again.addEventListener('click', function () {
        pendingDoc = doc;
        cameraInput.click();
      });
      box.appendChild(again);

      if (mayOverride) {
        var note = el('p', 'quality-override-note',
          'ניסיתם כמה פעמים. אם אין באפשרותכם לצלם טוב יותר, אפשר לשלוח ' +
          'את הצילום הקיים - המשרד יבדוק אותו ויחזור אליכם.');
        var force = el('button', 'btn btn-outline', 'שליחה בכל זאת');
        force.type = 'button';
        force.addEventListener('click', function () {
          attempts[doc.id] = 0;
          acceptFile(doc, file);
        });
        box.appendChild(note);
        box.appendChild(force);
      }

      slot.appendChild(box);
    });
  }

  /* ---- הודעה צפה ---- */

  var toastTimer = null;
  function toast(msg) {
    var box = $('toast');
    box.textContent = msg;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { box.hidden = true; }, 5000);
  }

  /* ---- הגדלת טקסט ---- */

  var SIZE_KEY = 'bl_text_scale_v1';

  function applyScale(big) {
    document.documentElement.style.setProperty('--scale', big ? '1.25' : '1');
    var btn = $('textSizeBtn');
    btn.setAttribute('aria-pressed', String(big));
    btn.textContent = big ? 'טקסט רגיל' : 'הגדלת טקסט';
  }

  $('textSizeBtn').addEventListener('click', function () {
    var big = localStorage.getItem(SIZE_KEY) !== 'big';
    try { localStorage.setItem(SIZE_KEY, big ? 'big' : 'normal'); } catch (e) {}
    applyScale(big);
  });

  try { applyScale(localStorage.getItem(SIZE_KEY) === 'big'); } catch (e) { applyScale(false); }

  /* ---- יציאה ---- */

  $('logoutBtn').addEventListener('click', function () { Auth.logout(); });

  /* ---- עזר ליצירת אלמנטים ---- */

  function el(tag, className, text, decorative) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    if (decorative) node.setAttribute('aria-hidden', 'true');
    return node;
  }

  /* ==========================================================
     משך ההליך

     שני התאריכים כבר קיימים בנתונים: openedAt הוא פתיחת התיק
     במשרד, ו-stageDates[2] הוא יום הגשת התביעה לביטוח לאומי.
     אלה שני דברים שונים והלקוח שואל על שניהם.
     ========================================================== */

  /** מספר הימים שעברו מתאריך נתון ועד היום */
  function daysSince(iso) {
    if (!iso) return null;
    var then = new Date(iso + 'T00:00:00');
    if (isNaN(then)) return null;
    var now = new Date();
    now.setHours(0, 0, 0, 0);
    return Math.max(0, Math.round((now - then) / 86400000));
  }

  /** "שנה ושלושה חודשים" קריא יותר מ-"438 ימים" */
  function humanSpan(days) {
    if (days == null) return '';
    if (days < 31) return days === 1 ? 'יום אחד' : days + ' ימים';

    var months = Math.floor(days / 30.44);
    var years  = Math.floor(months / 12);
    var rem    = months % 12;

    var parts = [];
    if (years)  parts.push(years === 1 ? 'שנה' : years + ' שנים');
    if (rem)    parts.push(rem === 1 ? 'חודש' : rem + ' חודשים');
    if (!parts.length) parts.push('חודש');
    return parts.join(' ו');
  }

  function statLine(label, value, note) {
    var box = el('div', 'stat');
    box.appendChild(el('div', 'stat-label', label));
    box.appendChild(el('div', 'stat-value', value));
    if (note) box.appendChild(el('div', 'stat-note', note));
    return box;
  }

  function renderDuration() {
    var host = $('durationBody');
    if (!host) return;
    host.textContent = '';

    var openedDays = daysSince(caseFile.openedAt);
    var filedOn    = caseFile.stageDates && caseFile.stageDates[2];
    var filedDays  = daysSince(filedOn);

    var grid = el('div', 'stat-grid');
    grid.appendChild(statLine(
      'מאז פתיחת התיק במשרד',
      humanSpan(openedDays),
      caseFile.openedAt ? 'נפתח ב-' + formatDate(caseFile.openedAt) : ''
    ));

    if (filedDays != null) {
      grid.appendChild(statLine(
        'מאז הגשת התביעה לביטוח לאומי',
        humanSpan(filedDays),
        'הוגשה ב-' + formatDate(filedOn)
      ));
    } else {
      grid.appendChild(statLine(
        'הגשת התביעה לביטוח לאומי',
        'טרם הוגשה',
        'זה השלב שאנחנו עובדים עליו'
      ));
    }
    host.appendChild(grid);

    /* ---- השוואה לממוצע ---- */

    var est = typeof durationEstimate === 'function'
            ? durationEstimate(caseFile.claimType) : null;
    if (!est || openedDays == null) return;

    var estDays = Math.round(est.typicalMonths * 30.44);
    var pct     = Math.min(100, Math.round((openedDays / estDays) * 100));

    var cmp = el('div', 'compare');
    cmp.appendChild(el('h3', null,
      'מול משך טיפול אופייני בתביעת ' + caseFile.claimType));

    var bar = el('div', 'compare-bar');
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label',
      'התיק שלך נמצא בכ-' + pct + ' אחוז ממשך הטיפול האופייני');
    var fill = el('i');
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    cmp.appendChild(bar);

    cmp.appendChild(el('p', 'compare-note',
      'טיפול אופייני בתביעה מסוג זה נמשך בערך ' + est.typicalMonths +
      ' חודשים, ובדרך כלל בין ' + est.rangeMonths[0] + ' ל-' +
      est.rangeMonths[1] + ' חודשים. ' + est.note));

    /* הסייג מוצג ללקוח כל עוד המספרים לא אושרו על ידי המשרד.
       זה מכוון: עדיף שהלקוח יידע שזו הערכה מאשר שיבנה עליה. */
    if (!est.verified) {
      var warn = el('p', 'estimate-warning');
      warn.appendChild(el('strong', null, 'שימו לב: זו הערכה כללית בלבד. '));
      warn.appendChild(document.createTextNode(
        'המספרים כאן אינם נתונים רשמיים ואינם הבטחה לגבי התיק שלכם. ' +
        'כל תיק מתנהל בקצב משלו. לשאלה על לוח הזמנים שלכם - דברו עם המשרד.'
      ));
      cmp.appendChild(warn);
    }

    host.appendChild(cmp);
  }

  /* ---- אנימציית מפת הדרכים ----
     רצה פעם אחת בכניסה למסך. nav.js קורא לה.
     ביטול תנועה מטופל ב-CSS, ולכן אין כאן בדיקה כפולה. */
  window.playRoadmap = function () {
    var list = $('stepsList');
    if (!list) return;
    list.classList.remove('steps-in');
    void list.offsetWidth;          // מאלץ reflow כדי שהאנימציה תרוץ שוב
    list.classList.add('steps-in');
  };

  /* ---- הפעלה ---- */

  renderStatus();
  renderDecision();
  renderTodo();
  renderDocs();
  renderNextSteps();
  renderStages();
  renderDuration();
  renderMessages();
  renderContact();
})();
