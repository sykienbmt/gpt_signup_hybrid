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
    // UPI payment polling: jobId → intervalId (null = stopped)
    upiPolling: new Map(),
    upiPaid: new Set(),
    // Watch mode
    watch: {
      active: false,
      slots: [],         // [{slot_idx, job_id, email, status, status_msg, has_screenshot, screenshot_ts}]
      pollInterval: null,
    },
  };

  const $ = (id) => document.getElementById(id);
  const dom = {
    comboInput: $('link-combo-input'),
    modeHint: $('link-mode-hint'),
    btnRun: $('link-btn-run'),
    btnStopAll: $('link-btn-stop-all'),
    btnClearInput: $('link-btn-clear-input'),
    btnClearDone: $('link-btn-clear-done'),
    btnClearAll: $('link-btn-clear-all'),
    btnCopyError: $('link-btn-copy-error'),
    btnWatch: $('link-btn-watch'),
    comboCount: $('link-combo-count'),
    jobTimeout: $('link-job-timeout'),
    regionSelect: $('link-region-select'),
    jobList: $('link-job-list'),
    jobSummary: $('link-job-summary'),
    logPane: $('link-log-pane'),
    logTarget: $('link-log-target'),
    errorPane: $('link-error-pane'),
    watchPanel: $('link-watch-panel'),
    watchSlots: $('link-watch-slots'),
    watchStopAll: $('link-watch-stop-all'),
    watchClose: $('link-watch-close'),
    watchPicker: $('link-watch-picker'),
    watchPickerList: $('link-watch-picker-list'),
    watchPickerStart: $('link-watch-picker-start'),
    watchPickerCancel: $('link-watch-picker-cancel'),
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

      const isTrial = job.is_trial === true;
      const isPaid  = job.is_trial === false;
      const upiPaid = state.upiPaid.has(id);
      const isPolling = state.upiPolling.has(id);

      const actions = [];
      if (job.payment_link) {
        actions.push(
          `<button class="icon-btn" data-action="copy-link" data-id="${escHtml(id)}" title="Copy payment link">${window.GptUi.icon('link')}</button>`,
        );
        // Trial: only copy + delete; no UPI, no open-link action buttons
        if (!isTrial) {
          actions.push(
            `<a class="icon-btn" href="${escHtml(job.payment_link)}" target="_blank" rel="noopener noreferrer" title="Open payment link">🔗</a>`,
          );
          if (job.region === 'IN' && !upiPaid) {
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
          // UPI polling controls (only after upiDone and not yet paid)
          if (state.upiDone.has(id) && !upiPaid) {
            if (isPolling) {
              actions.push(
                `<button class="icon-btn" data-action="upi-stop-poll" data-id="${escHtml(id)}" title="Stop watching payment" style="color:var(--red)">⏹</button>`,
              );
            } else {
              actions.push(
                `<button class="icon-btn" data-action="upi-start-poll" data-id="${escHtml(id)}" title="Watch for payment">👁</button>`,
              );
            }
          }
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

      // Trial / paid badge
      let trialBadge = '';
      if (upiPaid) {
        trialBadge = `<span style="color:var(--green);font-weight:700;font-size:11px">💸 PAID</span>`;
      } else if (isTrial) {
        const days = job.trial_days ? ` (${job.trial_days}d)` : '';
        trialBadge = `<span style="color:var(--green);font-weight:700;font-size:11px">✅ FREE Trial${days}</span>`;
      } else if (isPaid && job.amount_due >= 0) {
        const amt = (job.amount_due / 100).toFixed(2);
        trialBadge = `<span style="color:var(--red);font-weight:600;font-size:11px">💳 ${escHtml(amt)} ${escHtml(job.currency)}</span>`;
      } else if (isPolling) {
        trialBadge = `<span style="color:var(--blue);font-size:11px">👁 Watching…</span>`;
      }

      // Border color based on trial status
      let borderStyle = '';
      if (upiPaid) borderStyle = 'border-left:3px solid var(--green)';
      else if (isTrial) borderStyle = 'border-left:3px solid var(--green)';
      else if (isPaid) borderStyle = '';

      const meta = job.payment_link
        ? `<div class="job-meta" title="${escHtml(job.payment_link)}">${trialBadge ? trialBadge + ' · ' : ''}${escHtml(job.payment_link)}</div>`
        : '';

      const shots = (job.screenshot_urls && job.screenshot_urls.length)
        ? `<div class="job-meta">📸 ${job.screenshot_urls.map((u, i) =>
            `<a href="${escHtml(u)}" data-action="show-shot" data-url="${escHtml(u)}" class="shot-link" title="Xem ảnh">shot${job.screenshot_urls.length > 1 ? (i + 1) : ''}</a><button class="shot-copy-btn" data-action="copy-shot" data-url="${escHtml(u)}" title="Copy ảnh vào clipboard">📋</button>`
          ).join(' · ')}</div>`
        : '';

      const modeTag = job.mode && job.mode !== 'combo' ? `<span class="muted">[${escHtml(job.mode)}]</span> ` : '';

      return `
        <div class="${cls}" data-id="${escHtml(id)}"${borderStyle ? ` style="${borderStyle}"` : ''}>
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

  // ─── Watch Mode ───────────────────────────────────────────────────────────

  const WATCH_STATUS_LABEL = {
    idle: '⏳ Idle', opening: '🔄 Opening browser…', navigating: '🌐 Navigating…',
    filling: '📝 Filling billing…', submitting: '🚀 Submitting…',
    waiting_qr: '⏳ Waiting for QR…', qr_visible: '📱 QR visible',
    done: '✅ Paid', failed: '❌ Failed', off: '🔴 Closed', error: '⚠ Error',
  };
  const WATCH_STATUS_COLOR = {
    qr_visible: 'var(--blue)', done: 'var(--green)', failed: 'var(--red)',
    error: 'var(--red)', off: 'var(--muted)',
  };

  function renderWatchPanel() {
    const { active, slots } = state.watch;
    dom.watchPanel.style.display = active ? '' : 'none';
    if (!active) return;

    dom.watchSlots.innerHTML = slots.map((s) => {
      const color = WATCH_STATUS_COLOR[s.status] || 'var(--fg)';
      const label = WATCH_STATUS_LABEL[s.status] || s.status;
      const isDone = ['done', 'failed', 'off'].includes(s.status);
      const shotUrl = s.has_screenshot
        ? `/api/link/watch/slot/${s.slot_idx}/screenshot?t=${s.screenshot_ts}`
        : '';
      const actionBtns = isDone ? '' : `
        <div class="watch-slot-actions">
          <button class="btn btn-ghost btn-small" data-watch-action="done" data-slot="${s.slot_idx}">✅ Done</button>
          <button class="btn btn-ghost btn-small" data-watch-action="fail" data-slot="${s.slot_idx}">❌ Fail</button>
          <button class="btn btn-ghost btn-small" data-watch-action="off"  data-slot="${s.slot_idx}">🔴 Off</button>
        </div>`;
      return `<div class="watch-slot">
        <div class="watch-slot-header">
          <span class="watch-slot-idx">${s.slot_idx + 1}</span>
          <span class="watch-slot-email" title="${escHtml(s.email)}">${escHtml(s.email)}</span>
          <span class="watch-slot-status" style="color:${color}">${label}</span>
        </div>
        <div class="watch-slot-msg muted">${escHtml(s.status_msg)}</div>
        ${shotUrl ? `<img class="watch-slot-shot" src="${escHtml(shotUrl)}" alt="screenshot">` : '<div class="watch-slot-no-shot muted">No screenshot yet</div>'}
        ${actionBtns}
      </div>`;
    }).join('');
  }

  async function pollWatchStatus() {
    try {
      const data = await api('/api/link/watch/status');
      state.watch.slots = data.slots || [];
      renderWatchPanel();
    } catch (err) {
      console.error('[watch poll]', err.message);
    }
  }

  function startWatchPolling() {
    if (state.watch.pollInterval) return;
    state.watch.pollInterval = setInterval(pollWatchStatus, 3000);
  }

  function stopWatchPolling() {
    if (state.watch.pollInterval) {
      clearInterval(state.watch.pollInterval);
      state.watch.pollInterval = null;
    }
  }

  function openWatchPicker() {
    // Collect eligible India jobs with a payment link
    const eligible = state.order
      .map((id) => state.jobs.get(id))
      .filter((j) => j && j.status === 'success' && j.payment_link && j.region === 'IN');

    if (!eligible.length) {
      alert('No India jobs with payment links available. Get links for India region first.');
      return;
    }

    dom.watchPickerList.innerHTML = eligible.map((j, i) => `
      <label class="watch-picker-row">
        <input type="checkbox" class="watch-picker-cb" value="${escHtml(j.id)}" data-idx="${i}" ${i < 3 ? 'checked' : ''}>
        <span>${escHtml(j.email)}</span>
        <span class="muted" style="font-size:11px">${escHtml((j.payment_link || '').slice(0, 48))}…</span>
      </label>
    `).join('');

    // Enforce max 3 checkboxes
    dom.watchPickerList.querySelectorAll('.watch-picker-cb').forEach((cb) => {
      cb.addEventListener('change', () => {
        const checked = dom.watchPickerList.querySelectorAll('.watch-picker-cb:checked');
        if (checked.length > 3) cb.checked = false;
      });
    });

    dom.watchPicker.style.display = '';
  }

  dom.btnWatch.addEventListener('click', openWatchPicker);

  dom.watchPickerCancel.addEventListener('click', () => {
    dom.watchPicker.style.display = 'none';
  });

  dom.watchPickerStart.addEventListener('click', async () => {
    const checked = [...dom.watchPickerList.querySelectorAll('.watch-picker-cb:checked')];
    if (!checked.length) { alert('Select at least one account.'); return; }

    const slots = checked.slice(0, 3).map((cb, i) => {
      const jobId = cb.value;
      const job = state.jobs.get(jobId);
      return {
        slot_idx: i,
        job_id: jobId,
        email: job?.email || '',
        payment_url: job?.payment_link || '',
        publishable_key: job?.publishable_key || null,
        checkout_session_id: job?.checkout_session_id || null,
      };
    });

    dom.watchPicker.style.display = 'none';
    dom.watchPickerStart.disabled = true;

    try {
      const data = await api('/api/link/watch/start', {
        method: 'POST',
        body: JSON.stringify({ slots }),
      });
      state.watch.active = true;
      state.watch.slots = data.slots || [];
      renderWatchPanel();
      startWatchPolling();
    } catch (err) {
      alert('Watch start failed: ' + err.message);
    } finally {
      dom.watchPickerStart.disabled = false;
    }
  });

  dom.watchStopAll.addEventListener('click', async () => {
    try {
      await api('/api/link/watch/stop-all', { method: 'POST' });
      await pollWatchStatus();
    } catch (err) {
      console.error(err);
    }
  });

  dom.watchClose.addEventListener('click', () => {
    stopWatchPolling();
    state.watch.active = false;
    renderWatchPanel();
  });

  // Watch slot action buttons (event delegation on watch slots container)
  dom.watchSlots.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-watch-action]');
    if (!btn) return;
    const action = btn.dataset.watchAction;
    const slotIdx = parseInt(btn.dataset.slot, 10);
    btn.disabled = true;
    try {
      await api(`/api/link/watch/slot/${slotIdx}/action`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      });
      await pollWatchStatus();
    } catch (err) {
      alert('Action failed: ' + err.message);
      btn.disabled = false;
    }
  });

  // ─── UPI payment polling ───
  function startUpiPolling(jobId) {
    if (state.upiPolling.has(jobId)) return;
    const job = state.jobs.get(jobId);
    if (!job || !job.checkout_session_id || !job.publishable_key) {
      alert('Không có Stripe session ID để poll. Hãy thử lại lấy link.');
      return;
    }
    const intervalId = setInterval(async () => {
      try {
        const data = await api('/api/stripe/poll-paid', {
          method: 'POST',
          body: JSON.stringify({
            session_id: job.checkout_session_id,
            publishable_key: job.publishable_key,
          }),
        });
        if (data.paid) {
          stopUpiPolling(jobId);
          state.upiPaid.add(jobId);
          renderJobs();
        }
      } catch (err) {
        console.error('[upi-poll] error:', err.message);
      }
    }, 5000);
    state.upiPolling.set(jobId, intervalId);
    renderJobs();
  }

  function stopUpiPolling(jobId) {
    const intervalId = state.upiPolling.get(jobId);
    if (intervalId != null) clearInterval(intervalId);
    state.upiPolling.delete(jobId);
    renderJobs();
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
        if (job && job.payment_link) showQrModal(job.payment_link, job.email);
      } else if (action === 'show-shot') {
        event.preventDefault();
        const url = actionBtn.dataset.url;
        if (url) showShotModal(url);
      } else if (action === 'copy-shot') {
        event.preventDefault();
        const url = actionBtn.dataset.url;
        if (!url) return;
        fetch(url)
          .then((r) => r.blob())
          .then((blob) => {
            const item = new ClipboardItem({ [blob.type || 'image/png']: blob });
            return navigator.clipboard.write([item]);
          })
          .then(() => {
            const orig = actionBtn.textContent;
            actionBtn.textContent = '✅';
            setTimeout(() => { actionBtn.textContent = orig; }, 1500);
          })
          .catch((err) => alert('Không copy được ảnh: ' + err.message));
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
      } else if (action === 'upi-start-poll') {
        startUpiPolling(id);
      } else if (action === 'upi-stop-poll') {
        stopUpiPolling(id);
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

  dom.btnClearAll.addEventListener('click', () => {
    if (!confirm('Cancel ALL running jobs and remove the entire list?')) return;
    api('/api/link/jobs/clear-all', { method: 'POST' }).catch((err) => alert(err.message));
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
  const qrModalEmail = document.getElementById('qr-modal-email');
  const qrModalClose = document.getElementById('qr-modal-close');
  const qrModalBackdrop = qrModal.querySelector('.qr-modal-backdrop');
  let _qrInstance = null;

  function showQrModal(url, email) {
    qrCanvasWrap.innerHTML = '';
    _qrInstance = new QRCode(qrCanvasWrap, {
      text: url,
      width: 440,
      height: 440,
      colorDark: '#000000',
      colorLight: '#ffffff',
      correctLevel: QRCode.CorrectLevel.M,
    });
    qrModalEmail.textContent = email || '';
    qrModalEmail.style.display = email ? '' : 'none';
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
