/* ==========================================================
   dashboard.js - רינדור האזור האישי
   ----------------------------------------------------------
   מבנה: טור אחד, לפי סדר החשיבות ללקוח -
   איפה התיק עומד -> מה עליי לעשות -> מסמכים -> המשך -> קשר.
   ========================================================== */

(function () {
  'use strict';

  /* הזהות והנתונים מגיעים מהשרת בלבד. אין כאן בדיקה מקומית
     ואין נפילה ל-localStorage: מסך שמראה נתון ישן על תיק משפטי
     גרוע ממסך שאומר שלא הצלחנו לטעון. */
  var user = null;
  var caseFile = null;
  var CLAIM_STAGES = [];

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

  /* היה ב-data.js. חוק תצוגה פשוט, לא חוק עסק - השרת הוא
     שקובע את הסטטוס עצמו. */
  function DOC_NEEDS_UPLOAD(doc) {
    return doc.status === 'missing' || doc.status === 'rejected';
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

  /* המזהים חייבים להיות זהים ל-REPLY_KINDS שב-server/api_client.py.
     עד 18.09 שלושה מהם היו שונים, והשרת דחה אותם ב-400 - כלומר
     שלושה מארבעת כפתורי התגובה פשוט לא עבדו. */
  var REPLY_OPTIONS = [
    { kind: 'dont-have',   label: 'אין לי את המסמך' },
    { kind: 'need-help',   label: 'צריך עזרה בהשגתו' },
    { kind: 'sent-mail',   label: 'שלחתי בדואר' },
    { kind: 'gave-office', label: 'כבר מסרתי למשרד' }
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
      send.disabled = true;
      /* התגובה נשלחת לשרת. הוא מוודא שהמסמך שייך לתיק של הלקוח
         המחובר - client_id מגיע מה-session ולא מהבקשה. */
      Api.replyToDocument(doc.id, chosen, area.value.trim())
         .then(function () { return reload(); })
         .then(function () { toast('העדכון נשלח למשרד. ניצור קשר בהקדם.'); })
         .catch(function (err) {
           send.disabled = false;
           toast(err.message || 'שליחת התגובה נכשלה. אפשר לנסות שוב.');
         });
    });

    wrap.appendChild(row);
    wrap.appendChild(form);
    return wrap;
  }

  /* ---- 1. כותרת ומצב התביעה ---- */


  /* ==========================================================
     סט הסמלים
     ----------------------------------------------------------
     SVG ולא אימוג'י: אימוג'י משתנה בין מערכות הפעלה, וקורא מסך
     מקריא אותו בקול כחלק מהמשפט. כאן הסמל תמיד aria-hidden,
     ותמיד יש טקסט גלוי לצידו - הוא מסייע להבנה ואינו מחליף אותה.
     ========================================================== */

  var ICON_PATHS = {
    alert:    'M12 3 L22 20 H2 Z M12 9 v5 M12 17 v.6',
    calendar: 'M4 6h16v15H4z M4 11h16 M8 3v5 M16 3v5',
    check:    'M4 12.5 L9.5 18 L20 6',
    doc:      'M6 3h8l4 4v14H6z M14 3v4h4 M9 12h6 M9 16h6',
    clock:    'M12 3a9 9 0 100 18 9 9 0 000-18z M12 7v5.5l3.5 2',
    message:  'M3 5h18v12H8l-5 4z',
    phone:    'M5 4h4l2 5-2.5 1.5a12 12 0 005 5L15 13l5 2v4a1 1 0 01-1 1A16 16 0 014 5a1 1 0 011-1z',
    upload:   'M12 16V4 M7 9l5-5 5 5 M4 17v2a1 1 0 001 1h14a1 1 0 001-1v-2'
  };

  /** סמל דקורטיבי. לעולם לא לבד - תמיד עם טקסט לצידו. */
  function icon(name) {
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    var p = document.createElementNS(ns, 'path');
    p.setAttribute('d', ICON_PATHS[name] || ICON_PATHS.doc);
    p.setAttribute('fill', 'none');
    p.setAttribute('stroke', 'currentColor');
    p.setAttribute('stroke-width', '1.6');
    p.setAttribute('stroke-linecap', 'round');
    p.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(p);
    return svg;
  }

  /** שורת סמל + טקסט. הטקסט הוא המידע; הסמל רק מסייע לסרוק אותו. */
  function iconLine(name, text, tone) {
    var row = el('p', 'icon-line' + (tone ? ' icon-line-' + tone : ''));
    row.appendChild(icon(name));
    row.appendChild(el('span', null, text));
    return row;
  }


  /* ==========================================================
     התמצית - המסך שהלקוח נוחת עליו
     ----------------------------------------------------------
     ארבע שורות ותו לא: איפה התיק, מה זה אומר, מה נדרש ממנו,
     ומתי המועד הקרוב. כל השאר עבר לקטגוריות שבסרגל.
     ========================================================== */

  function renderSummary() {
    var host = $('summaryBody');
    if (!host) return;
    host.textContent = '';

    var total = CLAIM_STAGES.length;
    var n     = caseFile.currentStage;
    var stage = n ? CLAIM_STAGES[n - 1] : null;

    /* תיק שטרם נרשם לו שלב. קורה בין פתיחת התיק במשרד לבין רישום
       השלב הראשון, ולכן זה מצב חוקי ולא תקלה. אומרים זאת במפורש
       ויוצאים - בלי המחוון, שאין לו מה להצביע עליו. חשוב: בלי
       היציאה הזאת renderSummary היה זורק על stage.title, paint כולו
       היה נעצר, וכל מה שמתחתיו היה נשאר ריק בלי שום הודעת שגיאה. */
    if (!stage) {
      var head0 = el('div', 'sum-title');
      head0.appendChild(el('p', 'sum-eyebrow', 'השלב הנוכחי'));
      head0.appendChild(el('h2', 'sum-stage', 'התיק נפתח וטרם נקבע לו שלב'));
      host.appendChild(head0);
      host.appendChild(el('p', 'sum-what',
        'המשרד פתח את התיק ועדיין לא רשם את השלב הראשון. ' +
        'ברגע שהשלב ייקבע הוא יופיע כאן.'));
      return;
    }

    /* המספר הגדול - אותה מחווה של 01-08 באקורדיון שבדף הציבורי */
    var head = el('div', 'sum-head');
    var num  = el('div', 'sum-num');
    num.appendChild(el('span', 'sum-num-now', pad2(n)));
    num.appendChild(el('span', 'sum-num-rule', '', true));
    num.appendChild(el('span', 'sum-num-all', pad2(total)));
    head.appendChild(num);

    var title = el('div', 'sum-title');
    title.appendChild(el('p', 'sum-eyebrow', 'השלב הנוכחי'));
    title.appendChild(el('h2', 'sum-stage', stage.title));
    head.appendChild(title);
    host.appendChild(head);

    /* מחוון מקוטע - שמונה משבצות שאפשר לספור בעין */
    var track = el('div', 'ticks');
    track.setAttribute('role', 'img');
    track.setAttribute('aria-label',
      'שלב ' + n + ' מתוך ' + total + ' בתביעה');
    for (var i = 1; i <= total; i++) {
      var cls = 'tick' + (i < n ? ' tick-done' : i === n ? ' tick-now' : '');
      track.appendChild(el('span', cls, '', true));
    }
    host.appendChild(track);

    host.appendChild(el('p', 'sum-what', stage.desc));

    /* שורות הסמלים. אם אין פעולה פתוחה - אומרים זאת, לא משמיטים. */
    var open = caseFile.documents.filter(function (d) {
      return d.status === 'missing' || d.status === 'rejected';
    }).length;

    if (open) {
      host.appendChild(iconLine('alert',
        open === 1 ? 'נדרש ממך מסמך אחד' : 'נדרשים ממך ' + open + ' מסמכים', 'warn'));
    } else {
      host.appendChild(iconLine('check', 'כל המסמכים שביקשנו נמצאים אצלנו', 'ok'));
    }

    var when = caseFile.nextHearing;
    if (when) {
      var left = daysUntil(when);
      var note = 'המועד הקרוב: ' + formatDate(when);
      if (left != null && left >= 0) {
        note += left === 0 ? ' (היום)' : ' (בעוד ' + left + (left === 1 ? ' יום)' : ' ימים)');
      }
      host.appendChild(iconLine('calendar', note));
    } else {
      host.appendChild(iconLine('clock', 'טרם נקבע מועד נוסף. נעדכן ברגע שייקבע.'));
    }

    var go = el('button', 'btn btn-primary sum-cta', open ? 'למה שנדרש ממני' : 'למסמכים שהעליתי');
    go.type = 'button';
    go.addEventListener('click', function () {
      if (window.goToView) window.goToView(open ? 'todo' : 'uploaded');
    });
    host.appendChild(go);
  }

  /** "05" ולא "5" - המספר הדו-ספרתי הוא חלק מהעיצוב */
  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  /* כותרת הזיהוי בלבד. מצב התביעה עצמו עבר ל-renderSummary,
     שמחליף את כרטיס הסטטוס הישן ואת סרגל ההתקדמות הרציף. */
  function renderStatus() {
    $('userName').textContent = user.name;
    $('greeting').textContent = 'שלום ' + user.name.split(' ')[0];
    $('caseSummary').textContent =
      'תביעת ' + caseFile.claimType + ' · תיק מספר ' + caseFile.caseNumber;
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

    /* RIGHTS_EXPLAINER היה ב-data.js ועדיין אין לו endpoint.
       בהיעדרו מציגים את ההפניה למשרד - בדיוק ההתנהגות שתוכננה
       כשהתוכן לא אושר. */
    var explainer = (typeof RIGHTS_EXPLAINER !== 'undefined') ? RIGHTS_EXPLAINER : {};
    var info = explainer[d.outcome];

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

  /* ==========================================================
     רצועת הסיכום של "מה נדרש ממך"
     ----------------------------------------------------------
     עד 12.09 הפונקציה הזו רינדרה את המסמכים בעצמה, ובמקביל
     docListOpen רינדר אותם שוב - אותם מסמכים הופיעו פעמיים באותו
     מסך. כאן נשאר רק הסיכום, והרשימה היא של docListOpen בלבד.
     ========================================================== */

  function renderTodo() {
    var body = $('todoBody');
    if (!body) return;
    body.textContent = '';

    var all  = caseFile.documents;
    var done = all.filter(function (d) { return !isOpenDoc(d) && d.file; }).length;
    var open = all.filter(isOpenDoc).length;

    if (!open) {
      body.appendChild(iconLine('check',
        'אין כרגע משימות פתוחות. כל המסמכים הדרושים התקבלו, והמשרד ממשיך לטפל בתיק.', 'ok'));
      return;
    }

    /* מד התקדמות - כמה כבר נמסר מתוך מה שביקשנו */
    var meter = el('div', 'meter');
    var fill  = el('span', 'meter-fill', '', true);
    fill.style.width = Math.round((done / all.length) * 100) + '%';
    meter.appendChild(fill);

    var strip = el('div', 'todo-strip');
    var lead  = el('div', 'todo-strip-main');
    lead.appendChild(el('p', 'todo-count', countDocs(open) + ' עוד ממתינים לך'));
    lead.appendChild(el('p', 'todo-sub', done + ' מתוך ' + all.length + ' כבר אצלנו'));
    lead.appendChild(meter);
    strip.appendChild(lead);

    if (caseFile.nextHearing) {
      var d = el('div', 'todo-strip-when');
      d.appendChild(iconLine('calendar', 'לפני הדיון ב-' + formatDate(caseFile.nextHearing)));
      strip.appendChild(d);
    }
    body.appendChild(strip);
  }

  /* ==========================================================
     המסמכים - שתי רשימות מאותו מערך
     ----------------------------------------------------------
     "מה נדרש ממני" מציג רק את מה שיש עליו פעולה פתוחה.
     "המסמכים שהעליתי" מציג את מה שכבר התקבל, כדי שהלקוח יראה
     מה הוא כבר עשה ולא רק מה חסר.

     מסמך נדחה מופיע ברשימה הפתוחה ולא ברשימת ההישגים: יש עליו
     פעולה, והצגתו כ"הועלה" הייתה מטעה. שתי הרשימות יחד מכסות
     את כל המסמכים בתיק, בלי חפיפה ובלי נשירה.
     ========================================================== */

  function isOpenDoc(d) {
    return d.status === 'missing' || d.status === 'rejected';
  }

  /** שורת מסמך אחת. `withUpload` מוסיף את אזור ההעלאה. */
  function docRow(doc, withUpload) {
    var st = STATUS[doc.status];
    var li = el('li', 'doc-task' + (withUpload ? '' : ' doc-task-done'));

    /* כותרת: שם המסמך, ולצידו הסטטוס בסמל ובטקסט */
    var head = el('div', 'doc-head');
    var name = el('div', 'doc-name');
    name.appendChild(el('h3', null, doc.name));
    if (!doc.required) name.appendChild(el('span', 'doc-optional', 'לא חובה'));
    head.appendChild(name);

    var tag = el('span', 'tag ' + st.tag);
    tag.appendChild(el('span', null, st.mark, true));
    tag.appendChild(document.createTextNode(st.text));
    head.appendChild(tag);
    li.appendChild(head);

    li.appendChild(el('p', 'item-note', doc.note));

    if (doc.file) {
      var got = el('p', 'doc-file');
      got.appendChild(icon('doc'));
      got.appendChild(el('span', null, doc.file + ' · התקבל ב-' + formatDate(doc.date)));
      li.appendChild(got);
    }

    if (doc.status === 'rejected' && doc.rejectReason) {
      li.appendChild(el('p', 'reject-note', 'סיבת הדחייה: ' + doc.rejectReason));
    }

    if (withUpload && DOC_NEEDS_UPLOAD(doc)) {
      li.appendChild(uploadButton(doc, 'בחירת קובץ עבור ' + doc.name));
      li.appendChild(qualitySlot(doc));

      /* התגובות המהירות מקופלות. פתוחות לכל מסמך הן ארבע תיבות
         כפולות במספר המסמכים, וזה מה שהפך את המסך לרועש.
         <details> נותן פתיחה נגישה במקלדת בלי JavaScript. */
      var more = el('details', 'doc-more');
      var sum  = el('summary', 'doc-more-q', 'לא מצליחים להעלות?');
      more.appendChild(sum);
      more.appendChild(replyBox(doc));
      li.appendChild(more);
    }

    return li;
  }

  function renderDocs() {
    var all  = caseFile.documents;
    var open = all.filter(isOpenDoc);
    var done = all.filter(function (d) { return !isOpenDoc(d) && d.file; });

    /* ---- מה נדרש ממני ---- */
    var openList = $('docListOpen');
    if (openList) {
      openList.textContent = '';
      $('docsOpenIntro').textContent = open.length
        ? 'נותרו ' + open.length + ' מסמכים להשלמה מתוך ' + all.length + ' שביקשנו.'
        : 'אין כרגע מסמכים להשלמה. קיבלנו את הכול.';
      /* הנדחים ראשונים - עליהם הלקוח כבר עבד פעם אחת */
      open.sort(function (a, b) {
        return (a.status === 'rejected' ? 0 : 1) - (b.status === 'rejected' ? 0 : 1);
      });
      open.forEach(function (d) { openList.appendChild(docRow(d, true)); });
    }

    /* ---- המסמכים שהעליתי ---- */
    var doneList = $('docListDone');
    if (doneList) {
      doneList.textContent = '';
      $('docsDoneIntro').textContent = done.length
        ? done.length + ' מסמכים כבר אצלנו, מתוך ' + all.length + ' שביקשנו.'
        : 'עדיין לא התקבלו מסמכים. ברגע שתעלה מסמך הוא יופיע כאן.';
      /* החדש למעלה - סדר כרונולוגי הפוך לפי מועד ההעלאה */
      done.sort(function (a, b) { return (b.date || '').localeCompare(a.date || ''); });
      done.forEach(function (d) { doneList.appendChild(docRow(d, false)); });
    }

    /* תגי הספירה במגירה */
    setBadge('navDocsBadge', open.length);
    setBadge('navDoneBadge', done.length);
  }

  function setBadge(id, n) {
    var b = $(id);
    if (!b) return;
    b.textContent = n;
    b.hidden = !n;
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
    if (!l) {
      /* ה-API טרם מחזיר את פרטי עורך הדין המטפל. מציגים את
         פרטי המשרד במקום להשאיר שדות ריקים. */
      $('lawyerName').textContent = 'משרד עורכי הדין Nahmani Ben-Dahan';
      $('lawyerRole').textContent = 'הצוות המטפל בתיק';
      var p = $('lawyerPhone');
      p.textContent = 'התקשרות למשרד: 03-5551234';
      p.href = 'tel:035551234';
      $('lawyerEmail').href = 'mailto:office@nahmani-bendahan.co.il';
      return;
    }
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
  /* ==========================================================
     אזור ההעלאה
     ----------------------------------------------------------
     גרירה היא תוספת, לא התחליף: שני הכפתורים נשארים בתוך האזור
     ונגישים למקלדת ולקורא מסך. מי שלא יכול לגרור לא מאבד דבר.

     האזור כולו לחיץ ופותח את בוחר הקבצים, ולכן מטרת המגע היא
     הריבוע השלם ולא כפתור בגודל אצבע.
     ========================================================== */
  function uploadButton(doc, label) {
    var zone = el('div', 'dropzone');

    var head = el('div', 'dz-head');
    head.appendChild(icon('upload'));
    head.appendChild(el('span', 'dz-title', 'גררו לכאן קובץ, או בחרו מהמכשיר'));
    zone.appendChild(head);

    zone.appendChild(el('p', 'dz-hint', 'קובץ PDF או תמונה, עד 12MB'));

    var acts = el('div', 'dz-actions');

    var cam = el('button', 'btn btn-primary dz-btn', 'צילום המסמך');
    cam.type = 'button';
    cam.setAttribute('aria-label', 'צילום המסמך ' + doc.name);
    cam.addEventListener('click', function (e) {
      e.stopPropagation();
      pendingDoc = doc;
      cameraInput.click();
    });

    var pick = el('button', 'btn btn-outline dz-btn', 'בחירת קובץ');
    pick.type = 'button';
    pick.setAttribute('aria-label', label);
    pick.addEventListener('click', function (e) {
      e.stopPropagation();
      pendingDoc = doc;
      fileInput.click();
    });

    acts.appendChild(cam);
    acts.appendChild(pick);
    zone.appendChild(acts);

    /* לחיצה על השטח עצמו - קיצור למי שלא מכוון לכפתור קטן */
    zone.addEventListener('click', function () {
      pendingDoc = doc;
      fileInput.click();
    });

    /* גרירה. preventDefault על dragover חובה, אחרת הדפדפן פותח
       את הקובץ בלשונית במקום למסור אותו לנו. */
    ['dragenter', 'dragover'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) {
        e.preventDefault();
        zone.classList.add('dz-over');
      });
    });
    ['dragleave', 'dragend'].forEach(function (ev) {
      zone.addEventListener(ev, function () { zone.classList.remove('dz-over'); });
    });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('dz-over');
      var file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      processFile(doc, file);
    });

    return zone;
  }

  /* חיווט בוררי הקבצים. שתי השורות האלה נמחקו בטעות בקומיט 07a20d0
     כשהכפתורים הוחלפו באזור גרירה, ומאז בחירת קובץ נפתחה אך הקובץ
     נזרק בשקט - רק הגרירה עבדה. */
  fileInput.addEventListener('change', function () { handlePick(fileInput); });
  cameraInput.addEventListener('change', function () { handlePick(cameraInput); });

  function handlePick(input) {
    var file = input.files[0];
    var doc  = pendingDoc;
    input.value = '';
    processFile(doc, file);
  }

  /* אותו מסלול בדיוק לבחירת קובץ, לצילום ולגרירה - כולל בדיקת
     האיכות. גרירה שעוקפת את הבדיקה הייתה מחזירה אותנו לצילומים
     לא קריאים, וזה מה שהפיצ'ר הזה נועד למנוע מלכתחילה. */
  function processFile(doc, file) {
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
    /* הקובץ נשלח לשרת, ורק אחרי שהשרת אישר אנחנו טוענים מחדש
       את התיק ממנו. אין עדכון אופטימי של המסך: אם ההעלאה נכשלה,
       הלקוח חייב לדעת שהמסמך לא הגיע. */
    pendingDoc = null;
    Api.uploadFile(doc.id, file).then(function (res) {
      return reload().then(function () {
        toast('המסמך "' + doc.name + '" נשלח למשרד.');
      });
    }).catch(function (err) {
      toast(err.message || 'העלאת המסמך נכשלה. אפשר לנסות שוב.');
    });
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

  $('logoutBtn').addEventListener('click', function () {
    Api.clientLogout().then(function () { location.replace('index.html'); },
                            function () { location.replace('index.html'); });
  });

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

  /* ==========================================================
     הפעלה
     ----------------------------------------------------------
     שני צעדים: מי אני, ואז מה התיק שלי. שניהם מהשרת.
     התיק נבחר לפי ה-session ולא לפי מזהה שהדפדפן מחזיק, ולכן
     אין כאן דרך לבקש תיק של מישהו אחר.
     ========================================================== */

  /* ---- בקשות שאינן מסמך ----
     מסמכים כבר מוצגים ב-docListOpen. כאן רק מה שאינו מסמך:
     לחתום, למסור פרטים, ליצור קשר. אחרת אותו דבר היה מופיע
     פעמיים באותו מסך. */

  var REQ_KIND_LABEL = {
    'info': 'מסירת מידע', 'signature': 'חתימה', 'form': 'השלמת טופס',
    'contact': 'יצירת קשר עם המשרד', 'action': 'ביצוע פעולה', 'other': 'בקשה'
  };

  function renderRequirements() {
    var block = $('clientReqBlock');
    var list  = $('clientReqList');
    if (!block || !list) return;

    var items = caseFile.requirements || [];
    block.hidden = !items.length;
    list.textContent = '';
    if (!items.length) return;

    $('clientReqIntro').textContent = items.length === 1
      ? 'יש בקשה אחת שאינה מסמך.'
      : 'יש ' + items.length + ' בקשות שאינן מסמך.';

    items.forEach(function (q) {
      var li = el('li', 'doc-task');
      var head = el('div', 'doc-head');
      head.appendChild(el('span', 'doc-name', q.title));
      head.appendChild(el('span', 'card-cat', REQ_KIND_LABEL[q.kind] || 'בקשה'));
      li.appendChild(head);
      if (q.guidance) li.appendChild(el('p', 'item-note', q.guidance));
      if (q.dueAt) {
        li.appendChild(iconLine('calendar', 'עד ' + formatDate(q.dueAt.slice(0, 10))));
      }
      list.appendChild(li);
    });
  }

  /* ---- שיחה עם המשרד ----
     עד 25.09 הלקוח יכול היה רק לקבל. עכשיו הוא יכול גם להשיב. */

  function renderChat() {
    var thread = $('clientChat');
    if (!thread) return;

    Api.clientConversation().then(function (data) {
      thread.textContent = '';
      var msgs = data.messages || [];
      $('clientChatIntro').textContent = msgs.length
        ? 'ההתכתבות שלך עם המשרד.'
        : 'אין עדיין הודעות. אפשר לכתוב למשרד מכאן.';

      msgs.forEach(function (m) {
        var li = el('li', 'chat-msg chat-' + m.direction);
        var head = el('p', 'chat-head');
        head.appendChild(el('span', 'chat-who',
          m.direction === 'inbound' ? 'את/ה' : 'המשרד'));
        head.appendChild(el('span', 'chat-when num', formatDate(m.at.slice(0, 10))));
        li.appendChild(head);
        li.appendChild(el('p', 'chat-body', m.body));
        if (m.requirementTitle) {
          li.appendChild(el('p', 'chat-link', 'בקשר ל: ' + m.requirementTitle));
        }
        thread.appendChild(li);
      });
    }).catch(function () {
      $('clientChatIntro').textContent = 'לא הצלחנו לטעון את ההודעות.';
    });
  }

  var chatForm = $('clientChatForm');
  if (chatForm) {
    chatForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var err = $('clientChatError');
      err.hidden = true;

      var body = $('clientChatBody').value.trim();
      if (!body) {
        err.textContent = 'אי אפשר לשלוח הודעה ריקה.';
        err.hidden = false;
        return $('clientChatBody').focus();
      }

      Api.clientSendMessage(body)
        .then(function () {
          $('clientChatBody').value = '';
          renderChat();
          toast('ההודעה נשלחה למשרד.');
        })
        .catch(function (e2) {
          err.textContent = e2.message || 'השליחה נכשלה.';
          err.hidden = false;
        });
    });
  }

  function paint() {
    renderStatus();
    renderSummary();
    renderDecision();
    renderTodo();
    renderDocs();
    renderRequirements();
    renderChat();
    renderNextSteps();
    renderStages();
    renderDuration();
    renderMessages();
    renderContact();
  }

  /** טוען מחדש את התיק מהשרת ומצייר. משמש אחרי כל פעולה. */
  function reload() {
    return Api.clientCase(caseFile.id).then(function (data) {
      caseFile = normalise(data);
      paint();
    });
  }

  /** מתרגם את תשובת ה-API למבנה שהרינדור הקיים מצפה לו. */
  function normalise(data) {
    CLAIM_STAGES = (data.stages || []).map(function (st) {
      return { id: st.position, title: st.title, desc: st.desc };
    });
    data.stageDates = {};
    (data.stages || []).forEach(function (st) {
      if (st.occurredAt) data.stageDates[st.position] = st.occurredAt;
    });
    /* שדות שה-API טרם מספק. מוצהרים ריקים במפורש כדי שהרינדור
       יציג "אין" במקום להיכשל על undefined. */
    data.nextSteps = data.nextSteps || [];
    data.decision = data.decision || null;
    data.lawyer = data.lawyer || null;
    data.clientReplies = data.clientReplies || [];
    data.requirements = data.requirements || [];
    return data;
  }

  function fail(message) {
    var host = $('summaryBody') || document.body;
    host.textContent = '';
    var box = el('div', 'alert alert-error');
    box.textContent = message;
    host.appendChild(box);
  }

  Api.me()
     .then(function (who) {
       if (who.type !== 'client') {
         location.replace(who.type === 'user' ? 'admin.html' : 'index.html');
         return null;
       }
       user = { name: who.name };
       return Api.clientCases();
     })
     .then(function (list) {
       if (!list) return null;
       if (!list.cases.length) {
         fail('לא נמצא תיק פעיל. יש לפנות למשרד.');
         return null;
       }
       return Api.clientCase(list.cases[0].id);
     })
     .then(function (data) {
       if (!data) return;
       caseFile = normalise(data);
       paint();
     })
     .catch(function (err) {
       /* 401 כבר הפנה למסך הכניסה דרך Api.onUnauthorized. */
       if (err && err.status !== 401) {
         fail(err.message || 'לא הצלחנו לטעון את התיק. נסה לרענן.');
       }
     });
})();
