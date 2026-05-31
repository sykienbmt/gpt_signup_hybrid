/* gpt_signup_hybrid — Change Password tab
 * Input: combos (email|current_pass|2fa) + new password
 * Per-item: browser login → Settings → Security → change password
 * Features: per-item retry, End-all, click-email-to-copy, export success
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const dom = {
    input:       $('cp2-input'),
    newPass:     $('cp2-new-pass'),
    randomPass:  $('cp2-random-pass'),
    parallel:    $('cp2-parallel'),
    btnRun:      $('cp2-btn-run'),
    btnEnd:      $('cp2-btn-end'),
    btnClear:    $('cp2-btn-clear'),
    btnExport:   $('cp2-btn-export'),
    count:       $('cp2-count'),
    resultList:  $('cp2-result-list'),
    summary:     $('cp2-summary'),
    logPanel:    $('cp2-log-panel'),
    logLabel:    $('cp2-log-label'),
    logBody:     $('cp2-log-body'),
    logClose:    $('cp2-log-close'),
  };

  // ── State ─────────────────────────────────────────────────────────────
  let _combos      = [];   // [{email, pass, secret, newPass}]
  let _results     = [];   // null | {success, error, logs}
  let _controllers = [];   // AbortController | null per item
  let _liveLogs    = [];   // realtime logs by item while request is running
  let _requestIds  = [];   // backend log request_id by item
  let _running     = false;
  let _aborted     = false;
  let _selectedIdx = -1;   // index whose logs are shown in inline panel

  // ── Helpers ───────────────────────────────────────────────────────────
  function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function copyText(t) {
    if (window.GptUi?.copyText) return window.GptUi.copyText(t);
    return navigator.clipboard?.writeText(t);
  }

  function flash(msg) {
    const el = document.createElement('div');
    el.className = 'flash-msg';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2200);
  }

  function makeid(n = 8) {
    return Math.random().toString(36).slice(2, 2 + n);
  }

  function randomPassword() {
    const chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789!@#$';
    let p = '';
    for (let i = 0; i < 14; i++) p += chars[Math.floor(Math.random() * chars.length)];
    return p;
  }

  function resultSession(res) {
    return res?.session || res?.new_session || null;
  }

  // ── Parse input ───────────────────────────────────────────────────────
  function parseInput() {
    const lines = dom.input.value.split('\n').map(l => l.trim()).filter(Boolean);
    const newPassBase = dom.randomPass?.checked ? null : (dom.newPass?.value?.trim() || null);
    return lines.map(line => {
      const parts = line.split('|').map(p => p.trim());
      return {
        email:   parts[0] || '',
        pass:    parts[1] || '',
        secret:  parts[2] || '',
        session: parts[3] || '',  // optional old session — informational
        newPass: newPassBase ?? randomPassword(),
        _raw:    line,
      };
    }).filter(c => c.email && c.pass);
  }

  function updateCount() {
    const combos = parseInput();
    dom.count.textContent = `${combos.length} combo${combos.length !== 1 ? 's' : ''}`;
  }

  // ── Render ────────────────────────────────────────────────────────────
  function render() {
    const done   = _results.filter(r => r !== null).length;
    const ok     = _results.filter(r => r?.success).length;
    const failed = _results.filter(r => r !== null && !r?.success).length;
    dom.summary.textContent = _combos.length
      ? `${done}/${_combos.length} done · ${ok} ok · ${failed} failed`
      : '';

    if (!_combos.length) {
      dom.resultList.innerHTML = '<div class="empty">Paste combos and click Run.</div>';
      return;
    }

    dom.resultList.innerHTML = _combos.map((c, i) => {
      const res = _results[i];
      const ctrl = _controllers[i];
      const inFlight = ctrl !== null;
      const liveLogs = _liveLogs[i] || [];

      let statusIcon = '⏳';
      let statusClass = 'status-pending';
      if (res !== null) {
        if (res.success) { statusIcon = '✅'; statusClass = 'status-success'; }
        else             { statusIcon = '❌'; statusClass = 'status-error'; }
      } else if (inFlight) {
        statusIcon = '🔄'; statusClass = 'status-running';
      }

      const emailEl = `<span class="email-copy" data-idx="${i}" title="Click to copy email">${escHtml(c.email)}</span>`;
      const errEl = res && !res.success
        ? `<span class="cp2-error-msg muted">${escHtml(res.error || 'unknown error')}</span>`
        : '';
      const newPassEl = res?.success
        ? `<span class="cp2-new-pass-badge" title="New password (click to copy)" data-newpass="${escHtml(c.newPass)}">${escHtml(c.newPass)}</span>`
        : '';
      const session = resultSession(res);
      const hasSession = res?.success && session?.accessToken;
      const sessionEl = hasSession
        ? `<span class="cp2-session-badge" title="New session fetched (click to copy)" data-session="${escHtml(JSON.stringify(session))}">📋 session</span>`
        : '';

      const retryBtn = res !== null && !inFlight
        ? `<button class="btn btn-ghost btn-small cp2-retry" data-idx="${i}">🔄 Retry</button>`
        : '';
      const cancelBtn = inFlight
        ? `<button class="btn btn-ghost btn-small cp2-cancel" data-idx="${i}">✕</button>`
        : '';
      const logsBtn = (res?.logs?.length || liveLogs.length || inFlight)
        ? `<button class="btn btn-ghost btn-small cp2-logs" data-idx="${i}">📋 Logs</button>`
        : '';

      return `<div class="cp2-row ${statusClass}" data-idx="${i}">
        <span class="cp2-status">${statusIcon}</span>
        ${emailEl}
        ${newPassEl}
        ${sessionEl}
        ${errEl}
        <span class="cp2-actions">${retryBtn}${cancelBtn}${logsBtn}</span>
      </div>`;
    }).join('');
  }

  // ── API call ──────────────────────────────────────────────────────────
  async function fetchLiveLogs(i) {
    const requestId = _requestIds[i];
    if (!requestId) return;
    try {
      const resp = await fetch(`/api/change-password/log/${encodeURIComponent(requestId)}`);
      if (!resp.ok) return;
      const data = await resp.json();
      _liveLogs[i] = Array.isArray(data.logs) ? data.logs : [];
      if (_selectedIdx === i) refreshLogPanel();
    } catch (_) {
      // Polling is best-effort; the main POST still owns final status.
    }
  }

  async function runOne(i) {
    const c = _combos[i];
    if (!c) return;

    const ctrl = new AbortController();
    const requestId = `cp_${Date.now()}_${i}_${makeid(10)}`;
    _controllers[i] = ctrl;
    _results[i] = null;
    _liveLogs[i] = [];
    _requestIds[i] = requestId;

    // Auto-open log panel for this item
    if (_selectedIdx < 0 || _selectedIdx === i) openLogPanel(i);
    render();
    if (_selectedIdx === i) refreshLogPanel();

    // Poll backend logs while request is in flight
    const pollId = setInterval(() => {
      fetchLiveLogs(i);
    }, 800);
    fetchLiveLogs(i);

    const combo = c.secret ? `${c.email}|${c.pass}|${c.secret}` : `${c.email}|${c.pass}`;

    try {
      const resp = await fetch('/api/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ combo, new_password: c.newPass, request_id: requestId }),
        signal: ctrl.signal,
      });
      const data = await resp.json();
      _results[i] = data;
      _liveLogs[i] = Array.isArray(data.logs) ? data.logs : (_liveLogs[i] || []);
    } catch (err) {
      if (err.name === 'AbortError') {
        _results[i] = { success: false, email: c.email, error: 'Cancelled', logs: _liveLogs[i] || [] };
      } else {
        _results[i] = { success: false, email: c.email, error: String(err), logs: _liveLogs[i] || [] };
      }
    } finally {
      clearInterval(pollId);
      await fetchLiveLogs(i);
      _controllers[i] = null;
      render();
      if (_selectedIdx === i) refreshLogPanel();
    }
  }

  async function runPool(indices, limit) {
    const queue = [...indices];
    const workers = Array.from({ length: limit }, async () => {
      while (queue.length && !_aborted) {
        const i = queue.shift();
        if (i === undefined) break;
        await runOne(i);
      }
    });
    await Promise.all(workers);
  }

  // ── Run ───────────────────────────────────────────────────────────────
  async function start() {
    _combos = parseInput();
    if (!_combos.length) { flash('No valid combos (need email|pass or email|pass|2fa)'); return; }
    _results     = _combos.map(() => null);
    _controllers = _combos.map(() => null);
    _liveLogs    = _combos.map(() => []);
    _requestIds  = _combos.map(() => '');
    _aborted     = false;
    _running     = true;
    _selectedIdx = 0;  // auto-open log for first item

    dom.btnRun.disabled = true;
    dom.btnEnd.style.display = '';
    dom.logPanel.style.display = '';
    render();

    const limit = parseInt(dom.parallel?.value || '1', 10);
    await runPool(_combos.map((_, i) => i), limit);

    _running = false;
    dom.btnRun.disabled = false;
    dom.btnEnd.style.display = 'none';
    render();
  }

  // ── Export ────────────────────────────────────────────────────────────
  function doExport() {
    const lines = _combos
      .map((c, i) => {
        if (!_results[i]?.success) return null;
        const base = `${c.email}|${c.newPass}|${c.secret}`;
        const newSession = resultSession(_results[i]);
        if (newSession?.accessToken) {
          return `${base}|${JSON.stringify(newSession)}`;
        }
        return base;
      })
      .filter(Boolean);
    if (!lines.length) { flash('No successful results to export'); return; }
    const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `changed-passwords-${Date.now()}.txt`;
    a.click();
  }

  // ── Inline log panel ──────────────────────────────────────────────────
  function openLogPanel(i) {
    _selectedIdx = i;
    refreshLogPanel();
    dom.logPanel.style.display = '';
  }

  function refreshLogPanel() {
    if (_selectedIdx < 0) return;
    const c = _combos[_selectedIdx];
    const res = _results[_selectedIdx];
    dom.logLabel.textContent = c ? c.email : '';
    const logs = res?.logs || _liveLogs[_selectedIdx] || [];
    const ctrl = _controllers[_selectedIdx];
    const inFlight = ctrl !== null;
    dom.logBody.textContent = logs.length
      ? logs.join('\n')
      : (inFlight ? '⏳ Running…' : 'No logs yet.');
    dom.logBody.scrollTop = dom.logBody.scrollHeight;
  }

  // ── Event delegation ─────────────────────────────────────────────────
  dom.resultList.addEventListener('click', async (e) => {
    const t = e.target.closest('[data-idx]');
    if (!t) return;
    const i = parseInt(t.dataset.idx, 10);

    if (t.classList.contains('cp2-retry')) {
      openLogPanel(i);
      await runOne(i);
    } else if (t.classList.contains('cp2-cancel')) {
      _controllers[i]?.abort();
    } else if (t.classList.contains('cp2-logs')) {
      openLogPanel(i);
    } else if (t.classList.contains('email-copy')) {
      await copyText(_combos[i]?.email || '');
      flash('Copied email');
    } else if (t.classList.contains('cp2-new-pass-badge')) {
      await copyText(t.dataset.newpass || '');
      flash('Copied new password');
    } else if (t.classList.contains('cp2-session-badge')) {
      await copyText(t.dataset.session || '');
      flash('Copied new session JSON');
    } else {
      openLogPanel(i);
    }
  });

  dom.logClose.addEventListener('click', () => {
    dom.logPanel.style.display = 'none';
    _selectedIdx = -1;
  });

  dom.btnRun.addEventListener('click', () => { if (!_running) start(); });

  dom.btnEnd.addEventListener('click', () => {
    _aborted = true;
    _controllers.forEach(ctrl => ctrl?.abort());
  });

  dom.btnClear.addEventListener('click', () => {
    dom.input.value = '';
    _combos = []; _results = []; _controllers = []; _liveLogs = []; _requestIds = [];
    updateCount(); render();
  });

  dom.btnExport.addEventListener('click', doExport);

  dom.input.addEventListener('input', updateCount);

  dom.randomPass?.addEventListener('change', () => {
    dom.newPass.disabled = !!dom.randomPass.checked;
  });

  // Initial count
  updateCount();
  render();
})();
