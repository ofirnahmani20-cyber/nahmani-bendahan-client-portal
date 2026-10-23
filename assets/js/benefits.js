/* ==========================================================
   benefits.js - "מה מגיע לי" באזור הלקוח (23.10, docs/10)
   ----------------------------------------------------------
   מחשבוני הקצבאות שרלוונטיים לתיק, וזכויות נוספות שמתאימות לו.

   החישוב בשרת (server/calculators.py), עם הנתונים שבתוקף. התוצאה
   היא טווח והסתייגות, לעולם לא "תקבל X". מחשבון שהמשרד עוד לא
   הזין לו נתונים מוצג כ"בקרוב", לא מוסתר ולא מנחש.
   ========================================================== */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function shekel(v) {
    return '₪' + Number(v || 0).toLocaleString('he-IL', { maximumFractionDigits: 0 });
  }

  function date(iso) {
    var p = (iso || '').slice(0, 10).split('-');
    return p.length === 3 ? p[2] + '.' + p[1] + '.' + p[0] : '';
  }

  var KIND = { monthly: ' לחודש', grant: ' (מענק חד-פעמי)', none: '' };

  function field(calcId, input) {
    var id = 'calc-' + calcId.replace(/\W/g, '') + '-' + input.name;
    var wrap = el('div', 'field');
    var label = el('label', null, input.label);
    label.htmlFor = id;
    var ctl;
    if (input.type === 'options_from') {
      ctl = el('select');
      (input.options || []).forEach(function (o) {
        var opt = el('option', null, o.label);
        opt.value = o.value;
        ctl.appendChild(opt);
      });
    } else if (input.type === 'bool') {
      ctl = el('input');
      ctl.type = 'checkbox';
      label.className = 'check';
      label.textContent = '';
      label.appendChild(ctl);
      label.appendChild(document.createTextNode(' ' + input.label));
      ctl.id = id;
      ctl.name = input.name;
      wrap.appendChild(label);
      return wrap;
    } else {
      ctl = el('input');
      ctl.type = 'number';
      ctl.inputMode = 'decimal';
      ctl.dir = 'ltr';
      if (input.min != null) ctl.min = input.min;
      if (input.max != null) ctl.max = input.max;
      if (input.value != null) ctl.value = input.value;
    }
    ctl.id = id;
    ctl.name = input.name;
    wrap.appendChild(label);
    wrap.appendChild(ctl);
    return wrap;
  }

  function calculatorCard(c) {
    var sec = el('section', 'calc-card');
    sec.appendChild(el('h3', null, c.title));
    if (!c.configured) {
      sec.appendChild(el('p', 'measure',
        'המחשבון הזה יהיה זמין בקרוב, אחרי שהמשרד יזין את שיעורי הקצבאות העדכניים.'));
      return sec;
    }
    var form = el('form');
    form.noValidate = true;
    c.inputs.forEach(function (i) { form.appendChild(field(c.id, i)); });
    var btn = el('button', 'btn btn-primary', 'חישוב');
    btn.type = 'submit';
    form.appendChild(btn);
    var out = el('div', 'calc-result');
    out.setAttribute('aria-live', 'polite');
    form.appendChild(out);

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var inputs = {};
      c.inputs.forEach(function (i) {
        var ctl = form.elements[i.name];
        inputs[i.name] = i.type === 'bool' ? ctl.checked
                       : i.type === 'options_from' ? ctl.value
                       : (ctl.value === '' ? null : Number(ctl.value));
      });
      out.textContent = '';
      Api.computeCalculator(c.id, inputs).then(function (r) {
        if (r.kind === 'none') {
          out.appendChild(el('p', 'calc-amount',
            'לפי הנתונים שהזנת, המחשבון אינו מראה זכאות לתשלום. כדאי לבדוק זאת עם עורך הדין.'));
        } else {
          out.appendChild(el('p', 'calc-amount',
            'בין ' + shekel(r.low) + ' ל-' + shekel(r.high) + KIND[r.kind] + ', בהערכה.'));
        }
        out.appendChild(el('p', 'calc-note', r.disclaimer));
        if (r.basedOn.length) {
          var src = el('p', 'calc-note');
          src.appendChild(document.createTextNode('לפי הנתונים בתוקף מ-' + date(r.basedOn[0].from)));
          var link = r.basedOn.filter(function (b) { return b.source; })[0];
          if (link) {
            src.appendChild(document.createTextNode(' · '));
            var a = el('a', null, 'מקור');
            a.href = link.source;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            src.appendChild(a);
          }
          out.appendChild(src);
        }
      }).catch(function (err) {
        out.appendChild(el('p', 'calc-note', err.message || 'החישוב נכשל. בדוק את הנתונים ונסה שוב.'));
      });
    });
    sec.appendChild(form);
    return sec;
  }

  function rightItem(r) {
    var li = el('li', 'right-item');
    li.appendChild(el('h3', null, r.title));
    if (r.provider) li.appendChild(el('p', 'calc-note', r.provider));
    li.appendChild(el('p', 'measure', r.summary));
    if (r.link) {
      var a = el('a', null, 'לפרטים ולהגשה');
      a.href = r.link;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      li.appendChild(a);
    }
    return li;
  }

  var loaded = false;

  window.loadBenefits = function () {
    if (loaded || !$('calcBody')) return;
    loaded = true;
    Api.clientCalculators().then(function (data) {
      var host = $('calcBody');
      host.textContent = '';
      if (!data.calculators.length) {
        host.appendChild(el('p', 'measure', 'אין עדיין מחשבון שמתאים לסוג התיק שלך.'));
        return;
      }
      data.calculators.forEach(function (c) { host.appendChild(calculatorCard(c)); });
    }).catch(function () { loaded = false; });

    Api.clientRights().then(function (data) {
      var host = $('rightsBody');
      host.textContent = '';
      if (!data.rights.length) {
        host.appendChild(el('p', 'measure',
          'כרגע אין ברשימה זכויות נוספות שמתאימות לנתוני התיק שלך. עורך הדין יעדכן אותך אם יימצאו.'));
        return;
      }
      var ul = el('ul', 'plain-list');
      data.rights.forEach(function (r) { ul.appendChild(rightItem(r)); });
      host.appendChild(ul);
    }).catch(function () { loaded = false; });
  };
})();
