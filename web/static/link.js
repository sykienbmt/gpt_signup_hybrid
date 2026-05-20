/* gpt_signup_hybrid — Get Link tab logic (3 modes: combo, session_json, access_token) */
(() => {
  'use strict';

  const MODE_CONFIG = {
    combo: {
      hint: 'One line per combo: email|password|2fa_secret',
      placeholder: 'email@hotmail.com|password123|DNPARKKMM5EYOPDG...\nemail2@outlook.com|pass456|I77PEBZQNEBE67SU...',
    },
    session_json: {
      hint: 'Paste one session JSON object (from /api/auth/session)',
      placeholder: '{\n  "accessToken": "eyJhbGci...",\n  "user": { "email": "user@example.com", ... },\n  ...\n}',
    },
    access_token: {
      hint: 'One raw accessToken (JWT) per line',
      placeholder: 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ...\neyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ...',
    },
  };

  const state = {
    jobs: new Map(),
    order: [],
    activeJobId: null,
    maxConcurrent: 1,
    mode: 'combo',
  };

  const $ = (id) => document.getElementById(id);
  const dom = {
    comboInput: $('link-combo-input'),
    modeHint: $('link-mode-hint'),
    btnRun: $('link-btn-run'),
    btnStopAll: $('link-btn-stop-all'),
    btnClearInput: $('link-btn-clear-input'),
    btnClearDone: $('link-btn-clear-done'),
    btnCopyError: $('link-btn-copy-error'),
    comboCount: $('link-combo-count'),
    jobTimeout: $('link-job-timeout'),
    jobList: $('link-job-list'),
    jobSummary: $('link-job-summary'),
    logPane: $('link-log-pane'),
    logTarget: $('link-log-target'),
    errorPane: $('link-error-pane'),
  };

  // ─── Mode switching ───
  const modeBtns = document.querySelectorAll('.link-mode-btn');
  modeBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      modeBtns.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.mode = btn.dataset.mode;
      const cfg = MODE_CONFIG[state.mode];
      dom.modeHint.textContent = cfg.hint;
      dom.comboInput.placeholder = cfg.placeholder;
      updateComboCount();
    });
  });

  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function fmtDuration(secs) {
    if (secs == null) return '';
    if (secs < 60) return secs.toFixed(1) + 's';
    return Math.floor(secs / 60) + 'm' + Math.floor(secs % 60) + 's';
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

  function updateComboCount() {
    const text = dom.comboInput.value.trim();
    let count = 0;

    if (state.mode === 'combo' || state.mode === 'access_token') {
      count = text.split('\n').filter((line) => {
        const trimmed = line.trim();
        return trimmed && !trimmed.startsWith('#');
      }).length;
    } else if (state.mode === 'session_json') {
      // Single JSON only — valid or not
      count = text.length > 0 ? 1 : 0;
    }

    const label = state.mode === 'session_json' ? 'session' : 'item';
    dom.comboCount.textContent = `${count} ${label}${count === 1 ? '' : 's'}`;
  }

  function renderErrors() {
    const errors = state.order
      .map((id) => state.jobs.get(id))
      .filter((job) => job && job.status === 'error')
      .map((job) => `${job.email}  →  ${job.error || 'unknown'}`);

    dom.errorPane.textContent = errors.length ? errors.join('\n') : 'No errors yet.';
  }

  function renderJobs() {
    if (state.order.length === 0) {
      dom.jobList.innerHTML = '<div class="empty">Paste input and click Get Link.</div>';
      dom.jobSummary.textContent = '0 total';
      renderErrors();
      return;
    }

    const stats = { queued: 0, running: 0, success: 0, error: 0, cancelled: 0 };
    const html = state.order.map((id) => {
      const job = state.jobs.get(id);
      if (!job) return '';

      stats[job.status] = (stats[job.status] || 0) + 1;
      const cls = state.activeJobId === id ? 'job is-active' : 'job';

      const actions = [];
      if (job.payment_link) {
        actions.push(
          `<button class="icon-btn" data-action="copy-link" data-id="${escHtml(id)}" title="Copy payment link">${window.GptUi.icon('link')}</button>`,
        );
      }
      if (job.status === 'running') {
        actions.push(
          `<button class="icon-btn icon-danger" data-action="stop" data-id="${escHtml(id)}" title="Stop">${window.GptUi.icon('stop')}</button>`,
        );
      } else if (job.status === 'error') {
        actions.push(
          `<button class="icon-btn" data-action="retry" data-id="${escHtml(id)}" title="Retry">${window.GptUi.icon('retry')}</button>`,
        );
      }
      actions.push(
        `<button class="icon-btn icon-danger" data-action="remove" data-id="${escHtml(id)}" title="Remove">${window.GptUi.icon('remove')}</button>`,
      );

      const meta = job.payment_link
        ? `<div class="job-meta" title="${escHtml(job.payment_link)}">${escHtml(job.payment_link)}</div>`
        : '';

      const modeTag = job.mode && job.mode !== 'combo' ? `<span class="muted">[${escHtml(job.mode)}]</span> ` : '';

      return `
        <div class="${cls}" data-id="${escHtml(id)}">
          <div class="job-status status-${escHtml(job.status)}">${escHtml(job.status)}</div>
          <div class="job-main">
            <div class="job-email" title="${escHtml(job.email)}">${modeTag}${escHtml(job.email)}</div>
            ${meta}
          </div>
          <div class="job-duration">${escHtml(fmtDuration(job.duration))}</div>
          <div class="job-actions">${actions.join('')}</div>
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
    renderErrors();
  }

  function renderLog(jobId) {
    if (!jobId) {
      dom.logPane.textContent = '';
      dom.logTarget.textContent = '—';
      return;
    }

    const job = state.jobs.get(jobId);
    if (!job) return;

    dom.logTarget.textContent = job.email;
    api(`/api/link/jobs/${jobId}`).then((data) => {
      dom.logPane.textContent = (data.log_lines || []).join('\n');
      dom.logPane.scrollTop = dom.logPane.scrollHeight;
    }).catch((err) => {
      dom.logPane.textContent = `[error] ${err.message}`;
    });
  }

  function applySnapshot(jobs) {
    state.order = jobs.map((job) => job.id);
    state.jobs.clear();
    jobs.forEach((job) => state.jobs.set(job.id, job));
    renderJobs();
    if (state.activeJobId && !state.jobs.has(state.activeJobId)) {
      state.activeJobId = null;
      renderLog(null);
    }
  }

  function applyJobUpdate(job) {
    if (!state.jobs.has(job.id)) state.order.push(job.id);
    state.jobs.set(job.id, job);
    renderJobs();
    if (state.activeJobId === job.id) renderLog(job.id);
  }

  function applyRemove(jobId) {
    state.jobs.delete(jobId);
    state.order = state.order.filter((id) => id !== jobId);
    if (state.activeJobId === jobId) {
      state.activeJobId = null;
      renderLog(null);
    }
    renderJobs();
  }

  function applyLog(jobId, line) {
    if (state.activeJobId !== jobId) return;
    dom.logPane.textContent += `${line}\n`;
    dom.logPane.scrollTop = dom.logPane.scrollHeight;
  }

  function connectSSE() {
    const es = new EventSource('/api/link/events');
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'snapshot') {
          state.maxConcurrent = data.max_concurrent || state.maxConcurrent;
          if (data.job_timeout) dom.jobTimeout.value = data.job_timeout;
          applySnapshot(data.jobs || []);
        } else if (data.type === 'job') {
          applyJobUpdate(data.job);
        } else if (data.type === 'log') {
          applyLog(data.job_id, data.line);
        } else if (data.type === 'remove') {
          applyRemove(data.job_id);
        } else if (data.type === 'clear_finished') {
          api('/api/link/jobs').then((response) => applySnapshot(response.jobs || [])).catch(console.error);
        }
      } catch (err) {
        console.error('Link SSE parse error', err);
      }
    };
    es.onerror = () => {
      es.close();
      setTimeout(connectSSE, 3000);
    };
  }

  dom.jobList.addEventListener('click', (event) => {
    const actionBtn = event.target.closest('[data-action]');
    if (actionBtn) {
      const action = actionBtn.dataset.action;
      const id = actionBtn.dataset.id;
      event.stopPropagation();

      if (action === 'copy-link') {
        const job = state.jobs.get(id);
        if (job && job.payment_link) {
          window.GptUi.copyText(job.payment_link);
        }
      } else if (action === 'retry') {
        api(`/api/link/jobs/${id}/retry`, { method: 'POST' }).catch((err) => alert(err.message));
      } else if (action === 'stop' || action === 'remove') {
        api(`/api/link/jobs/${id}`, { method: 'DELETE' }).catch((err) => alert(err.message));
      }
      return;
    }

    const row = event.target.closest('.job');
    if (!row) return;
    state.activeJobId = row.dataset.id;
    renderJobs();
    renderLog(state.activeJobId);
  });

  dom.btnRun.addEventListener('click', async () => {
    const combos = dom.comboInput.value.trim();
    if (!combos) {
      alert('Paste input first.');
      return;
    }

    dom.btnRun.disabled = true;
    try {
      await api('/api/link/jobs', {
        method: 'POST',
        body: JSON.stringify({ combos, mode: state.mode }),
      });
    } catch (err) {
      alert('Error: ' + err.message);
    } finally {
      dom.btnRun.disabled = false;
    }
  });

  dom.btnStopAll.addEventListener('click', () => {
    api('/api/link/jobs/stop-all', { method: 'POST' }).catch((err) => alert(err.message));
  });

  dom.btnClearInput.addEventListener('click', () => {
    dom.comboInput.value = '';
    updateComboCount();
  });

  dom.btnClearDone.addEventListener('click', () => {
    api('/api/link/jobs/clear-finished', { method: 'POST' }).catch((err) => alert(err.message));
  });

  dom.btnCopyError.addEventListener('click', () => {
    window.GptUi.copyText(dom.errorPane.textContent);
  });

  dom.comboInput.addEventListener('input', updateComboCount);
  updateComboCount();
  connectSSE();

  setInterval(() => {
    let hasRunning = false;
    for (const [, job] of state.jobs) {
      if (job.status === 'running' && job.started_at) {
        hasRunning = true;
        job.duration = (Date.now() / 1000) - job.started_at;
      }
    }
    if (hasRunning) renderJobs();
  }, 1000);
})();
