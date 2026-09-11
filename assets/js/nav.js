/* ==========================================================
   nav.js - התפריט הנשלף והחלפת המסכים

   האתר נשאר עמוד אחד. שלוש התצוגות כבר נמצאות ב-DOM, והמעבר
   ביניהן הוא הסתרה והצגה - אין טעינה מחדש ואין איבוד של מצב.

   הנגישות כאן אינה תוספת: תפריט נשלף שלא מנהל מיקוד הוא מלכודת
   למי שגולש במקלדת או בקורא מסך. לכן המיקוד נכנס למגירה, נלכד
   בתוכה, וחוזר לכפתור בסגירה.
   ========================================================== */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var menuBtn   = $('menuBtn');
  var drawer    = $('drawer');
  var overlay   = $('drawerOverlay');
  var closeBtn  = $('drawerClose');
  var announce  = $('viewAnnounce');
  var main      = $('main');
  var topbar    = document.querySelector('.topbar');

  if (!menuBtn || !drawer) return;

  /* ---- התצוגות ---- */

  var VIEWS = {
    home:    { el: 'view-home',    title: 'האזור האישי שלי' },
    docs:    { el: 'view-docs',    title: 'מסמכים נדרשים' },
    roadmap: { el: 'view-roadmap', title: 'מפת הדרכים של ההליך' }
  };

  var current = null;

  function showView(name, moveFocus) {
    if (!VIEWS[name]) name = 'home';
    if (name === current) return;

    Object.keys(VIEWS).forEach(function (key) {
      var el = $(VIEWS[key].el);
      if (el) el.hidden = (key !== name);
    });

    // סימון הפריט הפעיל בתפריט
    links.forEach(function (btn) {
      var on = btn.getAttribute('data-view') === name;
      if (on) { btn.setAttribute('aria-current', 'page'); }
      else    { btn.removeAttribute('aria-current'); }
    });

    current = name;
    if (location.hash.slice(1) !== name) {
      history.replaceState(null, '', '#' + name);
    }

    if (announce) announce.textContent = 'נפתח המסך: ' + VIEWS[name].title;

    // המיקוד עובר לכותרת המסך, אחרת קורא מסך נשאר במקום הקודם
    if (moveFocus) {
      var head = $(VIEWS[name].el).querySelector('.view-title');
      if (head) head.focus();
    }
    window.scrollTo(0, 0);

    // מפת הדרכים מונפשת רק כשנכנסים אליה
    if (name === 'roadmap' && typeof window.playRoadmap === 'function') {
      window.playRoadmap();
    }
  }

  var links = Array.prototype.slice.call(
    drawer.querySelectorAll('.drawer-link[data-view]')
  );

  links.forEach(function (btn) {
    btn.addEventListener('click', function () {
      /* הסגירה לא מחזירה את המיקוד לכפתור התפריט - showView כבר
         העביר אותו לכותרת המסך החדש, וזה מה שקורא מסך צריך לשמוע. */
      closeDrawer(false);
      showView(btn.getAttribute('data-view'), true);
    });
  });

  /* ---- פתיחה וסגירה ---- */

  var lastFocus = null;

  function openDrawer() {
    lastFocus = document.activeElement;
    drawer.hidden = false;
    overlay.hidden = false;

    /* קריאת offsetWidth מאלצת חישוב פריסה מיידי, וכך הדפדפן רואה
       את מצב הפתיחה כשינוי ומריץ את המעבר במקום לקפוץ.
       במכוון לא requestAnimationFrame: הוא אינו רץ בלשונית מוסתרת
       או ממוזערת, ואז המגירה לא הייתה נפתחת כלל. פתיחת תפריט היא
       שינוי מצב תפקודי ואסור לה להיות תלויה בפריים אנימציה. */
    void drawer.offsetWidth;
    document.body.classList.add('drawer-open');
    menuBtn.setAttribute('aria-expanded', 'true');
    setOutsideInert(true);
    var first = drawer.querySelector('.drawer-link, .drawer-close');
    if (first) first.focus();
    document.addEventListener('keydown', onKeydown, true);
  }

  function closeDrawer(restoreFocus) {
    if (drawer.hidden) return;
    if (restoreFocus === undefined) restoreFocus = true;
    document.body.classList.remove('drawer-open');
    menuBtn.setAttribute('aria-expanded', 'false');
    setOutsideInert(false);
    document.removeEventListener('keydown', onKeydown, true);

    // ההסתרה מחכה לסיום המעבר, אחרת המגירה נעלמת באמצע
    window.setTimeout(function () {
      drawer.hidden = true;
      overlay.hidden = true;
    }, prefersReducedMotion() ? 0 : 220);

    /* המיקוד חייב לנחות במקום הגיוני, אחרת מי שגולש במקלדת נזרק
       לתחילת העמוד. ברירת המחדל היא הכפתור שפתח את התפריט;
       lastFocus משמש רק אם הוא באמת אלמנט שאפשר למקד. */
    if (restoreFocus) {
      var back = (lastFocus && lastFocus !== document.body && document.contains(lastFocus))
               ? lastFocus : menuBtn;
      back.focus();
    }
  }

  /** שאר העמוד מנוטרל כל עוד המגירה פתוחה, כדי שהמיקוד לא יברח אליו */
  function setOutsideInert(on) {
    [main, topbar].forEach(function (el) {
      if (!el) return;
      if (on) { el.setAttribute('inert', ''); el.setAttribute('aria-hidden', 'true'); }
      else    { el.removeAttribute('inert'); el.removeAttribute('aria-hidden'); }
    });
  }

  function prefersReducedMotion() {
    return window.matchMedia &&
           window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  /** מלכודת מיקוד + ESC. inert אינו נתמך בכל דפדפן, ולכן גם Tab נשמר ידנית. */
  function onKeydown(e) {
    if (e.key === 'Escape') { e.preventDefault(); closeDrawer(); return; }
    if (e.key !== 'Tab') return;

    var items = drawer.querySelectorAll(
      'a[href], button:not([disabled])'
    );
    if (!items.length) return;

    var first = items[0];
    var last  = items[items.length - 1];

    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  menuBtn.addEventListener('click', function () {
    if (drawer.hidden) openDrawer(); else closeDrawer();
  });
  /* עטיפה ולא העברה ישירה: מאזין שמועבר כמו שהוא מקבל את אובייקט
     האירוע כארגומנט הראשון, וזה היה נכנס לתוך restoreFocus. */
  if (closeBtn) closeBtn.addEventListener('click', function () { closeDrawer(true); });
  if (overlay)  overlay.addEventListener('click', function () { closeDrawer(true); });

  /* יציאה מתוך התפריט - מפעיל את אותו כפתור יציאה שכבר קיים */
  var drawerOut = $('drawerLogout');
  if (drawerOut) {
    drawerOut.addEventListener('click', function () {
      var real = $('logoutBtn');
      if (real) real.click();
    });
  }

  /* ---- ניתוב ---- */

  window.addEventListener('hashchange', function () {
    showView(location.hash.slice(1), false);
  });

  showView(location.hash.slice(1) || 'home', false);

})();
