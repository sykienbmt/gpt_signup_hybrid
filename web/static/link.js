/* gpt_signup_hybrid — Get Link tab logic (3 modes: combo, session_json, access_token) */
(() => {
  'use strict';

  const MODE_CONFIG = {
    combo: {
      hint: 'One line per combo: email|password|2fa_secret',
      placeholder: 'email@hotmail.com|password123|DNPARKKMM5EYOPDG...\nemail2@outlook.com|pass456|I77PEBZQNEBE67SU...',
    },
    session_json: {
      hint: 'One session JSON per line (each line = 1 account). Hỗ trợ paste 1 JSON nhiều dòng hoặc array.',
      placeholder: '{"accessToken":"eyJhbGci...","user":{"email":"a@x.com"}}\n{"accessToken":"eyJabc...","user":{"email":"b@x.com"}}',
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
    mode: 'session_json',
    region: 'VN',
    upiInProgress: new Set(),
    upiDone: new Set(),
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
    regionSelect: $('link-region-select'),
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
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    return fetch(path, {
      ...opts,
      headers,
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
      // Đếm số JSON object: thử parse cả block; fallback theo dòng
      if (!text) {
        count = 0;
      } else {
        try {
          const parsed = JSON.parse(text);
          count = Array.isArray(parsed) ? parsed.length : 1;
        } catch {
          count = text.split('\n').filter((line) => {
            const trimmed = line.trim();
            return trimmed && !trimmed.startsWith('#');
          }).length;
        }
      }
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
    const html = state.order.map((id, idx) => {
      const job = state.jobs.get(id);
      if (!job) return '';

      stats[job.status] = (stats[job.status] || 0) + 1;
      const cls = state.activeJobId === id ? 'job is-active' : 'job';

      const actions = [];
      if (job.payment_link) {
        actions.push(
          `<button class="icon-btn" data-action="copy-link" data-id="${escHtml(id)}" title="Copy payment link">${window.GptUi.icon('link')}</button>`,
          `<button class="icon-btn" data-action="show-qr" data-id="${escHtml(id)}" title="Show QR code">▣</button>`,
        );
        if (job.region === 'IN') {
          const upiBusy = state.upiInProgress.has(id);
          const upiDone = state.upiDone.has(id);
          let upiLabel;
          if (upiBusy) upiLabel = '<span class="upi-spinner"></span>';
          else if (upiDone) upiLabel = '✅';
          else upiLabel = '🇮🇳 UPI';
          const upiAttrs = upiBusy ? 'disabled' : '';
          const upiTitle = upiDone ? 'UPI filled — click to re-run' : 'Auto fill UPI &amp; subscribe';
          actions.push(
            `<button class="icon-btn upi-btn" data-action="upi-fill" data-id="${escHtml(id)}" title="${upiTitle}" ${upiAttrs}>${upiLabel}</button>`,
          );
        }
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

      const shots = (job.screenshot_urls && job.screenshot_urls.length)
        ? `<div class="job-meta">📸 ${job.screenshot_urls.map((u, i) =>
            `<a href="${escHtml(u)}" data-action="show-shot" data-url="${escHtml(u)}" class="shot-link" title="${escHtml(u)}">shot${job.screenshot_urls.length > 1 ? (i + 1) : ''}</a>`
          ).join(' · ')}</div>`
        : '';

      const modeTag = job.mode && job.mode !== 'combo' ? `<span class="muted">[${escHtml(job.mode)}]</span> ` : '';

      return `
        <div class="${cls}" data-id="${escHtml(id)}">
          <div class="job-index">${idx + 1}</div>
          <div class="job-status status-${escHtml(job.status)}">${escHtml(job.status)}</div>
          <div class="job-main">
            <div class="job-email" title="${escHtml(job.email)}">${modeTag}${escHtml(job.email)}</div>
            ${meta}
            ${shots}
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
    const es = window.GptUi.authEventSource('/api/link/events');
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'snapshot') {
          state.maxConcurrent = data.max_concurrent || state.maxConcurrent;
          if (data.job_timeout) dom.jobTimeout.value = data.job_timeout;
          if (data.region) {
            state.region = data.region;
            dom.regionSelect.value = data.region;
          }
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
      } else if (action === 'show-qr') {
        const job = state.jobs.get(id);
        if (job && job.payment_link) showQrModal(job.payment_link);
      } else if (action === 'show-shot') {
        event.preventDefault();
        const url = actionBtn.dataset.url;
        if (url) showShotModal(url);
      } else if (action === 'upi-fill') {
        if (state.upiInProgress.has(id)) return;
        state.upiInProgress.add(id);
        renderJobs();
        api(`/api/link/jobs/${id}/upi-fill`, { method: 'POST' })
          .then((res) => {
            if (res.ok) state.upiDone.add(id);
            else alert('UPI fill failed: ' + (res.error || 'unknown'));
          })
          .catch((err) => {
            alert('UPI fill error: ' + err.message);
          })
          .finally(() => {
            state.upiInProgress.delete(id);
            renderJobs();
          });
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
        body: JSON.stringify({ combos, mode: state.mode, region: state.region }),
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

  dom.jobTimeout.addEventListener('change', async () => {
    const val = parseInt(dom.jobTimeout.value, 10);
    if (isNaN(val) || val < 30 || val > 600) return;
    try {
      await api('/api/link/config', {
        method: 'POST',
        body: JSON.stringify({ job_timeout: val }),
      });
    } catch (err) { console.error(err); }
  });

  dom.regionSelect.addEventListener('change', async () => {
    state.region = dom.regionSelect.value;
    try {
      await api('/api/link/config', {
        method: 'POST',
        body: JSON.stringify({ region: state.region }),
      });
    } catch (err) { console.error(err); }
  });

  // ─── QR Modal ───
  const qrModal = document.getElementById('qr-modal');
  const qrCanvasWrap = document.getElementById('qr-canvas-wrap');
  const qrModalUrl = document.getElementById('qr-modal-url');
  const qrModalClose = document.getElementById('qr-modal-close');
  const qrModalBackdrop = qrModal.querySelector('.qr-modal-backdrop');
  let _qrInstance = null;

  function showQrModal(url) {
    qrCanvasWrap.innerHTML = '';
    _qrInstance = new QRCode(qrCanvasWrap, {
      text: url,
      width: 440,
      height: 440,
      colorDark: '#000000',
      colorLight: '#ffffff',
      correctLevel: QRCode.CorrectLevel.M,
    });
    qrModalUrl.textContent = url;
    qrModal.classList.remove('hidden');
  }

  function closeQrModal() {
    qrModal.classList.add('hidden');
    qrCanvasWrap.innerHTML = '';
    _qrInstance = null;
  }

  qrModalClose.addEventListener('click', closeQrModal);
  qrModalBackdrop.addEventListener('click', closeQrModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !qrModal.classList.contains('hidden')) closeQrModal();
    if (e.key === 'Escape' && !shotModal.classList.contains('hidden')) closeShotModal();
  });

  // ─── Screenshot Modal ───
  const shotModal = document.getElementById('shot-modal');
  const shotModalImg = document.getElementById('shot-modal-img');
  const shotModalUrl = document.getElementById('shot-modal-url');
  const shotModalClose = document.getElementById('shot-modal-close');

  function showShotModal(url) {
    shotModalImg.src = url;
    shotModalUrl.textContent = url;
    shotModal.classList.remove('hidden');
  }
  function closeShotModal() {
    shotModal.classList.add('hidden');
    shotModalImg.src = '';
  }
  shotModalClose.addEventListener('click', closeShotModal);
  shotModal.querySelectorAll('[data-shot-close]').forEach((el) =>
    el.addEventListener('click', closeShotModal)
  );

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
