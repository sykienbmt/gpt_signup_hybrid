"""Smoke test Change Password reauth only.

Reads CHPWD_SMOKE_COMBO=email|password|totp_secret and stops once the
password-change reauth page is reached. It does not submit a new password.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from gpt_signup_hybrid._nextauth_bootstrap import bootstrap_authorize_url  # noqa: E402
from gpt_signup_hybrid.change_password_phase import (  # noqa: E402
    _attach_network_debug_logging,
    _fill_mfa_if_needed,
    _safe_debug_url,
    _submit_reauth_form,
    _wait_proxy_ready,
)
from gpt_signup_hybrid.config import browser_proxy_config, load_settings  # noqa: E402
from gpt_signup_hybrid.web.manager import get_manager  # noqa: E402


def _log(msg: str) -> None:
    print(msg, flush=True)


async def _login(page: Any, email: str, password: str, secret: str | None) -> None:
    device_id = str(uuid.uuid4())
    logging_id = str(uuid.uuid4())
    _attach_network_debug_logging(page, _log)

    _log("[smoke] loading chatgpt.com")
    await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
    authorize_url = await bootstrap_authorize_url(
        page,
        email=email,
        device_id=device_id,
        logging_id=logging_id,
    )
    await page.goto(authorize_url, wait_until="domcontentloaded")
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
        raise RuntimeError(f"Login password field not found. URL: {page.url}")

    await pwd_input.click(force=True)
    await pwd_input.fill("")
    await pwd_input.type(password, delay=40)
    for btn in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Log in")'):
        try:
            await page.click(btn, timeout=3000)
            _log("[smoke] login password submitted")
            break
        except Exception:
            continue

    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        url = page.url
        if "chatgpt.com" in url and "auth.openai.com" not in url:
            break
        if "mfa-challenge" in url:
            _log("[smoke] MFA challenge detected")
            await _fill_mfa_if_needed(page, secret, _log, step="smoke-login")
            break
        await asyncio.sleep(1.0)

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if "chatgpt.com" in page.url and "auth.openai.com" not in page.url:
            _log(f"[smoke] login OK: {_safe_debug_url(page.url)}")
            return
        await asyncio.sleep(1.0)
    raise RuntimeError(f"Login did not finish. URL: {page.url}")


async def _reauth_only(ctx: Any, page: Any, email: str, proxy: str | None) -> None:
    _log("[smoke] triggering password-change reauth")
    reauth_form = await page.evaluate(
        """
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
""",
        email,
    )
    action_url = (reauth_form or {}).get("action") or ""
    csrf_token = (reauth_form or {}).get("csrfToken") or ""
    if not action_url or not csrf_token:
        raise RuntimeError(f"Unexpected reauth form data: {reauth_form}")

    _log(f"[smoke] submitting reauth form: {_safe_debug_url(action_url)}")
    await _submit_reauth_form(ctx, page, action_url, csrf_token, _log, proxy)
    await asyncio.sleep(2.0)
    _log(f"[smoke] after reauth: {_safe_debug_url(page.url)}")
    if "/log-in/password" not in page.url:
        raise RuntimeError(f"Expected /log-in/password, got: {page.url}")


async def _main() -> int:
    combo = os.environ.get("CHPWD_SMOKE_COMBO", "").strip()
    if not combo:
        print("Missing CHPWD_SMOKE_COMBO", file=sys.stderr)
        return 2
    parts = [p.strip() for p in combo.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        print("CHPWD_SMOKE_COMBO must be email|password|totp_secret", file=sys.stderr)
        return 2

    email = parts[0]
    password = parts[1]
    secret = parts[2] if len(parts) >= 3 and parts[2] else None
    settings = load_settings()
    proxy = os.environ.get("CHPWD_SMOKE_PROXY") or get_manager().proxy
    proxy_kwargs: dict[str, Any] = {}
    if proxy:
        proxy_kwargs["proxy"] = browser_proxy_config(proxy)
        await _wait_proxy_ready(proxy, _log)
        _log("[smoke] browser proxy configured")

    profile_dir = settings.profiles_dir / f"smoke_chpwd_{uuid.uuid4().hex[:8]}"
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True)

    from camoufox.async_api import AsyncCamoufox
    from camoufox.utils import Screen

    w, h = settings.browser_viewport_width, settings.browser_viewport_height
    chrome_h = 85
    ctx_mgr = AsyncCamoufox(
        headless=settings.browser_headless,
        persistent_context=True,
        user_data_dir=str(profile_dir),
        viewport={"width": w, "height": h},
        screen=Screen(min_width=w, max_width=w, min_height=h + chrome_h, max_height=h + chrome_h),
        config={
            "window.innerWidth": w,
            "window.innerHeight": h,
            "window.outerWidth": w,
            "window.outerHeight": h + chrome_h,
            "screen.width": w,
            "screen.height": h + chrome_h,
            "screen.availWidth": w,
            "screen.availHeight": h + chrome_h,
        },
        **proxy_kwargs,
    )

    ctx = await ctx_mgr.__aenter__()
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await _login(page, email, password, secret)
        await _reauth_only(ctx, page, email, proxy)
    finally:
        await ctx_mgr.__aexit__(None, None, None)
        shutil.rmtree(profile_dir, ignore_errors=True)

    print("OK smoke change password reauth only")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
