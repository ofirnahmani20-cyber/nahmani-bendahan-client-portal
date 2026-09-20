/* ==========================================================
   viewnav.js - החלפת מסכים, לשני מפלסים

   נגזר מ-nav.js של אזור הלקוח, שעשה בדיוק את זה עבור שש
   התצוגות שם. ההבדל היחיד: שם המפה והמזהים היו קבועים בקוד,
   וכאן הם פרמטרים - כי ממשק הניהול צריך *שני* ניווטים, אחד
   לשמונת האזורים ואחד לשש הלשוניות שבתוך תיק.

   הנגישות אינה תוספת: החלפת מסך שלא מזיזה מיקוד ולא מכריזה
   משאירה את מי שגולש בקורא מסך בדיוק במקום שבו היה, בלי לדעת
   שהתוכן מתחתיו התחלף.
   ========================================================== */
(function (global) {
  'use strict';

  /**
   * יוצר ניווט מעל קבוצת תצוגות.
   *
   * views      - { key: { el: '&lt;id&gt;', title: '&lt;כותרת להכרזה&gt;' } }
   * links      - סלקטור לפריטי הניווט. כל פריט נושא data-view.
   * announce   - מזהה אזור ההכרזה (role="status"), אופציונלי.
   * titleSel   - סלקטור הכותרת שאליה עובר המיקוד בתוך התצוגה.
   * onShow     - נקרא אחרי כל החלפה, עם שם התצוגה.
   * fallback   - התצוגה שנפתחת כשהמבוקשת אינה מוכרת.
   */
  function create(options) {
    var views    = options.views;
    /* focusTitle: false - אל תעביר מיקוד לכותרת התצוגה. משמש
       כשהכותרת מוסתרת חזותית ומי שלחץ כבר מחזיק את המיקוד על
       הפריט שנלחץ; העברה לכותרת מוסתרת מעלימה את סימון המיקוד. */
    var focusTitle = options.focusTitle !== false;
    var titleSel = options.titleSel || '.view-title';
    var fallback = options.fallback || Object.keys(views)[0];
    var announce = options.announce ? document.getElementById(options.announce) : null;

    var links = Array.prototype.slice.call(
      document.querySelectorAll(options.links)
    );

    var current = null;

    function show(name, moveFocus) {
      if (!views[name]) name = fallback;

      Object.keys(views).forEach(function (key) {
        var node = document.getElementById(views[key].el);
        if (node) node.hidden = (key !== name);
      });

      links.forEach(function (btn) {
        if (btn.getAttribute('data-view') === name) {
          btn.setAttribute('aria-current', 'page');
        } else {
          btn.removeAttribute('aria-current');
        }
      });

      /* יציאה מוקדמת רק *אחרי* סימון הפריט הפעיל: כשחוזרים
         לאותה תצוגה מנתיב אחר, הסימון עדיין צריך להתעדכן. */
      var changed = (name !== current);
      current = name;

      if (changed && announce) {
        announce.textContent = 'נפתח: ' + views[name].title;
      }

      if (moveFocus && focusTitle) {
        var host = document.getElementById(views[name].el);
        var head = host && host.querySelector(titleSel);
        if (head) head.focus();
      }

      if (changed && typeof options.onShow === 'function') {
        options.onShow(name);
      }
      return changed;
    }

    links.forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        if (btn.tagName === 'A') e.preventDefault();
        if (typeof options.onLink === 'function') options.onLink();
        show(btn.getAttribute('data-view'), true);
      });
    });

    return {
      show: show,
      current: function () { return current; },
      has: function (name) { return !!views[name]; }
    };
  }

  /* ---- המגירה ----
     מועתקת במכוון מ-nav.js ולא מיובאת ממנו: nav.js נטען רק
     בדשבורד של הלקוח, והכפלת הקובץ הייתה מכניסה שני מימושים
     של אותה התנהגות. כאן זו פונקציה אחת ששני העמודים יכולים
     להשתמש בה. */
  function drawer(options) {
    var btn     = document.getElementById(options.button);
    var panel   = document.getElementById(options.panel);
    var overlay = options.overlay ? document.getElementById(options.overlay) : null;
    var closeBtn = options.close ? document.getElementById(options.close) : null;
    var outside = (options.inert || []).map(function (sel) {
      return document.querySelector(sel);
    });

    if (!btn || !panel) return { close: function () {} };

    var lastFocus = null;

    function reduced() {
      return global.matchMedia &&
             global.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    /** שאר העמוד מנוטרל כל עוד המגירה פתוחה, כדי שהמיקוד לא יברח */
    function setInert(on) {
      outside.forEach(function (node) {
        if (!node) return;
        if (on) {
          node.setAttribute('inert', '');
          node.setAttribute('aria-hidden', 'true');
        } else {
          node.removeAttribute('inert');
          node.removeAttribute('aria-hidden');
        }
      });
    }

    /** מלכודת מיקוד + ESC. inert אינו נתמך בכל דפדפן. */
    function onKeydown(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(true); return; }
      if (e.key !== 'Tab') return;

      var items = panel.querySelectorAll('a[href], button:not([disabled])');
      if (!items.length) return;
      var first = items[0];
      var last  = items[items.length - 1];

      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus();
      }
    }

    function open() {
      lastFocus = document.activeElement;
      panel.hidden = false;
      if (overlay) overlay.hidden = false;

      /* קריאת offsetWidth מאלצת חישוב פריסה, וכך המעבר רץ
         במקום לקפוץ. במכוון לא requestAnimationFrame: הוא אינו
         רץ בלשונית מוסתרת, ואז התפריט לא היה נפתח כלל. */
      void panel.offsetWidth;
      document.body.classList.add('drawer-open');
      btn.setAttribute('aria-expanded', 'true');
      setInert(true);

      var first = panel.querySelector('.drawer-link, .drawer-close');
      if (first) first.focus();
      document.addEventListener('keydown', onKeydown, true);
    }

    function close(restoreFocus) {
      if (panel.hidden) return;
      if (restoreFocus === undefined) restoreFocus = true;

      document.body.classList.remove('drawer-open');
      btn.setAttribute('aria-expanded', 'false');
      setInert(false);
      document.removeEventListener('keydown', onKeydown, true);

      global.setTimeout(function () {
        panel.hidden = true;
        if (overlay) overlay.hidden = true;
      }, reduced() ? 0 : 220);

      if (restoreFocus) {
        var back = (lastFocus && lastFocus !== document.body &&
                    document.contains(lastFocus)) ? lastFocus : btn;
        back.focus();
      }
    }

    btn.addEventListener('click', function () {
      if (panel.hidden) open(); else close();
    });
    if (closeBtn) closeBtn.addEventListener('click', function () { close(true); });
    if (overlay)  overlay.addEventListener('click', function () { close(true); });

    return { close: close };
  }

  global.ViewNav = { create: create, drawer: drawer };

})(window);
