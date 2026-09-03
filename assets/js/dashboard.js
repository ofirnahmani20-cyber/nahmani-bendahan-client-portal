/* ==========================================================
   dashboard.js - לוגיקת האזור האישי
   ========================================================== */

const user = Auth.requireLogin();
if (user) {

  const caseFile = CaseStore.load(user.idNumber);
  let activeFilter = 'all';
  let targetDocId = null;   // המסמך שאליו משויכת ההעלאה הנוכחית

  const $ = id => document.getElementById(id);
  const initials = name => name.split(' ').map(w => w[0]).slice(0, 2).join('');
  const fmt = d => d ? new Date(d).toLocaleDateString('he-IL', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—';

  const STATUS_TEXT = { missing: 'חסר', 'pending-review': 'בבדיקת המשרד', approved: 'התקבל ואושר' };
  const STATUS_ICON = { missing: '⚠️', 'pending-review': '🕓', approved: '✅' };

  /* ---------- כותרת עליונה ---------- */
  $('userInitials').textContent = initials(user.name);
  $('userName').textContent = user.name;
  $('greeting').textContent = 'שלום ' + user.name;
  $('caseSummary').textContent =
    'תיק מספר ' + caseFile.caseNumber + ' · ' + caseFile.claimType + ' · נפתח בתאריך ' + fmt(caseFile.openedAt);
  $('logoutBtn').addEventListener('click', () => Auth.logout());

  /* ---------- כרטיס סטטוס ---------- */
  const stage = CLAIM_STAGES.find(s => s.id === caseFile.currentStage);
  $('stageTitle').textContent = 'שלב ' + stage.id + ' מתוך ' + CLAIM_STAGES.length + ': ' + stage.title;
  $('stageDesc').textContent = stage.desc;
  $('metaCase').textContent = caseFile.caseNumber;
  $('metaType').textContent = caseFile.claimType;
  $('metaBranch').textContent = caseFile.branch;
  $('metaSince').textContent = fmt(caseFile.stageEnteredAt);

  const pct = Math.round((caseFile.currentStage / CLAIM_STAGES.length) * 100);
  $('progressText').textContent = 'הושלמו ' + (caseFile.currentStage - 1) + ' שלבים מתוך ' + CLAIM_STAGES.length;
  $('progressPct').textContent = pct + '%';
  setTimeout(() => { $('progressFill').style.width = pct + '%'; }, 120);

  /* ---------- ציר השלבים ---------- */
  $('timeline').innerHTML = CLAIM_STAGES.map(s => {
    const state = s.id < caseFile.currentStage ? 'done'
                : s.id === caseFile.currentStage ? 'current' : 'pending';
    const pillText = { done: 'הושלם', current: 'בשלב זה כעת', pending: 'ממתין' }[state];
    const date = state === 'done' ? caseFile.stageDates[s.id]
               : state === 'current' ? caseFile.stageEnteredAt : null;
    const dateLine = date
      ? '<div class="step-date">🗓️ ' + (state === 'done' ? 'הושלם ב-' : 'החל ב-') + fmt(date) + '</div>'
      : '';
    return '' +
      '<div class="step ' + state + '">' +
        '<div class="step-dot">' + (state === 'done' ? '✓' : s.id) + '</div>' +
        '<div class="step-body">' +
          '<h4>' + s.title + ' <span class="pill ' + state + '">' + pillText + '</span></h4>' +
          '<p>' + s.desc + '</p>' + dateLine +
        '</div>' +
      '</div>';
  }).join('');

  /* ---------- הצעדים הבאים ---------- */
  $('nextSteps').innerHTML = caseFile.nextSteps.map((n, i) =>
    '<div class="next-item">' +
      '<div class="num">' + (i + 1) + '</div>' +
      '<div><h5>' + n.title + '</h5><p>' + n.desc + '</p>' +
      '<div class="eta">🕒 ' + n.eta + '</div></div>' +
    '</div>'
  ).join('');

  /* ---------- מסמכים ---------- */
  function countBy(status) {
    return caseFile.documents.filter(d => d.status === status).length;
  }

  function renderTabs() {
    const tabs = [
      { key: 'all',            label: 'הכל',            n: caseFile.documents.length },
      { key: 'missing',        label: 'מסמכים חסרים',   n: countBy('missing') },
      { key: 'pending-review', label: 'בבדיקת המשרד',   n: countBy('pending-review') },
      { key: 'approved',       label: 'אושרו',          n: countBy('approved') }
    ];
    $('docTabs').innerHTML = tabs.map(t =>
      '<button class="doc-tab' + (activeFilter === t.key ? ' active' : '') + '" data-filter="' + t.key + '">' +
        t.label + '<span class="count">' + t.n + '</span></button>'
    ).join('');

    $('docTabs').querySelectorAll('.doc-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        activeFilter = btn.dataset.filter;
        renderTabs();
        renderDocs();
      });
    });
  }

  function renderDocs() {
    const list = activeFilter === 'all'
      ? caseFile.documents
      : caseFile.documents.filter(d => d.status === activeFilter);

    if (!list.length) {
      $('docList').innerHTML =
        '<div class="empty"><span class="ic">🎉</span>אין מסמכים בקטגוריה זו.</div>';
      return;
    }

    $('docList').innerHTML = list.map(d => {
      const reqBadge = d.required && d.status === 'missing' ? '<span class="req-badge">חובה</span>' : '';
      const fileLine = d.file
        ? '<div class="file-name">📎 ' + d.file + ' · הועלה ב-' + fmt(d.date) + '</div>'
        : '';
      const action = d.status === 'approved'
        ? '<button class="btn btn-ghost btn-sm" data-upload="' + d.id + '">החלפה</button>'
        : '<button class="btn btn-gold btn-sm" data-upload="' + d.id + '">' +
            (d.status === 'missing' ? 'העלאה' : 'החלפה') + '</button>';

      return '' +
        '<div class="doc-row ' + d.status + '">' +
          '<div class="doc-icon">' + STATUS_ICON[d.status] + '</div>' +
          '<div class="doc-info">' +
            '<h4>' + d.name + reqBadge + '</h4>' +
            '<p>' + d.note + '</p>' + fileLine +
          '</div>' +
          '<div class="doc-actions">' +
            '<span class="status-tag ' + d.status + '">' + STATUS_TEXT[d.status] + '</span>' +
            action +
          '</div>' +
        '</div>';
    }).join('');

    $('docList').querySelectorAll('[data-upload]').forEach(btn => {
      btn.addEventListener('click', () => {
        targetDocId = btn.dataset.upload;
        $('fileInput').click();
      });
    });
  }

  /* ---------- מבט מהיר ---------- */
  function renderStats() {
    const missing = countBy('missing');
    const rows = [
      { ic: '⚠️', bg: 'var(--red-bg)',   label: 'מסמכים חסרים',   val: missing + (missing === 1 ? ' מסמך' : ' מסמכים') },
      { ic: '🕓', bg: 'var(--amber-bg)', label: 'בבדיקת המשרד',   val: countBy('pending-review') + ' מסמכים' },
      { ic: '✅', bg: 'var(--green-bg)', label: 'התקבלו ואושרו',  val: countBy('approved') + ' מסמכים' },
      { ic: '📅', bg: 'var(--blue-bg)',  label: 'המועד הקרוב',    val: fmt(caseFile.nextHearing) }
    ];
    $('sideStats').innerHTML = rows.map(r =>
      '<div class="stat-row">' +
        '<div class="stat-ic" style="background:' + r.bg + '">' + r.ic + '</div>' +
        '<div><span>' + r.label + '</span><strong>' + r.val + '</strong></div>' +
      '</div>'
    ).join('');
  }

  /* ---------- הודעות ---------- */
  $('messages').innerHTML = caseFile.messages.map(m =>
    '<div class="msg' + (m.important ? ' important' : '') + '">' +
      '<div class="msg-head"><h5>' + (m.important ? '📌 ' : '') + m.title + '</h5>' +
      '<time>' + fmt(m.date) + '</time></div>' +
      '<p>' + m.body + '</p>' +
    '</div>'
  ).join('');

  /* ---------- הצוות המטפל ---------- */
  const L = caseFile.lawyer;
  $('lawyerInitials').textContent = initials(L.name.replace(/^עו"ד\s*/, ''));
  $('lawyerName').textContent = L.name;
  $('lawyerRole').textContent = L.role;
  $('lawyerPhone').textContent = L.phone;
  $('lawyerPhone').href = 'tel:' + L.phone.replace(/-/g, '');
  $('lawyerEmail').textContent = L.email;
  $('lawyerEmail').href = 'mailto:' + L.email;
  $('callBtn').href = 'mailto:' + L.email +
    '?subject=' + encodeURIComponent('בקשת שיחה חוזרת - תיק ' + caseFile.caseNumber) +
    '&body=' + encodeURIComponent('שלום,\nאשמח לשיחה חוזרת בנוגע לתיק ' + caseFile.caseNumber + '.\n\n' + user.name + '\n' + user.phone);

  /* ---------- העלאת קבצים ---------- */
  const MAX_MB = 10;
  const OK_TYPES = ['application/pdf', 'image/jpeg', 'image/png'];

  function handleFile(file) {
    if (!file) return;

    if (OK_TYPES.indexOf(file.type) === -1) {
      return toast('סוג הקובץ אינו נתמך. ניתן להעלות PDF, JPG או PNG בלבד.', false);
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      return toast('הקובץ גדול מ-' + MAX_MB + 'MB. נא לדחוס אותו או לפצל למספר קבצים.', false);
    }

    // ללא מסמך יעד - משייכים לחסר החובה הראשון, אחרת לחסר הראשון
    let docId = targetDocId;
    if (!docId) {
      const missing = caseFile.documents.filter(d => d.status === 'missing');
      const pick = missing.find(d => d.required) || missing[0];
      if (!pick) return toast('כל המסמכים הנדרשים כבר הועלו.', false);
      docId = pick.id;
    }

    const patch = { status: 'pending-review', file: file.name, date: new Date().toISOString().slice(0, 10) };
    CaseStore.saveDocument(user.idNumber, docId, patch);

    const doc = caseFile.documents.find(d => d.id === docId);
    Object.assign(doc, patch);

    targetDocId = null;
    renderTabs();
    renderDocs();
    renderStats();
    toast('הקובץ "' + file.name + '" הועלה עבור: ' + doc.name + '. המשרד יבדוק אותו תוך 3 ימי עסקים.', true);
  }

  $('fileInput').addEventListener('change', e => {
    handleFile(e.target.files[0]);
    e.target.value = '';
  });

  $('dropZone').addEventListener('click', () => { targetDocId = null; $('fileInput').click(); });
  $('uploadTopBtn').addEventListener('click', () => { targetDocId = null; $('fileInput').click(); });

  ['dragenter', 'dragover'].forEach(ev =>
    $('dropZone').addEventListener(ev, e => { e.preventDefault(); $('dropZone').classList.add('drag'); }));
  ['dragleave', 'drop'].forEach(ev =>
    $('dropZone').addEventListener(ev, e => { e.preventDefault(); $('dropZone').classList.remove('drag'); }));
  $('dropZone').addEventListener('drop', e => { targetDocId = null; handleFile(e.dataTransfer.files[0]); });

  /* ---------- טוסט ---------- */
  let toastTimer;
  function toast(msg, ok) {
    const t = $('toast');
    t.textContent = (ok ? '✅ ' : '⚠️ ') + msg;
    t.className = 'toast show' + (ok ? ' ok' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 4800);
  }

  /* ---------- אתחול ---------- */
  renderTabs();
  renderDocs();
  renderStats();
}
