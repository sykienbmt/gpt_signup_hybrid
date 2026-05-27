/* gpt_signup_hybrid — Check Payment tab
 * Flow per session: accessToken → OpenAI checkout → Stripe init → is_free?
 * UI: all sessions shown immediately as "checking", updated progressively.
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const dom = {
    input:      $('cp-input'),
    btnRun:     $('cp-btn-run'),
    btnClear:   $('cp-btn-clear'),
    region:     $('cp-region'),
    count:      $('cp-count'),
    resultList: $('cp-result-list'),
    summary:    $('cp-summary'),
    logPanel:   $('cp-log-panel'),
    logLabel:   $('cp-log-label'),
    logBody:    $('cp-log-body'),
    logClose:   $('cp-log-close'),
  };

  // ── State ─────────────────────────────────────────────────────────────
  // _results[i] = null (checking) | result object
  let _sessions = [];   // parsed sessions: { token, email }
  let _results  = [];   // same length, null while pending
  let _selectedIdx = -1;
  let _running = false;

  // ── Helpers ───────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function fmtAmount(amount, currency) {
    if (amount == null || amount < 0) return '—';
    return `${(amount / 100).toFixed(2)} ${(currency || '').toUpperCase()}`;
  }

  function parseSessionLine(line) {
    line = line.trim();
    if (!line) return null;
    if (line.startsWith('{')) {
      try {
        const obj = JSON.parse(line);
        const token = obj.accessToken || obj.access_token || obj.token;
        const email = obj.email || (obj.user && obj.user.email) || '';
        if (token) return { token, email };
      } catch { /* fall through */ }
    }
    if (line.startsWith('eyJ') || line.length > 100) {
      return { token: line, email: '' };
    }
    return null;
  }

  // ── Count update ──────────────────────────────────────────────────────
  function updateCount() {
    const n = dom.input.value.split('\n').filter(l => l.trim()).length;
    dom.count.textContent = `${n} session${n !== 1 ? 's' : ''}`;
  }
  dom.input.addEventListener('input', updateCount);

  // ── Render one compact card ───────────────────────────────────────────
  function renderCard(i) {
    const r = _results[i];
    const s = _sessions[i];
    const email = (r && r.email) || s.email || `#${i + 1}`;

    const copyEmailBtn = `<button class="icon-btn" onclick="event.stopPropagation();window.GptUi.copyText(${JSON.stringify(email)})" title="Copy email">📧</button>`;
    const logBtn = `<button class="icon-btn cp-log-btn" data-idx="${i}" title="Show log">📋</button>`;

    // Still checking
    if (r === null) {
      return `<div class="job-compact cp-result-item" data-idx="${i}" style="border-left:3px solid var(--border)">
        <div class="job-index">${i + 1}</div>
        <div class="job-status status-running">…</div>
        <div class="job-compact-main">
          <span class="job-compact-email muted">${escHtml(s.email || `Session ${i + 1}`)}</span>
        </div>
        <div class="job-compact-actions"></div>
      </div>`;
    }

    // Partial: has URL but Stripe data missing
    if (r.error && r.payment_url) {
      const linkBtn = `<button class="icon-btn" onclick="event.stopPropagation();window.GptUi.copyText(${JSON.stringify(r.payment_url)})" title="Copy link">🔗</button>`;
      return `<div class="job-compact cp-result-item" data-idx="${i}" style="border-left:3px solid #f5a623">
        <div class="job-index">${i + 1}</div>
        <div class="job-status status-running">?</div>
        <div class="job-compact-main">
          <span class="job-compact-email">${escHtml(email)}</span>
          <span class="job-compact-badge" style="color:#f5a623">⚠ unknown</span>
        </div>
        <div class="job-compact-actions">${copyEmailBtn}${linkBtn}${logBtn}</div>
      </div>`;
    }

    // Error
    if (r.error) {
      return `<div class="job-compact cp-result-item" data-idx="${i}" style="border-left:3px solid var(--red)">
        <div class="job-index">${i + 1}</div>
        <div class="job-status status-error">err</div>
        <div class="job-compact-main">
          <span class="job-compact-email">${escHtml(email)}</span>
          <span class="job-compact-badge" style="color:var(--red);font-weight:normal;font-size:10px">${escHtml(r.error.slice(0, 40))}</span>
        </div>
        <div class="job-compact-actions">${copyEmailBtn}${logBtn}</div>
      </div>`;
    }

    // Success
    const isFree = r.is_free;
    const color = isFree ? 'var(--green)' : 'var(--red)';
    const badge = isFree
      ? `✅ FREE${r.trial_days ? ` (${r.trial_days}d)` : ''}`
      : `💳 ${escHtml(fmtAmount(r.amount_due, r.currency))}`;
    const linkBtn = r.payment_url
      ? `<button class="icon-btn" onclick="event.stopPropagation();window.GptUi.copyText(${JSON.stringify(r.payment_url)})" title="Copy link">🔗</button>`
      : '';

    return `<div class="job-compact cp-result-item" data-idx="${i}" style="border-left:3px solid ${color}">
      <div class="job-index">${i + 1}</div>
      <div class="job-status ${isFree ? 'status-success' : 'status-error'}">${isFree ? 'free' : 'paid'}</div>
      <div class="job-compact-main">
        <span class="job-compact-email">${escHtml(email)}</span>
        <span class="job-compact-badge" style="color:${color}">${badge}</span>
      </div>
      <div class="job-compact-actions">${copyEmailBtn}${linkBtn}${logBtn}</div>
    </div>`;
  }

  // ── Update a single card in DOM (replace in place) ────────────────────
  function updateCardDOM(i) {
    const el = dom.resultList.querySelector(`.cp-result-item[data-idx="${i}"]`);
    if (!el) return;
    const wrap = document.createElement('div');
    wrap.innerHTML = renderCard(i);
    const newEl = wrap.firstElementChild;
    if (newEl) {
      // Preserve selected state
      if (_selectedIdx === i) newEl.classList.add('job-selected');
      el.replaceWith(newEl);
    }
  }

  // ── Update summary bar ────────────────────────────────────────────────
  function updateSummary() {
    const done = _results.filter(r => r !== null).length;
    const total = _results.length;
    const free  = _results.filter(r => r && r.is_free === true).length;
    const paid  = _results.filter(r => r && r.is_free === false).length;
    const errs  = _results.filter(r => r && r.error && !r.payment_url).length;
    dom.summary.textContent = done < total
      ? `${done}/${total} done · ${free} free · ${paid} paid`
      : `${total} total · ${free} free · ${paid} paid${errs ? ` · ${errs} errors` : ''}`;
  }

  // ── Log panel ─────────────────────────────────────────────────────────
  function showLog(idx) {
    const r = _results[idx];
    if (!r || !(r.logs || []).length) return;
    _selectedIdx = idx;

    // Highlight selected
    dom.resultList.querySelectorAll('.cp-result-item').forEach((el, i) => {
      el.classList.toggle('job-selected', i === idx);
    });

    dom.logLabel.textContent = r.email || _sessions[idx]?.email || `#${idx + 1}`;
    dom.logBody.textContent = r.logs.join('\n');
    dom.logPanel.style.display = '';
    dom.logBody.scrollTop = dom.logBody.scrollHeight;
  }

  dom.logClose.addEventListener('click', () => {
    dom.logPanel.style.display = 'none';
    _selectedIdx = -1;
    dom.resultList.querySelectorAll('.cp-result-item').forEach(el => el.classList.remove('job-selected'));
  });

  // ── Event delegation (once, on the container) ─────────────────────────
  dom.resultList.addEventListener('click', (e) => {
    const logBtn = e.target.closest('.cp-log-btn');
    if (logBtn) {
      e.stopPropagation();
      showLog(parseInt(logBtn.dataset.idx, 10));
      return;
    }
    const row = e.target.closest('.cp-result-item');
    if (row) showLog(parseInt(row.dataset.idx, 10));
  });

  // ── Clear ─────────────────────────────────────────────────────────────
  dom.btnClear.addEventListener('click', () => {
    if (_running) return;
    dom.input.value = '';
    updateCount();
    dom.resultList.innerHTML = '<div class="empty">Paste sessions and click Check.</div>';
    dom.summary.textContent = '';
    dom.logPanel.style.display = 'none';
    _sessions = [];
    _results = [];
    _selectedIdx = -1;
  });

  // ── Run ───────────────────────────────────────────────────────────────
  dom.btnRun.addEventListener('click', async () => {
    if (_running) return;
    const lines = dom.input.value.split('\n').map(l => l.trim()).filter(Boolean);
    const parsed = lines.map(parseSessionLine).filter(Boolean);
    if (!parsed.length) { alert('Paste session JSON or accessToken strings first.'); return; }

    _running = true;
    _sessions = parsed;
    _results = parsed.map(() => null);   // all pending
    _selectedIdx = -1;
    dom.logPanel.style.display = 'none';
    dom.btnRun.disabled = true;
    dom.btnRun.textContent = '⏳ Checking…';
    dom.summary.textContent = `0/${parsed.length} done`;

    // Render all cards immediately as "checking"
    dom.resultList.innerHTML = parsed.map((_, i) => renderCard(i)).join('');

    const region = dom.region.value || 'VN';
    const apiToken = window.GptUi?.apiToken || '';
    const headers = { 'Content-Type': 'application/json', ...(apiToken ? { 'X-API-Token': apiToken } : {}) };

    // Process all sessions in parallel, update UI as each completes
    await Promise.allSettled(parsed.map(async (session, i) => {
      try {
        const res = await fetch('/api/check-payment', {
          method: 'POST',
          headers,
          body: JSON.stringify({ sessions: [{ token: session.token, email: session.email }], region }),
        });
        const data = await res.json();
        _results[i] = (data.results || [])[0] || { email: session.email, error: 'No result returned', logs: [] };
      } catch (err) {
        _results[i] = { email: session.email, error: err.message, logs: [] };
      }
      updateCardDOM(i);
      updateSummary();
      // If this item is selected and now has logs, refresh the log panel
      if (_selectedIdx === i) showLog(i);
    }));

    _running = false;
    dom.btnRun.disabled = false;
    dom.btnRun.textContent = '▶ Check';
    updateSummary();
  });

  updateCount();
})();
