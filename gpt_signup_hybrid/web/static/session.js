/* gpt_signup_hybrid — Get Session tab logic */
(() => {
  'use strict';

  // ── State ─────────────────────────────────────────────────────────
  const state = {
    jobs: new Map(),
    order: [],
    activeJobId: null,
    maxConcurrent: 1,
  };

  // ── DOM refs ──────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const dom = {
    comboInput:   $('ses-combo-input'),
    btnRun:       $('ses-btn-run'),
    btnStopAll:   $('ses-btn-stop-all'),
    btnClearInput: $('ses-btn-clear-input'),
    comboCount:   $('ses-combo-count'),
    jobTimeout:   $('ses-job-timeout'),
    jobList:      $('ses-job-list'),
    jobSummary:   $('ses-job-summary'),
    logPane:      $('ses-log-pane'),
    logTarget:    $('ses-log-target'),
    successPane:  null,
    errorPane:    $('ses-error-pane'),
    btnCopyError:   $('ses-btn-copy-error'),
    btnClearDone:   $('ses-btn-clear-done'),
  };

  // ── Helpers ───────────────────────────────────────────────────────
  function fmtDuration(secs) {
    if (secs == null) return '';
    if (secs < 60) return secs.toFixed(1) + 's';
    return Math.floor(secs / 60) + 'm' + Math.floor(secs % 60) + 's';
  }

  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function api(path, opts = {}) {
    return fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    }).then((r) => {
      if (!r.ok) return r.text().then((t) => { throw new Error(`HTTP ${r.status}: ${t}`); });
      return r.json();
    });
  }

  // ── Combo counter ─────────────────────────────────────────────────
  function updateComboCount() {
    const lines = dom.comboInput.value.split('\n').filter((l) => {
      const s = l.trim();
      return s && !s.startsWith('#');
    });
    dom.comboCount.textContent = `${lines.length} combo`;
  }
  dom.comboInput.addEventListener('input', updateComboCount);

  // ── Render job list ───────────────────────────────────────────────
  function renderJobs() {
    if (state.order.length === 0) {
      dom.jobList.innerHTML = '<div class="empty">Paste combo + bấm Get Session.</div>';
      dom.jobSummary.textContent = '0 total';
      return;
    }

    const stats = { queued: 0, running: 0, success: 0, error: 0, cancelled: 0 };
    const html = state.order.map((id) => {
      const j = state.jobs.get(id);
      if (!j) return '';
      stats[j.status] = (stats[j.status] || 0) + 1;
      const cls = state.activeJobId === id ? 'job is-active' : 'job';

      let actionBtns = '';
      if (j.status === 'running') {
        actionBtns = `<button class="icon-btn icon-danger" data-action="stop" data-id="${escHtml(id)}" title="Dừng"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg></button>`;
      } else if (j.status === 'success') {
        actionBtns = `
          <button class="icon-btn" data-action="download" data-id="${escHtml(id)}" title="Download JSON"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>
          <button class="icon-btn" data-action="copy-json" data-id="${escHtml(id)}" title="Copy JSON"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button>
          <button class="icon-btn" data-action="copy-token" data-id="${escHtml(id)}" title="Copy Access Token"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg></button>
        `;
      } else {
        actionBtns = `<button class="icon-btn" data-action="retry" data-id="${escHtml(id)}" title="Retry"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg></button>`;
      }

      return `
        <div class="${cls}" data-id="${escHtml(id)}">
          <div class="job-status status-${escHtml(j.status)}">${escHtml(j.status)}</div>
          <div class="job-email" title="${escHtml(j.email)}">${escHtml(j.email)}</div>
          <div class="job-duration">${escHtml(fmtDuration(j.duration))}</div>
          <div class="job-actions">
            ${actionBtns}
            <button class="icon-btn icon-danger" data-action="remove" data-id="${escHtml(id)}" title="Xoá"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
          </div>
        </div>
      `;
    }).join('');

    dom.jobList.innerHTML = html;
    dom.jobSummary.textContent = [
      `${state.order.length} total`,
      stats.running ? `${stats.running} running` : '',
      stats.success ? `${stats.success} ok` : '',
      stats.error ? `${stats.error} err` : '',
    ].filter(Boolean).join(' · ');
  }

  // ── Render outputs ────────────────────────────────────────────────
  // Session data lưu local khi job success (để copy/download)
  const sessionCache = new Map(); // job_id → session_data

  function renderOutputs() {
    const errorLines = [];
    for (const id of state.order) {
      const j = state.jobs.get(id);
      if (!j) continue;
      if (j.status === 'success' && j.has_session && !sessionCache.has(id)) {
        // Auto-fetch session data khi job success
        loadSessionData(id);
      } else if (j.status === 'error') {
        errorLines.push(`${j.email}  →  ${j.error || 'unknown'}`);
      }
    }
    dom.errorPane.textContent = errorLines.length
      ? errorLines.join('\n')
      : 'Chưa có lỗi.';
  }

  function loadSessionData(jobId) {
    api(`/api/session/jobs/${jobId}`).then((data) => {
      if (data.session_data) {
        sessionCache.set(jobId, data.session_data);
      }
    }).catch(() => {});
  }

  // ── Render log ────────────────────────────────────────────────────
  function renderLog(jobId) {
    if (!jobId) {
      dom.logPane.textContent = '';
      dom.logTarget.textContent = '—';
      return;
    }
    const j = state.jobs.get(jobId);
    if (!j) return;
    dom.logTarget.textContent = j.email;
    api(`/api/session/jobs/${jobId}/log`).then((data) => {
      const lines = data.log || [];
      dom.logPane.innerHTML = lines.map((l) => {
        const cls = /(error|FAILED|fatal)/i.test(l) ? 'log-line-error' : 'log-line-info';
        return `<span class="${cls}">${escHtml(l)}</span>`;
      }).join('\n');
      dom.logPane.scrollTop = dom.logPane.scrollHeight;
    }).catch((err) => {
      dom.logPane.textContent = `[error] ${err.message}`;
    });
  }

  // ── Job actions ───────────────────────────────────────────────────
  dom.jobList.addEventListener('click', (e) => {
    const actionBtn = e.target.closest('[data-action]');
    if (actionBtn) {
      const action = actionBtn.dataset.action;
      const id = actionBtn.dataset.id;
      e.stopPropagation();
      if (action === 'retry') {
        api(`/api/session/jobs/${id}/retry`, { method: 'POST' }).catch(alert);
      } else if (action === 'stop') {
        api(`/api/session/jobs/${id}`, { method: 'DELETE' }).catch(alert);
      } else if (action === 'remove') {
        api(`/api/session/jobs/${id}`, { method: 'DELETE' }).catch(alert);
      } else if (action === 'download' || action === 'copy-json' || action === 'copy-token') {
        // Lấy session data
        const cached = sessionCache.get(id);
        if (cached) {
          doSessionAction(action, id, cached);
        } else {
          api(`/api/session/jobs/${id}`).then((data) => {
            if (data.session_data) {
              sessionCache.set(id, data.session_data);
              doSessionAction(action, id, data.session_data);
            }
          }).catch(alert);
        }
      }
      return;
    }
    const row = e.target.closest('.job');
    if (row) {
      state.activeJobId = row.dataset.id;
      renderJobs();
      renderLog(state.activeJobId);
    }
  });

  function doSessionAction(action, jobId, sessionData) {
    const j = state.jobs.get(jobId);
    const email = j ? j.email : 'session';
    if (action === 'download') {
      const filename = `session.${email}.json`;
      const blob = new Blob([JSON.stringify(sessionData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } else if (action === 'copy-json') {
      navigator.clipboard.writeText(JSON.stringify(sessionData, null, 2));
    } else if (action === 'copy-token') {
      if (sessionData.accessToken) {
        navigator.clipboard.writeText(sessionData.accessToken);
      }
    }
  }

  // ── Run button ────────────────────────────────────────────────────
  dom.btnRun.addEventListener('click', async () => {
    const combos = dom.comboInput.value.trim();
    if (!combos) { alert('Paste combo trước.'); return; }
    dom.btnRun.disabled = true;
    try {
      // Sync config
      const target = document.getElementById('mode').value === 'single' ? 1 : 3;
      await api('/api/session/config', {
        method: 'POST',
        body: JSON.stringify({ max_concurrent: target }),
      });
      await api('/api/session/jobs', {
        method: 'POST',
        body: JSON.stringify({ combos }),
      });
    } catch (err) {
      alert('Lỗi: ' + err.message);
    } finally {
      dom.btnRun.disabled = false;
    }
  });

  dom.btnClearInput.addEventListener('click', () => {
    dom.comboInput.value = '';
    updateComboCount();
  });

  dom.btnStopAll.addEventListener('click', async () => {
    try {
      await api('/api/session/jobs/stop-all', { method: 'POST' });
    } catch (err) { alert(err.message); }
  });

  dom.btnClearDone.addEventListener('click', async () => {
    try {
      await api('/api/session/jobs/clear-finished', { method: 'POST' });
    } catch (err) { alert(err.message); }
  });

  dom.jobTimeout.addEventListener('change', async () => {
    const val = parseInt(dom.jobTimeout.value, 10);
    if (isNaN(val) || val < 30) return;
    try {
      await api('/api/session/config', {
        method: 'POST',
        body: JSON.stringify({ job_timeout: val }),
      });
    } catch (err) { console.error(err); }
  });

  // ── Copy error button ──────────────────────────────────────────────
  dom.btnCopyError.addEventListener('click', () => {
    navigator.clipboard.writeText(dom.errorPane.textContent);
  });

  // ── SSE ───────────────────────────────────────────────────────────
  function applySnapshot(jobs) {
    state.order = jobs.map((j) => j.id);
    state.jobs.clear();
    for (const j of jobs) state.jobs.set(j.id, j);
    renderJobs();
    renderOutputs();
  }

  function applyJobUpdate(j) {
    if (!state.jobs.has(j.id)) state.order.push(j.id);
    state.jobs.set(j.id, j);
    renderJobs();
    renderOutputs();
    if (state.activeJobId === j.id) renderLog(j.id);
  }

  function applyRemove(jobId) {
    state.jobs.delete(jobId);
    state.order = state.order.filter((id) => id !== jobId);
    if (state.activeJobId === jobId) { state.activeJobId = null; renderLog(null); }
    renderJobs();
    renderOutputs();
  }

  function applyLog(jobId, line) {
    if (state.activeJobId !== jobId) return;
    const cls = /(error|FAILED|fatal)/i.test(line) ? 'log-line-error' : 'log-line-info';
    const span = document.createElement('span');
    span.className = cls;
    span.textContent = line + '\n';
    dom.logPane.appendChild(span);
    dom.logPane.scrollTop = dom.logPane.scrollHeight;
  }

  function connectSSE() {
    const es = new EventSource('/api/session/events');
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'snapshot') {
          state.maxConcurrent = data.max_concurrent;
          applySnapshot(data.jobs);
        } else if (data.type === 'job') {
          applyJobUpdate(data.job);
        } else if (data.type === 'remove') {
          applyRemove(data.job_id);
        } else if (data.type === 'clear_finished') {
          api('/api/session/jobs').then((r) => applySnapshot(r.jobs)).catch(console.error);
        } else if (data.type === 'log') {
          applyLog(data.job_id, data.line);
        }
      } catch (err) { console.error('SSE parse err', err); }
    };
    es.onerror = () => {
      es.close();
      setTimeout(connectSSE, 3000);
    };
  }

  // ── Tab switching ─────────────────────────────────────────────────
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach((t) => t.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
    });
  });

  // ── Init ──────────────────────────────────────────────────────────
  updateComboCount();
  connectSSE();

  // Duration timer
  setInterval(() => {
    let hasRunning = false;
    for (const [, j] of state.jobs) {
      if (j.status === 'running' && j.started_at) {
        hasRunning = true;
        j.duration = (Date.now() / 1000) - j.started_at;
      }
    }
    if (hasRunning) renderJobs();
  }, 1000);
})();
