/* gpt_signup_hybrid — shared UI utilities + proxy strip */
(() => {
  'use strict';

  const LS_PROXY = 'gpt_link.proxy_url';

  // ── Icons ─────────────────────────────────────────────────────────
  const icons = Object.freeze({
    stop:     '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>',
    retry:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
    remove:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    copy:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
    link:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l2.83-2.83a5 5 0 0 0-7.07-7.07L11 4"/><path d="M14 11a5 5 0 0 0-7.07 0L4.1 13.83a5 5 0 1 0 7.07 7.07L13 19"/></svg>',
  });

  function icon(name) { return icons[name] || ''; }

  function copyText(text) {
    return navigator.clipboard.writeText(text).catch(() => {
      alert('Copy failed.');
      throw new Error('copy failed');
    });
  }

  function authEventSource(url) {
    return new EventSource(url);
  }

  // ── API helper ────────────────────────────────────────────────────
  function api(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    return fetch(path, { ...opts, headers }).then((r) => {
      if (!r.ok) return r.text().then((t) => { throw new Error(`HTTP ${r.status}: ${t}`); });
      return r.json();
    });
  }

  // ── Proxy strip ───────────────────────────────────────────────────
  const proxyInput        = document.getElementById('proxy-input');
  const btnProxyTest      = document.getElementById('btn-proxy-test');
  const btnProxySave      = document.getElementById('btn-proxy-save');
  const proxyStatus       = document.getElementById('proxy-status');
  const proxyStatusLabel  = document.querySelector('#proxy-status .proxy-status-label');
  const proxyStatusDetail = document.getElementById('proxy-status-detail');

  let proxyEditing = false;

  function maskProxy(url) {
    if (!url) return '';
    const m = url.match(/^([a-z][a-z0-9+.-]*):\/\/([^@/]+)@(.+)$/i);
    return m ? `${m[1]}://***@${m[3]}` : url;
  }

  function setProxyStatus(kind, label, detail) {
    const map = {
      idle:    'proxy-status proxy-status-idle',
      testing: 'proxy-status proxy-status-testing',
      ok:      'proxy-status proxy-status-ok',
      fail:    'proxy-status proxy-status-fail',
      direct:  'proxy-status proxy-status-direct',
    };
    proxyStatus.className = map[kind] || map.idle;
    proxyStatusLabel.textContent = label;
    proxyStatusDetail.textContent = detail || '';
  }

  function applyProxyFromServer(url) {
    if (!proxyEditing) proxyInput.value = url || '';
    if (url) setProxyStatus('idle', 'proxy set', maskProxy(url));
    else     setProxyStatus('direct', 'direct', 'no proxy configured');
  }

  async function saveProxy() {
    const val = proxyInput.value.trim();
    btnProxySave.disabled = true;
    try {
      const r = await api('/api/link/config', {
        method: 'POST',
        body: JSON.stringify({ proxy: val }),
      });
      if (val) localStorage.setItem(LS_PROXY, val);
      else     localStorage.removeItem(LS_PROXY);
      proxyEditing = false;
      applyProxyFromServer(r.proxy);
      await testProxy({ silent: false });
    } catch (err) {
      setProxyStatus('fail', 'save fail', err.message);
    } finally {
      btnProxySave.disabled = false;
    }
  }

  async function testProxy(opts = {}) {
    const val = proxyInput.value.trim();
    setProxyStatus('testing', 'testing…', maskProxy(val) || 'direct');
    btnProxyTest.disabled = true;
    try {
      const r = await api('/api/proxy/test', {
        method: 'POST',
        body: JSON.stringify({ proxy: val || null }),
      });
      const ipPart = r.public_ip ? `IP ${r.public_ip}` : '';
      const failedTargets = (r.results || []).filter((x) => !x.ok).map((x) => x.target);
      if (r.ok) {
        setProxyStatus(val ? 'ok' : 'direct', val ? 'proxy ok' : 'direct ok',
          [ipPart, maskProxy(val)].filter(Boolean).join(' · ') || 'reachable');
      } else if (r.public_ip && !r.ms_reachable) {
        setProxyStatus('fail', 'MS blocked', `${ipPart} · Microsoft blocked`);
        if (!opts.silent) alert('Proxy alive but Microsoft is blocked. OTP polling will fail.');
      } else {
        const errPart = r.error || `fail: ${failedTargets.join(', ') || 'unknown'}`;
        setProxyStatus('fail', val ? 'proxy fail' : 'direct fail', errPart);
      }
    } catch (err) {
      setProxyStatus('fail', 'test error', err.message);
    } finally {
      btnProxyTest.disabled = false;
    }
  }

  btnProxyTest.addEventListener('click', () => testProxy({ silent: false }));
  btnProxySave.addEventListener('click', () => saveProxy());
  proxyInput.addEventListener('input', () => { proxyEditing = true; });
  proxyInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); saveProxy(); }
  });

  // Restore proxy from localStorage on load
  const _savedProxy = localStorage.getItem(LS_PROXY);
  if (_savedProxy) {
    proxyInput.value = _savedProxy;
    setProxyStatus('idle', 'proxy set', maskProxy(_savedProxy));
  }

  // Load current proxy from server config
  api('/api/link/config').then((cfg) => {
    if (!proxyEditing) applyProxyFromServer(cfg.proxy);
  }).catch(() => {});

  // ── Export window.GptUi ───────────────────────────────────────────
  window.GptUi = Object.assign(window.GptUi || {}, {
    icon,
    copyText,
    authEventSource,
    applyProxyFromServer,
  });
})();
