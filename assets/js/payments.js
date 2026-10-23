/* ==========================================================
   payments.js - "התשלומים שלך" באזור הלקוח (23.10, docs/10)
   ----------------------------------------------------------
   מה הלקוח קיבל, מה צפוי לו, ושכר הטרחה עם פירוט החישוב.

   אין כאן חשבון: המספרים מגיעים מהשרת (server/fees.py), אותו
   חישוב שהמשרד רואה. מחשבון שני בדפדפן היה סוטה ממנו ביום שבו
   אחד מהם מתוקן - ולקוח היה רואה סכום שונה ממה שנגבה ממנו.
   ========================================================== */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var KIND = { retro: 'תשלום רטרואקטיבי', grant: 'מענק', monthly: 'קצבה חודשית', other: 'תשלום' };
  var PAYER = { nii: 'ביטוח לאומי', mod: 'משרד הביטחון', insurer: 'חברת הביטוח',
                pension_fund: 'קרן הפנסיה', other: '' };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function shekel(v) {
    var n = Number(v || 0);
    return '₪' + n.toLocaleString('he-IL', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function date(iso) {
    if (!iso) return '';
    var p = iso.slice(0, 10).split('-');
    return p[2] + '.' + p[1] + '.' + p[0];
  }

  function row(dl, label, value) {
    dl.appendChild(el('dt', null, label));
    dl.appendChild(el('dd', 'num', value));
  }

  function renderCase(c) {
    var sec = el('section', 'pay-case');
    sec.appendChild(el('h2', null, c.claimType + ' · תיק ' + c.caseNumber));

    /* ---- מה הגוף משלם לך ---- */
    var b = c.calc.benefits;
    var dl = el('dl', 'pay-sum');
    row(dl, 'קיבלת עד היום', shekel(b.received));
    if (Number(b.expected)) row(dl, 'צפוי להגיע אליך', shekel(b.expected));
    if (Number(b.monthly)) row(dl, 'קצבה חודשית', shekel(b.monthly) + ' לחודש');
    if (Number(b.monthlyExpected)) row(dl, 'קצבה שנקבעה ותתחיל בקרוב', shekel(b.monthlyExpected) + ' לחודש');
    sec.appendChild(dl);

    if (c.payments.length) {
      var ul = el('ul', 'plain-list pay-list');
      c.payments.forEach(function (p) {
        var t = (KIND[p.kind] || 'תשלום') + (PAYER[p.payer] ? ' מ' + PAYER[p.payer] : '') +
                ': ' + shekel(p.amount) + (p.kind === 'monthly' ? ' לחודש' : '');
        if (p.kind === 'monthly') {
          t += ', החל מ-' + date(p.periodStart) + (p.periodEnd ? ' ועד ' + date(p.periodEnd) : '');
        } else if (p.status === 'received') {
          t += ', שולם ב-' + date(p.receivedDate);
        } else {
          t += p.expectedDate ? ', צפוי בסביבות ' + date(p.expectedDate) : ', טרם שולם';
        }
        ul.appendChild(el('li', null, t));
      });
      sec.appendChild(ul);
    }

    /* ---- שכר הטרחה ---- */
    var f = c.calc.fee;
    if (f) {
      sec.appendChild(el('h3', null, 'שכר הטרחה למשרד'));
      var how = 'לפי ההסכם: ' + f.percent + '%';
      var on = (c.agreement.appliesTo || []).map(function (k) {
        return k === 'monthly' ? c.agreement.monthlyMonths + ' חודשי הקצבה הראשונים' : (KIND[k] || k);
      });
      if (Number(f.percent)) how += ' מ' + on.join(', ');
      if (Number(f.fixed)) how += (Number(f.percent) ? ' ועוד ' : ' - ') + shekel(f.fixed) + ' קבוע';
      how += f.vatIncluded ? ' (כולל מע"מ).' : ', ועליו מע"מ ' + f.vatPercent + '%.';
      if (f.capped) how += ' הסכום הכולל הוגבל לתקרה של ' + shekel(f.cap) + '.';
      sec.appendChild(el('p', 'measure', how));

      var calc = el('dl', 'pay-sum');
      row(calc, 'הסכום שהאחוז חל עליו (מה שכבר שולם לך)', shekel(f.baseNow));
      row(calc, 'שכר טרחה לפני מע"מ', shekel(f.beforeVatNow));
      if (!f.vatIncluded) row(calc, 'מע"מ', shekel(f.vatNow));
      row(calc, 'סך הכול עד היום', shekel(f.dueNow));
      row(calc, 'שילמת', shekel(f.paid));
      row(calc, Number(f.balance) < 0 ? 'יתרת זכות לטובתך' : 'נותר לתשלום',
          shekel(Math.abs(Number(f.balance))));
      if (Number(f.dueLater)) {
        row(calc, 'יגיע בהמשך, כשיתקבלו התשלומים הצפויים', shekel(f.dueLater));
      }
      sec.appendChild(calc);
    }
    return sec;
  }

  var loaded = false;

  /* נקרא מ-nav.js בכניסה למסך. פעם אחת לכל טעינת דף. */
  window.loadPayments = function () {
    if (loaded || !$('paymentsBody')) return;
    loaded = true;
    var host = $('paymentsBody');
    Api.clientPayments().then(function (data) {
      host.textContent = '';
      if (!data.cases.length) {
        host.appendChild(el('p', 'measure',
          'עדיין לא נרשמו תשלומים בתיק שלך. כשתתקבל החלטה ויתחילו תשלומים, תראה אותם כאן.'));
        return;
      }
      data.cases.forEach(function (c) { host.appendChild(renderCase(c)); });
    }).catch(function (err) {
      loaded = false;
      host.textContent = '';
      host.appendChild(el('p', 'measure', err.message || 'לא הצלחנו לטעון את התשלומים. נסה שוב.'));
    });
  };
})();
