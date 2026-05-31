/* gpt_signup_hybrid — Check Payment tab
 * Flow per session: accessToken → OpenAI checkout → Stripe init → is_free?
 * Combo mode: login (email|pass|2fa) → accessToken → same flow.
 * Features: per-item retry, End-all, click-email-to-copy, export combos+session.
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const dom = {
    input:           $('cp-input'),
    btnRun:          $('cp-btn-run'),
    btnEnd:          $('cp-btn-end'),
    btnClear:        $('cp-btn-clear'),
    btnClearResults: $('cp-btn-clear-results'),
    btnExport:       $('cp-btn-export'),
    mode:            $('cp-mode'),
    region:          $('cp-region'),
    parallel:        $('cp-parallel'),
    count:           $('cp-count'),
    resultList:      $('cp-result-list'),
    summary:         $('cp-summary'),
    logPanel:        $('cp-log-panel'),
    logLabel:        $('cp-log-label'),
    logBody:         $('cp-log-body'),
    logClose:        $('cp-log-close'),
  };

  const PLACEHOLDER = {
    token: 'Paste session JSON objects or accessToken strings (one per line)\n\nExamples:\neyJhbGci...  (raw accessToken)\n{"accessToken":"eyJ...", "email":"user@example.com"}',
    combo: 'Paste combos (one per line) — login flow:\n\nemail|password\nemail|password|2fa_secret\n\nExample:\nuser@hotmail.com|Pa$$w0rd|JBSWY3DPEHPK3PXP',
  };

  // ── State ─────────────────────────────────────────────────────────────
  // _sessions[i]  — parsed input: { token, email } or { combo, email, password, secret }
  // _results[i]   — null (pending) | result object from API
  // _controllers[i] — AbortController for the in-flight fetch (null when not running)
  let _sessions    = [];
  let _results     = [];
  let _controllers = [];
  let _selectedIdx = -1;
  let _running     = false;
  let _aborted     = false;

  // ── Helpers ───────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function fmtAmount(amount, currency) {
    if (amount == null || amount < 0) return '—';
    return `${(amount / 100).toFixed(2)} ${(currency || '').toUpperCase()}`;
  }

  function copyText(t) {
    if (window.GptUi?.copyText) return window.GptUi.copyText(t);
    return navigator.clipboard?.writeText(t);
  }

  function flash(msg) {
    if (window.GptUi?.toast) return window.GptUi.toast(msg);
    console.log(msg);
  }

  function parseSessionLine(line) {
    line = line.trim();
    if (!line) return null;

    let emailHint = '';
    let sessionText = line;
    const jsonPos = line.indexOf('{');
    if (!line.startsWith('{') && line.includes('|')) {
      const parts = line.split('|').map(s => s.trim());
      emailHint = parts[0] || '';
      sessionText = parts.slice(3).join('|').trim() || line;
    } else if (!line.startsWith('{') && jsonPos >= 0) {
      sessionText = line.slice(jsonPos).trim();
    }

    if (sessionText.startsWith('{')) {
      try {
        const obj = JSON.parse(sessionText);
        const token = obj.accessToken || obj.access_token || obj.token;
        const email = obj.email || (obj.user && obj.user.email) || emailHint;
        if (token) return { token, email };
      } catch { /* fall through */ }
    }
    if (sessionText.startsWith('eyJ') || (sessionText.includes('.') && sessionText.length > 100)) {
      return { token: sessionText, email: emailHint };
    }
    return null;
  }

  function parseComboLine(line) {
    line = line.trim();
    if (!line || line.startsWith('#')) return null;
    const parts = line.split('|').map(s => s.trim());
    if (parts.length < 2 || !parts[0] || !parts[1]) return null;
    if (parts.length >= 4) {
      const session = parseSessionLine(line);
      if (session?.token) {
        return {
          token: session.token,
          email: session.email || parts[0],
          password: parts[1],
          secret: parts[2] || '',
        };
      }
    }
    return {
      combo: line,
      email: parts[0],
      password: parts[1],
      secret: parts[2] || '',
    };
  }

  // ── Count update ──────────────────────────────────────────────────────
  function updateCount() {
    const n = dom.input.value.split('\n').filter(l => l.trim()).length;
    const unit = dom.mode.value === 'combo' ? 'combo' : 'session';
    dom.count.textContent = `${n} ${unit}${n !== 1 ? 's' : ''}`;
  }
  dom.input.addEventListener('input', updateCount);
  dom.mode.addEventListener('change', () => {
    dom.input.placeholder = PLACEHOLDER[dom.mode.value] || PLACEHOLDER.token;
    updateCount();
  });
  dom.input.placeholder = PLACEHOLDER[dom.mode.value] || PLACEHOLDER.token;

  // ── Render one compact card ───────────────────────────────────────────
  function renderCard(i) {
    const r = _results[i];
    const s = _sessions[i];
    const email = (r && r.email) || s.email || `#${i + 1}`;
    const isRunning = _controllers[i] != null;

    const emailAttr = escHtml(JSON.stringify(email));
    const emailSpan = `<span class="job-compact-email cp-email" data-copy="${emailAttr}" title="Click to copy email" style="cursor:pointer">${escHtml(email || `Session ${i + 1}`)}</span>`;
    const logBtn   = `<button class="icon-btn cp-log-btn" data-idx="${i}" title="Show log">📋</button>`;
    const retryBtn = (!isRunning && r !== null)
      ? `<button class="icon-btn cp-retry-btn" data-idx="${i}" title="Retry this">🔄</button>`
      : '';

    // Still checking
    if (r === null) {
      const cancelBtn = isRunning
        ? `<button class="icon-btn cp-cancel-btn" data-idx="${i}" title="Cancel this">✕</button>`
        : '';
      return `<div class="job-compact cp-result-item" data-idx="${i}" style="border-left:3px solid var(--border)">
        <div class="job-index">${i + 1}</div>
        <div class="job-status status-running">…</div>
        <div class="job-compact-main">
          <span class="job-compact-email cp-email muted" data-copy="${emailAttr}" title="Click to copy email" style="cursor:pointer">${escHtml(s.email || `Session ${i + 1}`)}</span>
        </div>
        <div class="job-compact-actions">${cancelBtn}</div>
      </div>`;
    }

    // Partial: has URL but Stripe data missing
    if (r.error && r.payment_url) {
      const linkBtn = `<button class="icon-btn cp-link-btn" data-link="${escHtml(JSON.stringify(r.payment_url))}" title="Copy link">🔗</button>`;
      return `<div class="job-compact cp-result-item" data-idx="${i}" style="border-left:3px solid #f5a623">
        <div class="job-index">${i + 1}</div>
        <div class="job-status status-running">?</div>
        <div class="job-compact-main">
          ${emailSpan}
          <span class="job-compact-badge" style="color:#f5a623">⚠ unknown</span>
        </div>
        <div class="job-compact-actions">${linkBtn}${retryBtn}${logBtn}</div>
      </div>`;
    }

    // Error
    if (r.error) {
      return `<div class="job-compact cp-result-item" data-idx="${i}" style="border-left:3px solid var(--red)">
        <div class="job-index">${i + 1}</div>
        <div class="job-status status-error">err</div>
        <div class="job-compact-main">
          ${emailSpan}
          <span class="job-compact-badge" style="color:var(--red);font-weight:normal;font-size:10px">${escHtml(r.error.slice(0, 40))}</span>
        </div>
        <div class="job-compact-actions">${retryBtn}${logBtn}</div>
      </div>`;
    }

    // Success
    const isFree = r.is_free;
    const color = isFree ? 'var(--green)' : 'var(--red)';
    const badge = isFree
      ? `✅ FREE${r.trial_days ? ` (${r.trial_days}d)` : ''}`
      : `💳 ${escHtml(fmtAmount(r.amount_due, r.currency))}`;
    const linkBtn = r.payment_url
      ? `<button class="icon-btn cp-link-btn" data-link="${escHtml(JSON.stringify(r.payment_url))}" title="Copy link">🔗</button>`
      : '';

    return `<div class="job-compact cp-result-item" data-idx="${i}" style="border-left:3px solid ${color}">
      <div class="job-index">${i + 1}</div>
      <div class="job-status ${isFree ? 'status-success' : 'status-error'}">${isFree ? 'free' : 'paid'}</div>
      <div class="job-compact-main">
        ${emailSpan}
        <span class="job-compact-badge" style="color:${color}">${badge}</span>
      </div>
      <div class="job-compact-actions">${linkBtn}${retryBtn}${logBtn}</div>
    </div>`;
  }

  function updateCardDOM(i) {
    const el = dom.resultList.querySelector(`.cp-result-item[data-idx="${i}"]`);
    if (!el) return;
    const wrap = document.createElement('div');
    wrap.innerHTML = renderCard(i);
    const newEl = wrap.firstElementChild;
    if (newEl) {
      if (_selectedIdx === i) newEl.classList.add('job-selected');
      el.replaceWith(newEl);
    }
  }

  function updateSummary() {
    const done = _results.filter(r => r !== null).length;
    const total = _results.length;
    const free  = _results.filter(r => r && r.is_free === true).length;
    const paid  = _results.filter(r => r && r.is_free === false).length;
    const errs  = _results.filter(r => r && r.error && !r.payment_url).length;
    dom.summary.textContent = done < total
      ? `${done}/${total} done · ${free} free · ${paid} paid`
      : total
        ? `${total} total · ${free} free · ${paid} paid${errs ? ` · ${errs} errors` : ''}`
        : '';
  }

  function showLog(idx) {
    const r = _results[idx];
    if (!r || !(r.logs || []).length) return;
    _selectedIdx = idx;
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

  // ── Event delegation ──────────────────────────────────────────────────
  dom.resultList.addEventListener('click', (e) => {
    const retryBtn = e.target.closest('.cp-retry-btn');
    if (retryBtn) {
      e.stopPropagation();
      runOne(parseInt(retryBtn.dataset.idx, 10));
      return;
    }
    const cancelBtn = e.target.closest('.cp-cancel-btn');
    if (cancelBtn) {
      e.stopPropagation();
      const i = parseInt(cancelBtn.dataset.idx, 10);
      _controllers[i]?.abort();
      return;
    }
    const linkBtn = e.target.closest('.cp-link-btn');
    if (linkBtn) {
      e.stopPropagation();
      try { copyText(JSON.parse(linkBtn.dataset.link)); flash('Copied link'); } catch {}
      return;
    }
    const emailEl = e.target.closest('.cp-email');
    if (emailEl) {
      e.stopPropagation();
      try { copyText(JSON.parse(emailEl.dataset.copy)); flash('Copied email'); } catch {}
      return;
    }
    const logBtn = e.target.closest('.cp-log-btn');
    if (logBtn) {
      e.stopPropagation();
      showLog(parseInt(logBtn.dataset.idx, 10));
      return;
    }
    const row = e.target.closest('.cp-result-item');
    if (row) showLog(parseInt(row.dataset.idx, 10));
  });

  // ── Clear input ───────────────────────────────────────────────────────
  dom.btnClear.addEventListener('click', () => {
    if (_running) return;
    dom.input.value = '';
    updateCount();
  });

  // ── Clear results ─────────────────────────────────────────────────────
  dom.btnClearResults.addEventListener('click', () => {
    if (_running) return;
    dom.resultList.innerHTML = '<div class="empty">Paste sessions and click Check.</div>';
    dom.summary.textContent = '';
    dom.logPanel.style.display = 'none';
    _sessions = [];
    _results = [];
    _controllers = [];
    _selectedIdx = -1;
  });

  // ── Export results ────────────────────────────────────────────────────
  // Output per successful row: email|password|secret|access_token (combo mode)
  // or email|access_token (token mode). Skips errored/pending rows.
  dom.btnExport.addEventListener('click', async () => {
    const lines = [];
    for (let i = 0; i < _sessions.length; i++) {
      const s = _sessions[i];
      const r = _results[i];
      if (!r || r.error) continue;
      const token = r.access_token || s.token || '';
      if (!token) continue;
      const email = r.email || s.email || '';
      if (s.combo) {
        lines.push(`${email}|${s.password || ''}|${s.secret || ''}|${token}`);
      } else {
        lines.push(`${email}|${token}`);
      }
    }
    if (!lines.length) { alert('No completed accounts to export yet.'); return; }
    const text = lines.join('\n');
    try { await copyText(text); flash(`Copied ${lines.length} line(s) to clipboard`); }
    catch { /* fallback: prompt */ window.prompt(`${lines.length} lines — copy below:`, text); }
  });

  // ── End all (cancel pending + abort in-flight) ────────────────────────
  dom.btnEnd.addEventListener('click', () => {
    if (!_running) return;
    _aborted = true;
    _controllers.forEach(c => c && c.abort());
    // Mark all remaining pending as cancelled
    for (let i = 0; i < _results.length; i++) {
      if (_results[i] === null && _controllers[i] == null) {
        _results[i] = { email: _sessions[i].email, error: 'cancelled (not started)', logs: ['Cancelled by user before start'] };
        updateCardDOM(i);
      }
    }
    updateSummary();
  });

  // ── Single-task runner (also used by retry) ───────────────────────────
  async function runOne(i) {
    const session = _sessions[i];
    if (!session) return;
    if (_controllers[i]) return; // already in flight

    const region = dom.region.value || 'VN';
    const apiToken = window.GptUi?.apiToken || '';
    const headers = { 'Content-Type': 'application/json', ...(apiToken ? { 'X-API-Token': apiToken } : {}) };
    const payload = session.combo
      ? { combo: session.combo, email: session.email }
      : { token: session.token, email: session.email };

    const ctrl = new AbortController();
    _controllers[i] = ctrl;
    _results[i] = null;
    updateCardDOM(i);
    updateSummary();

    try {
      const res = await fetch('/api/check-payment', {
        method: 'POST',
        headers,
        body: JSON.stringify({ sessions: [payload], region }),
        signal: ctrl.signal,
      });
      const data = await res.json();
      _results[i] = (data.results || [])[0] || { email: session.email, error: 'No result returned', logs: [] };
    } catch (err) {
      if (err.name === 'AbortError') {
        _results[i] = { email: session.email, error: 'cancelled', logs: ['Request aborted by user'] };
      } else {
        _results[i] = { email: session.email, error: err.message, logs: [] };
      }
    } finally {
      _controllers[i] = null;
    }
    updateCardDOM(i);
    updateSummary();
    if (_selectedIdx === i) showLog(i);
  }

  // ── Concurrency pool that respects abort flag ─────────────────────────
  async function runPool(indices, limit) {
    let cursor = 0;
    async function worker() {
      while (cursor < indices.length && !_aborted) {
        const i = indices[cursor++];
        await runOne(i);
      }
    }
    const workers = Array.from({ length: Math.min(limit, indices.length) }, worker);
    await Promise.all(workers);
  }

  // ── Run (full batch) ──────────────────────────────────────────────────
  dom.btnRun.addEventListener('click', async () => {
    if (_running) return;
    const mode = dom.mode.value === 'combo' ? 'combo' : 'token';
    const lines = dom.input.value.split('\n').map(l => l.trim()).filter(Boolean);
    const parser = mode === 'combo' ? parseComboLine : parseSessionLine;
    const parsed = lines.map(parser).filter(Boolean);
    if (!parsed.length) {
      alert(mode === 'combo'
        ? 'Paste combos (email|password|2fa) first.'
        : 'Paste session JSON or accessToken strings first.');
      return;
    }

    _running = true;
    _aborted = false;
    _sessions    = parsed;
    _results     = parsed.map(() => null);
    _controllers = parsed.map(() => null);
    _selectedIdx = -1;
    dom.logPanel.style.display = 'none';
    dom.btnRun.disabled = true;
    dom.btnRun.textContent = '⏳ Checking…';
    dom.btnEnd.style.display = '';
    dom.summary.textContent = `0/${parsed.length} done`;

    dom.resultList.innerHTML = parsed.map((_, i) => renderCard(i)).join('');

    const concurrency = parseInt(dom.parallel.value, 10) || 10;
    const indices = parsed.map((_, i) => i);

    await runPool(indices, concurrency);

    _running = false;
    _aborted = false;
    dom.btnRun.disabled = false;
    dom.btnRun.textContent = '▶ Check';
    dom.btnEnd.style.display = 'none';
    updateSummary();
  });

  updateCount();
})();
