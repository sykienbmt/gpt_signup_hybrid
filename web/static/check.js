/* gpt_signup_hybrid — Check Account tab logic */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const dom = {
    input: $('check-input'),
    concurrent: $('check-concurrent'),
    btnRun: $('check-btn-run'),
    btnClearInput: $('check-btn-clear-input'),
    btnClearResults: $('check-btn-clear-results'),
    btnClearAll: $('check-btn-clear-all'),
    btnCopyPlus: $('check-btn-copy-plus'),
    btnCopyAll: $('check-btn-copy-all'),
    inputCount: $('check-input-count'),
    summary: $('check-summary'),
    resultList: $('check-result-list'),
  };

  let lastResults = [];

  function escHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function updateInputCount() {
    const count = dom.input.value.split('\n').filter((l) => {
      const t = l.trim();
      return t && !t.startsWith('#');
    }).length;
    dom.inputCount.textContent = `${count} account${count === 1 ? '' : 's'}`;
  }

  function statusClass(status) {
    if (status === 'plus' || status === 'pro' || status === 'team' || status === 'enterprise') return 'status-success';
    if (status === 'free') return 'status-queued';
    if (status === 'expired') return 'status-cancelled';
    return 'status-error';
  }

  function renderResults(results) {
    lastResults = results || [];
    if (!results || results.length === 0) {
      dom.resultList.innerHTML = '<div class="empty">No results yet.</div>';
      dom.summary.textContent = '0 total';
      return;
    }

    const stats = { plus: 0, pro: 0, team: 0, enterprise: 0, free: 0, expired: 0, error: 0, unknown: 0 };
    const html = results.map((r, idx) => {
      stats[r.status] = (stats[r.status] || 0) + 1;
      const cls = statusClass(r.status);
      const err = r.error ? `<div class="job-meta" title="${escHtml(r.error)}">${escHtml(r.error)}</div>` : '';
      const badge = r.is_plus ? '⭐ ' : '';
      return `
        <div class="job">
          <div class="job-index">${idx + 1}</div>
          <div class="job-status ${cls}">${escHtml(r.status)}</div>
          <div class="job-main">
            <div class="job-email" title="${escHtml(r.email)}">${escHtml(r.email)}</div>
            <div class="job-meta">${badge}${escHtml(r.plan || '—')}</div>
            ${err}
          </div>
        </div>
      `;
    }).join('');
    dom.resultList.innerHTML = html;

    const paid = (stats.plus + stats.pro + stats.team + stats.enterprise);
    dom.summary.textContent = [
      `${results.length} total`,
      paid ? `${paid} paid (${stats.plus} plus)` : '',
      stats.free ? `${stats.free} free` : '',
      stats.expired ? `${stats.expired} expired` : '',
      stats.error ? `${stats.error} error` : '',
    ].filter(Boolean).join(' · ');
  }

  function api(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    return fetch(path, { ...opts, headers }).then((r) => {
      if (!r.ok) return r.text().then((t) => { throw new Error(`HTTP ${r.status}: ${t}`); });
      return r.json();
    });
  }

  dom.btnRun.addEventListener('click', async () => {
    const lines = dom.input.value.trim();
    if (!lines) { alert('Paste account lines first.'); return; }
    dom.btnRun.disabled = true;
    dom.summary.textContent = 'Checking…';
    try {
      const res = await api('/api/check/run', {
        method: 'POST',
        body: JSON.stringify({
          lines,
          max_concurrent: parseInt(dom.concurrent.value, 10) || 5,
        }),
      });
      renderResults(res.results || []);
    } catch (err) {
      alert('Check failed: ' + err.message);
      dom.summary.textContent = 'failed';
    } finally {
      dom.btnRun.disabled = false;
    }
  });

  dom.btnClearInput.addEventListener('click', () => {
    dom.input.value = '';
    updateInputCount();
  });

  dom.btnClearResults.addEventListener('click', () => {
    renderResults([]);
  });

  dom.btnClearAll.addEventListener('click', () => {
    dom.input.value = '';
    updateInputCount();
    renderResults([]);
  });

  dom.btnCopyAll.addEventListener('click', () => {
    if (!lastResults.length) return;
    const text = lastResults.map((r) => `${r.email}\t${r.status}\t${r.plan}${r.error ? '\t' + r.error : ''}`).join('\n');
    window.GptUi.copyText(text);
  });

  dom.btnCopyPlus.addEventListener('click', () => {
    if (!lastResults.length) return;
    const text = lastResults.filter((r) => r.is_plus).map((r) => r.email).join('\n');
    window.GptUi.copyText(text || '(no plus accounts)');
  });

  dom.input.addEventListener('input', updateInputCount);
  updateInputCount();
})();
