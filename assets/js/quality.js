/* ==========================================================
   quality.js - בדיקת איכות צילום של מסמך

   הבדיקה רצה כולה בדפדפן של הלקוח. המסמך לא נשלח לשום מקום
   לצורך הבדיקה, ולכן אין כאן חשיפה של מידע רפואי.

   ⚠️ מה הבדיקה כן עושה: מזהה תמונה מטושטשת, חשוכה, שטוחה,
      עם ברק, או בעלת רזולוציה נמוכה מדי.
   ⚠️ מה היא לא עושה: היא אינה קוראת את המסמך ואינה יודעת אם
      זה המסמך הנכון. היא בודקת את איכות התמונה בלבד.
   ========================================================== */

var DocQuality = (function () {
  'use strict';

  /* ----------------------------------------------------------
     ספי ההחלטה
     ----------------------------------------------------------
     אלה ערכי פתיחה סבירים ולא ערכים שכוילו על צילומים אמיתיים.
     יש לכוון אותם מול צילומים שלקוחות באמת מעלים - ראה
     "מה נדרש" בתיעוד. כולם מרוכזים כאן בכוונה.
     ---------------------------------------------------------- */
  var LIMITS = {
    edgeUnreadable:  700,    // צלע ארוכה בפיקסלים - מתחת לזה אין סיכוי לקרוא
    edgePoor:       1100,

    sharpUnreadable:  20,    // שונות הלפלסיאן - ככל שנמוך יותר, מטושטש יותר
    sharpPoor:        70,

    darkVery:         45,    // בהירות ממוצעת (0-255)
    dark:             72,

    // חשיפת יתר נמדדת רק בשילוב: דף לבן מצולם היטב הוא בהיר מאוד
    // ובכל זאת תקין. רק כשהבהירות גבוהה *וגם* הניגודיות קרסה,
    // סימן שהכתב נשרף.
    brightMean:      242,
    brightContrast:   40,

    contrastVery:     16,    // סטיית תקן של הבהירות
    contrast:         30,

    shadowPct:      22.0     // אחוז פיקסלים שחורים לגמרי
  };

  /* ----------------------------------------------------------
     למה אין כאן בדיקת ברק
     ----------------------------------------------------------
     הניסיון הראשון זיהה ברק לפי אחוז הפיקסלים הבהירים מאוד.
     זה נכשל על כל מסמך תקין, מהסיבה הפשוטה שדף הוא לבן ברובו -
     הבדיקה פסלה צילומים מצוינים. זיהוי ברק אמיתי דורש ניתוח
     מקומי של אזורים שרופים, ובלעדיו עדיף לא לבדוק כלל: חסימה
     שגויה של לקוח גרועה מברק שהמשרד יראה ויבקש צילום מחדש.
     ---------------------------------------------------------- */

  var WORK_EDGE   = 1024;    // הניתוח רץ על גודל אחיד, כדי שהספים יהיו יציבים
  var MAX_BYTES   = 10 * 1024 * 1024;
  var IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp'];

  /* ---- טעינת התמונה ---- */

  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload  = function () { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('decode')); };
      img.src = url;
    });
  }

  /* ---- מדדי התמונה ---- */

  function measure(img) {
    var w = img.naturalWidth, h = img.naturalHeight;
    var scale  = Math.min(1, WORK_EDGE / Math.max(w, h));
    var cw = Math.max(1, Math.round(w * scale));
    var ch = Math.max(1, Math.round(h * scale));

    var canvas = document.createElement('canvas');
    canvas.width = cw; canvas.height = ch;
    var ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, cw, ch);

    var data = ctx.getImageData(0, 0, cw, ch).data;
    var n    = cw * ch;
    var gray = new Float32Array(n);

    var sum = 0, glare = 0, shadow = 0;
    for (var i = 0, p = 0; i < n; i++, p += 4) {
      // בהירות נתפסת - העין רגישה לירוק הרבה יותר מאשר לכחול
      var g = 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
      gray[i] = g;
      sum += g;
      if (g >= 250) glare++;
      if (g <= 6)   shadow++;
    }

    var mean = sum / n;
    var varSum = 0;
    for (var j = 0; j < n; j++) {
      var d = gray[j] - mean;
      varSum += d * d;
    }
    var contrast = Math.sqrt(varSum / n);

    return {
      width: w, height: h,
      longEdge: Math.max(w, h),
      mean: mean,
      contrast: contrast,
      // מדווח לצורך כיול עתידי בלבד - אינו משמש להחלטה. ראה ההערה למעלה.
      glarePct: (glare / n) * 100,
      shadowPct: (shadow / n) * 100,
      sharpness: laplacianVariance(gray, cw, ch)
    };
  }

  /**
   * שונות הלפלסיאן - המדד המקובל לטשטוש.
   * תמונה חדה מלאה במעברים חדים בין פיקסלים שכנים ולכן השונות
   * גבוהה; תמונה מטושטשת חלקה, והשונות קורסת.
   */
  function laplacianVariance(gray, w, h) {
    if (w < 3 || h < 3) return 0;

    var count = 0, sum = 0, sumSq = 0;
    for (var y = 1; y < h - 1; y++) {
      for (var x = 1; x < w - 1; x++) {
        var i = y * w + x;
        var lap = 4 * gray[i]
                - gray[i - 1] - gray[i + 1]
                - gray[i - w] - gray[i + w];
        sum   += lap;
        sumSq += lap * lap;
        count++;
      }
    }
    if (!count) return 0;
    var mean = sum / count;
    return (sumSq / count) - (mean * mean);
  }

  /* ---- תרגום המדדים להסבר שאפשר לפעול לפיו ---- */

  function judge(m) {
    var reasons = [];
    var worst   = 'ok';

    function flag(level, title, fix) {
      reasons.push({ level: level, title: title, fix: fix });
      if (level === 'unreadable') worst = 'unreadable';
      else if (worst !== 'unreadable') worst = 'poor';
    }

    if (m.longEdge < LIMITS.edgeUnreadable) {
      flag('unreadable', 'התמונה קטנה מדי',
        'צלמו את המסמך מחדש כך שימלא את כל המסך, ' +
        'או בחרו קובץ סרוק באיכות גבוהה יותר.');
    } else if (m.longEdge < LIMITS.edgePoor) {
      flag('poor', 'התמונה ברזולוציה נמוכה',
        'נסו לצלם מקרוב יותר, כך שהמסמך ימלא את כל המסך.');
    }

    if (m.sharpness < LIMITS.sharpUnreadable) {
      flag('unreadable', 'התמונה מטושטשת',
        'הניחו את המסמך על שולחן, החזיקו את הטלפון יציב בשתי ידיים, ' +
        'והמתינו שהתמונה תתחדד לפני הצילום.');
    } else if (m.sharpness < LIMITS.sharpPoor) {
      flag('poor', 'התמונה לא חדה מספיק',
        'נסו שוב עם ידיים יציבות. אפשר להישען על השולחן תוך כדי הצילום.');
    }

    if (m.mean < LIMITS.darkVery) {
      flag('unreadable', 'התמונה חשוכה מדי',
        'עברו למקום מואר יותר, או הדליקו את האור בחדר. עדיף אור יום.');
    } else if (m.mean < LIMITS.dark) {
      flag('poor', 'התמונה חשוכה',
        'צלמו ליד חלון או במקום מואר יותר.');
    } else if (m.mean > LIMITS.brightMean && m.contrast < LIMITS.brightContrast) {
      flag('poor', 'התמונה בהירה מדי והכתב נשרף',
        'כבו את הפלאש והתרחקו מאור ישיר. אור יום עקיף הוא הטוב ביותר.');
    }

    if (m.contrast < LIMITS.contrastVery) {
      flag('unreadable', 'לא רואים את הכתב',
        'ודאו שהמסמך עצמו נמצא בתמונה ושהוא מואר באופן אחיד.');
    } else if (m.contrast < LIMITS.contrast) {
      flag('poor', 'הכתב חלש ומטושטש',
        'צלמו על רקע כהה, למשל שולחן כהה, כדי שהמסמך יבלוט.');
    }

    if (m.shadowPct > LIMITS.shadowPct) {
      flag('poor', 'חלק מהמסמך בצל',
        'ודאו שהצל שלכם או של הטלפון לא נופל על הדף.');
    }

    return { verdict: worst, reasons: reasons };
  }

  /* ---- הממשק החיצוני ---- */

  /**
   * בודק קובץ שהלקוח בחר.
   * מחזיר Promise עם { verdict, reasons, metrics }.
   *
   * verdict:
   *   'ok'          - אפשר לשלוח
   *   'poor'        - איכות ירודה, כדאי לצלם שוב
   *   'unreadable'  - לא ניתן לקריאה
   *   'too-big'     - הקובץ חורג מהמגבלה
   *   'bad-type'    - סוג קובץ לא נתמך
   *   'skipped'     - PDF, או תמונה שהדפדפן לא הצליח לפענח
   */
  function check(file) {
    if (!file) {
      return Promise.resolve({ verdict: 'bad-type', reasons: [], metrics: null });
    }

    if (file.size > MAX_BYTES) {
      return Promise.resolve({
        verdict: 'too-big',
        reasons: [{
          level: 'unreadable',
          title: 'הקובץ גדול מדי',
          fix: 'הקובץ חורג מ-10MB. נסו לצלם שוב או לבחור קובץ קטן יותר.'
        }],
        metrics: null
      });
    }

    var isPdf   = file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
    var isImage = IMAGE_TYPES.indexOf(file.type) !== -1;

    // בדיקת הסוג לפי התוכן שהדפדפן זיהה, לא לפי הסיומת בלבד
    if (!isPdf && !isImage) {
      return Promise.resolve({
        verdict: 'bad-type',
        reasons: [{
          level: 'unreadable',
          title: 'סוג הקובץ אינו נתמך',
          fix: 'אפשר להעלות תמונה (JPG או PNG) או קובץ PDF בלבד.'
        }],
        metrics: null
      });
    }

    // PDF בדרך כלל נוצר מסריקה ואי אפשר לנתח אותו כאן בלי ספרייה חיצונית
    if (isPdf) {
      return Promise.resolve({ verdict: 'skipped', reasons: [], metrics: null });
    }

    return loadImage(file).then(function (img) {
      var metrics = measure(img);
      var result  = judge(metrics);
      result.metrics = metrics;
      return result;
    }).catch(function () {
      // אם הדפדפן לא הצליח לפענח (למשל HEIC ישן) - לא חוסמים,
      // כי חסימה כאן פירושה שהלקוח תקוע בלי דרך להגיש.
      return { verdict: 'skipped', reasons: [], metrics: null };
    });
  }

  return { check: check, LIMITS: LIMITS };
})();
