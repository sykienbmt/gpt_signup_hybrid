"""Get Session: Login ChatGPT bằng browser + password + 2FA → trả full session JSON.

Dùng browser thật vì auth.openai.com có Cloudflare JS challenge —
curl_cffi không bypass được.

Flow:
    1. Mở chatgpt.com → bootstrap NextAuth (csrf + signin/openai) → authorize URL
    2. Navigate authorize → /log-in/password
    3. Fill password → submit
    4. Nếu MFA → fill TOTP code → submit
    5. Đợi redirect chatgpt.com + session cookies
    6. Gọi /api/auth/session trong page context → return JSON
"""
from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from typing import Any, Callable

from ._browser_retry import (
    LAUNCH_RETRY_BACKOFF as _LAUNCH_RETRY_BACKOFF,
    LAUNCH_RETRY_MAX as _LAUNCH_RETRY_MAX,
    is_driver_dead_error as _is_driver_dead_error,
)
from ._nextauth_bootstrap import bootstrap_authorize_url
from .config import ensure_runtime_dirs, load_settings, prepare_profile_dir
from .totp_helper import generate_code


LogFn = Callable[[str], None]


class SessionError(Exception):
    """Login/session fetch failed."""


# JS: fetch /api/auth/session trong page context chatgpt.com
_FETCH_SESSION_JS = r"""
async () => {
    const r = await fetch('/api/auth/session', {credentials: 'include'});
    if (!r.ok) throw new Error('session HTTP ' + r.status);
    return await r.json();
}
"""


async def _get_session_browser(
    *,
    email: str,
    password: str,
    secret: str | None = None,
    headless: bool = True,
    proxy: str | None = None,
    tls_insecure: bool = False,
    log: LogFn = print,
) -> dict[str, Any]:
    """Login ChatGPT bằng browser thật → return session JSON.

    Retry: nếu driver pipe đóng sớm TRƯỚC khi submit password → relaunch.
    Sau khi đã submit password thì fail-fast để tránh nhiều lần thử login
    (rủi ro lockout / captcha challenge).
    """
    if tls_insecure:
        from .config import warn_insecure_tls
        warn_insecure_tls("session_phase")
        log("[security] TLS verification DISABLED — debug mode")

    settings = load_settings()
    job_id = f"session_{uuid.uuid4().hex[:10]}"
    preferred_engine = (
        "camoufox"
        if (settings.browser_engine or "camoufox").lower() == "camoufox"
        else "chromium"
    )
    engine_order = [preferred_engine]
    if preferred_engine == "camoufox":
        engine_order.append("chromium")
    w, h = settings.browser_viewport_width, settings.browser_viewport_height
    viewport = {"width": w, "height": h}
    proxy_kwargs: dict[str, Any] = {}
    if proxy:
        proxy_kwargs["proxy"] = {"server": proxy}

    progress = {"password_submitted": False}

    def _profile_bundle(engine: str) -> tuple[Any, Any]:
        if engine == "camoufox":
            return (
                settings.profiles_dir / f"camoufox_{job_id}",
                settings.browser_camoufox_profile_dir,
            )
        return (
            settings.profile_dir_for(job_id),
            settings.browser_profile_template_dir,
        )

    async def _drive_session_flow(ctx: Any, page: Any) -> dict[str, Any]:
        device_id = str(uuid.uuid4())
        logging_id = str(uuid.uuid4())

        # Step 1: bootstrap
        log("[session] loading chatgpt.com...")
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        log("[session] bootstrapping NextAuth...")
        authorize_url = await bootstrap_authorize_url(
            page,
            email=email,
            device_id=device_id,
            logging_id=logging_id,
        )
        log("[session] authorize URL ready")

        # Step 2: navigate authorize → login page
        await page.goto(authorize_url, wait_until="domcontentloaded")
        await asyncio.sleep(3.0)
        log(f"[session] at: {page.url.split('?')[0]}")

        # Có thể ở /log-in (email step) → cần fill email trước
        # Hoặc ở /log-in/password → fill password luôn
        if "/log-in/password" not in page.url:
            email_input = None
            for sel in (
                'input[name="email"]',
                'input[type="email"]',
                'input[inputmode="email"]',
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=8000):
                        email_input = loc
                        break
                except Exception:
                    continue

            if email_input:
                log("[session] filling email...")
                await email_input.click(force=True, timeout=3000)
                await email_input.fill("")
                await email_input.type(email, delay=30)
                await asyncio.sleep(0.3)
                for btn_sel in (
                    'button[type="submit"]',
                    'button:has-text("Continue")',
                ):
                    try:
                        await page.click(btn_sel, timeout=3000)
                        log(f"[session] submitted email ({btn_sel})")
                        break
                    except Exception:
                        continue
                await asyncio.sleep(3.0)
                log(f"[session] after email: {page.url.split('?')[0]}")

        # Step 3: fill password
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
            raise SessionError(f"password input not found. URL: {page.url}")

        log("[session] filling password...")
        await pwd_input.click(force=True, timeout=3000)
        await pwd_input.fill("")
        await pwd_input.type(password, delay=40)
        await asyncio.sleep(0.3)

        # Submit password — sau đây không retry để tránh login spam
        for btn_sel in (
            'button[type="submit"]',
            'button:has-text("Continue")',
            'button:has-text("Log in")',
        ):
            try:
                await page.click(btn_sel, timeout=3000)
                log(f"[session] clicked {btn_sel}")
                break
            except Exception:
                continue
        progress["password_submitted"] = True

        await asyncio.sleep(3.0)
        log(f"[session] after password: {page.url.split('?')[0]}")

        # Step 4: check if MFA required
        if "mfa" in page.url or "mfa" in (await page.content())[:5000].lower():
            if not secret:
                raise SessionError("account yêu cầu 2FA nhưng không có secret")

            log("[session] MFA page detected, generating TOTP...")
            code = generate_code(secret)

            otp_input = None
            for sel in (
                'input[name="code"]',
                'input[inputmode="numeric"]',
                'input[autocomplete="one-time-code"]',
                'input[maxlength="6"]',
            ):
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=5000):
                        otp_input = loc
                        break
                except Exception:
                    continue

            if not otp_input:
                raise SessionError(f"TOTP input not found. URL: {page.url}")

            await otp_input.click(force=True, timeout=3000)
            await otp_input.fill("")
            await otp_input.type(code, delay=60)
            log(f"[session] TOTP code: {code}")
            await asyncio.sleep(0.5)

            for btn_sel in (
                'button[type="submit"]',
                'button:has-text("Continue")',
                'button:has-text("Verify")',
            ):
                try:
                    await page.click(btn_sel, timeout=3000)
                    log(f"[session] clicked {btn_sel}")
                    break
                except Exception:
                    continue

            await asyncio.sleep(3.0)
            log(f"[session] after MFA: {page.url.split('?')[0]}")

        # Step 5: đợi redirect về chatgpt.com + session cookies
        deadline = time.monotonic() + 30.0
        session_ready = False
        while time.monotonic() < deadline:
            cookies = await ctx.cookies("https://chatgpt.com/")
            names = {c["name"] for c in cookies}
            has_session = (
                "__Secure-next-auth.session-token" in names
                or "__Secure-next-auth.session-token.0" in names
            )
            if has_session:
                log("[session] session cookies ready")
                session_ready = True
                break
            if "chatgpt.com" in page.url and "auth.openai.com" not in page.url:
                await asyncio.sleep(1.0)
                continue
            await asyncio.sleep(1.0)
        if not session_ready:
            raise SessionError(f"timeout waiting session cookies. URL: {page.url}")

        # Đảm bảo đang ở chatgpt.com
        if "chatgpt.com" not in page.url:
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            await asyncio.sleep(2.0)

        # Step 6: fetch session JSON
        log("[session] fetching /api/auth/session...")
        session_data = await page.evaluate(_FETCH_SESSION_JS)
        if not isinstance(session_data, dict) or not session_data.get("accessToken"):
            raise SessionError(
                f"session response invalid: {str(session_data)[:200]}"
            )

        log(
            f"[session] ✓ done — user: "
            f"{session_data.get('user', {}).get('email', '?')}"
        )
        return session_data

    async def _run_camoufox_once(profile_dir: Any) -> dict[str, Any]:
        from camoufox.async_api import AsyncCamoufox
        from camoufox.utils import Screen as _Screen

        chrome_h = 85
        extra_config: dict = {}
        extra_config["window.innerWidth"] = w
        extra_config["window.innerHeight"] = h
        extra_config["window.outerWidth"] = w
        extra_config["window.outerHeight"] = h + chrome_h
        extra_config["screen.width"] = w
        extra_config["screen.height"] = h + chrome_h
        extra_config["screen.availWidth"] = w
        extra_config["screen.availHeight"] = h + chrome_h
        fixed_screen = _Screen(
            min_width=w, max_width=w, min_height=h + chrome_h, max_height=h + chrome_h,
        )
        cf = AsyncCamoufox(
            headless=headless,
            persistent_context=True,
            user_data_dir=str(profile_dir),
            viewport=viewport,
            screen=fixed_screen,
            ignore_https_errors=tls_insecure,
            config=extra_config,
            **proxy_kwargs,
        )
        ctx = await cf.__aenter__()
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            return await _drive_session_flow(ctx, page)
        finally:
            try:
                await cf.__aexit__(None, None, None)
            except Exception:
                pass

    async def _run_chromium_once(profile_dir: Any) -> dict[str, Any]:
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        try:
            channel = settings.browser_channel or None
            ctx = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                channel=channel,
                viewport=viewport,
                ignore_https_errors=tls_insecure,
                **proxy_kwargs,
            )
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                return await _drive_session_flow(ctx, page)
            finally:
                try:
                    await ctx.close()
                except Exception:
                    pass
        finally:
            await playwright.stop()

    runners = {
        "camoufox": _run_camoufox_once,
        "chromium": _run_chromium_once,
    }
    last_exc: BaseException | None = None
    try:
        for engine_index, engine in enumerate(engine_order):
            profile_dir, template_dir = _profile_bundle(engine)
            ensure_runtime_dirs(settings, extra=(profile_dir,))
            prepare_profile_dir(
                profile_dir=profile_dir,
                template_dir=template_dir,
                use_template=settings.browser_use_profile_template,
            )
            if engine_index > 0:
                log(f"[session] fallback browser engine: {engine}")

            for attempt in range(1, _LAUNCH_RETRY_MAX + 1):
                progress["password_submitted"] = False
                try:
                    return await runners[engine](profile_dir)
                except SessionError:
                    raise
                except Exception as exc:
                    last_exc = exc
                    if not _is_driver_dead_error(exc):
                        raise SessionError(
                            f"browser launch/driver error: {type(exc).__name__}: {exc}"
                        ) from exc
                    if progress["password_submitted"]:
                        log(
                            f"[session] driver pipe đóng sau khi đã submit password — "
                            f"không retry để tránh login spam: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        raise SessionError(
                            f"driver pipe chết sau submit password (không retry): {exc}"
                        ) from exc
                    log(
                        f"[session] driver pipe đóng sớm "
                        f"(attempt {attempt}/{_LAUNCH_RETRY_MAX}): "
                        f"{type(exc).__name__}: {exc}"
                    )
                    if attempt >= _LAUNCH_RETRY_MAX:
                        break
                    shutil.rmtree(profile_dir, ignore_errors=True)
                    prepare_profile_dir(
                        profile_dir=profile_dir,
                        template_dir=template_dir,
                        use_template=settings.browser_use_profile_template,
                    )
                    await asyncio.sleep(_LAUNCH_RETRY_BACKOFF)

            if engine == "camoufox" and engine_index + 1 < len(engine_order):
                log(
                    "[session] camoufox chết sớm ở auth redirect — "
                    "thử fallback sang chromium"
                )
                continue

            if last_exc is not None and _is_driver_dead_error(last_exc):
                raise SessionError(
                    f"driver pipe đóng sau {_LAUNCH_RETRY_MAX} lần thử: {last_exc}"
                ) from last_exc

        raise SessionError("browser launch failed without specific error")
    finally:
        for engine in engine_order:
            profile_dir, _ = _profile_bundle(engine)
            shutil.rmtree(profile_dir, ignore_errors=True)


async def get_session(
    *,
    email: str,
    password: str,
    secret: str | None = None,
    headless: bool = True,
    proxy: str | None = None,
    tls_insecure: bool = False,
    log: LogFn = print,
) -> dict[str, Any]:
    """Async: login ChatGPT → return full /api/auth/session JSON."""
    return await _get_session_browser(
        email=email,
        password=password,
        secret=secret,
        headless=headless,
        proxy=proxy,
        tls_insecure=tls_insecure,
        log=log,
    )


def get_session_sync(
    *,
    email: str,
    password: str,
    secret: str | None = None,
    headless: bool = True,
    proxy: str | None = None,
    tls_insecure: bool = False,
    log: LogFn = print,
) -> dict[str, Any]:
    """Sync wrapper."""
    return asyncio.run(get_session(
        email=email,
        password=password,
        secret=secret,
        headless=headless,
        proxy=proxy,
        tls_insecure=tls_insecure,
        log=log,
    ))


# ─────────────────────────────────────────────────────────────────────
# HTTP-only session fetch (no browser) — dùng khi đã có cookies sẵn từ Phase 2.
# ─────────────────────────────────────────────────────────────────────


def _cookies_to_header(cookies: Any) -> str:
    """Convert cookies (list[dict] | dict | None) → "name=value; name=value" string.

    Hỗ trợ 2 format:
      - list[dict]: Playwright/SignupResult format [{"name":..., "value":..., "domain":...}, ...]
        → chỉ giữ cookies thuộc domain chatgpt.com (hoặc rỗng).
      - dict: {name: value} flat.
    """
    if not cookies:
        return ""
    pairs: list[str] = []
    if isinstance(cookies, list):
        for c in cookies:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            value = c.get("value")
            if not name or value is None:
                continue
            domain = (c.get("domain") or "").lstrip(".").lower()
            # Chỉ giữ cookies dùng được cho chatgpt.com
            if domain and "chatgpt.com" not in domain:
                continue
            pairs.append(f"{name}={value}")
    elif isinstance(cookies, dict):
        for name, value in cookies.items():
            if value is None:
                continue
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


async def fetch_session_via_http(
    *,
    cookies: Any,
    proxy: str | None = None,
    timeout: float = 30.0,
    impersonate: str = "chrome136",
) -> dict[str, Any]:
    """GET https://chatgpt.com/api/auth/session bằng curl_cffi với cookies có sẵn.

    Args:
        cookies: list[dict] (Playwright format) hoặc dict {name: value}.
        proxy: HTTP/HTTPS proxy URL.
        timeout: Request timeout (seconds).
        impersonate: curl_cffi browser impersonation key.

    Returns:
        Full session JSON (dict) với accessToken không rỗng.

    Raises:
        SessionError: HTTP non-200, JSON parse fail, hoặc accessToken thiếu/rỗng.
    """
    from curl_cffi.requests import AsyncSession

    cookie_header = _cookies_to_header(cookies)
    if not cookie_header:
        raise SessionError("không có cookie chatgpt.com để fetch session")

    proxies = {"http": proxy, "https": proxy} if proxy else None
    headers = {
        "Cookie": cookie_header,
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/",
    }

    async with AsyncSession(impersonate=impersonate, proxies=proxies) as sess:
        try:
            resp = await sess.get(
                "https://chatgpt.com/api/auth/session",
                headers=headers,
                timeout=timeout,
            )
        except Exception as exc:
            raise SessionError(f"network error: {exc}") from exc

    if resp.status_code != 200:
        body = (resp.text or "")[:200]
        raise SessionError(f"HTTP {resp.status_code}: {body}")

    try:
        data = resp.json()
    except Exception as exc:
        raise SessionError(f"JSON parse fail: {exc}") from exc

    if not isinstance(data, dict):
        raise SessionError(f"response không phải JSON object: {type(data).__name__}")

    token = data.get("accessToken")
    if not isinstance(token, str) or not token.strip():
        raise SessionError("accessToken thiếu hoặc rỗng")

    return data
