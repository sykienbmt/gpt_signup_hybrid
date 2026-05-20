/* gpt_signup_hybrid — frontend logic */
(() => {
  'use strict';

  // ── LocalStorage keys ─────────────────────────────────────────────
  const LS_MODE = 'gpt_reg.mail_mode';
  const LS_WORKER = 'gpt_reg.worker_config';
  const LS_SETTINGS = 'gpt_reg.settings'; // {mode, headless, debug, job_timeout, default_password}
  const LS_PROXY = 'gpt_reg.proxy_url';
  const LS_ACTIVE_TAB = 'gpt_reg.active_tab';

  // ── State ─────────────────────────────────────────────────────────
  function loadSettings() {
    try { return JSON.parse(localStorage.getItem(LS_SETTINGS)) || {}; } catch { return {}; }
  }
  function saveSettings(patch) {
    const cur = loadSettings();
    Object.assign(cur, patch);
    localStorage.setItem(LS_SETTINGS, JSON.stringify(cur));
  }

  const _savedSettings = loadSettings();
  const state = {
    jobs: new Map(),          // id → job dict
    order: [],                // job id order
    activeJobId: null,        // job đang xem log
    maxConcurrent: 3,
    mode: _savedSettings.mode || 'multi',
    headless: _savedSettings.headless !== undefined ? _savedSettings.headless : false,
    debug: _savedSettings.debug || false,
    mailModes: [],            // [{id, label, input_placeholder, input_help, config_schema}]
    currentMailMode: 'outlook',
    proxy: null,              // proxy URL hiện active (từ server)
    proxyEditing: false,      // user đang gõ vào input → đừng overwrite từ SSE
  };

  // ── DOM refs ──────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const dom = {
    comboInput:  $('combo-input'),
    btnRun:      $('btn-run'),
    btnStopAll:  $('btn-stop-all'),
    btnClearInput: $('btn-clear-input'),
    comboCount:  $('combo-count'),
    defaultPassword: $('default-password'),
    jobTimeout:     $('job-timeout'),
    jobList:     $('job-list'),
    jobSummary:  $('job-summary'),
    logPane:     $('log-pane'),
    logTarget:   $('log-target'),
    successPane: $('success-pane'),
    errorPane:   $('error-pane'),
    btnCopySuccess: $('btn-copy-success'),
    btnCopyError:   $('btn-copy-error'),
    statusPill:  $('status-pill'),
    modeSelect:  $('mode'),
    headlessToggle: $('headless-toggle'),
    debugToggle: $('debug-toggle'),
    inputHint:   $('input-hint'),
    mailModeSelect: $('mail-mode-select'),
    mailModeConfigHost: $('mail-mode-config-host'),
    // Post-reg toggles
    postRegSessionToggle: $('post-reg-session-toggle'),
    postRegLinkToggle:    $('post-reg-link-toggle'),
    // Proxy strip
    proxyInput:        $('proxy-input'),
    btnProxyTest:      $('btn-proxy-test'),
    btnProxySave:      $('btn-proxy-save'),
    proxyStatus:       $('proxy-status'),
    proxyStatusLabel:  document.querySelector('#proxy-status .proxy-status-label'),
    proxyStatusDetail: $('proxy-status-detail'),
  };

  // ── Helpers ───────────────────────────────────────────────────────
  const icons = Object.freeze({
    stop: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>',
    retry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
    remove: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
    link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07L11 4"/><path d="M14 11a5 5 0 0 0-7.07 0L4.1 13.83a5 5 0 1 0 7.07 7.07L13 19"/></svg>',
    token: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 2l-2 2"/><path d="M7.61 13.39a5.5 5.5 0 1 0 7.78 7.78L21 15.5l-7.5-7.5-5.89 5.39Z"/><path d="m14.5 6.5 3 3"/></svg>',
  });
  const mailModeUiCopy = Object.freeze({
    outlook: {
      input_help: 'One Outlook combo per line.',
      input_placeholder: 'email|password|refresh_token|client_id',
    },
    worker: {
      input_help: 'One iCloud email per line via Worker OTP.',
      input_placeholder: 'user@icloud.com',
    },
    gmail_advanced: {
      input_help: 'Mỗi dòng: api_url hoặc email|api_url. Pre-check mail_status=live.',
      input_placeholder: 'https://checkotpgmail.live/otp/...\nbrandonspencer7424@gmail.com|https://checkotpgmail.live/otp/...',
    },
  });

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

  function icon(name) {
    return icons[name] || '';
  }

  function copyText(text) {
    return navigator.clipboard.writeText(text).catch(() => {
      alert('Copy failed.');
      throw new Error('copy failed');
    });
  }

  function activateTab(tabId) {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    document.querySelectorAll('.tab-content').forEach((tab) => {
      tab.classList.toggle('active', tab.id === `tab-${tabId}`);
    });
    localStorage.setItem(LS_ACTIVE_TAB, tabId);
  }

  function initTabs() {
    if (document.body.dataset.tabsBound === 'true') return;
    document.body.dataset.tabsBound = 'true';
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', () => activateTab(btn.dataset.tab));
    });
    const initialTab = localStorage.getItem(LS_ACTIVE_TAB) || document.querySelector('.tab-btn.active')?.dataset.tab || 'reg';
    activateTab(initialTab);
  }

  window.GptUi = Object.assign(window.GptUi || {}, {
    icon,
    copyText,
    activateTab,
    initTabs,
  });

  // ── Combo counter ─────────────────────────────────────────────────
  function updateComboCount() {
    const lines = dom.comboInput.value.split('\n').filter((l) => {
      const s = l.trim();
      return s && !s.startsWith('#');
    });
    dom.comboCount.textContent = `${lines.length} combo${lines.length === 1 ? '' : 's'}`;
  }

  dom.comboInput.addEventListener('input', updateComboCount);

  // ── Render job list ──────────────────────────────────────────────
  function renderJobs() {
    if (state.order.length === 0) {
      dom.jobList.innerHTML = '<div class="empty">No jobs yet. Paste combos and click Run.</div>';
      dom.jobSummary.textContent = '0 total';
      return;
    }

    const stats = { queued: 0, running: 0, success: 0, error: 0, cancelled: 0 };
    const html = state.order.map((id) => {
      const j = state.jobs.get(id);
      if (!j) return '';
      stats[j.status] = (stats[j.status] || 0) + 1;
      const cls = state.activeJobId === id ? 'job is-active' : 'job';
      const actionBtn = j.status === 'running'
        ? `<button class="icon-btn icon-danger" data-action="stop" data-id="${escHtml(id)}" title="Stop">${icon('stop')}</button>`
        : `<button class="icon-btn" data-action="retry" data-id="${escHtml(id)}" title="Retry">${icon('retry')}</button>`;
      let postRegBtns = '';
      if (j.has_session) {
        postRegBtns += `<button class="icon-btn" data-action="copy-session" data-id="${escHtml(id)}" title="Copy session JSON">${icon('copy')}</button>`;
        postRegBtns += `<button class="icon-btn" data-action="download-session" data-id="${escHtml(id)}" title="Download session JSON">${icon('download')}</button>`;
      }
      if (j.payment_link) {
        postRegBtns += `<button class="icon-btn" data-action="copy-link" data-id="${escHtml(id)}" title="Copy payment link">${icon('link')}</button>`;
      }
      return `
        <div class="${cls}" data-id="${escHtml(id)}">
          <div class="job-status status-${escHtml(j.status)}">${escHtml(j.status)}</div>
          <div class="job-main">
            <div class="job-email" title="${escHtml(j.email)}">${escHtml(j.email)}<span class="badge-mode badge-mode-${escHtml(j.mail_mode || 'outlook')}">${escHtml(j.mail_mode || 'outlook')}</span></div>
          </div>
          <div class="job-duration">${escHtml(fmtDuration(j.duration))}</div>
          <div class="job-actions">
            ${postRegBtns}
            ${actionBtn}
            <button class="icon-btn icon-danger" data-action="remove" data-id="${escHtml(id)}" title="Remove">${icon('remove')}</button>
          </div>
        </div>
      `;
    }).join('');

    dom.jobList.innerHTML = html;
    dom.jobSummary.textContent = [
      `${state.order.length} total`,
      stats.running ? `${stats.running} running` : '',
      stats.queued  ? `${stats.queued} queued`   : '',
      stats.success ? `${stats.success} done`    : '',
      stats.error   ? `${stats.error} failed`    : '',
    ].filter(Boolean).join(' · ');

    updateStatusPill(stats);
  }

  function updateStatusPill(stats) {
    if (stats.running > 0) {
      dom.statusPill.className = 'pill pill-running';
      dom.statusPill.textContent = `running ${stats.running}/${state.maxConcurrent}`;
    } else if (stats.queued > 0) {
      dom.statusPill.className = 'pill pill-running';
      dom.statusPill.textContent = `queued ${stats.queued}`;
    } else if (stats.error > 0 && stats.success === 0) {
      dom.statusPill.className = 'pill pill-error';
      dom.statusPill.textContent = 'error';
    } else if (stats.success > 0) {
      dom.statusPill.className = 'pill pill-success';
      dom.statusPill.textContent = `done ${stats.success}`;
    } else {
      dom.statusPill.className = 'pill pill-idle';
      dom.statusPill.textContent = 'idle';
    }
  }

  // ── Render success/error output ──────────────────────────────────
  function renderOutputs() {
    const successLines = [];
    const errorLines = [];
    for (const id of state.order) {
      const j = state.jobs.get(id);
      if (!j) continue;
      if (j.status === 'success' && j.secret) {
        successLines.push(`${j.email}|${j.password || ''}|${j.secret}`);
      } else if (j.status === 'error') {
        // Nếu đã có password (signup OK, 2FA fail) → vẫn xuất session output
        if (j.password) {
          successLines.push(`${j.email}|${j.password}|no_2fa`);
        }
        errorLines.push(`${j.email}  →  ${j.error || 'unknown'}`);
      }
    }
    dom.successPane.textContent = successLines.length
      ? successLines.join('\n')
      : 'Format: email|password|secret_2fa';
    dom.errorPane.textContent = errorLines.length
      ? errorLines.join('\n')
      : 'No errors yet.';
  }

  // ── Render log của 1 job ─────────────────────────────────────────
  function renderLog(jobId) {
    if (!jobId) {
      dom.logPane.textContent = '';
      dom.logTarget.textContent = '—';
      return;
    }
    const j = state.jobs.get(jobId);
    if (!j) return;
    dom.logTarget.textContent = j.email;
    api(`/api/jobs/${jobId}/log`).then((data) => {
      const lines = data.log || [];
      dom.logPane.innerHTML = lines.map((l) => {
        const cls = /(error|FAILED|fatal)/i.test(l)
          ? 'log-line-error'
          : 'log-line-info';
        return `<span class="${cls}">${escHtml(l)}</span>`;
      }).join('\n');
      dom.logPane.scrollTop = dom.logPane.scrollHeight;
    }).catch((err) => {
      dom.logPane.textContent = `[error] ${err.message}`;
    });
  }

  // ── Job actions ──────────────────────────────────────────────────
  dom.jobList.addEventListener('click', (e) => {
    const target = e.target;
    const actionBtn = target.closest('[data-action]');
    if (actionBtn) {
      const action = actionBtn.dataset.action;
      const id = actionBtn.dataset.id;
      e.stopPropagation();

      if (action === 'retry') {
        if (!confirm('Retry this job?')) return;
        api(`/api/jobs/${id}/retry`, { method: 'POST' }).catch((err) => alert(err.message));
      } else if (action === 'stop') {
        if (!confirm('Stop this running job?')) return;
        api(`/api/jobs/${id}`, { method: 'DELETE' }).catch((err) => alert(err.message));
      } else if (action === 'remove') {
        if (!confirm('Remove this job from the list and textarea?')) return;
        const j = state.jobs.get(id);
        if (j) removeFromTextarea(j.email);
        api(`/api/jobs/${id}`, { method: 'DELETE' }).catch((err) => alert(err.message));
      } else if (action === 'copy-session') {
        api(`/api/jobs/${id}`).then(d => {
          if (d.session_data) {
            copyText(JSON.stringify(d.session_data, null, 2));
          }
        }).catch(console.error);
      } else if (action === 'download-session') {
        api(`/api/jobs/${id}`).then(d => {
          if (d.session_data) {
            const blob = new Blob([JSON.stringify(d.session_data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `session_${d.email || id}.json`;
            a.click();
            URL.revokeObjectURL(url);
          }
        }).catch(console.error);
      } else if (action === 'copy-link') {
        const j = state.jobs.get(id);
        if (j && j.payment_link) copyText(j.payment_link);
      }
      return;
    }
    const row = target.closest('.job');
    if (row) {
      state.activeJobId = row.dataset.id;
      renderJobs();
      renderLog(state.activeJobId);
    }
  });

  function removeFromTextarea(email) {
    const lines = dom.comboInput.value.split('\n');
    const filtered = lines.filter((l) => {
      const m = l.trim().split('|')[0];
      return m.toLowerCase() !== email.toLowerCase();
    });
    dom.comboInput.value = filtered.join('\n');
    updateComboCount();
  }

  // ── Run button ───────────────────────────────────────────────────
  dom.btnRun.addEventListener('click', async () => {
    const combos = dom.comboInput.value.trim();
    if (!combos) {
      alert('Paste combos first.');
      return;
    }
    dom.btnRun.disabled = true;
    try {
      // Luôn sync config server trước khi chạy
      const target = state.mode === 'single' ? 1 : 2;
      await api('/api/config', {
        method: 'POST',
        body: JSON.stringify({ max_concurrent: target }),
      });
      state.maxConcurrent = target;

      // Build payload theo mail mode
      const payload = {
        combos,
        default_password: dom.defaultPassword.value.trim() || null,
        mail_mode: state.currentMailMode,
      };
      if (state.currentMailMode === 'worker') {
        // Đọc trực tiếp từ DOM input (không chỉ localStorage — user có thể chưa trigger persist)
        const urlInp = dom.mailModeConfigHost.querySelector('input[data-config-key="logs_url"]');
        const keyInp = dom.mailModeConfigHost.querySelector('input[data-config-key="api_key"]');
        payload.email_logs_url = (urlInp && urlInp.value.trim()) || '';
        payload.email_api_key = (keyInp && keyInp.value.trim()) || '';
      }

      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
    } catch (err) {
      alert('Error: ' + err.message);
    } finally {
      dom.btnRun.disabled = false;
      validateWorkerConfig();
    }
  });

  dom.btnClearInput.addEventListener('click', () => {
    dom.comboInput.value = '';
    updateComboCount();
  });

  dom.btnStopAll.addEventListener('click', async () => {
    if (!confirm('Stop all running or queued jobs?')) return;
    try {
      const res = await api('/api/jobs/stop-all', { method: 'POST' });
      console.log('stopped:', res.stopped);
    } catch (err) {
      alert('Error: ' + err.message);
    }
  });

  document.getElementById('btn-clear-done').addEventListener('click', async () => {
    try {
      const res = await api('/api/jobs/clear-finished', { method: 'POST' });
      // Refresh list (SSE sẽ broadcast clear_finished event)
      console.log('cleared:', res.removed);
    } catch (err) {
      alert('Error: ' + err.message);
    }
  });

  dom.modeSelect.addEventListener('change', async () => {
    state.mode = dom.modeSelect.value;
    saveSettings({ mode: state.mode });
    const target = state.mode === 'single' ? 1 : 2;
    try {
      await api('/api/config', {
        method: 'POST',
        body: JSON.stringify({ max_concurrent: target }),
      });
      state.maxConcurrent = target;
    } catch (err) {
      console.error(err);
    }
  });

  dom.headlessToggle.addEventListener('change', async () => {
    const headless = dom.headlessToggle.checked;
    try {
      await api('/api/config', {
        method: 'POST',
        body: JSON.stringify({ headless }),
      });
      state.headless = headless;
      saveSettings({ headless });
    } catch (err) {
      console.error(err);
      dom.headlessToggle.checked = state.headless;
    }
  });

  dom.debugToggle.addEventListener('change', async () => {
    const debug = dom.debugToggle.checked;
    try {
      await api('/api/config', {
        method: 'POST',
        body: JSON.stringify({ debug }),
      });
      state.debug = debug;
      saveSettings({ debug });
    } catch (err) {
      console.error(err);
      dom.debugToggle.checked = state.debug;
    }
  });

  dom.postRegSessionToggle.addEventListener('change', async () => {
    const val = dom.postRegSessionToggle.checked;
    try {
      await api('/api/config', {
        method: 'POST',
        body: JSON.stringify({ post_reg_get_session: val }),
      });
      saveSettings({ post_reg_get_session: val });
    } catch (err) {
      console.error(err);
      dom.postRegSessionToggle.checked = !val;
    }
  });

  dom.postRegLinkToggle.addEventListener('change', async () => {
    const val = dom.postRegLinkToggle.checked;
    try {
      await api('/api/config', {
        method: 'POST',
        body: JSON.stringify({ post_reg_get_link: val }),
      });
      saveSettings({ post_reg_get_link: val });
    } catch (err) {
      console.error(err);
      dom.postRegLinkToggle.checked = !val;
    }
  });

  dom.jobTimeout.addEventListener('change', async () => {
    const val = parseInt(dom.jobTimeout.value, 10);
    if (isNaN(val) || val < 30 || val > 600) return;
    saveSettings({ job_timeout: val });
    try {
      await api('/api/config', {
        method: 'POST',
        body: JSON.stringify({ job_timeout: val }),
      });
    } catch (err) {
      console.error(err);
    }
  });

  // Password field persist
  dom.defaultPassword.addEventListener('input', () => {
    saveSettings({ default_password: dom.defaultPassword.value });
  });

  // ── Copy buttons ─────────────────────────────────────────────────
  dom.btnCopySuccess.addEventListener('click', () => copyText(dom.successPane.textContent));
  dom.btnCopyError.addEventListener('click', () => copyText(dom.errorPane.textContent));

  // ── SSE event stream ─────────────────────────────────────────────
  function applySnapshot(jobs) {
    state.order = jobs.map((j) => j.id);
    state.jobs.clear();
    for (const j of jobs) state.jobs.set(j.id, j);
    renderJobs();
    renderOutputs();
  }

  function applyJobUpdate(j) {
    if (!state.jobs.has(j.id)) {
      state.order.push(j.id);
    }
    state.jobs.set(j.id, j);
    renderJobs();
    renderOutputs();
    if (state.activeJobId === j.id) {
      // refresh log nếu đang xem
      renderLog(j.id);
    }
  }

  function applyRemove(jobId) {
    state.jobs.delete(jobId);
    state.order = state.order.filter((id) => id !== jobId);
    if (state.activeJobId === jobId) {
      state.activeJobId = null;
      renderLog(null);
    }
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
    const es = new EventSource('/api/events');
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'snapshot') {
          state.maxConcurrent = data.max_concurrent;
          if (typeof data.headless === 'boolean') {
            state.headless = data.headless;
            dom.headlessToggle.checked = data.headless;
          }
          if (typeof data.debug === 'boolean') {
            state.debug = data.debug;
            dom.debugToggle.checked = data.debug;
          }
          if (data.job_timeout) {
            dom.jobTimeout.value = data.job_timeout;
          }
          if (typeof data.post_reg_get_session === 'boolean') {
            dom.postRegSessionToggle.checked = data.post_reg_get_session;
          }
          if (typeof data.post_reg_get_link === 'boolean') {
            dom.postRegLinkToggle.checked = data.post_reg_get_link;
          }
          if ('proxy' in data) {
            applyProxyStateFromServer(data.proxy);
          }
          applySnapshot(data.jobs);
        } else if (data.type === 'job') {
          applyJobUpdate(data.job);
        } else if (data.type === 'remove') {
          applyRemove(data.job_id);
        } else if (data.type === 'clear_finished') {
          // Server cleared finished jobs — refresh full list
          api('/api/jobs').then((r) => applySnapshot(r.jobs)).catch(console.error);
        } else if (data.type === 'log') {
          applyLog(data.job_id, data.line);
        }
      } catch (err) {
        console.error('SSE parse err', err);
      }
    };
    es.onerror = () => {
      console.warn('SSE disconnected, retry in 3s');
      es.close();
      setTimeout(connectSSE, 3000);
    };
  }

  // ── Mail Mode ─────────────────────────────────────────────────────
  let _workerConfigDebounce = null;

  function getWorkerConfig() {
    try {
      return JSON.parse(localStorage.getItem(LS_WORKER)) || {};
    } catch { return {}; }
  }

  function saveWorkerConfig(cfg) {
    localStorage.setItem(LS_WORKER, JSON.stringify(cfg));
  }

  function renderMailModeSelector(modes) {
    dom.mailModeSelect.innerHTML = modes.map(m =>
      `<option value="${escHtml(m.id)}">${escHtml(m.label)}</option>`
    ).join('');
  }

  function renderMailModeConfig(modes, modeId) {
    const spec = modes.find(m => m.id === modeId);
    if (!spec || spec.config_schema.length === 0) {
      dom.mailModeConfigHost.innerHTML = '';
      return;
    }
    const saved = getWorkerConfig();
    // Ensure defaults are persisted immediately
    let needSave = false;
    for (const f of spec.config_schema) {
      if (saved[f.key] === undefined) {
        saved[f.key] = f.default;
        needSave = true;
      }
    }
    if (needSave) saveWorkerConfig(saved);
    const fields = spec.config_schema.map(f => {
      const val = saved[f.key] !== undefined ? saved[f.key] : f.default;
      const widthClass = f.key === 'api_key' ? 'config-field-short' : 'config-field-long';
      return `
        <label class="input-group ${widthClass}">
          <span class="input-label">${escHtml(f.label)}${f.required ? ' *' : ''}</span>
          <input type="text" data-config-key="${escHtml(f.key)}" value="${escHtml(val)}" spellcheck="false" autocomplete="off" />
          <span class="input-error" id="err-${escHtml(f.key)}"></span>
        </label>
      `;
    }).join('');
    // Sử dụng display:contents wrapper — elements trực tiếp nằm trong flex row
    dom.mailModeConfigHost.innerHTML = `<div class="mail-mode-config-panel">${fields}</div>`;
    // Attach events
    dom.mailModeConfigHost.querySelectorAll('input[data-config-key]').forEach(inp => {
      inp.addEventListener('input', () => debouncePersistWorkerConfig());
      inp.addEventListener('blur', () => debouncePersistWorkerConfig());
    });
    validateWorkerConfig();
  }

  function debouncePersistWorkerConfig() {
    clearTimeout(_workerConfigDebounce);
    _workerConfigDebounce = setTimeout(() => {
      const cfg = {};
      dom.mailModeConfigHost.querySelectorAll('input[data-config-key]').forEach(inp => {
        cfg[inp.dataset.configKey] = inp.value;
      });
      saveWorkerConfig(cfg);
      validateWorkerConfig();
    }, 500);
  }

  function validateWorkerConfig() {
    if (state.currentMailMode !== 'worker') {
      dom.btnRun.disabled = false;
      return;
    }
    const spec = state.mailModes.find(m => m.id === 'worker');
    if (!spec) return;
    let valid = true;
    for (const f of spec.config_schema) {
      const inp = dom.mailModeConfigHost.querySelector(`input[data-config-key="${f.key}"]`);
      const errEl = document.getElementById(`err-${f.key}`);
      if (!inp || !errEl) continue;
      const val = inp.value.trim();
      if (f.validate_prefix && f.validate_prefix.length) {
        if (!f.validate_prefix.some(p => val.startsWith(p))) {
          errEl.textContent = `Must start with ${f.validate_prefix.join(' or ')}`;
          errEl.className = 'input-error';
          valid = false;
          continue;
        }
      }
      if (f.required && !val) {
        errEl.textContent = 'Required';
        errEl.className = 'input-error';
        valid = false;
        continue;
      }
      if (!f.required && !val) {
        errEl.textContent = 'Blank — Worker sends no Authorization header';
        errEl.className = 'input-warn';
        continue;
      }
      errEl.textContent = '';
    }
    dom.btnRun.disabled = !valid;
  }

  function applyMailMode(modeId) {
    state.currentMailMode = modeId;
    dom.mailModeSelect.value = modeId;
    localStorage.setItem(LS_MODE, modeId);
    const spec = state.mailModes.find(m => m.id === modeId);
    if (spec) {
      const uiCopy = mailModeUiCopy[modeId] || {};
      dom.comboInput.placeholder = uiCopy.input_placeholder || spec.input_placeholder;
      dom.inputHint.textContent = uiCopy.input_help || spec.input_help;
    }
    renderMailModeConfig(state.mailModes, modeId);
  }

  async function bootstrapMailModes() {
    try {
      const data = await api('/api/mail-modes');
      state.mailModes = data.modes || [];
    } catch (err) {
      console.error('Failed to load mail modes:', err);
      state.mailModes = [
        { id: 'outlook', label: 'Hotmail (combo)', input_placeholder: 'email|password|refresh_token|client_id', input_help: 'One Outlook combo per line.', config_schema: [] },
      ];
    }
    renderMailModeSelector(state.mailModes);
    // Restore from localStorage
    const saved = localStorage.getItem(LS_MODE);
    const validIds = state.mailModes.map(m => m.id);
    const initial = (saved && validIds.includes(saved)) ? saved : 'outlook';
    applyMailMode(initial);
    // Listen change
    dom.mailModeSelect.addEventListener('change', () => {
      applyMailMode(dom.mailModeSelect.value);
    });
  }

  // ── Proxy ─────────────────────────────────────────────────────────
  // Hiển thị credential ẩn (user:pass@host → ***@host) khi log/render
  function maskProxyForDisplay(url) {
    if (!url) return '';
    const m = url.match(/^([a-z][a-z0-9+.-]*):\/\/([^@/]+)@(.+)$/i);
    if (m) return `${m[1]}://***@${m[3]}`;
    return url;
  }

  function setProxyStatus(kind, label, detail) {
    // kind: idle | testing | ok | fail | direct
    const map = {
      idle:    'proxy-status proxy-status-idle',
      testing: 'proxy-status proxy-status-testing',
      ok:      'proxy-status proxy-status-ok',
      fail:    'proxy-status proxy-status-fail',
      direct:  'proxy-status proxy-status-direct',
    };
    dom.proxyStatus.className = map[kind] || map.idle;
    dom.proxyStatusLabel.textContent = label;
    dom.proxyStatusDetail.textContent = detail || '';
  }

  function applyProxyStateFromServer(url) {
    state.proxy = url || null;
    // Sync input nếu user chưa edit thủ công
    if (!state.proxyEditing) {
      dom.proxyInput.value = url || '';
    }
    if (url) {
      setProxyStatus('idle', 'proxy set', maskProxyForDisplay(url));
    } else {
      setProxyStatus('direct', 'direct', 'no proxy configured');
    }
  }

  async function saveProxy() {
    const val = dom.proxyInput.value.trim();
    dom.btnProxySave.disabled = true;
    try {
      const r = await api('/api/config', {
        method: 'POST',
        body: JSON.stringify({ proxy: val }),
      });
      // Persist localStorage để load lại khi F5 (server đã giữ in-memory)
      if (val) localStorage.setItem(LS_PROXY, val);
      else localStorage.removeItem(LS_PROXY);
      state.proxy = r.proxy || null;
      state.proxyEditing = false;
      applyProxyStateFromServer(r.proxy);
      // Sau khi save → tự test luôn 1 lần để biết còn sống không
      await testProxy({ silent: false });
    } catch (err) {
      setProxyStatus('fail', 'save fail', err.message);
    } finally {
      dom.btnProxySave.disabled = false;
    }
  }

  async function testProxy(opts) {
    opts = opts || {};
    const val = dom.proxyInput.value.trim();
    setProxyStatus('testing', 'testing…', maskProxyForDisplay(val) || 'direct');
    dom.btnProxyTest.disabled = true;
    try {
      const r = await api('/api/proxy/test', {
        method: 'POST',
        body: JSON.stringify({ proxy: val || null }),
      });
      const ipPart = r.public_ip ? `IP ${r.public_ip}` : '';
      const failedTargets = (r.results || []).filter(x => !x.ok).map(x => x.target);
      if (r.ok) {
        const label = val ? 'proxy ok' : 'direct ok';
        setProxyStatus(val ? 'ok' : 'direct', label, [ipPart, maskProxyForDisplay(val)].filter(Boolean).join(' · ') || 'reachable');
      } else if (r.public_ip && !r.ms_reachable) {
        const msg = `Proxy is alive (IP: ${r.public_ip}) but Microsoft is blocked.\nThis proxy cannot be used for Outlook OTP.\nSwitch to a proxy that can reach login.microsoftonline.com and graph.microsoft.com.`;
        setProxyStatus('fail', 'MS blocked', `${ipPart} · Microsoft blocked — OTP polling unavailable`);
        if (!opts.silent) alert(msg);
      } else {
        const errPart = r.error ? r.error : `fail: ${failedTargets.join(', ') || 'unknown'}`;
        setProxyStatus('fail', val ? 'proxy fail' : 'direct fail', errPart);
      }
    } catch (err) {
      setProxyStatus('fail', 'test error', err.message);
    } finally {
      dom.btnProxyTest.disabled = false;
    }
  }

  dom.btnProxyTest.addEventListener('click', () => testProxy({ silent: false }));
  dom.btnProxySave.addEventListener('click', () => saveProxy());
  dom.proxyInput.addEventListener('input', () => {
    state.proxyEditing = true;
  });
  dom.proxyInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      saveProxy();
    }
  });

  // ── Init ─────────────────────────────────────────────────────────
  // Restore settings from localStorage
  dom.modeSelect.value = state.mode;
  dom.headlessToggle.checked = state.headless;
  dom.debugToggle.checked = state.debug;
  if (_savedSettings.job_timeout) dom.jobTimeout.value = _savedSettings.job_timeout;
  if (_savedSettings.default_password) dom.defaultPassword.value = _savedSettings.default_password;
  if (_savedSettings.post_reg_get_session) dom.postRegSessionToggle.checked = true;
  if (_savedSettings.post_reg_get_link) dom.postRegLinkToggle.checked = true;

  // Restore proxy from localStorage (server in-memory, mất sau restart)
  const _savedProxy = localStorage.getItem(LS_PROXY) || '';
  if (_savedProxy) {
    dom.proxyInput.value = _savedProxy;
    state.proxy = _savedProxy;
  }
  setProxyStatus(_savedProxy ? 'idle' : 'direct', _savedProxy ? 'proxy set' : 'direct',
                 _savedProxy ? maskProxyForDisplay(_savedProxy) : 'no proxy configured');

  // Sync server config on load — đẩy proxy localStorage lên server lần đầu
  api('/api/config', {
    method: 'POST',
    body: JSON.stringify({
      max_concurrent: state.mode === 'single' ? 1 : 2,
      headless: state.headless,
      debug: state.debug,
      job_timeout: parseInt(dom.jobTimeout.value, 10) || 240,
      proxy: _savedProxy,
      post_reg_get_session: dom.postRegSessionToggle.checked,
      post_reg_get_link: dom.postRegLinkToggle.checked,
    }),
  }).then((r) => {
    applyProxyStateFromServer(r.proxy);
  }).catch(console.error);

  initTabs();
  updateComboCount();
  bootstrapMailModes();
  connectSSE();

  // Timer cập nhật duration cho jobs đang running mỗi giây
  setInterval(() => {
    let hasRunning = false;
    for (const [id, j] of state.jobs) {
      if (j.status === 'running' && j.started_at) {
        hasRunning = true;
        j.duration = (Date.now() / 1000) - j.started_at;
      }
    }
    if (hasRunning) renderJobs();
  }, 1000);
})();
