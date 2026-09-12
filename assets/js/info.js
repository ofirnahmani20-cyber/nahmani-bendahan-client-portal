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
      var li = el('li', 'card');

      var media = el('div', 'card-media');
      var ph = el('div', 'ph');
      ph.setAttribute('aria-hidden', 'true');
      media.appendChild(ph);
      if (a.category) media.appendChild(el('span', 'card-cat', a.category));
      li.appendChild(media);

      li.appendChild(el('div', 'card-rule'));

      var body = el('div', 'card-body');
      body.appendChild(el('p', 'card-when', 'קריאה של כ-' + a.minutes + ' דקות'));
      body.appendChild(el('h3', 'card-title', a.title));
      body.appendChild(el('p', 'card-sum', a.summary));

      /* המאמרים טרם נכתבו, ולכן הכפתור מסומן כמושבת במקום להוביל
         לקישור שבור. כשייכתבו - להחליף ב-<a href> ולהסיר את disabled. */
      var btn = el('button', 'btn btn-outline', 'קראו עוד');
      btn.type = 'button';
      btn.disabled = true;
      btn.setAttribute('aria-describedby', 'articlesIntro');
      btn.title = 'המאמר יפורסם בקרוב';
      body.appendChild(btn);

      li.appendChild(body);
      list.appendChild(li);
    });
  }

  /* ---- שלבי הליווי ----
     נבנה מ-CLAIM_STAGES, אותו מקור שממנו נבנית מפת הדרכים באזור
     האישי. שינוי שם שלב שם ישתקף כאן אוטומטית.
     <details> כמו ב-FAQ: פתיחה במקלדת בלי שורת JavaScript. */

  function renderStages() {
    var host = $('stageList');
    if (!host || typeof CLAIM_STAGES === 'undefined') return;
    host.textContent = '';

    CLAIM_STAGES.forEach(function (stage) {
      var d = el('details', 'acc-item');
      var s = el('summary', 'acc-q');
      s.appendChild(el('span', 'acc-n', String(stage.id).padStart(2, '0')));
      s.appendChild(el('span', null, stage.title));
      d.appendChild(s);
      d.appendChild(el('p', 'acc-a', stage.desc));
      host.appendChild(d);
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
  renderStages();
  renderFaq();

  /* אם הגיעו ישירות ל-#faq, נפתח את השאלה הראשונה כדי שיהיה ברור
     שהתוכן נפתח ולא נשאר רשימת כותרות */
  if (location.hash === '#faq') {
    var first = document.querySelector('.faq-item');
    if (first) first.open = true;
  }

})();
