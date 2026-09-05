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

  var fileInput = $('fileInput');
  var pendingDoc = null;

  function uploadButton(doc, label) {
    var btn = el('button', 'btn btn-primary doc-actions', uploadLabel(doc));
    btn.type = 'button';
    btn.setAttribute('aria-label', label);
    btn.addEventListener('click', function () {
      pendingDoc = doc;
      fileInput.click();
    });
    return btn;
  }

  fileInput.addEventListener('change', function () {
    var file = fileInput.files[0];
    if (!file || !pendingDoc) return;

    if (file.size > 10 * 1024 * 1024) {
      toast('הקובץ גדול מ-10MB. נא לבחור קובץ קטן יותר.');
      fileInput.value = '';
      return;
    }

    CaseStore.saveDocument(user.idNumber, pendingDoc.id, {
      status: 'pending-review',
      file: file.name,
      date: new Date().toISOString().slice(0, 10)
    });

    toast('המסמך "' + pendingDoc.name + '" נשלח למשרד.');

    caseFile = CaseStore.load(user.idNumber);
    renderTodo();
    renderDocs();

    pendingDoc = null;
    fileInput.value = '';
  });

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

  /* ---- הפעלה ---- */

  renderStatus();
  renderTodo();
  renderDocs();
  renderNextSteps();
  renderStages();
  renderMessages();
  renderContact();
})();
