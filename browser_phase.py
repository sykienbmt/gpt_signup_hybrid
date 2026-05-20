"""Phase 1: Browser signup — register (email+pass) → OTP → /about-you → session.

Flow (theo HAR mới):
  1. chatgpt.com → bootstrap NextAuth (csrf + signin/openai) → authorize URL
  2. Navigate authorize → /email-verification page load
  3. Click "Continue with password" → /create-account/password
  4. Fill password → submit → POST /api/accounts/user/register {username, password}
  5. Server trigger OTP (GET /email-otp/send) → redirect /email-verification (OTP form)
  6. Poll OTP → submit → POST /email-otp/validate
  7. /about-you → fill name+age → POST /create_account
  8. Đợi session-token cookie (đã login)
  9. Exfil cookies → BrowserHandoff

Retry (account đã tồn tại):
  - Register trả lỗi "already exists" → fallback OTP-only login
  - HOẶC: OTP → login → chatgpt.com

Kết quả: BrowserHandoff đủ context để Phase 2 extract session/access_token.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings, ensure_runtime_dirs, prepare_profile_dir
from .mail_providers import MailProvider
from .models import BrowserHandoff, SignupRequest


class BrowserPhaseError(Exception):
    """Phase 1 failed."""


# Cookies bắt buộc cho Phase 2 (chatgpt.com session).
_REQUIRED_AUTH_COOKIES = (
    "oai-did",
    "__cf_bm",
    "cf_clearance",
)


# ─────────────────────────────────────────────────────────────────────
# JS helpers
# ─────────────────────────────────────────────────────────────────────

_NEXTAUTH_BOOTSTRAP_JS = r"""
async ({email, deviceId, loggingId}) => {
    const params = new URLSearchParams({
        'prompt': 'login',
        'ext-oai-did': deviceId,
        'auth_session_logging_id': loggingId,
        'ext-passkey-client-capabilities': '0100',
        'screen_hint': 'login_or_signup',
        'login_hint': email,
    }).toString();

    const csrfRes = await fetch('/api/auth/csrf', {credentials: 'include'});
    if (!csrfRes.ok) throw new Error('csrf HTTP ' + csrfRes.status);
    const csrfData = await csrfRes.json();
    const csrfToken = csrfData.csrfToken;
    if (!csrfToken) throw new Error('csrf token missing');

    const body = new URLSearchParams({
        callbackUrl: 'https://chatgpt.com/',
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
    if (!signData.url) throw new Error('signin missing url: ' + JSON.stringify(signData));
    return signData.url;
}
"""

# JS: POST /api/accounts/user/register trên auth.openai.com page context
_REGISTER_USER_JS = r"""
async ({username, password}) => {
    const res = await fetch('/api/accounts/user/register', {
        method: 'POST',
        credentials: 'include',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Origin': window.location.origin,
            'Referer': window.location.origin + '/create-account/password',
        },
        body: JSON.stringify({username, password}),
    });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = text; }
    return {status: res.status, body};
}
"""

# JS: fill /about-you (Sentinel monitor form interactions)
_PAGE_CREATE_ACCOUNT_JS = r"""
async ({name, birthdate}) => {
    const res = await fetch('/api/accounts/create_account', {
        method: 'POST',
        credentials: 'include',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({name, birthdate}),
    });
    const text = await res.text();
    let body = null;
    try { body = JSON.parse(text); } catch { body = text; }
    return {status: res.status, body};
}
"""


# ─────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────

async def _bootstrap_oauth_url(page, *, email: str, device_id: str, logging_id: str, log) -> str:
    """Gọi /api/auth/csrf + POST /signin/openai trong page context chatgpt.com."""
    log("[browser] bootstrapping NextAuth (csrf + signin)...")
    url = await page.evaluate(
        _NEXTAUTH_BOOTSTRAP_JS,
        {"email": email, "deviceId": device_id, "loggingId": logging_id},
    )
    if not isinstance(url, str) or "auth.openai.com" not in url:
        raise BrowserPhaseError(f"bootstrap returned bad URL: {url!r}")
    log(f"[browser] authorize URL ready: {url[:120]}...")
    return url


async def _register_with_password(page, *, email: str, password: str, log) -> str:
    """Đăng ký account bằng POST /api/accounts/user/register trên auth.openai.com.

    Flow:
      1. Click "Continue with password" (nếu cần)
      2. POST /api/accounts/user/register {username, password}
      3. GET continue_url (/email-otp/send) → trigger OTP

    Returns: "otp_sent" (success) hoặc raise error.
    """
    # Click "Continue with password" button nếu đang ở /email-verification
    try:
        pwd_btn = page.locator('button:has-text("password"), a:has-text("password")')
        if await pwd_btn.count() > 0:
            await pwd_btn.first.click(timeout=3000)
            log("[browser] clicked 'Continue with password'")
            # Đợi page navigate tới /create-account/password (SPA)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if "password" in page.url:
                    break
                # Hoặc password input visible
                try:
                    pwd_input = page.locator('input[type="password"]').first
                    if await pwd_input.is_visible(timeout=500):
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.3)
            log(f"[browser] page ready: {page.url.split('?')[0]}")
    except Exception:
        pass

    await asyncio.sleep(0.5)

    # Check: nếu page ở /log-in/password → account đã tồn tại → login thay vì register
    if "log-in" in page.url:
        log("[browser] account exists → login with password")
        # Fill password form
        pwd_input = None
        for sel in ('input[type="password"]', 'input[name="password"]'):
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=2000):
                    pwd_input = loc
                    break
            except Exception:
                continue
        if pwd_input:
            await pwd_input.click(force=True, timeout=3000)
            await pwd_input.fill("")
            await pwd_input.type(password, delay=50)
            await asyncio.sleep(0.3)
            for btn in ('button[type="submit"]', 'button:has-text("Continue")'):
                try:
                    await page.click(btn, timeout=3000)
                    break
                except Exception:
                    continue
            log("[browser] submitted login password")
            return "login"
        raise BrowserPhaseError(f"login page but no password input. URL: {page.url}")

    # POST /api/accounts/user/register
    log(f"[browser] POST /api/accounts/user/register (email={email})")
    result = await page.evaluate(_REGISTER_USER_JS, {"username": email, "password": password})

    if not isinstance(result, dict):
        raise BrowserPhaseError(f"register unexpected result: {result}")

    status = result.get("status")
    body = result.get("body") or {}

    if status == 200:
        # Success → navigate tới continue_url để trigger OTP send
        continue_url = None
        if isinstance(body, dict):
            continue_url = body.get("continue_url")
        log(f"[browser] register OK → continue_url={continue_url}")

        if continue_url:
            if continue_url.startswith("/"):
                continue_url = f"https://auth.openai.com{continue_url}"
            await page.goto(continue_url, wait_until="domcontentloaded")
            log("[browser] OTP send triggered")
        # Đợi 1s để page settle — otp_started_at sẽ được set SAU đây bởi caller
        await asyncio.sleep(1.0)
        return "otp_sent"

    # Error cases
    body_str = json.dumps(body) if isinstance(body, dict) else str(body or "")

    # Account already exists → fallback: submit OTP trực tiếp (email đã gửi)
    if "already" in body_str.lower() or "exists" in body_str.lower() or status == 409:
        log(f"[browser] register: account already exists (HTTP {status}) — fallback OTP login")
        return "already_exists"

    raise BrowserPhaseError(f"register failed HTTP {status}: {body_str[:200]}")


async def _wait_otp_form(page, *, timeout_seconds: float, log) -> str:
    """Đợi OTP form xuất hiện. Return selector."""
    selectors = (
        'input[name="code"]',
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
    )
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=int(timeout_seconds * 1000))
            log(f"[browser] OTP input ready ({sel})")
            return sel
        except Exception:
            continue
    raise BrowserPhaseError(f"OTP input không xuất hiện sau {timeout_seconds}s. URL: {page.url}")


async def _submit_otp(page, *, otp_code: str, otp_selector: str, log) -> None:
    """Fill OTP + click submit. Fallback: gọi validate API trực tiếp."""
    log(f"[browser] typing OTP {otp_code}")
    await page.fill(otp_selector, otp_code)
    for btn in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Verify")'):
        try:
            await page.click(btn, timeout=2000)
            log(f"[browser] clicked {btn}")
            return
        except Exception:
            continue
    # Fallback: gọi validate API trực tiếp
    log("[browser] no submit button — calling email-otp/validate API directly")
    await page.evaluate(
        """async (code) => {
            await fetch('/api/accounts/email-otp/validate', {
                method: 'POST',
                credentials: 'include',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                body: JSON.stringify({code}),
            });
        }""",
        otp_code,
    )


async def _wait_after_login(page, *, timeout_seconds: float, log) -> str:
    """Sau submit login password, đợi:
    - chatgpt.com (login OK, không cần OTP)
    - /email-verification (cần OTP)
    - error
    Returns: 'chatgpt' hoặc 'otp_required'.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        cur = page.url
        if "chatgpt.com" in cur and "auth.openai.com" not in cur and "/auth/error" not in cur:
            log("[browser] login OK — redirected to chatgpt.com")
            return "chatgpt"
        if "/email-verification" in cur or "/email-otp" in cur:
            log("[browser] login requires OTP")
            return "otp_required"
        if "/auth/error" in cur:
            raise BrowserPhaseError(f"login error page: {cur}")
        # Detect OTP form xuất hiện (SPA case)
        try:
            otp_input = page.locator('input[name="code"], input[autocomplete="one-time-code"]').first
            if await otp_input.is_visible(timeout=300):
                log("[browser] login OTP form detected (SPA)")
                return "otp_required"
        except Exception:
            pass
        # Detect login error (sai password)
        try:
            err_el = page.locator('[role="alert"], [class*="error"]').first
            err_text = await err_el.text_content(timeout=300)
            if err_text and ("incorrect" in err_text.lower() or "wrong password" in err_text.lower() or "invalid" in err_text.lower()):
                raise BrowserPhaseError(f"login error: {err_text.strip()}")
        except BrowserPhaseError:
            raise
        except Exception:
            pass
        await asyncio.sleep(0.5)
    raise BrowserPhaseError(f"timeout {timeout_seconds}s after login submit. URL: {page.url}")


async def _detect_screen(page) -> str:
    """Detect màn hình hiện tại từ URL + DOM. Return 1 trong:
      - 'chatgpt'         : đã login xong, page ở chatgpt.com
      - 'about_you'       : form name+age (auth.openai.com/about-you)
      - 'otp'             : OTP input visible (/email-verification or SPA)
      - 'password_create' : /create-account/password (form set password mới)
      - 'password_login'  : /log-in/password (form login với account đã tồn tại)
      - 'continue'        : /email-verification trang chọn 'Continue with password'
      - 'auth_error'      : page lỗi /auth/error
      - 'unknown'         : không nhận diện được
    """
    cur = page.url
    if "/auth/error" in cur:
        return "auth_error"
    if "chatgpt.com" in cur and "auth.openai.com" not in cur:
        return "chatgpt"
    if "auth.openai.com/about-you" in cur:
        return "about_you"
    # Nội dung SPA có thể đã render /about-you mà URL chưa đổi
    try:
        name_el = page.locator('input[name="name"], input[autocomplete="name"]').first
        if await name_el.is_visible(timeout=200):
            return "about_you"
    except Exception:
        pass
    if "/create-account/password" in cur:
        return "password_create"
    if "/log-in/password" in cur:
        return "password_login"
    # /email-verification: ƯU TIÊN button "Continue with password" để bắt buộc set password
    # Nếu cả OTP input và password button cùng visible, password button thắng
    if "/email-verification" in cur or "/email-otp" in cur:
        try:
            pwd_btn = page.locator('button:has-text("Continue with password"), a:has-text("Continue with password")').first
            if await pwd_btn.is_visible(timeout=300):
                return "continue"
        except Exception:
            pass
    # OTP form (URL có thể là /email-verification hoặc /email-otp)
    try:
        otp_input = page.locator('input[name="code"], input[autocomplete="one-time-code"]').first
        if await otp_input.is_visible(timeout=200):
            return "otp"
    except Exception:
        pass
    if "/email-verification" in cur or "/email-otp" in cur:
        return "otp"  # fallback: chỉ có OTP form, không có password button
    return "unknown"


async def _drive_signup_flow(
    *, ctx, page, request, mail_provider, callback_holder, otp_started_at, log,
    overall_timeout: float = 240.0,
) -> tuple[str, float]:
    """State machine: check URL/DOM hiện tại, dispatch handler tương ứng.
    Lặp đến khi đến được chatgpt.com (có session) hoặc gặp lỗi không phục hồi.

    Returns: (callback_url, otp_seconds).
    """
    deadline = time.monotonic() + overall_timeout
    otp_seconds_total = 0.0
    otp_already_polled = False  # tránh poll OTP nhiều lần trong cùng batch
    register_attempted = False
    login_attempted = False
    continue_clicked = False
    otp_submitted = False
    tried_codes: set[str] = set()  # codes đã submit + bị reject
    last_screen = None
    same_screen_count = 0

    while time.monotonic() < deadline:
        screen = await _detect_screen(page)
        if screen != last_screen:
            log(f"[flow] screen={screen} url={page.url.split('?')[0]}")
            last_screen = screen
            same_screen_count = 0
        else:
            same_screen_count += 1

        if screen == "chatgpt":
            await _wait_chatgpt_session(ctx, page, timeout_seconds=30.0, log=log)
            return callback_holder.get("url") or page.url, otp_seconds_total

        if screen == "auth_error":
            raise BrowserPhaseError(f"auth error page: {page.url}")

        if screen == "continue":
            if continue_clicked:
                # Đã click rồi mà page chưa chuyển → đợi thêm rồi retry detect
                await asyncio.sleep(1.0)
                continue
            try:
                pwd_btn = page.locator('button:has-text("Continue with password"), a:has-text("Continue with password")').first
                await pwd_btn.click(timeout=3000)
                log("[flow] clicked 'Continue with password'")
                continue_clicked = True
            except Exception as exc:
                log(f"[flow] click continue failed: {exc}")
            await asyncio.sleep(1.5)
            continue

        if screen == "password_create":
            if register_attempted:
                await asyncio.sleep(1.0)
                continue
            log(f"[flow] POST /api/accounts/user/register (email={request.email})")
            result = await page.evaluate(
                _REGISTER_USER_JS, {"username": request.email, "password": request.password},
            )
            register_attempted = True
            if not isinstance(result, dict):
                raise BrowserPhaseError(f"register unexpected result: {result}")
            status = result.get("status")
            body = result.get("body") or {}
            if status == 200:
                continue_url = body.get("continue_url") if isinstance(body, dict) else None
                log(f"[flow] register OK → continue_url={continue_url}")
                if continue_url:
                    if continue_url.startswith("/"):
                        continue_url = f"https://auth.openai.com{continue_url}"
                    await page.goto(continue_url, wait_until="domcontentloaded")
                await asyncio.sleep(1.0)
                continue
            body_str = json.dumps(body) if isinstance(body, dict) else str(body or "")
            if "already" in body_str.lower() or "exists" in body_str.lower() or status == 409:
                log("[flow] account already exists — page sẽ chuyển login")
                await asyncio.sleep(1.5)
                continue
            raise BrowserPhaseError(f"register failed HTTP {status}: {body_str[:200]}")

        if screen == "password_login":
            if login_attempted:
                await asyncio.sleep(1.0)
                continue
            log("[flow] login with password")
            pwd_input = None
            for sel in ('input[type="password"]', 'input[name="password"]'):
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible(timeout=2000):
                        pwd_input = loc
                        break
                except Exception:
                    continue
            if not pwd_input:
                raise BrowserPhaseError(f"login page but no password input. URL: {page.url}")
            await pwd_input.click(force=True, timeout=3000)
            await pwd_input.fill("")
            await pwd_input.type(request.password, delay=50)
            await asyncio.sleep(0.3)
            for btn in ('button[type="submit"]', 'button:has-text("Continue")'):
                try:
                    await page.click(btn, timeout=3000)
                    break
                except Exception:
                    continue
            log("[flow] submitted login password")
            login_attempted = True
            await asyncio.sleep(1.5)
            continue

        if screen == "otp":
            # Detect "incorrect code" error → resend email + poll code mới
            try:
                err_el = page.locator('[role="alert"], [class*="error"]').first
                err_text = await err_el.text_content(timeout=200)
                if err_text and any(k in err_text.lower() for k in ("incorrect", "wrong", "invalid", "expired")):
                    log(f"[flow] OTP rejected: {err_text.strip()[:80]} — resend email & poll lại")
                    # Click "Resend email" để trigger mail mới
                    try:
                        resend_btn = page.locator('button:has-text("Resend"), a:has-text("Resend")').first
                        await resend_btn.click(timeout=3000)
                        log("[flow] clicked 'Resend email'")
                    except Exception as exc:
                        log(f"[flow] resend button not found: {exc}")
                    # Reset state để poll code mới
                    otp_submitted = False
                    same_screen_count = 0
                    # Clear input
                    try:
                        otp_inp = page.locator('input[name="code"]').first
                        await otp_inp.fill("")
                    except Exception:
                        pass
                    await asyncio.sleep(2.0)
            except Exception:
                pass

            if otp_submitted:
                # Đã submit rồi, đợi page chuyển. Nếu stuck quá lâu → retry submit.
                if same_screen_count > 30:  # ~15s ở cùng OTP screen sau submit
                    log("[flow] OTP screen vẫn ở đây sau submit — thử click submit lại")
                    for btn in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Verify")'):
                        try:
                            await page.click(btn, timeout=2000)
                            break
                        except Exception:
                            continue
                    same_screen_count = 0
                # Stuck quá 60 lần (~30s) → giả định OTP code sai dù không có error visible → re-poll
                if same_screen_count > 60:
                    log("[flow] OTP stuck >30s không có error visible — re-poll code mới")
                    otp_submitted = False
                    same_screen_count = 0
                    try:
                        otp_inp = page.locator('input[name="code"]').first
                        await otp_inp.fill("")
                    except Exception:
                        pass
                await asyncio.sleep(0.5)
                continue
            # Đợi OTP input fully ready
            try:
                otp_selector = await _wait_otp_form(page, timeout_seconds=10.0, log=log)
            except BrowserPhaseError:
                await asyncio.sleep(0.5)
                continue
            await asyncio.sleep(1.0)
            # Reset timestamp khi sắp poll — bỏ qua code cũ trước thời điểm này
            poll_started = datetime.now(timezone.utc).replace(microsecond=0)
            t_otp = time.monotonic()
            recipient = request.source_email or request.email
            log(f"[flow] polling OTP (recipient={recipient}) since {poll_started.isoformat()}")
            
            # Poll OTP, skip codes đã thử
            while True:
                otp_code = await mail_provider.poll_otp(
                    recipient=recipient,
                    started_at=poll_started,
                    timeout_seconds=request.otp_timeout_seconds,
                    poll_interval_seconds=request.otp_poll_interval_seconds,
                    log=log,
                )
                if otp_code not in tried_codes:
                    break
                log(f"[flow] OTP={otp_code} đã thử rồi, skip và poll tiếp")
                await asyncio.sleep(request.otp_poll_interval_seconds)
                # Nếu poll_otp timeout → raise, không loop vô hạn
                if time.monotonic() - t_otp > request.otp_timeout_seconds:
                    raise BrowserPhaseError(f"OTP timeout {request.otp_timeout_seconds}s, chỉ nhận được codes cũ")
            
            otp_seconds_total += time.monotonic() - t_otp
            log(f"[flow] OTP={otp_code} got in {time.monotonic() - t_otp:.1f}s")
            tried_codes.add(otp_code)
            await _submit_otp(page, otp_code=otp_code, otp_selector=otp_selector, log=log)
            otp_submitted = True
            otp_already_polled = True
            await asyncio.sleep(2.0)
            continue

        if screen == "about_you":
            try:
                await _wait_oai_sc(ctx, timeout_seconds=15, log=log)
            except BrowserPhaseError:
                pass  # cookie có thể chưa cần thiết, thử fill xem có pass không
            callback_url = await _fill_about_you(
                page, name=request.name, birthdate=request.birthdate,
                timeout_seconds=60.0, log=log,
            )
            # Sau /about-you có thể vẫn còn step (rare), tiếp tục loop để chờ chatgpt.com
            await _wait_chatgpt_session(ctx, page, timeout_seconds=60.0, log=log)
            return callback_url, otp_seconds_total

        # screen == 'unknown' → đợi page settle
        await asyncio.sleep(0.7)

    raise BrowserPhaseError(f"flow timeout {overall_timeout}s. last URL: {page.url}, last screen: {last_screen}")


async def _handle_login_after_password(
    *, ctx, page, request, mail_provider, callback_holder, log,
) -> tuple[str, float]:
    """Sau khi submit login password, xử lý cả 2 case:
    - Login thẳng → chatgpt.com
    - Cần OTP → poll OTP → submit → /about-you HOẶC chatgpt.com
    Returns: (callback_url, otp_seconds).
    """
    otp_seconds = 0.0
    login_branch = await _wait_after_login(page, timeout_seconds=20.0, log=log)
    if login_branch == "chatgpt":
        await _wait_chatgpt_session(ctx, page, timeout_seconds=30.0, log=log)
        return callback_holder.get("url") or page.url, otp_seconds

    # Cần OTP cho login (hoặc account chưa hoàn thành onboarding)
    otp_selector = await _wait_otp_form(page, timeout_seconds=15.0, log=log)
    await asyncio.sleep(2.0)
    otp_started_at = datetime.now(timezone.utc).replace(microsecond=0)

    t_otp = time.monotonic()
    recipient = request.source_email or request.email
    log(f"[browser] polling OTP for login (recipient={recipient})")
    otp_code = await mail_provider.poll_otp(
        recipient=recipient,
        started_at=otp_started_at,
        timeout_seconds=request.otp_timeout_seconds,
        poll_interval_seconds=request.otp_poll_interval_seconds,
        log=log,
    )
    otp_seconds = time.monotonic() - t_otp
    log(f"[browser] login OTP={otp_code} in {otp_seconds:.1f}s")
    await _submit_otp(page, otp_code=otp_code, otp_selector=otp_selector, log=log)

    # Sau OTP có 2 case:
    # 1. /about-you (account chưa onboard) → fill name+age → callback
    # 2. chatgpt.com (login bình thường) → wait session-token
    otp_branch = await _wait_after_otp(page, timeout_seconds=60.0, log=log)
    if otp_branch == "signup":
        await _wait_oai_sc(ctx, timeout_seconds=15, log=log)
        callback_url = await _fill_about_you(
            page,
            name=request.name,
            birthdate=request.birthdate,
            timeout_seconds=30.0,
            log=log,
        )
    else:
        callback_url = callback_holder.get("url") or page.url

    await _wait_chatgpt_session(ctx, page, timeout_seconds=60.0, log=log)
    return callback_url, otp_seconds


async def _wait_after_otp(page, *, timeout_seconds: float, log) -> str:
    """Sau submit OTP, đợi navigation: /about-you (signup) hoặc chatgpt.com (login).

    Returns: "signup" hoặc "login".
    """
    deadline = time.monotonic() + timeout_seconds
    otp_resubmit_attempted = False
    while time.monotonic() < deadline:
        cur = page.url
        if "auth.openai.com/about-you" in cur:
            log("[browser] reached /about-you (signup)")
            return "signup"
        if "chatgpt.com" in cur and "auth.openai.com" not in cur and "/auth/error" not in cur:
            log("[browser] redirected to chatgpt.com (login — account exists)")
            return "login"
        if "auth/error" in cur:
            raise BrowserPhaseError(f"error page: {cur}")
        # SPA case: URL vẫn /email-verification nhưng form /about-you đã render
        try:
            name_el = page.locator('input[name="name"], input[autocomplete="name"]').first
            if await name_el.is_visible(timeout=300):
                log("[browser] detected /about-you form (SPA, URL unchanged)")
                return "signup"
        except Exception:
            pass
        # Check OTP error message (wrong code)
        try:
            err_el = page.locator('[role="alert"], [class*="error"]').first
            err_text = await err_el.text_content(timeout=300)
            if err_text and ("wrong" in err_text.lower() or "invalid" in err_text.lower() or "incorrect" in err_text.lower()):
                raise BrowserPhaseError(f"OTP wrong code: {err_text.strip()}")
        except BrowserPhaseError:
            raise
        except Exception:
            pass
        # Check: OTP form still visible nhưng submit button bị disabled (đang processing)
        # Hoặc nếu 15s trôi qua và vẫn stuck ở /email-verification → thử click submit lại
        elapsed = time.monotonic() - (deadline - timeout_seconds)
        if elapsed > 15.0 and not otp_resubmit_attempted:
            try:
                otp_input = page.locator('input[name="code"]').first
                if await otp_input.is_visible(timeout=500):
                    val = await otp_input.input_value()
                    if val and len(val) == 6:
                        log("[browser] OTP form still visible after 15s — retrying submit")
                        for btn in ('button[type="submit"]', 'button:has-text("Continue")'):
                            try:
                                await page.click(btn, timeout=2000)
                                log(f"[browser] re-clicked {btn}")
                                break
                            except Exception:
                                continue
                        otp_resubmit_attempted = True
            except Exception:
                pass
        await asyncio.sleep(0.5)
    raise BrowserPhaseError(f"timeout {timeout_seconds}s after OTP submit. URL: {page.url}")


async def _fill_about_you(page, *, name: str, birthdate: str, timeout_seconds: float, log) -> str:
    """Điền form /about-you (name + age), submit, return callback URL."""
    log(f"[browser] /about-you: fill name={name!r}")

    # Capture callback URL via request listener
    callback_holder: dict[str, str] = {}

    def _on_req(request):
        url = request.url
        if "chatgpt.com/api/auth/callback/openai" in url and "code=" in url:
            callback_holder.setdefault("url", url)

    page.on("request", _on_req)
    try:
        # Name input
        name_input = None
        for sel in ('input[name="name"]', 'input[autocomplete="name"]', 'input[id*="name" i]'):
            try:
                await page.wait_for_selector(sel, state="visible", timeout=5000)
                name_input = sel
                break
            except Exception:
                continue
        if not name_input:
            raise BrowserPhaseError("không tìm thấy name input trên /about-you")

        await page.click(name_input, force=True, timeout=3000)
        await page.fill(name_input, "")
        await page.type(name_input, name, delay=80)
        await asyncio.sleep(0.2)

        # Age (parse from birthdate)
        try:
            year, month, day = birthdate.split("-")
            today = datetime.utcnow()
            age = today.year - int(year) - ((today.month, today.day) < (int(month), int(day)))
        except ValueError as exc:
            raise BrowserPhaseError(f"birthdate format sai: {birthdate}") from exc

        # Try date input first, fallback to age number input
        date_input = None
        try:
            date_input = await page.wait_for_selector('input[type="date"]', state="visible", timeout=1500)
        except Exception:
            pass

        if date_input:
            await page.fill('input[type="date"]', birthdate)
            log(f"[browser] filled birthday={birthdate}")
        else:
            age_input = None
            for sel in ('input[name="age"]', 'input[type="number"]', 'input[inputmode="numeric"]'):
                try:
                    await page.wait_for_selector(sel, state="visible", timeout=1500)
                    age_input = sel
                    break
                except Exception:
                    continue
            if age_input:
                await page.click(age_input, force=True, timeout=3000)
                await page.fill(age_input, "")
                await page.type(age_input, str(age), delay=120)
                log(f"[browser] typed age={age}")
            else:
                await page.keyboard.press("Tab")
                await asyncio.sleep(0.4)
                await page.keyboard.type(str(age), delay=120)
                log(f"[browser] Tab + typed age={age}")

        await asyncio.sleep(0.3)

        # Submit
        for btn in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Agree")'):
            try:
                await page.click(btn, timeout=2000)
                log(f"[browser] clicked {btn}")
                break
            except Exception:
                continue

        # Đợi callback URL hoặc navigate đến chatgpt.com
        deadline = time.monotonic() + timeout_seconds
        retry_submit_at = time.monotonic() + 10.0  # nếu stuck /about-you > 10s → thử submit lại
        submitted_again = False
        while time.monotonic() < deadline:
            if "url" in callback_holder:
                log(f"[browser] callback URL captured")
                return callback_holder["url"]
            cur = page.url
            if "auth/error" in cur:
                raise BrowserPhaseError(f"error page: {cur}")
            # Nếu page đã navigate ra khỏi /about-you → chatgpt.com
            if "chatgpt.com" in cur:
                log(f"[browser] navigated to chatgpt.com (no explicit callback)")
                return callback_holder.get("url") or cur
            # Detect consent/modal buttons mới
            for accept_btn in (
                'button:has-text("Okay")',
                'button:has-text("I agree")',
                'button:has-text("Accept")',
                'button:has-text("Got it")',
                'button:has-text("Let")',
            ):
                try:
                    btn_el = page.locator(accept_btn).first
                    if await btn_el.is_visible(timeout=200):
                        await btn_el.click(timeout=2000)
                        log(f"[browser] clicked modal button: {accept_btn}")
                        break
                except Exception:
                    continue
            # Retry submit nếu vẫn stuck /about-you
            if not submitted_again and time.monotonic() > retry_submit_at and "about-you" in cur:
                submitted_again = True
                log("[browser] still on /about-you after 10s — retrying submit")
                for btn in ('button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Agree")'):
                    try:
                        await page.click(btn, timeout=2000)
                        log(f"[browser] re-clicked {btn}")
                        break
                    except Exception:
                        continue
            await asyncio.sleep(0.5)

        # Fallback: page.url nếu đã navigate qua callback hoặc chatgpt.com
        if "chatgpt.com" in page.url:
            return callback_holder.get("url") or page.url
        if "callback" in page.url and "code=" in page.url:
            return page.url

        raise BrowserPhaseError(f"timeout {timeout_seconds}s waiting callback URL. URL: {page.url}")
    finally:
        try:
            page.remove_listener("request", _on_req)
        except Exception:
            pass


async def _wait_chatgpt_session(ctx, page, *, timeout_seconds: float, log) -> None:
    """Đợi cookie session-token xuất hiện trên chatgpt.com."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        cookies = await ctx.cookies("https://chatgpt.com/")
        names = {c["name"] for c in cookies}
        has_session = (
            "__Secure-next-auth.session-token" in names
            or "__Secure-next-auth.session-token.0" in names
        )
        if has_session and "_account" in names:
            log(f"[browser] chatgpt session ready ({len(cookies)} cookies)")
            await asyncio.sleep(0.3)
            return
        await asyncio.sleep(0.5)
    raise BrowserPhaseError(f"timeout {timeout_seconds}s waiting session-token. URL: {page.url}")


async def _wait_oai_sc(ctx, *, timeout_seconds: float, log) -> None:
    """Đợi cookie oai-sc (Sentinel SDK fired)."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        cookies = await ctx.cookies("https://auth.openai.com/")
        if any(c["name"] == "oai-sc" for c in cookies):
            log("[browser] sentinel cookie oai-sc ready")
            return
        await asyncio.sleep(0.5)
    raise BrowserPhaseError(f"timeout {timeout_seconds}s waiting oai-sc")



def _extract_state_from_authorize(url: str) -> str | None:
    """Parse state query param từ authorize URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    return qs["state"][0] if "state" in qs and qs["state"] else None


async def _extract_state_from_url(page, *, log) -> str | None:
    """Lấy state từ navigation history."""
    try:
        entries = await page.evaluate(
            "() => performance.getEntriesByType('navigation').concat(performance.getEntriesByType('resource'))"
            ".map(e => e.name).filter(u => u.includes('state='))"
        )
        for entry in entries or []:
            parsed = urlparse(entry)
            qs = parse_qs(parsed.query)
            if "state" in qs and qs["state"][0]:
                return qs["state"][0]
    except Exception as exc:
        log(f"[browser] state extract failed: {exc}")
    return None


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────

async def run_browser_phase(
    *,
    request: SignupRequest,
    settings: Settings,
    mail_provider: MailProvider,
    otp_started_at: datetime,
    log,
) -> tuple[BrowserHandoff, float]:
    """Phase 1: browser signup + set password post-login.

    Returns: (handoff, otp_seconds).
    """
    engine = settings.browser_engine or "chrome"
    job_id = f"hybrid_{uuid.uuid4().hex[:10]}"

    # Profile
    if engine == "camoufox":
        profile_dir = settings.profiles_dir / f"camoufox_{job_id}"
        template_dir = settings.browser_camoufox_profile_dir
    else:
        profile_dir = settings.profile_dir_for(job_id)
        template_dir = settings.browser_profile_template_dir

    ensure_runtime_dirs(settings, extra=(profile_dir,))
    prepare_profile_dir(
        profile_dir=profile_dir,
        template_dir=template_dir,
        use_template=request.profile_template,
    )

    # HAR capture
    har_kwargs: dict[str, Any] = {}
    if request.har_capture:
        har_dir = settings.runtime_dir / "har_hybrid"
        har_dir.mkdir(parents=True, exist_ok=True)
        har_path = har_dir / f"hybrid-{datetime.now():%Y%m%d-%H%M%S}-{job_id}.har"
        har_kwargs["record_har_path"] = str(har_path)
        har_kwargs["record_har_content"] = "embed"
        har_kwargs["record_har_mode"] = "full"
        log(f"[browser] HAR capture → {har_path}")

    device_id = str(uuid.uuid4())
    logging_id = str(uuid.uuid4())
    log(f"[browser] device_id={device_id} logging_id={logging_id}")

    w, h = settings.browser_viewport_width, settings.browser_viewport_height
    viewport = {"width": w, "height": h}

    proxy_kwargs: dict[str, Any] = {}
    if request.proxy:
        proxy_kwargs["proxy"] = {"server": request.proxy}

    state_param: str | None = None
    handoff_cookies: list[dict[str, Any]] = []
    authorize_url: str | None = None
    otp_seconds = 0.0

    if engine == "camoufox":
        from camoufox.async_api import AsyncCamoufox
        from camoufox.utils import Screen as _Screen

        chrome_h = 85
        extra_config: dict = {"fonts:spacing_seed": 0} if request.off_font else {}
        extra_config["window.innerWidth"] = w
        extra_config["window.innerHeight"] = h
        extra_config["window.outerWidth"] = w
        extra_config["window.outerHeight"] = h + chrome_h
        extra_config["screen.width"] = w
        extra_config["screen.height"] = h + chrome_h
        extra_config["screen.availWidth"] = w
        extra_config["screen.availHeight"] = h + chrome_h
        fixed_screen = _Screen(
            min_width=w, max_width=w, min_height=h + chrome_h, max_height=h + chrome_h
        )

        cf = AsyncCamoufox(
            headless=request.headless,
            persistent_context=True,
            user_data_dir=str(profile_dir),
            viewport=viewport,
            screen=fixed_screen,
            ignore_https_errors=True,
            config=extra_config,
            **proxy_kwargs,
            **har_kwargs,
        )
        ctx = await cf.__aenter__()

        # Capture callback URL
        callback_holder: dict[str, str] = {}

        def _capture_callback(req) -> None:
            url = req.url
            if "chatgpt.com/api/auth/callback/openai" in url and "code=" in url:
                callback_holder.setdefault("url", url)

        ctx.on("request", _capture_callback)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            # ── Step 1: bootstrap ──
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            log("[browser] chatgpt.com loaded")
            authorize_url = await _bootstrap_oauth_url(
                page, email=request.email, device_id=device_id, logging_id=logging_id, log=log,
            )

            # ── Step 2: navigate authorize → /email-verification ──
            await page.goto(authorize_url, wait_until="domcontentloaded")
            await asyncio.sleep(1.0)

            # ── Step 3: state-machine driver ──
            # Auto-detect screen (password_create/password_login/otp/about_you/chatgpt)
            # và dispatch handler tương ứng cho đến khi đến chatgpt.com
            callback_url, otp_seconds = await _drive_signup_flow(
                ctx=ctx, page=page, request=request,
                mail_provider=mail_provider,
                callback_holder=callback_holder,
                otp_started_at=otp_started_at,
                log=log,
            )

            # ── Exfil ──
            state_param = (
                _extract_state_from_authorize(authorize_url)
                or await _extract_state_from_url(page, log=log)
            )
            handoff_cookies = await ctx.cookies()

        finally:
            try:
                ctx.remove_listener("request", _capture_callback)
            except Exception:
                pass
            if request.keep_browser_open and not request.headless:
                log("[browser] debug: giữ browser mở — cancel job để đóng")
            else:
                try:
                    await cf.__aexit__(None, None, None)
                except Exception:
                    pass
                shutil.rmtree(profile_dir, ignore_errors=True)

    else:
        # Chromium fallback (playwright)
        from playwright.async_api import async_playwright

        playwright = await async_playwright().start()
        try:
            channel = settings.browser_channel or None
            ctx = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=request.headless,
                channel=channel,
                viewport=viewport,
                ignore_https_errors=True,
                **proxy_kwargs,
                **har_kwargs,
            )

            callback_holder: dict[str, str] = {}

            def _capture_callback(req) -> None:
                url = req.url
                if "chatgpt.com/api/auth/callback/openai" in url and "code=" in url:
                    callback_holder.setdefault("url", url)

            ctx.on("request", _capture_callback)

            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            log("[browser] chatgpt.com loaded")
            authorize_url = await _bootstrap_oauth_url(
                page, email=request.email, device_id=device_id, logging_id=logging_id, log=log,
            )

            # ── Step 2: navigate authorize → /email-verification ──
            await page.goto(authorize_url, wait_until="domcontentloaded")
            await asyncio.sleep(1.0)

            # ── Step 3: state-machine driver ──
            # Auto-detect screen (password_create/password_login/otp/about_you/chatgpt)
            # và dispatch handler tương ứng cho đến khi đến chatgpt.com
            callback_url, otp_seconds = await _drive_signup_flow(
                ctx=ctx, page=page, request=request,
                mail_provider=mail_provider,
                callback_holder=callback_holder,
                otp_started_at=otp_started_at,
                log=log,
            )

            # ── Exfil ──
            state_param = (
                _extract_state_from_authorize(authorize_url)
                or await _extract_state_from_url(page, log=log)
            )
            handoff_cookies = await ctx.cookies()

            if not (request.keep_browser_open and not request.headless):
                await ctx.close()
        finally:
            if request.keep_browser_open and not request.headless:
                log("[browser] debug: giữ browser mở — cancel job để đóng")
            else:
                await playwright.stop()
                shutil.rmtree(profile_dir, ignore_errors=True)

    if not state_param:
        raise BrowserPhaseError("không lấy được oauth state từ navigation history")

    # Sanity check required cookies
    auth_cookies = {c["name"] for c in handoff_cookies if "openai.com" in (c.get("domain") or "")}
    missing = [c for c in _REQUIRED_AUTH_COOKIES if c not in auth_cookies]
    if missing:
        raise BrowserPhaseError(f"thiếu cookies: {missing}. có: {sorted(auth_cookies)}")

    log(f"[browser] handoff: {len(handoff_cookies)} cookies, state={state_param[:20]}...")
    return (
        BrowserHandoff(
            cookies=handoff_cookies,
            state_param=state_param,
            device_id=device_id,
            auth_session_logging_id=logging_id,
            callback_url=callback_url,
        ),
        otp_seconds,
    )
