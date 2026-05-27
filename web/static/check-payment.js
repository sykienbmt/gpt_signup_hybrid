/* gpt_signup_hybrid — Check Payment tab
 * Flow: session JSON / accessToken → OpenAI checkout → Stripe init → is_free?
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
    logCard:    $('cp-log-card'),
    logLabel:   $('cp-log-label'),
    logBody:    $('cp-log-body'),
    logClose:   $('cp-log-close'),
  };

  // ── Stored results (with logs) ────────────────────────────────────────
  let _results = [];
  let _selectedIdx = -1;

  function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  // ── Parse one line of input ───────────────────────────────────────────
  function parseSessionLine(line) {
    line = line.trim();
    if (!line) return null;
    if (line.startsWith('{')) {
      try {
        const obj = JSON.parse(line);
        const token = obj.accessToken || obj.access_token || obj.token;
        const email = obj.email || (obj.user && obj.user.email) || '';
        if (token) return { token, email, raw: line };
      } catch { /* fall through */ }
    }
    // Raw accessToken (JWT starts with "eyJ")
    if (line.startsWith('eyJ') || line.length > 20) {
      return { token: line, email: '', raw: line };
    }
    return null;
  }

  function updateCount() {
    const lines = dom.input.value.split('\n').filter(l => l.trim());
    const n = lines.length;
    dom.count.textContent = `${n} session${n !== 1 ? 's' : ''}`;
  }
  dom.input.addEventListener('input', updateCount);

  function fmtAmount(amount, currency) {
    if (amount == null || amount < 0) return '—';
    return `${(amount / 100).toFixed(2)} ${(currency || '').toUpperCase()}`;
  }

  // ── Log panel ──────────────────────────────────────────────────────────
  function showLog(idx) {
    const r = _results[idx];
    if (!r) return;
    _selectedIdx = idx;

    // Highlight selected item
    dom.resultList.querySelectorAll('.cp-result-item').forEach((el, i) => {
      el.classList.toggle('job-selected', i === idx);
    });

    const label = r.email || `#${idx + 1}`;
    dom.logLabel.textContent = label;
    dom.logBody.textContent = (r.logs || []).join('\n') || '(no log)';
    dom.logCard.style.display = '';
    dom.logBody.scrollTop = dom.logBody.scrollHeight;
  }

  dom.logClose.addEventListener('click', () => {
    dom.logCard.style.display = 'none';
    _selectedIdx = -1;
    dom.resultList.querySelectorAll('.cp-result-item').forEach(el => el.classList.remove('job-selected'));
  });

  // ── Clear ──────────────────────────────────────────────────────────────
  dom.btnClear.addEventListener('click', () => {
    dom.input.value = '';
    updateCount();
    dom.resultList.innerHTML = '';
    dom.summary.textContent = '';
    dom.logCard.style.display = 'none';
    _results = [];
    _selectedIdx = -1;
  });

  // ── Run ───────────────────────────────────────────────────────────────
  dom.btnRun.addEventListener('click', async () => {
    const lines = dom.input.value.split('\n').map(l => l.trim()).filter(Boolean);
    const sessions = lines.map(parseSessionLine).filter(Boolean);
    if (!sessions.length) { alert('Paste session JSON or accessToken strings first.'); return; }

    dom.btnRun.disabled = true;
    dom.btnRun.textContent = '⏳ Checking…';
    dom.resultList.innerHTML = '<div class="empty">Checking…</div>';
    dom.logCard.style.display = 'none';
    _results = [];
    _selectedIdx = -1;

    const region = dom.region.value || 'VN';
    const apiToken = window.GptUi?.apiToken || '';

    try {
      const res = await fetch('/api/check-payment', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiToken ? { 'X-API-Token': apiToken } : {}),
        },
        body: JSON.stringify({
          sessions: sessions.map(s => ({ token: s.token, email: s.email })),
          region,
        }),
      });
      const data = await res.json();
      _results = data.results || [];
      renderResults(_results, sessions);
    } catch (err) {
      dom.resultList.innerHTML = `<div class="empty" style="color:var(--red)">Error: ${escHtml(err.message)}</div>`;
    } finally {
      dom.btnRun.disabled = false;
      dom.btnRun.textContent = '▶ Check';
    }
  });

  // ── Render ────────────────────────────────────────────────────────────
  function renderResults(results, sessions) {
    if (!results.length) {
      dom.resultList.innerHTML = '<div class="empty">No results.</div>';
      return;
    }
    let free = 0, paid = 0, errors = 0;

    const html = results.map((r, i) => {
      const sessionEmail = (sessions[i] || {}).email || '';
      const hasLogs = (r.logs || []).length > 0;
      const logBtn = `<button class="icon-btn cp-log-btn" data-idx="${i}" title="Show log">📋</button>`;

      // Partial result: has payment URL but Stripe data unavailable
      if (r.error && r.payment_url) {
        return `<div class="job cp-result-item" data-idx="${i}" style="border-left:3px solid var(--yellow,#f5a623);cursor:pointer">
          <div class="job-index">${i + 1}</div>
          <div class="job-status status-running">?</div>
          <div class="job-main">
            <div class="job-email">${escHtml(r.email || sessionEmail || '—')}</div>
            <div class="job-meta" style="color:var(--yellow,#f5a623)">⚠ ${escHtml(r.error)}</div>
            <div class="job-meta muted" style="font-size:10px;word-break:break-all">${escHtml(r.payment_url)}</div>
          </div>
          <div class="job-actions">
            <button class="icon-btn" onclick="event.stopPropagation();window.GptUi.copyText(${JSON.stringify(r.payment_url)})" title="Copy payment link">🔗</button>
            ${hasLogs ? logBtn : ''}
          </div>
        </div>`;
      }

      if (r.error) {
        errors++;
        return `<div class="job cp-result-item" data-idx="${i}" style="border-left:3px solid var(--red);cursor:pointer">
          <div class="job-index">${i + 1}</div>
          <div class="job-status status-error">error</div>
          <div class="job-main">
            <div class="job-email">${escHtml(r.email || sessionEmail || '—')}</div>
            <div class="job-meta" style="color:var(--red)">${escHtml(r.error)}</div>
          </div>
          <div class="job-actions">${hasLogs ? logBtn : ''}</div>
        </div>`;
      }

      const isFree = r.is_free;
      isFree ? free++ : paid++;
      const badge = isFree
        ? `<span style="color:var(--green);font-weight:600">✅ FREE${r.trial_days ? ` (${r.trial_days}d trial)` : ''}</span>`
        : `<span style="color:var(--red);font-weight:600">💳 PAID — ${escHtml(fmtAmount(r.amount_due, r.currency))}</span>`;

      const linkBtn = r.payment_url
        ? `<button class="icon-btn" onclick="event.stopPropagation();window.GptUi.copyText(${JSON.stringify(r.payment_url)})" title="Copy payment link">🔗</button>`
        : '';

      return `<div class="job cp-result-item" data-idx="${i}" style="border-left:3px solid ${isFree ? 'var(--green)' : 'var(--red)'};cursor:pointer">
        <div class="job-index">${i + 1}</div>
        <div class="job-status ${isFree ? 'status-success' : 'status-error'}">${isFree ? 'free' : 'paid'}</div>
        <div class="job-main">
          <div class="job-email">${escHtml(r.email || sessionEmail || '—')}</div>
          <div class="job-meta">${badge}${r.product ? ' · ' + escHtml(r.product) : ''}</div>
          ${r.payment_url ? `<div class="job-meta muted" style="font-size:10px;word-break:break-all">${escHtml(r.payment_url)}</div>` : ''}
        </div>
        <div class="job-actions">${linkBtn}${hasLogs ? logBtn : ''}</div>
      </div>`;
    }).join('');

    dom.resultList.innerHTML = html;
    dom.summary.textContent = `${results.length} total · ${free} free · ${paid} paid${errors ? ` · ${errors} errors` : ''}`;

    // ── Event delegation for log + row click ──────────────────────────
    dom.resultList.addEventListener('click', (e) => {
      const logBtn = e.target.closest('.cp-log-btn');
      const row = e.target.closest('.cp-result-item');
      if (logBtn) {
        e.stopPropagation();
        showLog(parseInt(logBtn.dataset.idx, 10));
      } else if (row) {
        showLog(parseInt(row.dataset.idx, 10));
      }
    }, { once: false });
  }

  updateCount();
})();
