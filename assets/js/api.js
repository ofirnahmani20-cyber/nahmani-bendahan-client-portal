/* ==========================================================
   api.js - השכבה היחידה שמדברת עם השרת.

   למה קובץ נפרד
   --------------
   עד 13.09 הדפדפן היה מקור האמת: data.js החזיק את כל הלקוחות,
   את הסיסמאות ואת חוקי העסק, ו-CaseStore כתב שינויים ל-
   localStorage. הזהות נקבעה בדפדפן, ולכן אפשר היה להתחזות לכל
   לקוח בשורה אחת בקונסולה.

   עכשיו הדפדפן אינו יודע דבר. הוא שואל את השרת מי המשתמש
   (/api/me), מקבל ממנו נתונים, ושולח אליו פעולות. אין כאן
   מצב מקומי שאפשר לזייף.

   אין fallback
   ------------
   כישלון שרת מציג שגיאה. הוא אינו נופל חזרה ל-localStorage
   ואינו מציג נתונים ישנים - מסך שמראה מידע שגוי על תיק משפטי
   גרוע ממסך שאומר "לא הצלחנו לטעון".
   ========================================================== */
(function (global) {
  'use strict';

  var CSRF_COOKIE = 'portal_csrf';

  function readCookie(name) {
    var parts = ('; ' + document.cookie).split('; ' + name + '=');
    return parts.length === 2 ? parts.pop().split(';').shift() : null;
  }

  /** שגיאה שמגיעה מהשרת, עם הקוד כדי שהקורא יוכל להבחין. */
  function ApiError(status, message) {
    this.name = 'ApiError';
    this.status = status;
    this.message = message || 'הפעולה נכשלה.';
  }
  ApiError.prototype = Object.create(Error.prototype);

  function request(method, path, body, options) {
    options = options || {};
    var init = {
      method: method,
      /* same-origin: ה-cookie של ה-session חייב להישלח, והוא
         HttpOnly ולכן ה-JS אינו רואה אותו ואינו יכול לצרף אותו ידנית. */
      credentials: 'same-origin',
      headers: {},
    };

    if (method !== 'GET') {
      /* טוקן ה-CSRF יושב ב-cookie קריא ונשלח בכותרת. השרת משווה
         בין השניים - תבנית double-submit. */
      var csrf = readCookie(CSRF_COOKIE);
      if (csrf) init.headers['X-CSRF-Token'] = csrf;
    }

    if (body instanceof FormData) {
      init.body = body;               // הדפדפן קובע boundary בעצמו
    } else if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }

    return fetch(path, init).then(function (response) {
      if (response.status === 204) return null;

      return response.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }

        if (response.ok) return data;

        /* 401 על נתיב מוגן = ה-session נגמר. ההפניה לכניסה היא
           התנהגות נכונה, אך לא כשאנחנו *בתוך* מסך הכניסה. */
        if (response.status === 401 && !options.allowUnauthorized) {
          Api.onUnauthorized();
        }
        throw new ApiError(response.status, (data && data.detail) || null);
      });
    }, function () {
      throw new ApiError(0, 'אין חיבור לשרת. בדוק את החיבור לרשת ונסה שוב.');
    });
  }

  var Api = {
    ApiError: ApiError,

    /** נקרא כשה-session פג. נדרס בדפים שמטפלים בזה בעצמם. */
    onUnauthorized: function () {
      if (location.pathname.indexOf('index.html') === -1) {
        location.replace('index.html');
      }
    },

    get:  function (path, options) { return request('GET', path, undefined, options); },
    post: function (path, body, options) { return request('POST', path, body, options); },

    /* חשוף עבור בקשה שאינה יכולה לעבור דרך request() - כרגע רק
       הניתוח המקצועי, שקורא את התשובה כזרם. */
    csrfToken: function () { return readCookie(CSRF_COOKIE); },

    // ---- זהות ----
    me: function (options) { return Api.get('/api/me', options); },

    // ---- לקוח ----
    requestOtp: function (nationalId) {
      return Api.post('/api/client/auth/request-otp',
                      { national_id: nationalId }, { allowUnauthorized: true });
    },
    verifyOtp: function (nationalId, code) {
      return Api.post('/api/client/auth/verify-otp',
                      { national_id: nationalId, code: code },
                      { allowUnauthorized: true });
    },
    clientLogout: function () { return Api.post('/api/client/auth/logout'); },
    clientCases:  function () { return Api.get('/api/client/cases'); },
    clientCase:   function (id) { return Api.get('/api/client/cases/' + encodeURIComponent(id)); },

    replyToDocument: function (documentId, kind, text) {
      return Api.post('/api/client/documents/' +
                      encodeURIComponent(documentId) + '/replies',
                      { kind: kind, text: text || null });
    },

    uploadFile: function (documentId, file) {
      var form = new FormData();
      form.append('file', file);
      return Api.post('/api/client/documents/' +
                      encodeURIComponent(documentId) + '/files', form);
    },

    // ---- צוות ----
    staffLogin: function (email, password) {
      return Api.post('/api/office/auth/login',
                      { email: email, password: password },
                      { allowUnauthorized: true });
    },
    staffLogout: function () { return Api.post('/api/office/auth/logout'); },
    officeCases: function () { return Api.get('/api/office/cases'); },
    officeCase:  function (id) { return Api.get('/api/office/cases/' + encodeURIComponent(id)); },

    setStage: function (caseId, stageTemplateId, note) {
      return Api.post('/api/office/cases/' + encodeURIComponent(caseId) + '/stage-events',
                      { stage_template_id: stageTemplateId, note: note || null });
    },
    reviewDocument: function (documentId, decision, rejectReason) {
      return Api.post('/api/office/documents/' + encodeURIComponent(documentId) + '/review',
                      { decision: decision, reject_reason: rejectReason || null });
    },
    addDocument: function (caseId, name, guidance, isRequired) {
      return Api.post('/api/office/cases/' + encodeURIComponent(caseId) + '/documents',
                      { name: name, guidance: guidance, is_required: !!isRequired });
    },
    sendMessage: function (caseId, title, body, important) {
      return Api.post('/api/office/cases/' + encodeURIComponent(caseId) + '/messages',
                      { title: title, body: body, is_important: !!important });
    },
    recordDecision: function (caseId, decision) {
      return Api.post('/api/office/cases/' + encodeURIComponent(caseId) + '/decisions',
                      decision);
    },
    cancelDocument: function (documentId) {
      return Api.post('/api/office/documents/' +
                      encodeURIComponent(documentId) + '/cancel');
    },
    documentTemplates: function (caseId) {
      return Api.get('/api/office/cases/' + encodeURIComponent(caseId) +
                     '/document-templates');
    },
    auditLog: function (caseId) {
      return Api.get('/api/office/audit-log' +
                     (caseId ? '?case_id=' + encodeURIComponent(caseId) : ''));
    },

    // ---- משימות ----
    /* המיון והמדרגות מגיעים מהשרת. הדפדפן אינו מחשב דחיפות
       ואינו ממיין מחדש - אחרת שני מסכים היו יכולים לסטות. */
    officeTasks: function (query) {
      var parts = [];
      query = query || {};
      if (query.assignee) parts.push('assignee=' + encodeURIComponent(query.assignee));
      if (query.status)   parts.push('status=' + encodeURIComponent(query.status));
      if (query.bucket)   parts.push('bucket=' + encodeURIComponent(query.bucket));
      if (query.includeDone) parts.push('include_done=true');
      return Api.get('/api/office/tasks' + (parts.length ? '?' + parts.join('&') : ''));
    },
    caseTasks: function (caseId) {
      return Api.get('/api/office/cases/' + encodeURIComponent(caseId) + '/tasks');
    },
    taskTypes: function () { return Api.get('/api/office/task-types'); },
    officeStaff: function () { return Api.get('/api/office/staff'); },

    createTask: function (caseId, task) {
      return Api.post('/api/office/cases/' + encodeURIComponent(caseId) + '/tasks',
                      task);
    },
    updateTask: function (taskId, task) {
      return Api.post('/api/office/tasks/' + encodeURIComponent(taskId) + '/update',
                      task);
    },
    reassignTask: function (taskId, userId) {
      return Api.post('/api/office/tasks/' + encodeURIComponent(taskId) + '/assignee',
                      { assignee_user_id: userId || null });
    },
    completeTask: function (taskId) {
      return Api.post('/api/office/tasks/' + encodeURIComponent(taskId) + '/complete');
    },
    reopenTask: function (taskId) {
      return Api.post('/api/office/tasks/' + encodeURIComponent(taskId) + '/reopen');
    },
  };

  global.Api = Api;

})(window);
