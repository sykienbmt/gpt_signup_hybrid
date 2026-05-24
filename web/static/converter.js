/* ChatGPT Session Converter — adapted from local index.html */
(() => {
  'use strict';

  const OUTPUT_LABELS = {
    sub2api: 'sub2api',
    cpa: 'CPA',
    cockpit: 'Cockpit',
    '9router': '9router',
    axonhub: 'AxonHub',
    codexmanager: 'Codex-Manager',
  };

  const AXONHUB_PLACEHOLDER_REFRESH_TOKEN = '__missing_refresh_token__';

  const state = {
    format: 'sub2api',
    sessions: [],
    converted: [],
    skipped: [],
    outputText: '',
  };

  const el = {
    input: document.getElementById('conv-input'),
    output: document.getElementById('conv-output'),
    fileInput: document.getElementById('conv-file-input'),
    pickFiles: document.getElementById('conv-pick-files'),
    loadExample: document.getElementById('conv-load-example'),
    clear: document.getElementById('conv-clear'),
    copy: document.getElementById('conv-copy'),
    download: document.getElementById('conv-download'),
    inputStatus: document.getElementById('conv-input-status'),
    outputStatus: document.getElementById('conv-output-status'),
    outputSubtitle: document.getElementById('conv-output-subtitle'),
    statCount: document.getElementById('conv-stat-count'),
    statFormat: document.getElementById('conv-stat-format'),
    statErrors: document.getElementById('conv-stat-errors'),
    accountBody: document.getElementById('conv-account-body'),
    issues: document.getElementById('conv-issues'),
    fmtBtns: Array.from(document.querySelectorAll('.conv-fmt-btn')),
  };

  const exampleSession = {
    user: { id: 'user-example', email: 'mark@example.com' },
    expires: '2026-08-06T14:29:36.155Z',
    account: { id: '00000000-0000-4000-9000-000000000000', planType: 'plus' },
    accessToken: 'paste-real-access-token-here',
    sessionToken: 'paste-real-session-token-here',
    authProvider: 'openai',
  };

  // ── Utilities ──

  function isObj(v) { return Boolean(v) && typeof v === 'object' && !Array.isArray(v); }

  function first(...values) {
    for (const v of values) {
      if (typeof v === 'string' && v.trim() !== '') return v.trim();
    }
    return undefined;
  }

  function escHtml(v) {
    return String(v ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function decodeB64Url(v) {
    const n = v.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(v.length / 4) * 4, '=');
    return new TextDecoder().decode(Uint8Array.from(atob(n), (c) => c.charCodeAt(0)));
  }

  function b64UrlFromBytes(bytes) {
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000)
      bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function encodeB64UrlJson(v) {
    return b64UrlFromBytes(new TextEncoder().encode(JSON.stringify(v)));
  }

  function parseJwtPayload(token) {
    if (typeof token !== 'string' || !token.trim()) return undefined;
    const parts = token.split('.');
    if (parts.length < 2) return undefined;
    try { return JSON.parse(decodeB64Url(parts[1])); } catch { return undefined; }
  }

  function getAuthSection(payload) {
    if (!isObj(payload)) return {};
    const a = payload['https://api.openai.com/auth'];
    return isObj(a) ? a : {};
  }

  function getProfileSection(payload) {
    if (!isObj(payload)) return {};
    const p = payload['https://api.openai.com/profile'];
    return isObj(p) ? p : {};
  }

  function normalizeTs(v) {
    if (v instanceof Date && !isNaN(v)) return v.toISOString();
    if (typeof v === 'number' && isFinite(v)) {
      const d = new Date(v > 1e11 ? v : v * 1000);
      return isNaN(d) ? undefined : d.toISOString();
    }
    if (typeof v !== 'string' || !v.trim()) return undefined;
    const d = new Date(v);
    return isNaN(d) ? undefined : d.toISOString();
  }

  function tsFromUnix(v) {
    const n = Number(v);
    if (!isFinite(n)) return undefined;
    const d = new Date(n * 1000);
    return isNaN(d) ? undefined : d.toISOString();
  }

  function unixFromExp(v) {
    const n = Number(v);
    return (isFinite(n) && n > 0) ? Math.trunc(n) : undefined;
  }

  function epochFromValue(v) {
    if (v === undefined || v === null || v === '') return 0;
    const n = Number(v);
    if (isFinite(n)) return Math.trunc(n > 1e11 ? n / 1000 : n);
    const p = Date.parse(String(v));
    return isFinite(p) ? Math.trunc(p / 1000) : 0;
  }

  function buildSyntheticIdToken(email, accountId, planType, userId, expiresAt) {
    if (!accountId) return undefined;
    const now = Math.trunc(Date.now() / 1000);
    const auth = { chatgpt_account_id: accountId };
    const expires = epochFromValue(expiresAt) || now + 90 * 86400;
    if (planType) auth.chatgpt_plan_type = planType;
    if (userId) { auth.chatgpt_user_id = userId; auth.user_id = userId; }
    const payload = { iat: now, exp: expires, 'https://api.openai.com/auth': auth };
    if (email) payload.email = email;
    return `${encodeB64UrlJson({ alg: 'none', typ: 'JWT', cpa_synthetic: true })}.${encodeB64UrlJson(payload)}.synthetic`;
  }

  function getExpiresIn(expiresAt, now = new Date()) {
    if (!expiresAt) return undefined;
    const ms = new Date(expiresAt).getTime();
    return isNaN(ms) ? undefined : Math.max(0, Math.floor((ms - now.getTime()) / 1000));
  }

  function getAxonHubLastRefresh(expiresAt, now = new Date()) {
    const ms = expiresAt ? new Date(expiresAt).getTime() : NaN;
    return isNaN(ms) ? normalizeTs(now) : new Date(ms - 3600000).toISOString();
  }

  function stripEmpty(v) {
    if (Array.isArray(v)) return v.map(stripEmpty).filter((i) => i !== undefined);
    if (isObj(v)) {
      const entries = Object.entries(v)
        .map(([k, i]) => [k, stripEmpty(i)])
        .filter(([, i]) => i !== undefined);
      return entries.length ? Object.fromEntries(entries) : undefined;
    }
    return (v === undefined || v === null || v === '') ? undefined : v;
  }

  function toEmailKey(email) {
    if (typeof email !== 'string') return undefined;
    return email.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  }

  function sanitizeFileToken(v, fallback = 'chatgpt-session') {
    const base = first(v, fallback) || fallback;
    return base.replace(/\.[^.]+$/u, '').replace(/[\\/:*?"<>|]+/g, '-')
      .replace(/\s+/g, '-').replace(/-+/g, '-').replace(/^-+|-+$/g, '')
      .toLowerCase().slice(0, 80) || fallback;
  }

  function getTimestampToken(d = new Date()) {
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}`;
  }

  function formatDate(v) {
    if (!v) return '';
    const d = new Date(v);
    if (isNaN(d)) return v;
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  // ── Session parsing ──

  function collectSessions(value, sourceName = 'pasted-json') {
    const found = [];
    const visited = new WeakSet();

    function visit(item, path) {
      if (!isObj(item) && !Array.isArray(item)) return;
      if (isObj(item)) {
        if (visited.has(item)) return;
        visited.add(item);
        const token = first(
          item.accessToken, item.access_token,
          item.tokens?.accessToken, item.tokens?.access_token,
          item.token?.accessToken, item.token?.access_token,
          item.credentials?.accessToken, item.credentials?.access_token,
        );
        const hasIdentity = isObj(item.user) || first(
          item.email, item.name, item.label, item.meta?.label,
          item.tokens?.accountId, item.tokens?.chatgptAccountId,
          item.providerSpecificData?.chatgptAccountId, item.id,
        );
        if (token && hasIdentity) { found.push({ value: item, sourceName, path }); return; }
        for (const [k, child] of Object.entries(item)) {
          if (k === 'accessToken' || k === 'access_token' || k === 'sessionToken') continue;
          visit(child, `${path}.${k}`);
        }
        return;
      }
      item.forEach((child, i) => visit(child, `${path}[${i}]`));
    }

    visit(value, '$');
    return found;
  }

  function parseInput(text) {
    if (typeof text !== 'string' || !text.trim()) return [];
    let parsed;
    try { parsed = JSON.parse(text); } catch (e) { throw new Error(`JSON parse error: ${e.message}`); }
    return collectSessions(parsed);
  }

  function convertSession(record, opts = {}) {
    if (!isObj(record)) throw new Error('Not a JSON object');
    const accessToken = first(
      record.accessToken, record.access_token,
      record.tokens?.accessToken, record.tokens?.access_token,
      record.token?.accessToken, record.token?.access_token,
      record.credentials?.accessToken, record.credentials?.access_token,
    );
    if (!accessToken) throw new Error('Missing accessToken');

    const sessionToken = first(record.sessionToken, record.session_token, record.tokens?.sessionToken, record.tokens?.session_token, record.credentials?.session_token);
    const refreshToken = first(record.refreshToken, record.refresh_token, record.tokens?.refreshToken, record.tokens?.refresh_token, record.credentials?.refresh_token);
    const inputIdToken = first(record.idToken, record.id_token, record.tokens?.idToken, record.tokens?.id_token, record.credentials?.id_token);

    const payload = parseJwtPayload(accessToken);
    const idPayload = parseJwtPayload(inputIdToken);
    const auth = getAuthSection(payload);
    const idAuth = getAuthSection(idPayload);
    const profile = getProfileSection(payload);

    const accessTokenExpiresAt = unixFromExp(payload?.exp);
    const expiresAt = first(
      payload ? tsFromUnix(payload.exp) : undefined,
      normalizeTs(record.expires), normalizeTs(record.expiresAt),
      normalizeTs(record.expired), normalizeTs(record.expires_at),
    );
    const email = first(
      record.user?.email, record.email, record.meta?.label, record.label,
      record.credentials?.email, record.providerSpecificData?.email,
      profile.email, idPayload?.email, payload?.email,
    );
    const accountId = first(
      record.account?.id, record.account_id, record.tokens?.accountId, record.tokens?.account_id,
      record.chatgptAccountId, record.chatgpt_account_id,
      record.meta?.chatgptAccountId, record.tokens?.chatgptAccountId,
      record.providerSpecificData?.chatgptAccountId,
      record.credentials?.chatgpt_account_id,
      auth.chatgpt_account_id, idAuth.chatgpt_account_id,
      record.provider === 'codex' ? record.id : undefined,
    );
    const chatgptAccountId = first(
      record.chatgptAccountId, record.chatgpt_account_id,
      record.meta?.chatgptAccountId, record.tokens?.chatgptAccountId,
      record.providerSpecificData?.chatgptAccountId,
      record.credentials?.chatgpt_account_id,
      auth.chatgpt_account_id, idAuth.chatgpt_account_id,
    );
    const workspaceId = first(
      record.account?.workspaceId, record.account?.workspace_id,
      record.workspaceId, record.workspace_id,
      record.meta?.workspaceId, record.providerSpecificData?.workspaceId,
      record.credentials?.workspace_id, payload?.workspace_id, idPayload?.workspace_id,
    );
    const userId = first(
      record.user?.id, record.user_id, record.chatgptUserId,
      record.providerSpecificData?.chatgptUserId,
      auth.chatgpt_user_id, auth.user_id,
      idAuth.chatgpt_user_id, idAuth.user_id,
    );
    const planType = first(
      record.account?.planType, record.account?.plan_type,
      record.planType, record.plan_type,
      record.providerSpecificData?.chatgptPlanType,
      record.credentials?.plan_type,
      auth.chatgpt_plan_type, idAuth.chatgpt_plan_type,
    );

    const now = opts.now || new Date();
    const exportedAt = normalizeTs(now);
    const expiresIn = getExpiresIn(expiresAt, now);
    const sourceName = first(opts.sourceName, 'pasted-json');
    const sourceType = record.provider === 'codex' && record.authType === 'oauth' ? '9router' : 'chatgpt_web_session';
    const name = first(email, sourceName, 'ChatGPT Account');
    const syntheticIdToken = !inputIdToken ? buildSyntheticIdToken(email, accountId, planType, userId, expiresAt) : undefined;
    const idToken = first(inputIdToken, syntheticIdToken);

    const cpa = Object.fromEntries(Object.entries({
      type: 'codex', account_id: accountId, chatgpt_account_id: accountId,
      email, name, plan_type: planType, chatgpt_plan_type: planType,
      id_token: idToken, id_token_synthetic: Boolean(syntheticIdToken) || undefined,
      access_token: accessToken, refresh_token: refreshToken || '',
      session_token: sessionToken, last_refresh: exportedAt,
      expired: expiresAt, disabled: Boolean(record.disabled) || undefined,
    }).filter(([, v]) => v !== undefined && v !== null));

    const cockpit = {
      type: 'codex', id_token: idToken, access_token: accessToken,
      refresh_token: refreshToken || '', account_id: accountId,
      last_refresh: exportedAt, email, expired: expiresAt,
      account_note: first(record.account_note, record.accountInfo, record.account_info, record.note, record.remark),
    };

    const sub2apiAccount = stripEmpty({
      name: first(name, email, sourceName, 'ChatGPT Account'),
      platform: 'openai', type: 'oauth',
      expires_at: accessTokenExpiresAt,
      auto_pause_on_expired: true, concurrency: 10, priority: 1,
      credentials: { access_token: accessToken, chatgpt_account_id: accountId, chatgpt_user_id: userId, email, expires_at: expiresAt, expires_in: expiresIn, plan_type: planType },
      extra: { email, email_key: toEmailKey(email), name, auth_provider: first(record.authProvider, record.auth_provider), source: sourceType, last_refresh: exportedAt },
    });

    const priority = isFinite(Number(record.priority)) ? Number(record.priority) : 9;
    const isActive = typeof record.isActive === 'boolean' ? record.isActive : !Boolean(record.disabled);
    const createdAt = normalizeTs(record.createdAt) || exportedAt;
    const updatedAt = normalizeTs(record.updatedAt) || exportedAt;
    const nineRouter = stripEmpty({
      accessToken, refreshToken, expiresAt,
      testStatus: first(record.testStatus, record.test_status, 'active'),
      expiresIn,
      providerSpecificData: { chatgptAccountId: accountId, chatgptPlanType: planType },
      id: accountId, provider: 'codex', authType: 'oauth',
      name, email, priority, isActive, createdAt, updatedAt,
    });

    const axonHub = stripEmpty({
      auth_mode: 'chatgpt',
      last_refresh: getAxonHubLastRefresh(expiresAt, now),
      tokens: { access_token: accessToken, refresh_token: refreshToken || AXONHUB_PLACEHOLDER_REFRESH_TOKEN, id_token: idToken },
      axonhub_refresh_token_placeholder: refreshToken ? undefined : true,
      axonhub_note: refreshToken ? undefined : 'refresh_token is a placeholder; access_token works only until it expires.',
    });

    const codexManager = {
      tokens: {
        access_token: accessToken, refresh_token: refreshToken || '', id_token: inputIdToken || '',
        ...Object.fromEntries(Object.entries({ account_id: accountId, chatgpt_account_id: chatgptAccountId }).filter(([, v]) => v)),
      },
      meta: Object.fromEntries(Object.entries({
        label: first(name, email, sourceName, 'ChatGPT Account'),
        workspace_id: workspaceId, chatgpt_account_id: chatgptAccountId,
        note: 'Imported from ChatGPT session',
      }).filter(([, v]) => v)),
    };

    return { sourceName, email, name, expiresAt, cpa, cockpit, nineRouter, axonHub, codexManager, sub2apiAccount };
  }

  // ── Output ──

  function buildOutput() {
    const now = new Date();
    const c = state.converted;
    if (state.format === 'sub2api') return { exported_at: normalizeTs(now), proxies: [], accounts: c.map((i) => i.sub2apiAccount) };
    if (state.format === 'cpa')          return c.length === 1 ? c[0].cpa : c.map((i) => i.cpa);
    if (state.format === 'cockpit')      return c.length === 1 ? c[0].cockpit : c.map((i) => i.cockpit);
    if (state.format === '9router')      return c.length === 1 ? c[0].nineRouter : c.map((i) => i.nineRouter);
    if (state.format === 'axonhub')      return c.length === 1 ? c[0].axonHub : c.map((i) => i.axonHub);
    if (state.format === 'codexmanager') return c.length === 1 ? c[0].codexManager : c.map((i) => i.codexManager);
    return { exported_at: normalizeTs(now), proxies: [], accounts: c.map((i) => i.sub2apiAccount) };
  }

  function setStatus(domEl, text, tone = '') {
    domEl.textContent = text;
    domEl.className = 'conv-status' + (tone ? ` ${tone}` : '');
  }

  function renderAccounts() {
    if (!state.converted.length) {
      el.accountBody.innerHTML = '<tr><td colspan="3" class="conv-empty">No accounts yet.</td></tr>';
      return;
    }
    el.accountBody.innerHTML = state.converted.map((item) => `
      <tr>
        <td title="${escHtml(item.name)}">${escHtml(item.name || '-')}</td>
        <td title="${escHtml(item.email)}">${escHtml(item.email || '-')}</td>
        <td title="${escHtml(item.expiresAt)}">${escHtml(formatDate(item.expiresAt) || '-')}</td>
      </tr>`).join('');
  }

  function renderIssues() {
    if (!state.skipped.length) { el.issues.classList.add('hidden'); el.issues.innerHTML = ''; return; }
    el.issues.classList.remove('hidden');
    el.issues.innerHTML = state.skipped.map((i) => `<div>${escHtml(i.sourceName || 'input')} ${escHtml(i.path || '')}: ${escHtml(i.reason)}</div>`).join('');
  }

  function updateOutput() {
    const hasConverted = state.converted.length > 0;
    const outputText = hasConverted ? JSON.stringify(buildOutput(), null, 2) : '';
    state.outputText = outputText;
    el.output.value = outputText;
    el.copy.disabled = !outputText;
    el.download.disabled = !outputText;
    el.statCount.textContent = String(state.converted.length);
    el.statErrors.textContent = String(state.skipped.length);
    el.statFormat.textContent = OUTPUT_LABELS[state.format];
    el.outputSubtitle.textContent = `Format: ${OUTPUT_LABELS[state.format]}`;
    renderAccounts();
    renderIssues();
    if (outputText) setStatus(el.outputStatus, `Generated ${state.converted.length} account(s).`, 'ok');
    else setStatus(el.outputStatus, 'No output yet.', state.skipped.length ? 'error' : '');
  }

  function doConvert(text) {
    if (!text.trim()) {
      state.converted = []; state.skipped = []; state.sessions = [];
      updateOutput();
      setStatus(el.inputStatus, 'Waiting for input.');
      return;
    }
    try {
      const sources = parseInput(text);
      const converted = [], skipped = [];
      const now = new Date();
      sources.forEach((item, i) => {
        try { converted.push(convertSession(item.value, { now, sourceName: item.sourceName, sourcePath: item.path || `$[${i}]` })); }
        catch (e) { skipped.push({ sourceName: item.sourceName, path: item.path, reason: e.message || 'Cannot convert' }); }
      });
      if (!sources.length) skipped.push({ sourceName: 'pasted-json', path: '$', reason: 'No session object with accessToken + user/email found' });
      state.converted = converted; state.skipped = skipped; state.sessions = sources;
      updateOutput();
      setStatus(el.inputStatus, converted.length ? `Parsed: ${converted.length} account(s), skipped ${skipped.length}.` : 'No convertible accounts.', converted.length ? 'ok' : 'error');
    } catch (e) {
      state.converted = []; state.skipped = [{ sourceName: 'pasted-json', path: '$', reason: e.message }];
      state.outputText = '';
      updateOutput();
      setStatus(el.inputStatus, e.message, 'error');
    }
  }

  // ── File reading ──

  async function readFiles(files) {
    const jsonFiles = Array.from(files).filter((f) => f.name.toLowerCase().endsWith('.json'));
    if (!jsonFiles.length) { setStatus(el.inputStatus, 'No JSON files selected.', 'error'); return; }
    const documents = [], skipped = [];
    for (const file of jsonFiles) {
      try {
        const text = await file.text();
        const parsed = JSON.parse(text);
        const found = collectSessions(parsed, file.webkitRelativePath || file.name);
        if (!found.length) skipped.push({ sourceName: file.name, path: '$', reason: 'No session found' });
        documents.push(...found);
      } catch (e) { skipped.push({ sourceName: file.name, path: '$', reason: e.message }); }
    }
    const now = new Date();
    const converted = [], convertSkipped = [...skipped];
    documents.forEach((item) => {
      try { converted.push(convertSession(item.value, { now, sourceName: item.sourceName, sourcePath: item.path })); }
      catch (e) { convertSkipped.push({ sourceName: item.sourceName, path: item.path, reason: e.message }); }
    });
    state.sessions = documents; state.converted = converted; state.skipped = convertSkipped;
    el.input.value = documents.length === 1 ? JSON.stringify(documents[0].value, null, 2) : JSON.stringify(documents.map((i) => i.value), null, 2);
    updateOutput();
    setStatus(el.inputStatus, `Read ${jsonFiles.length} file(s), ${converted.length} account(s), skipped ${convertSkipped.length}.`, converted.length ? 'ok' : 'error');
  }

  // ── Events ──

  el.fmtBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      state.format = btn.dataset.format;
      el.fmtBtns.forEach((b) => b.classList.toggle('active', b === btn));
      updateOutput();
    });
  });

  el.input.addEventListener('input', () => doConvert(el.input.value));

  el.pickFiles.addEventListener('click', () => el.fileInput.click());
  el.fileInput.addEventListener('change', (e) => { readFiles(e.target.files); e.target.value = ''; });

  el.clear.addEventListener('click', () => { el.input.value = ''; doConvert(''); });

  el.loadExample.addEventListener('click', () => {
    el.input.value = JSON.stringify(exampleSession, null, 2);
    doConvert(el.input.value);
  });

  el.copy.addEventListener('click', async () => {
    if (!state.outputText) return;
    try {
      await navigator.clipboard.writeText(state.outputText);
    } catch {
      el.output.select();
      document.execCommand('copy');
    }
    setStatus(el.outputStatus, 'Copied to clipboard.', 'ok');
  });

  el.download.addEventListener('click', () => {
    if (!state.outputText) return;
    const first2 = state.converted[0];
    const base = sanitizeFileToken(first2?.email || first2?.name || state.format);
    const fileName = `${base}.${state.format}.${getTimestampToken()}.json`;
    const blob = new Blob([state.outputText], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = fileName;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  // Drag & drop onto input textarea
  el.input.addEventListener('dragover', (e) => { e.preventDefault(); });
  el.input.addEventListener('drop', (e) => {
    e.preventDefault();
    if (e.dataTransfer?.files?.length) readFiles(e.dataTransfer.files);
  });

  updateOutput();
})();
