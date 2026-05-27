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
    btnClearAll:    $('ses-btn-clear-all'),
    btnExport:      $('ses-btn-export'),
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
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    return fetch(path, {
      ...opts,
      headers,
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
    dom.comboCount.textContent = `${lines.length} combo${lines.length === 1 ? '' : 's'}`;
  }
  dom.comboInput.addEventListener('input', updateComboCount);

  // ── Render job list ───────────────────────────────────────────────
  function renderJobs() {
    if (state.order.length === 0) {
      dom.jobList.innerHTML = '<div class="empty">Paste combos and click Get Session.</div>';
      dom.jobSummary.textContent = '0 total';
      return;
    }

    const stats = { queued: 0, running: 0, success: 0, error: 0, cancelled: 0 };
    const html = state.order.map((id, idx) => {
      const j = state.jobs.get(id);
      if (!j) return '';
      stats[j.status] = (stats[j.status] || 0) + 1;
      const cls = state.activeJobId === id ? 'job is-active' : 'job';

      let actionBtns = '';
      if (j.status === 'running') {
        actionBtns = `<button class="icon-btn icon-danger" data-action="stop" data-id="${escHtml(id)}" title="Stop">${window.GptUi.icon('stop')}</button>`;
      } else if (j.status === 'success') {
        actionBtns = `
          <button class="icon-btn" data-action="copy-combo" data-id="${escHtml(id)}" title="Copy email|pass|2fa|json">📋</button>
          <button class="icon-btn" data-action="download" data-id="${escHtml(id)}" title="Download JSON">${window.GptUi.icon('download')}</button>
          <button class="icon-btn" data-action="copy-json" data-id="${escHtml(id)}" title="Copy JSON">${window.GptUi.icon('copy')}</button>
          <button class="icon-btn" data-action="copy-token" data-id="${escHtml(id)}" title="Copy access token">${window.GptUi.icon('token')}</button>
        `;
      } else {
        actionBtns = `<button class="icon-btn" data-action="retry" data-id="${escHtml(id)}" title="Retry">${window.GptUi.icon('retry')}</button>`;
      }

      return `
        <div class="${cls}" data-id="${escHtml(id)}">
          <div class="job-index">${idx + 1}</div>
          <div class="job-status status-${escHtml(j.status)}">${escHtml(j.status)}</div>
          <div class="job-main">
            <div class="job-email" title="${escHtml(j.email)}">${escHtml(j.email)}</div>
          </div>
          <div class="job-duration">${escHtml(fmtDuration(j.duration))}</div>
          <div class="job-actions">
            ${actionBtns}
            <button class="icon-btn icon-danger" data-action="remove" data-id="${escHtml(id)}" title="Remove">${window.GptUi.icon('remove')}</button>
          </div>
        </div>
      `;
    }).join('');

    dom.jobList.innerHTML = html;
    dom.jobSummary.textContent = [
      `${state.order.length} total`,
      stats.running ? `${stats.running} running` : '',
      stats.success ? `${stats.success} done` : '',
      stats.error ? `${stats.error} failed` : '',
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
      : 'No errors yet.';
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
        api(`/api/session/jobs/${id}/retry`, { method: 'POST' }).catch((err) => alert(err.message));
      } else if (action === 'stop') {
        api(`/api/session/jobs/${id}`, { method: 'DELETE' }).catch((err) => alert(err.message));
      } else if (action === 'remove') {
        api(`/api/session/jobs/${id}`, { method: 'DELETE' }).catch((err) => alert(err.message));
      } else if (action === 'copy-combo') {
        const btn = actionBtn;
        api(`/api/session/jobs/${id}`).then((res) => {
          const j2 = state.jobs.get(id);
          const email = j2 ? j2.email : id;
          const pass = res.password || '';
          const twofa = res.secret || '';
          const json = res.session_data ? JSON.stringify(res.session_data) : '';
          const line = `${email}|${pass}|${twofa}|${json}`;
          window.GptUi.copyText(line);
          const orig = btn.textContent;
          btn.textContent = '✅';
          setTimeout(() => { btn.textContent = orig; }, 1500);
        }).catch((err) => alert('Copy lỗi: ' + err.message));
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
          }).catch((err) => alert(err.message));
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
      window.GptUi.copyText(JSON.stringify(sessionData, null, 2));
    } else if (action === 'copy-token') {
      if (sessionData.accessToken) {
        window.GptUi.copyText(sessionData.accessToken);
      }
    }
  }

  // ── Run button ────────────────────────────────────────────────────
  dom.btnRun.addEventListener('click', async () => {
    const combos = dom.comboInput.value.trim();
    if (!combos) { alert('Paste combos first.'); return; }
    dom.btnRun.disabled = true;
    try {
      // Sync config
      const target = parseInt(document.getElementById('mode').value, 10) || 1;
      await api('/api/session/config', {
        method: 'POST',
        body: JSON.stringify({ max_concurrent: target }),
      });
      await api('/api/session/jobs', {
        method: 'POST',
        body: JSON.stringify({ combos }),
      });
    } catch (err) {
      alert('Error: ' + err.message);
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

  dom.btnClearAll.addEventListener('click', async () => {
    if (!confirm('Cancel ALL running jobs and remove the entire list?')) return;
    try {
      await api('/api/session/jobs/clear-all', { method: 'POST' });
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
    window.GptUi.copyText(dom.errorPane.textContent);
  });

  // ── Export button ─────────────────────────────────────────────────
  dom.btnExport.addEventListener('click', async () => {
    const successIds = state.order.filter((id) => {
      const j = state.jobs.get(id);
      return j && j.status === 'success' && j.has_session;
    });
    if (!successIds.length) { alert('Không có session thành công nào để export.'); return; }

    dom.btnExport.disabled = true;
    dom.btnExport.textContent = '⏳ Exporting…';
    try {
      const lines = await Promise.all(successIds.map(async (id) => {
        let data = sessionCache.get(id);
        let password = null;
        let secret = null;
        if (!data) {
          const res = await api(`/api/session/jobs/${id}`);
          if (res.session_data) { sessionCache.set(id, res.session_data); data = res.session_data; }
          password = res.password;
          secret = res.secret;
        } else {
          // Re-fetch to get password/secret (not cached separately)
          const res = await api(`/api/session/jobs/${id}`);
          password = res.password;
          secret = res.secret;
        }
        const j = state.jobs.get(id);
        const email = j ? j.email : id;
        const pass = password || '';
        const twofa = secret || '';
        const json = data ? JSON.stringify(data) : '';
        return `${email}|${pass}|${twofa}|${json}`;
      }));
      const content = lines.join('\n');
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 16);
      const filename = `sessions-${ts}.txt`;
      const blob = new Blob([content], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert('Export lỗi: ' + err.message);
    } finally {
      dom.btnExport.disabled = false;
      dom.btnExport.textContent = '⬇ Export';
    }
  });

  // ── SSE ───────────────────────────────────────────────────────────
  function applySnapshot(jobs) {
    state.order = jobs.map((j) => j.id);
    state.jobs.clear();
    for (const j of jobs) state.jobs.set(j.id, j);
    // Prune sessionCache: chỉ giữ entry cho jobs còn trong snapshot
    for (const cachedId of Array.from(sessionCache.keys())) {
      if (!state.jobs.has(cachedId)) sessionCache.delete(cachedId);
    }
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
    sessionCache.delete(jobId);
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
    const es = window.GptUi.authEventSource('/api/session/events');
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
