/* ==========================================================
   login.js - מסך הכניסה של הלקוח.

   חולץ מתוך index.html ב-13.09: ה-CSP שנוסף בהקשחה מגדיר
   script-src 'self', ולכן סקריפט inline נחסם בשקט - הטופס
   נשלח כ-GET רגיל והלוגיקה מעולם לא רצה. חילוץ לקובץ חיצוני
   הוא התיקון הנכון; הוספת unsafe-inline הייתה מבטלת את ההגנה.
   ========================================================== */
(function () {
  'use strict';

    /* הזהות נקבעת בשרת בלבד. אם כבר יש session תקף - פנימה. */
    Api.me({ allowUnauthorized: true })
       .then(function (who) {
         if (who && who.type === 'client') location.replace('dashboard.html');
       })
       .catch(function () { /* לא מחובר, נשארים כאן */ });

    var form   = document.getElementById('loginForm');
    var idBox  = document.getElementById('idNumber');
    var pwBox  = document.getElementById('password');
    var errBox = document.getElementById('loginError');
    var codeField = document.getElementById('codeField');
    var submitBtn = document.getElementById('submitBtn');

    /* שני שלבים באותו טופס: קודם בקשת קוד, אחר כך אימותו. */
    var stage = 'request';

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      clearError();

      var id = idBox.value.trim();
      if (id.length !== 9 || !/^\d+$/.test(id)) {
        return showError('מספר תעודת הזהות צריך להיות 9 ספרות.', idBox);
      }

      if (stage === 'request') return askForCode(id);

      var code = pwBox.value.trim();
      if (!code) return showError('צריך להזין את הקוד שנשלח.', pwBox);

      busy(true);
      Api.verifyOtp(id, code)
         .then(function () { location.href = 'dashboard.html'; })
         .catch(function (err) {
           busy(false);
           showError(err.status === 429
             ? 'יותר מדי ניסיונות. נסה שוב בעוד מספר דקות.'
             : 'הקוד שגוי או שפג תוקפו.', pwBox);
         });
    });

    function askForCode(id) {
      busy(true);
      Api.requestOtp(id)
         .then(function () {
           busy(false);
           stage = 'verify';
           codeField.hidden = false;
           submitBtn.textContent = 'כניסה';
           idBox.readOnly = true;
           pwBox.focus();
         })
         .catch(function (err) {
           busy(false);
           showError(err.status === 503
             ? 'שליחת קוד אינה זמינה כרגע. יש לפנות למשרד.'
             : (err.message || 'לא הצלחנו לשלוח קוד. נסה שוב.'), idBox);
         });
    }

    function busy(on) {
      submitBtn.disabled = on;
      submitBtn.textContent = on ? 'רגע…'
                                 : (stage === 'request' ? 'שליחת קוד' : 'כניסה');
    }

    function showError(msg, focusOn) {
      errBox.textContent = msg;
      errBox.hidden = false;
      if (focusOn) {
        focusOn.setAttribute('aria-invalid', 'true');
        focusOn.focus();
      }
    }

    function clearError() {
      errBox.hidden = true;
      idBox.removeAttribute('aria-invalid');
      pwBox.removeAttribute('aria-invalid');
    }

})();
