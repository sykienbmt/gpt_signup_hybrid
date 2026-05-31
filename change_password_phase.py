"""Change ChatGPT account password via browser automation.

Flow:
    1. Browser login (same as session_phase)
    2. Settings → Security → click password field → auth.openai.com
    3. Verify current password
    4. Enter new password (×2) and submit
    5. If MFA challenge → fill TOTP code
"""
from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from typing import Any, Callable
from urllib.parse import urljoin

from ._browser_retry import (
    LAUNCH_RETRY_BACKOFF as _BACKOFF,
    LAUNCH_RETRY_MAX as _RETRY_MAX,
    is_driver_dead_error as _is_dead,
)
from ._nextauth_bootstrap import bootstrap_authorize_url_http
from .config import browser_proxy_config, load_settings
from .totp_helper import generate_code

LogFn = Callable[[str], None]
_REAUTH_FORM_NAV_WAIT_SECONDS = 4.0


_FETCH_SESSION_JS = """
async (nonce) => {
    const resp = await fetch('/api/auth/session?chpwd=' + nonce, {
        credentials: 'include',
        cache: 'no-store',
        headers: {
            'Accept': 'application/json',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
    });
    const text = await resp.text();
    if (!resp.ok) {
        return {ok: false, status: resp.status, body: text.slice(0, 200)};
    }
    try {
        return {ok: true, status: resp.status, data: JSON.parse(text)};
    } catch (err) {
        return {ok: false, status: resp.status, body: text.slice(0, 200), parseError: String(err)};
    }
}
"""


class ChangePasswordError(Exception):
    """Password change failed."""


async def change_password(
    *,
    email: str,
    current_password: str,
    new_password: str,
    secret: str | None = None,
    headless: bool = True,
    proxy: str | None = None,
    log: LogFn = print,
) -> dict:
    """Change ChatGPT account password. Returns the refreshed session JSON."""
    settings = load_settings()
    job_id = f"chpwd_{uuid.uuid4().hex[:10]}"
    proxy_kwargs: dict[str, Any] = {}
    if proxy:
        proxy_kwargs["proxy"] = browser_proxy_config(proxy)
        await _wait_proxy_ready(proxy, log)
        log("[chpwd] browser proxy configured with split credentials")

    preferred = (
        settings.change_password_browser_engine
        or settings.browser_engine
        or "camoufox"
    ).lower()
    if preferred not in {"camoufox", "chromium"}:
        log(f"[chpwd] invalid browser engine {preferred!r}; using camoufox")
        preferred = "camoufox"
    engine_order = [preferred]
    w, h = settings.browser_viewport_width, settings.browser_viewport_height
    viewport = {"width": w, "height": h}
    progress = {"login_password_submitted": False}

    last_err: Exception | None = None
    for attempt in range(1, _RETRY_MAX + 1):
        progress["login_password_submitted"] = False
        engine = engine_order[(attempt - 1) % len(engine_order)]
        if engine == "camoufox":
            profile_dir = settings.profiles_dir / f"camoufox_{job_id}"
        else:
            profile_dir = settings.profiles_dir / job_id

        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        try:
            log(f"[chpwd] browser engine: {engine} (attempt {attempt}/{_RETRY_MAX})")
            if engine == "camoufox":
                from camoufox.async_api import AsyncCamoufox
                from camoufox.utils import Screen as _Screen

                chrome_h = 85
                extra_config: dict[str, int] = {
                    "window.innerWidth": w,
                    "window.innerHeight": h,
                    "window.outerWidth": w,
                    "window.outerHeight": h + chrome_h,
                    "screen.width": w,
                    "screen.height": h + chrome_h,
                    "screen.availWidth": w,
                    "screen.availHeight": h + chrome_h,
                }
                fixed_screen = _Screen(
                    min_width=w,
                    max_width=w,
                    min_height=h + chrome_h,
                    max_height=h + chrome_h,
                )

                cf = AsyncCamoufox(
                    headless=headless,
                    persistent_context=True,
                    user_data_dir=str(profile_dir),
                    viewport=viewport,
                    screen=fixed_screen,
                    config=extra_config,
                    **proxy_kwargs,
                )
                ctx = await cf.__aenter__()
                try:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    return await _drive(ctx, page, email, current_password, new_password, secret, h, log, progress, proxy)
                finally:
                    try:
                        await cf.__aexit__(None, None, None)
                    except Exception:
                        pass
            else:
                from playwright.async_api import async_playwright

                playwright = await async_playwright().start()
                try:
                    channel = settings.browser_channel or None
                    ctx = await playwright.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=headless,
                        channel=channel,
                        viewport=viewport,
                        **proxy_kwargs,
                    )
                    try:
                        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                        return await _drive(ctx, page, email, current_password, new_password, secret, h, log, progress, proxy)
                    finally:
                        try:
                            await ctx.close()
                        except Exception:
                            pass
                finally:
                    try:
                        await playwright.stop()
                    except Exception:
                        pass

        except ChangePasswordError:
            raise
        except Exception as exc:
            if _is_dead(exc) and attempt < _RETRY_MAX:
                if progress["login_password_submitted"]:
                    log(
                        "[chpwd] driver died after login password was submitted; "
                        "not retrying to avoid reopening browser and logging in again"
                    )
                    raise ChangePasswordError(
                        f"Browser driver died after login password submit: {exc}"
                    ) from exc
                log(f"[chpwd] driver error (attempt {attempt}): {exc} — retry in {_BACKOFF}s")
                await asyncio.sleep(_BACKOFF)
                last_err = exc
                continue
            raise ChangePasswordError(f"Browser error: {exc}") from exc

    raise ChangePasswordError(f"Failed after {_RETRY_MAX} attempts: {last_err}")


async def _wait_proxy_ready(proxy: str, log: LogFn) -> None:
    """Wait until the configured proxy accepts HTTPS traffic before browser launch."""
    import httpx

    timeout = httpx.Timeout(connect=8.0, read=8.0, write=8.0, pool=8.0)
    deadline = time.monotonic() + 45.0
    attempt = 0
    last_error = ""
    log("[chpwd] checking proxy before browser launch...")
    while time.monotonic() < deadline:
        attempt += 1
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=timeout, follow_redirects=False) as client:
                resp = await client.get("https://chatgpt.com/")
            if resp.status_code < 500:
                log(f"[chpwd] proxy ready: HTTP {resp.status_code}")
                return
            last_error = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        log(f"[chpwd] proxy not ready (attempt {attempt}): {last_error}")
        await asyncio.sleep(3.0)
    raise ChangePasswordError(
        "Proxy is not reachable for ChatGPT after rotate/wait: "
        f"{last_error}. Check proxy URL or wait for provider to finish rotating."
    )


async def _drive(
    ctx: Any, page: Any,
    email: str, current_password: str, new_password: str,
    secret: str | None, viewport_h: int, log: LogFn,
    progress: dict[str, bool],
    proxy: str | None,
) -> dict:
    """Full browser flow: login → reauth → password change → refreshed session."""
    _attach_network_debug_logging(page, log)
    device_id = str(uuid.uuid4())
    logging_id = str(uuid.uuid4())

    # Phase 1: get authorize URL + chatgpt.com cookies via HTTP
    log("[chpwd] bootstrapping NextAuth via HTTP...")
    authorize_url, chatgpt_cookies = await bootstrap_authorize_url_http(
        proxy=proxy,
        email=email,
        device_id=device_id,
        logging_id=logging_id,
    )
    log("[chpwd] navigating directly to login page...")

    # Inject chatgpt.com cookies so the OAuth callback can validate state/CSRF
    if chatgpt_cookies:
        try:
            await ctx.add_cookies(chatgpt_cookies)
        except Exception:
            pass

    await page.goto(authorize_url, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(3.0)

    if "/log-in/password" not in page.url:
        for sel in ('input[name="email"]', 'input[type="email"]'):
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=8000):
                    await loc.click(force=True)
                    await loc.fill("")
                    await loc.type(email, delay=30)
                    for btn in ('button[type="submit"]', 'button:has-text("Continue")'):
                        try:
                            await page.click(btn, timeout=3000)
                            break
                        except Exception:
                            continue
                    await asyncio.sleep(3.0)
                    break
            except Exception:
                continue

    pwd_input = None
    for sel in ('input[type="password"]', 'input[name="password"]'):
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=15000):
                pwd_input = loc
                break
        except Exception:
            continue
    if not pwd_input:
        raise ChangePasswordError(f"Login password field not found. URL: {page.url}")

    await pwd_input.click(force=True)
    await pwd_input.fill("")
    await pwd_input.type(current_password, delay=40)
    for btn in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Log in")'):
        try:
            await page.click(btn, timeout=3000)
            progress["login_password_submitted"] = True
            log("[chpwd] login password submitted")
            break
        except Exception:
            continue

    # Wait for either chatgpt.com (login done) OR mfa-challenge (need TOTP)
    log("[chpwd] waiting for post-login redirect...")
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        url = page.url
        if "chatgpt.com" in url and "auth.openai.com" not in url:
            break
        if "mfa-challenge" in url:
            log("[chpwd] MFA challenge detected at login step")
            await _fill_mfa_if_needed(page, secret, log, step="login")
            break
        await asyncio.sleep(1.0)

    # Final wait for chatgpt.com (may need more time after MFA submit)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if "chatgpt.com" in page.url and "auth.openai.com" not in page.url:
            break
        await asyncio.sleep(1.0)
    if "auth.openai.com" in page.url:
        raise ChangePasswordError(f"Login failed — still on auth page. URL: {page.url}")
    log(f"[chpwd] login OK. URL: {page.url.split('?')[0]}")

    # Phase 2: Trigger password-change reauth via a real top-level NextAuth form
    # submit. Camoufox has been unstable with direct page.goto(auth URL), and
    # location.assign can be ignored from this page, so let NextAuth's HTTP
    # redirect perform the cross-origin navigation.
    log("[chpwd] triggering password-change reauth via NextAuth form...")
    _REAUTH_FORM_JS = """
async (email) => {
    const csrfResp = await fetch('/api/auth/csrf', {credentials: 'include'});
    const {csrfToken} = await csrfResp.json();
    const oaiDid = (document.cookie.split(';')
        .find(c => c.trim().startsWith('oai-did=')) || '').split('=')[1] || '';
    const qs = new URLSearchParams({
        connection: 'password',
        login_hint: email,
        reauth: 'password',
        post_login_password_reset: 'true',
        max_age: '0',
        'ext-oai-did': oaiDid,
    });
    return {
        action: '/api/auth/signin/openai?' + qs.toString(),
        csrfToken,
    };
}
"""
    try:
        reauth_form = await page.evaluate(_REAUTH_FORM_JS, email)
        action_url = (reauth_form or {}).get("action") or ""
        csrf_token = (reauth_form or {}).get("csrfToken") or ""
    except Exception as exc:
        raise ChangePasswordError(f"NextAuth reauth form build failed: {exc}")

    if not action_url or not csrf_token:
        raise ChangePasswordError(f"Unexpected reauth form data: {reauth_form}")

    log(f"[chpwd] submitting reauth form: {_safe_debug_url(action_url)}")
    await _submit_reauth_form(ctx, page, action_url, csrf_token, log, proxy)
    await asyncio.sleep(2.0)
    log(f"[chpwd] at: {page.url.split('?')[0]}")

    if "log-in/password" not in page.url:
        raise ChangePasswordError(f"Expected /log-in/password, got: {page.url}")

    # Phase 3: Verify current password
    await asyncio.sleep(1.0)
    cur_input = None
    for sel in ('input[type="password"]',):
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=8000):
                cur_input = loc
                break
        except Exception:
            continue
    if not cur_input:
        raise ChangePasswordError("Current password input not found on verify page")

    await cur_input.click(force=True)
    await cur_input.fill("")
    await cur_input.type(current_password, delay=40)
    for btn in ('button[type="submit"]', 'button:has-text("Continue")'):
        try:
            await page.click(btn, timeout=5000)
            break
        except Exception:
            continue

    try:
        await page.wait_for_url("**/reset-password/**", timeout=15000)
        log("[chpwd] on new password page")
    except Exception:
        content = await page.content()
        if any(x in content.lower() for x in ("wrong password", "incorrect", "invalid")):
            raise ChangePasswordError("Current password is incorrect")
        raise ChangePasswordError(f"Did not reach reset-password page. URL: {page.url}")

    # Phase 4: Set new password
    await asyncio.sleep(1.0)
    pwd_locs = page.locator('input[type="password"]')
    visible = []
    for i in range(await pwd_locs.count()):
        try:
            if await pwd_locs.nth(i).is_visible(timeout=2000):
                visible.append(pwd_locs.nth(i))
        except Exception:
            continue
    if not visible:
        raise ChangePasswordError("New password inputs not found on reset page")

    await visible[0].click(force=True)
    await visible[0].fill("")
    await visible[0].type(new_password, delay=40)
    if len(visible) >= 2:
        await visible[1].click(force=True)
        await visible[1].fill("")
        await visible[1].type(new_password, delay=40)
    log(f"[chpwd] new password filled: {new_password} ({len(visible)} field(s))")
    await asyncio.sleep(0.5)

    for btn in ('button[type="submit"]', 'button:has-text("Continue")'):
        try:
            await page.click(btn, timeout=5000)
            break
        except Exception:
            continue
    await asyncio.sleep(3.0)

    # Phase 5: MFA challenge after password reset (if any)
    await _complete_post_reset_auth(page, secret, log)
    log(f"[chpwd] ✓ password changed. URL: {page.url.split('?')[0]}")

    # Phase 6: navigate to chatgpt.com home and fetch session from this browser.
    # The browser is already authenticated — no need to launch a new browser.
    return await _fetch_refreshed_session(ctx, page, email, log, proxy)


async def _fetch_refreshed_session(
    ctx: Any,
    page: Any,
    email: str,
    log: LogFn,
    proxy: str | None = None,
) -> dict:
    """Fetch /api/auth/session after password change without relaunching browser."""
    log("[chpwd] fetching /api/auth/session in current browser...")
    deadline = time.monotonic() + 45.0
    last_error = ""

    while time.monotonic() < deadline:
        try:
            current_url = page.url
            needs_nav = "chatgpt.com" not in current_url or "auth/error" in current_url
            if needs_nav:
                log(f"[chpwd] navigating to chatgpt.com home from {current_url.split('?')[0]}")
                await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
                await asyncio.sleep(2.0)

            cookies = await ctx.cookies("https://chatgpt.com/")
            names = {c.get("name") for c in cookies}
            has_session_cookie = (
                "__Secure-next-auth.session-token" in names
                or "__Secure-next-auth.session-token.0" in names
            )
            if not has_session_cookie:
                last_error = "missing chatgpt session cookie"
                log(f"[chpwd] session cookie not ready; cookies={sorted(n for n in names if n)}")
                await asyncio.sleep(1.5)
                continue

            result = await page.evaluate(_FETCH_SESSION_JS, str(int(time.time() * 1000)))
            if not isinstance(result, dict):
                last_error = f"unexpected JS result: {type(result).__name__}"
                log(f"[chpwd] session fetch invalid JS result: {type(result).__name__}")
                await asyncio.sleep(1.5)
                continue
            if not result.get("ok"):
                detail = result.get("parseError") or result.get("body", "")
                last_error = f"session HTTP {result.get('status')}: {detail}"
                log(f"[chpwd] session fetch failed: {last_error}")
                await asyncio.sleep(1.5)
                continue

            data = result.get("data")
            if isinstance(data, dict) and data.get("accessToken"):
                session_email = (data.get("user") or {}).get("email") or "?"
                if email and session_email != "?" and session_email.lower() != email.lower():
                    raise ChangePasswordError(
                        f"session email mismatch after password change: {session_email}"
                    )
                token_ok, token_detail = await _validate_access_token(
                    str(data.get("accessToken") or ""),
                    proxy=proxy,
                )
                if not token_ok:
                    last_error = f"session accessToken invalid: {token_detail}"
                    log(f"[chpwd] {last_error}")
                    await asyncio.sleep(1.5)
                    continue
                token_len = len(str(data.get("accessToken") or ""))
                session_token_len = len(str(data.get("sessionToken") or ""))
                log(
                    f"[chpwd] ✓ session API OK: email={session_email}, "
                    f"accessToken_len={token_len}, sessionToken_len={session_token_len}"
                )
                return data
            last_error = f"session response missing accessToken: {str(data)[:160]}"
            log(f"[chpwd] {last_error}")
        except ChangePasswordError:
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            log(f"[chpwd] session fetch exception: {last_error}")
        await asyncio.sleep(1.5)

    raise ChangePasswordError(
        "Password changed but could not fetch new session in current browser: "
        f"{last_error or 'timeout'}"
    )


async def _validate_access_token(token: str, *, proxy: str | None = None) -> tuple[bool, str]:
    """Return whether the fetched ChatGPT accessToken is accepted by backend API."""
    if not token:
        return False, "missing token"
    from curl_cffi.requests import AsyncSession

    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        async with AsyncSession(impersonate="chrome136", proxies=proxies) as sess:
            resp = await sess.get(
                "https://chatgpt.com/backend-api/me",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "*/*",
                    "Origin": "https://chatgpt.com",
                    "Referer": "https://chatgpt.com/",
                },
                timeout=12.0,
            )
    except Exception as exc:
        return False, f"validation network error: {exc}"

    if resp.status_code == 200:
        return True, "HTTP 200"
    return False, f"HTTP {resp.status_code}: {(resp.text or '')[:160]}"


def _safe_debug_url(url: str) -> str:
    return str(url or "").split("?", 1)[0]


def _is_debug_url(url: str) -> bool:
    clean = _safe_debug_url(url)
    return any(
        marker in clean
        for marker in (
            "/api/auth/session",
            "/api/auth/csrf",
            "/api/auth/signin/openai",
            "/api/auth/callback",
            "/authorize",
            "/log-in/password",
            "/reset-password",
        )
    )


def _attach_network_debug_logging(page: Any, log: LogFn) -> None:
    """Log auth/session request failures and important HTTP statuses."""
    def on_response(response: Any) -> None:
        try:
            url = getattr(response, "url", "")
            if not _is_debug_url(url):
                return
            status = int(getattr(response, "status", 0) or 0)
            request = getattr(response, "request", None)
            method = getattr(request, "method", "?") if request else "?"
            clean = _safe_debug_url(url)
            if (
                "/api/auth/session" in clean
                or "/api/auth/csrf" in clean
                or "/api/auth/signin/openai" in clean
                or status >= 400
            ):
                log(f"[net] HTTP {status} {method} {_safe_debug_url(url)}")
        except Exception:
            return

    def on_request_failed(request: Any) -> None:
        try:
            url = getattr(request, "url", "")
            if not _is_debug_url(url):
                return
            failure = getattr(request, "failure", None)
            if callable(failure):
                failure = failure()
            method = getattr(request, "method", "?")
            log(f"[net] FAILED {method} {_safe_debug_url(url)}: {failure}")
        except Exception:
            return

    try:
        page.on("response", on_response)
        page.on("requestfailed", on_request_failed)
    except Exception as exc:
        log(f"[chpwd] could not attach network debug logging: {exc}")


async def _submit_reauth_form(
    ctx: Any,
    page: Any,
    action_url: str,
    csrf_token: str,
    log: LogFn,
    proxy: str | None = None,
) -> None:
    """Submit NextAuth reauth as a top-level form and wait for Auth0/OpenAI."""
    submit_js = """
({actionUrl, csrfToken}) => {
    const old = document.getElementById('chpwd-reauth-form');
    if (old) old.remove();
    const form = document.createElement('form');
    form.id = 'chpwd-reauth-form';
    form.method = 'POST';
    form.action = actionUrl;
    form.style.position = 'fixed';
    form.style.left = '8px';
    form.style.top = '8px';
    form.style.zIndex = '2147483647';
    form.style.width = '1px';
    form.style.height = '1px';
    form.style.overflow = 'hidden';

    const callback = document.createElement('input');
    callback.type = 'hidden';
    callback.name = 'callbackUrl';
    callback.value = 'https://chatgpt.com/';
    form.appendChild(callback);

    const csrf = document.createElement('input');
    csrf.type = 'hidden';
    csrf.name = 'csrfToken';
    csrf.value = csrfToken;
    form.appendChild(csrf);

    const submit = document.createElement('button');
    submit.id = 'chpwd-reauth-submit';
    submit.type = 'submit';
    submit.textContent = 'continue';
    form.appendChild(submit);

    document.body.appendChild(form);
}
"""
    try:
        await page.evaluate(submit_js, {"actionUrl": action_url, "csrfToken": csrf_token})
        await page.locator("#chpwd-reauth-submit").click(timeout=5000, force=True)
    except Exception as exc:
        raise ChangePasswordError(f"NextAuth reauth form submit failed: {exc}") from exc

    deadline = time.monotonic() + _REAUTH_FORM_NAV_WAIT_SECONDS
    last_url = page.url
    while time.monotonic() < deadline:
        current = page.url
        if current != last_url:
            log(f"[chpwd] reauth navigation: {_safe_debug_url(current)}")
            last_url = current
        if "auth.openai.com" in current or "/log-in/password" in current:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception as exc:
                log(f"[chpwd] reauth load-state wait skipped: {exc}")
            return
        await asyncio.sleep(0.25)

    log("[chpwd] reauth form did not navigate after 4s; requesting redirect URL via external HTTP...")
    redirect_url, new_reauth_cookies = await _fetch_reauth_redirect_url(ctx, action_url, csrf_token, log, proxy)
    log(f"[chpwd] reauth redirect URL ready: {_safe_debug_url(redirect_url)}")

    # Inject new NextAuth state cookies into browser BEFORE navigating to the
    # redirect URL.  The state embedded in redirect_url was generated by the HTTP
    # POST above; without these cookies NextAuth will reject the callback and
    # redirect to /auth/error.
    if new_reauth_cookies:
        try:
            await ctx.add_cookies(new_reauth_cookies)
            log(f"[chpwd] injected {len(new_reauth_cookies)} reauth state cookie(s) into browser")
        except Exception as ce:
            log(f"[chpwd] reauth cookie inject skipped: {ce}")

    await _click_hidden_reauth_link(page, redirect_url, log)


async def _fetch_reauth_redirect_url(
    ctx: Any,
    action_url: str,
    csrf_token: str,
    log: LogFn,
    proxy: str | None = None,
) -> tuple[str, list[dict]]:
    """Returns (redirect_url, new_chatgpt_cookies).

    The new cookies contain the NextAuth state/CSRF generated by this HTTP
    request. They must be injected into the browser before navigating to the
    redirect URL, otherwise auth.openai.com's callback will fail with /auth/error
    because the state in the redirect won't match any cookie in the browser.
    """
    from curl_cffi.requests import AsyncSession

    absolute_action_url = urljoin("https://chatgpt.com/", action_url)
    cookies = await ctx.cookies("https://chatgpt.com/")
    cookie_header = _cookies_to_header(cookies)
    if not cookie_header:
        raise ChangePasswordError("No chatgpt.com cookies available for reauth request")

    proxies = {"http": proxy, "https": proxy} if proxy else None
    new_browser_cookies: list[dict] = []
    try:
        async with AsyncSession(impersonate="chrome136", proxies=proxies) as sess:
            resp = await sess.post(
                absolute_action_url,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Cookie": cookie_header,
                    "Origin": "https://chatgpt.com",
                    "Referer": "https://chatgpt.com/",
                },
                data={
                    "callbackUrl": "https://chatgpt.com/",
                    "csrfToken": csrf_token,
                    "json": "true",
                },
                timeout=15000,
            )
            # Capture new NextAuth state cookies (next-auth.csrf-token,
            # next-auth.callback-url, etc.) so the caller can inject them into the
            # browser.  Without these the auth.openai.com callback will fail with
            # /auth/error because the state embedded in the redirect URL won't match
            # any cookie in the browser context.
            raw_new = sess.cookies.get_dict(domain="chatgpt.com") or {}
            new_browser_cookies = [
                {
                    "name": name,
                    "value": value,
                    "domain": ".chatgpt.com",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                }
                for name, value in raw_new.items()
            ]
        text = resp.text or ""
        try:
            data = resp.json()
        except Exception:
            data = None
        result = {
            "ok": 200 <= resp.status_code < 300,
            "status": resp.status_code,
            "data": data,
            "text": text[:240],
        }
    except Exception as exc:
        raise ChangePasswordError(f"NextAuth reauth request failed: {exc}") from exc

    log(f"[chpwd] reauth request HTTP {result.get('status')}")
    if not result.get("ok"):
        raise ChangePasswordError(
            f"NextAuth reauth request HTTP {result.get('status')}: {result.get('text', '')}"
        )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    redirect_url = data.get("url") or ""
    if "auth.openai.com" not in redirect_url:
        raise ChangePasswordError(f"NextAuth reauth response has no auth URL: {result}")
    return redirect_url, new_browser_cookies


def _cookies_to_header(cookies: Any) -> str:
    pairs: list[str] = []
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        domain = (cookie.get("domain") or "").lstrip(".").lower()
        if not name or value is None:
            continue
        if domain and "chatgpt.com" not in domain:
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


async def _click_hidden_reauth_link(page: Any, redirect_url: str, log: LogFn) -> None:
    try:
        await page.evaluate(
            """
target => {
    const a = document.createElement('a');
    a.id = 'chpwd-reauth-continue';
    a.href = target;
    a.target = '_self';
    a.rel = 'noreferrer';
    a.textContent = 'continue';
    a.style.position = 'fixed';
    a.style.left = '8px';
    a.style.top = '8px';
    a.style.zIndex = '2147483647';
    document.body.appendChild(a);
}
""",
            redirect_url,
        )
        await page.locator("#chpwd-reauth-continue").click(timeout=5000, force=True)
    except Exception as exc:
        current = page.url
        if "auth.openai.com" in current or "/log-in/password" in current:
            log(f"[chpwd] hidden link click reached reauth despite click wait error: {_safe_debug_url(current)}")
            return
        log(f"[chpwd] hidden link click failed, falling back to page.goto: {exc}")
        await page.goto(redirect_url, wait_until="domcontentloaded")
        return

    deadline = time.monotonic() + 25.0
    last_url = page.url
    while time.monotonic() < deadline:
        current = page.url
        if current != last_url:
            log(f"[chpwd] reauth navigation: {_safe_debug_url(current)}")
            last_url = current
        if "auth.openai.com" in current or "/log-in/password" in current:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception as exc:
                log(f"[chpwd] reauth load-state wait skipped: {exc}")
            return
        await asyncio.sleep(0.25)

    raise ChangePasswordError(f"Timed out opening reauth redirect URL. Current URL: {page.url}")


async def _navigate_with_location_assign(page: Any, url: str, log: LogFn) -> None:
    """Navigate via page JS to avoid Camoufox driver crashes seen with page.goto."""
    try:
        await page.evaluate("target => window.location.assign(target)", url)
    except Exception as exc:
        log(f"[chpwd] location.assign failed, falling back to page.goto: {exc}")
        await page.goto(url, wait_until="domcontentloaded")
        return

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        current = page.url
        if "auth.openai.com" in current or "/log-in/password" in current:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception as exc:
                log(f"[chpwd] reauth load-state wait skipped: {exc}")
            return
        await asyncio.sleep(0.25)

    raise ChangePasswordError(f"Timed out navigating to reauth URL. Current URL: {page.url}")


async def _complete_post_reset_auth(page: Any, secret: str | None, log: LogFn) -> None:
    """Finish post-reset screens before fetching ChatGPT session."""
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        current = page.url
        if "chatgpt.com" in current and "auth.openai.com" not in current:
            return

        if "mfa-challenge" in current:
            await _fill_mfa_if_needed(page, secret, log, step="reset")
            await asyncio.sleep(1.0)
            continue

        if await _click_post_reset_continue_if_present(page, log):
            await asyncio.sleep(1.0)
            continue

        if "auth.openai.com" not in current:
            return

        await asyncio.sleep(0.5)

    if "auth.openai.com" in page.url:
        raise ChangePasswordError(f"Post-reset auth did not return to ChatGPT. URL: {page.url}")
    log(f"[chpwd] post-reset auth ended at {_safe_debug_url(page.url)}; continuing to session fetch")


async def _click_post_reset_continue_if_present(page: Any, log: LogFn) -> bool:
    """Follow Auth0/OpenAI post-reset continue action when the page exposes one."""
    for sel in (
        'button:has-text("Continue")',
        'a:has-text("Continue")',
        'button:has-text("Back to ChatGPT")',
        'a:has-text("Back to ChatGPT")',
    ):
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=1500):
                await loc.click(force=True, timeout=3000)
                log(f"[chpwd] clicked post-reset continue ({sel})")
                await asyncio.sleep(3.0)
                return True
        except Exception:
            continue
    return False


async def _fill_mfa_if_needed(page: Any, secret: str | None, log: LogFn, step: str = "") -> None:
    """Fill TOTP code if currently on an MFA challenge page."""
    if "mfa-challenge" not in page.url:
        return
    if not secret:
        raise ChangePasswordError(f"2FA required at {step} but no secret provided")
    code = generate_code(secret)
    log(f"[chpwd] MFA at {step}, code: {code}")
    for sel in ('input[name="code"]', 'input[inputmode="numeric"]', 'input[maxlength="6"]'):
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=5000):
                await loc.click(force=True)
                await loc.fill("")
                await loc.type(code, delay=60)
                await asyncio.sleep(0.5)
                for btn in ('button[type="submit"]', 'button:has-text("Continue")'):
                    try:
                        await page.click(btn, timeout=3000)
                        break
                    except Exception:
                        continue
                await asyncio.sleep(3.0)
                break
        except Exception:
            continue
