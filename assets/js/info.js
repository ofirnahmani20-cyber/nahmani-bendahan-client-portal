/* ==========================================================
   info.js - הדף הציבורי

   אין כאן התחברות ואין נתוני לקוח. הדף נגיש לכל אחד, ולכן
   אסור שיגיע אליו מידע מהתיקים.
   ========================================================== */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  /* ---- מאמרים ---- */

  function renderArticles() {
    var list = $('articleList');
    if (!list || typeof ARTICLES === 'undefined') return;
    list.textContent = '';

    ARTICLES.forEach(function (a) {
      var li = el('li');
      li.appendChild(el('h3', null, a.title));
      li.appendChild(el('p', 'item-note', a.summary));
      li.appendChild(el('p', 'item-when', 'קריאה של כ-' + a.minutes + ' דקות'));
      list.appendChild(li);
    });
  }

  /* ---- שאלות נפוצות ----
     <details> נותן פתיחה וסגירה נגישה בלי שורת JavaScript אחת,
     והוא נתמך במקלדת ובקוראי מסך כברירת מחדל. */

  function renderFaq() {
    var host = $('faqList');
    if (!host || typeof FAQ === 'undefined') return;
    host.textContent = '';

    FAQ.forEach(function (item) {
      var d = el('details', 'faq-item');
      var s = el('summary', 'faq-q', item.q);
      d.appendChild(s);
      d.appendChild(el('p', 'faq-a', item.a));
      host.appendChild(d);
    });
  }

  renderArticles();
  renderFaq();

  /* אם הגיעו ישירות ל-#faq, נפתח את השאלה הראשונה כדי שיהיה ברור
     שהתוכן נפתח ולא נשאר רשימת כותרות */
  if (location.hash === '#faq') {
    var first = document.querySelector('.faq-item');
    if (first) first.open = true;
  }

})();
