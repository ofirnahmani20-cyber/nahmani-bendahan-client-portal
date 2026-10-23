/* ==========================================================
   admin-benefits.js - נתוני המחשבונים וקטלוג הזכויות (23.10)
   ----------------------------------------------------------
   במסך ההגדרות. כל הצוות רואה; רק מנהל משנה (השרת אוכף).

   ערך חדש אינו דורס ערך קיים: הוא נכנס כשורה עם תאריך תחולה, כדי
   שאפשר יהיה לדעת מה הוצג ללקוח בכל יום. עדכון אוטומטי שנעצר
   (rates_watch.py) מוצג כ"ממתין" עם אישור ודחייה.
   ========================================================== */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var isAdmin = false;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function input(labelText, type, attrs) {
    var wrap = el('div', 'field');
    var id = 'ab' + Math.random().toString(36).slice(2, 9);
    var label = el('label', null, labelText);
    label.htmlFor = id;
    var ctl = el(type === 'textarea' ? 'textarea' : 'input');
    if (type !== 'textarea') ctl.type = type || 'text';
    ctl.id = id;
    Object.keys(attrs || {}).forEach(function (k) { ctl.setAttribute(k, attrs[k]); });
    wrap.appendChild(label);
    wrap.appendChild(ctl);
    return { wrap: wrap, ctl: ctl };
  }

  function errorBox() {
    var b = el('p', 'alert alert-error');
    b.setAttribute('role', 'alert');
    b.hidden = true;
    return b;
  }

  function fail(box, err) {
    box.textContent = err.message || 'הפעולה נכשלה.';
    box.hidden = false;
  }

  /* ---------------- פרמטרים ---------------- */

  function calcBlock(c) {
    var sec = el('section', 'block');
    sec.appendChild(el('h3', null, c.title));
    sec.appendChild(el('p', 'item-note', 'נדרש: ' + c.params.map(function (p) {
      return p.key + ' (' + p.label + ')';
    }).join(' · ')));

    /* הערך העדכני לכל מפתח, ומתחתיו מה שממתין לאישור */
    var seen = {};
    var ul = el('ul', 'plain-list');
    c.values.forEach(function (v) {
      if (v.status === 'pending') {
        var li = el('li', 'money-row');
        li.appendChild(el('span', null, 'ממתין לאישור: ' + v.key + ' = ' + v.value +
          ' (עדכון אוטומטי נעצר - שינוי חריג או דף שלא נקרא)'));
        if (isAdmin) {
          ['approve', 'reject'].forEach(function (action) {
            var b = el('button', 'btn btn-sm ' + (action === 'approve' ? 'btn-primary' : 'btn-quiet'),
                       action === 'approve' ? 'אישור' : 'דחייה');
            b.type = 'button';
            b.addEventListener('click', function () {
              Api.reviewCalcParameter(v.id, action).then(load, function (e) { alert(e.message); });
            });
            li.appendChild(b);
          });
        }
        ul.appendChild(li);
        return;
      }
      if (v.status !== 'active' || seen[v.key]) return;
      seen[v.key] = true;
      ul.appendChild(el('li', null, v.key + (v.label ? ' · ' + v.label : '') + ' = ' + v.value +
        ' · בתוקף מ-' + v.from + (v.origin === 'auto' ? ' · עודכן אוטומטית' : '') +
        (v.source ? ' · יש מקור לבדיקה יומית' : '')));
    });
    if (!ul.childNodes.length) ul.appendChild(el('li', 'item-note', 'לא הוזנו נתונים - המחשבון מוצג ללקוח כ"בקרוב".'));
    sec.appendChild(ul);

    if (!isAdmin) return sec;

    var form = el('form', 'money-form');
    form.noValidate = true;
    var key = input('מפתח', 'text', { dir: 'ltr', placeholder: c.params[0].key });
    var label = input('תיאור לתצוגה (למשל "74%")', 'text');
    var value = input('ערך', 'number', { step: 'any', dir: 'ltr' });
    var from = input('בתוקף מתאריך', 'date');
    var src = input('דף מקור לבדיקה יומית (לא חובה)', 'url', { dir: 'ltr', placeholder: 'https://www.btl.gov.il/...' });
    var pat = input('ביטוי שתופס את המספר בדף (קבוצה 1)', 'text', { dir: 'ltr' });
    [key, label, value, from, src, pat].forEach(function (f) { form.appendChild(f.wrap); });
    var err = errorBox();
    form.appendChild(err);
    var save = el('button', 'btn btn-primary btn-sm', 'הוספת ערך');
    save.type = 'submit';
    form.appendChild(save);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      err.hidden = true;
      Api.addCalcParameter({
        calculator: c.id, key: key.ctl.value.trim(), label: label.ctl.value.trim() || null,
        value: Number(value.ctl.value), effective_from: from.ctl.value || null,
        source_url: src.ctl.value.trim() || null, extract_pattern: pat.ctl.value.trim() || null
      }).then(load, function (x) { fail(err, x); });
    });
    sec.appendChild(form);
    return sec;
  }

  /* ---------------- זכויות ---------------- */

  var STATUS = { draft: 'טיוטה - לא מוצג ללקוחות', published: 'מפורסם', retired: 'הוסר' };

  function rightForm(r, known) {
    var form = el('form', 'money-form');
    form.noValidate = true;
    var title = input('כותרת', 'text', { maxlength: 160 });
    var summary = input('תיאור ללקוח', 'textarea', { rows: 4, maxlength: 2000 });
    var provider = input('הגוף שנותן (לא חובה)', 'text');
    var link = input('קישור (https)', 'url', { dir: 'ltr' });
    var cond = input('תנאים (JSON). מוכרים: ' + known.join(', '), 'text', { dir: 'ltr' });
    title.ctl.value = r ? r.title : '';
    summary.ctl.value = r ? r.summary : '';
    provider.ctl.value = r && r.provider ? r.provider : '';
    link.ctl.value = r && r.link ? r.link : '';
    cond.ctl.value = JSON.stringify(r ? r.conditions : {});
    [title, summary, provider, link, cond].forEach(function (f) { form.appendChild(f.wrap); });
    var err = errorBox();
    form.appendChild(err);
    var save = el('button', 'btn btn-primary btn-sm', r ? 'שמירה (חוזר לטיוטה)' : 'הוספת זכות');
    save.type = 'submit';
    form.appendChild(save);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      err.hidden = true;
      var conditions;
      try { conditions = JSON.parse(cond.ctl.value || '{}'); }
      catch (x) { return fail(err, { message: 'התנאים אינם JSON תקין.' }); }
      Api.saveRight({ id: r ? r.id : null, title: title.ctl.value.trim(),
                      summary: summary.ctl.value.trim(), provider: provider.ctl.value.trim() || null,
                      link_url: link.ctl.value.trim() || null, conditions: conditions })
        .then(load, function (x) { fail(err, x); });
    });
    return form;
  }

  function rightRow(r, known) {
    var li = el('li', 'right-row');
    li.appendChild(el('h4', null, r.title));
    li.appendChild(el('p', 'item-note', STATUS[r.status] || r.status));
    li.appendChild(el('p', null, r.summary));
    if (!isAdmin) return li;
    var acts = el('div', 'binder-acts');
    if (r.status !== 'published') {
      var pub = el('button', 'btn btn-primary btn-sm', 'אישור ופרסום ללקוחות');
      pub.type = 'button';
      pub.addEventListener('click', function () {
        if (!confirm('לפרסם? הנוסח יוצג ללקוחות שעומדים בתנאים, ושמך יירשם כמאשר.')) return;
        Api.setRightStatus(r.id, 'published').then(load, function (x) { alert(x.message); });
      });
      acts.appendChild(pub);
    } else {
      var off = el('button', 'btn btn-quiet btn-sm', 'הסרה מהפרסום');
      off.type = 'button';
      off.addEventListener('click', function () {
        Api.setRightStatus(r.id, 'retired').then(load, function (x) { alert(x.message); });
      });
      acts.appendChild(off);
    }
    var edit = el('button', 'btn btn-outline btn-sm', 'עריכה');
    edit.type = 'button';
    var slot = el('div');
    edit.addEventListener('click', function () {
      slot.textContent = '';
      slot.appendChild(rightForm(r, known));
    });
    acts.appendChild(edit);
    li.appendChild(acts);
    li.appendChild(slot);
    return li;
  }

  /* ---------------- טעינה ---------------- */

  function load() {
    var calcHost = $('calcParamsBody');
    var rightsHost = $('rightsAdminBody');
    if (!calcHost || !rightsHost) return Promise.resolve();
    return Promise.all([Api.calcParameters(), Api.officeRights()]).then(function (res) {
      calcHost.textContent = '';
      res[0].calculators.forEach(function (c) { calcHost.appendChild(calcBlock(c)); });

      rightsHost.textContent = '';
      var ul = el('ul', 'plain-list');
      res[1].rights.forEach(function (r) { ul.appendChild(rightRow(r, res[1].knownConditions)); });
      if (!res[1].rights.length) ul.appendChild(el('li', 'item-note', 'הקטלוג ריק.'));
      rightsHost.appendChild(ul);
      if (isAdmin) {
        rightsHost.appendChild(el('h3', null, 'זכות חדשה'));
        rightsHost.appendChild(rightForm(null, res[1].knownConditions));
      }
    });
  }

  window.AdminBenefits = {
    load: function (staff) {
      isAdmin = !!(staff && staff.role === 'admin');
      return load();
    }
  };
})();
