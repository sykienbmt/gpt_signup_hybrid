"""Get Session: Login ChatGPT bằng browser (Camoufox) + password + 2FA → trả full session JSON.

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
import json
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import Settings, ensure_runtime_dirs, load_settings, prepare_profile_dir
from .totp_helper import generate_code


LogFn = Callable[[str], None]


class SessionError(Exception):
    """Login/session fetch failed."""


# JS: bootstrap NextAuth → return authorize URL
_BOOTSTRAP_JS = r"""
async ({deviceId}) => {
    const csrfRes = await fetch('/api/auth/csrf', {credentials: 'include'});
    if (!csrfRes.ok) throw new Error('csrf HTTP ' + csrfRes.status);
    const csrfData = await csrfRes.json();
    const csrfToken = csrfData.csrfToken;

    const params = new URLSearchParams({
        'prompt': 'login',
        'ext-oai-did': deviceId,
    }).toString();
    const body = new URLSearchParams({
        callbackUrl: '/',
        csrfToken,
        json: 'true',
    }).toString();
    const signRes = await fetch('/api/auth/signin/openai?' + params, {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body,
    });
    if (!signRes.ok) throw new Error('signin HTTP ' + signRes.status);
    const signData = await signRes.json();
    if (!signData.url) throw new Error('no url');
    return signData.url;
}
"""

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
    log: LogFn = print,
) -> dict[str, Any]:
    """Login ChatGPT bằng Camoufox headless → return session JSON."""
    settings = load_settings()
    job_id = f"session_{uuid.uuid4().hex[:10]}"
    profile_dir = settings.profiles_dir / f"camoufox_{job_id}"
    ensure_runtime_dirs(settings, extra=(profile_dir,))
    prepare_profile_dir(
        profile_dir=profile_dir,
        template_dir=settings.browser_camoufox_profile_dir,
        use_template=True,
    )

    w, h = settings.browser_viewport_width, settings.browser_viewport_height

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
    fixed_screen = _Screen(min_width=w, max_width=w, min_height=h + chrome_h, max_height=h + chrome_h)

    proxy_kwargs: dict[str, Any] = {}
    if proxy:
        proxy_kwargs["proxy"] = {"server": proxy}

    cf = AsyncCamoufox(
        headless=headless,
        persistent_context=True,
        user_data_dir=str(profile_dir),
        viewport={"width": w, "height": h},
        screen=fixed_screen,
        ignore_https_errors=True,
        config=extra_config,
        **proxy_kwargs,
    )
    ctx = await cf.__aenter__()
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # Step 1: bootstrap
        log("[session] loading chatgpt.com...")
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        device_id = str(uuid.uuid4())
        log("[session] bootstrapping NextAuth...")
        authorize_url = await page.evaluate(_BOOTSTRAP_JS, {"deviceId": device_id})
        if not authorize_url or "auth.openai.com" not in authorize_url:
            raise SessionError(f"bootstrap failed: {authorize_url}")
        log("[session] authorize URL ready")

        # Step 2: navigate authorize → login page
        await page.goto(authorize_url, wait_until="domcontentloaded")
        await asyncio.sleep(3.0)
        log(f"[session] at: {page.url.split('?')[0]}")

        # Có thể ở /log-in (email step) → cần fill email trước
        # Hoặc ở /log-in/password → fill password luôn
        if "/log-in/password" not in page.url:
            # Check nếu cần nhập email trước
            email_input = None
            for sel in ('input[name="email"]', 'input[type="email"]', 'input[inputmode="email"]'):
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
                # Submit email
                for btn_sel in ('button[type="submit"]', 'button:has-text("Continue")'):
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

        # Submit password
        for btn_sel in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Log in")'):
            try:
                await page.click(btn_sel, timeout=3000)
                log(f"[session] clicked {btn_sel}")
                break
            except Exception:
                continue

        await asyncio.sleep(3.0)
        log(f"[session] after password: {page.url.split('?')[0]}")

        # Step 4: check if MFA required
        if "mfa" in page.url or "mfa" in (await page.content())[:5000].lower():
            if not secret:
                raise SessionError("account yêu cầu 2FA nhưng không có secret")

            log("[session] MFA page detected, generating TOTP...")
            code = generate_code(secret)

            # Find OTP input
            otp_input = None
            for sel in ('input[name="code"]', 'input[inputmode="numeric"]', 'input[autocomplete="one-time-code"]', 'input[maxlength="6"]'):
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

            # Submit MFA
            for btn_sel in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Verify")'):
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
        while time.monotonic() < deadline:
            cookies = await ctx.cookies("https://chatgpt.com/")
            names = {c["name"] for c in cookies}
            has_session = (
                "__Secure-next-auth.session-token" in names
                or "__Secure-next-auth.session-token.0" in names
            )
            if has_session:
                log("[session] session cookies ready")
                break
            # Check if still on auth page
            if "chatgpt.com" in page.url and "auth.openai.com" not in page.url:
                await asyncio.sleep(1.0)
                continue
            await asyncio.sleep(1.0)
        else:
            raise SessionError(f"timeout waiting session cookies. URL: {page.url}")

        # Đảm bảo đang ở chatgpt.com
        if "chatgpt.com" not in page.url:
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            await asyncio.sleep(2.0)

        # Step 6: fetch session JSON
        log("[session] fetching /api/auth/session...")
        session_data = await page.evaluate(_FETCH_SESSION_JS)
        if not isinstance(session_data, dict) or not session_data.get("accessToken"):
            raise SessionError(f"session response invalid: {str(session_data)[:200]}")

        log(f"[session] ✓ done — user: {session_data.get('user', {}).get('email', '?')}")
        return session_data

    finally:
        try:
            await cf.__aexit__(None, None, None)
        except Exception:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


async def get_session(
    *,
    email: str,
    password: str,
    secret: str | None = None,
    headless: bool = True,
    proxy: str | None = None,
    log: LogFn = print,
) -> dict[str, Any]:
    """Async: login ChatGPT → return full /api/auth/session JSON."""
    return await _get_session_browser(
        email=email,
        password=password,
        secret=secret,
        headless=headless,
        proxy=proxy,
        log=log,
    )


def get_session_sync(
    *,
    email: str,
    password: str,
    secret: str | None = None,
    headless: bool = True,
    proxy: str | None = None,
    log: LogFn = print,
) -> dict[str, Any]:
    """Sync wrapper."""
    return asyncio.run(get_session(
        email=email,
        password=password,
        secret=secret,
        headless=headless,
        proxy=proxy,
        log=log,
    ))
