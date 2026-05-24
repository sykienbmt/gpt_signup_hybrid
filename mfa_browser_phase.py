"""Enable 2FA qua ChatGPT browser UI (Settings → Security → Authenticator app).

Thay thế cho mfa_phase.py (API-based) khi OpenAI block /activate_enrollment API.

Flow dựa theo HAR recording:
  1. Mở Camoufox + inject chatgpt.com session cookies
  2. Navigate tới chatgpt.com (đã login qua cookies)
  3. Dismiss onboarding dialogs nếu có
  4. Click Settings → Security tab → Authenticator app toggle
  5. Modal mở → click "Having trouble scanning?" → đọc text secret
  6. Gen TOTP code → nhập vào #totp_otp → click Verify
  7. Return {"secret": ..., "first_code": ..., "activated": True}
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .totp_helper import generate_code, normalize_secret, time_remaining


class MfaBrowserError(Exception):
    """Browser-based 2FA enable failed."""


# Base32 pattern để extract secret từ text hiển thị trong modal
_BASE32_RE = re.compile(r'[A-Z2-7]{16,}')


async def _dismiss_dialogs(page, *, log) -> None:
    """Đóng onboarding / welcome dialogs nếu có."""
    for _ in range(4):
        dismissed = False
        for sel in (
            'button.btn-ghost',
            'button:has-text("Bỏ qua")',
            'button:has-text("Skip")',
            'button:has-text("Maybe later")',
            'button:has-text("No thanks")',
        ):
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=800):
                    await btn.click(timeout=2000)
                    log(f"[mfa_browser] dismissed dialog ({sel})")
                    dismissed = True
                    await asyncio.sleep(0.8)
                    break
            except Exception:
                continue
        if not dismissed:
            break


async def _open_settings_security(page, *, log) -> None:
    """Mở Settings dialog → click Security tab."""
    log("[mfa_browser] opening Settings...")

    # Bước 1: Mở profile menu (góc dưới trái) để Settings item hiện ra
    # HAR: click html tại (81, 853) trong viewport 1050x898
    profile_opened = False
    for sel in (
        "[data-testid='profile-button']",
        "nav button[aria-haspopup]",
        "nav button[aria-expanded]",
        "button[data-testid*='profile']",
        "button[data-testid*='account']",
        "button[data-testid*='user']",
        "nav a[href*='/account']",
    ):
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click(timeout=2000)
                log(f"[mfa_browser] opened profile menu ({sel})")
                profile_opened = True
                await asyncio.sleep(0.8)
                break
        except Exception:
            continue

    # Fallback: click vị trí góc dưới trái (theo tọa độ HAR)
    if not profile_opened:
        try:
            vp = page.viewport_size or {"width": 1440, "height": 800}
            x = int(vp["width"] * 0.075)   # ~8% từ trái
            y = int(vp["height"] * 0.95)    # ~95% từ trên (gần đáy)
            await page.click("html", position={"x": x, "y": y}, timeout=2000)
            log(f"[mfa_browser] clicked profile area (fallback pos {x},{y})")
            await asyncio.sleep(0.8)
        except Exception as exc:
            log(f"[mfa_browser] profile click fallback failed: {exc}")

    # Bước 2: Click Settings item trong menu vừa mở
    settings_clicked = False
    for sel in (
        "[data-testid='settings-menu-item']",
        'a[data-testid="settings-menu-item"]',
        '[role="menuitem"]:has-text("Settings")',
        '[role="menuitem"]:has-text("Cài đặt")',
        'a:has-text("Settings")',
        'button:has-text("Settings")',
    ):
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2500):
                await btn.click(timeout=3000)
                log(f"[mfa_browser] clicked Settings ({sel})")
                settings_clicked = True
                await asyncio.sleep(1.2)
                break
        except Exception:
            continue

    if not settings_clicked:
        raise MfaBrowserError("Không tìm thấy Settings menu item")

    # Click Security tab
    log("[mfa_browser] clicking Security tab...")
    for sel in (
        "[data-testid='security-tab']",
        "button:has-text('Bảo mật')",
        "button:has-text('Security')",
        '[role="tab"]:has-text("Security")',
        '[role="tab"]:has-text("Bảo mật")',
    ):
        try:
            tab = page.locator(sel).first
            if await tab.is_visible(timeout=3000):
                await tab.click(timeout=3000)
                log(f"[mfa_browser] clicked Security tab ({sel})")
                await asyncio.sleep(1.0)
                return
        except Exception:
            continue

    raise MfaBrowserError("Không tìm thấy Security tab trong Settings")


async def _get_secret_from_modal(page, *, log) -> str:
    """Click Authenticator toggle → modal → reveal text secret → đọc secret."""
    # Click toggle Authenticator app
    log("[mfa_browser] clicking Authenticator app toggle...")
    toggle_clicked = False
    for sel in (
        "[data-testid='mfa-authenticator-toggle']",
        'button:has-text("Authenticator app")',
        'button:has-text("Authenticator")',
    ):
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=3000):
                await btn.click(timeout=3000)
                log(f"[mfa_browser] clicked toggle ({sel})")
                toggle_clicked = True
                await asyncio.sleep(1.5)
                break
        except Exception:
            continue

    if not toggle_clicked:
        raise MfaBrowserError("Không tìm thấy Authenticator app toggle")

    # Đợi modal xuất hiện
    log("[mfa_browser] waiting for TOTP enrollment modal...")
    modal_sel = "[data-testid='modal-enroll-totp']"
    try:
        await page.wait_for_selector(modal_sel, timeout=10000)
        log("[mfa_browser] modal appeared")
    except Exception:
        raise MfaBrowserError("Modal đăng ký TOTP không hiện ra")

    # Click "Having trouble scanning?" để hiện text secret
    log("[mfa_browser] revealing text secret...")
    for sel in (
        f"{modal_sel} div.text-token-text-primary button",
        'button:has-text("Bạn gặp vấn đề")',
        'button:has-text("Having trouble")',
        'button:has-text("trouble")',
        'button:has-text("manually")',
    ):
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click(timeout=2000)
                log(f"[mfa_browser] clicked reveal button ({sel})")
                await asyncio.sleep(1.0)
                break
        except Exception:
            continue

    # Đọc secret text từ modal
    secret = None

    # Thử selector chính xác từ HAR
    for sel in (
        f"{modal_sel} div:nth-of-type(2) > div > div > div",
        f"{modal_sel} [class*='font-mono']",
        f"{modal_sel} code",
        f"{modal_sel} [class*='secret']",
        f"{modal_sel} [class*='token']",
    ):
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                text = (await el.inner_text(timeout=2000)).strip()
                text_clean = text.upper().replace(' ', '').replace('\n', '')
                match = _BASE32_RE.search(text_clean)
                if match:
                    secret = match.group(0)
                    log(f"[mfa_browser] secret extracted ({sel}): {secret[:8]}... len={len(secret)}")
                    break
        except Exception:
            continue

    # Fallback: scan toàn bộ text trong modal
    if not secret:
        try:
            modal_text = await page.locator(modal_sel).inner_text(timeout=3000)
            text_clean = modal_text.upper().replace(' ', '').replace('\n', '')
            match = _BASE32_RE.search(text_clean)
            if match:
                secret = match.group(0)
                log(f"[mfa_browser] secret extracted (modal fallback): {secret[:8]}... len={len(secret)}")
        except Exception:
            pass

    if not secret:
        raise MfaBrowserError("Không đọc được TOTP secret từ modal")

    return secret


async def _verify_totp_in_modal(page, *, secret: str, log, max_attempts: int = 3) -> None:
    """Nhập TOTP code vào #totp_otp → click Verify. Retry nếu code bị reject."""
    otp_sel = "#totp_otp"
    modal_sel = "[data-testid='modal-enroll-totp']"

    for attempt in range(1, max_attempts + 1):
        # Đợi window còn đủ thời gian
        remaining = time_remaining()
        if remaining < 5:
            log(f"[mfa_browser] TOTP window sắp hết ({remaining}s) — đợi window mới (attempt {attempt})...")
            await asyncio.sleep(remaining + 1)

        code = generate_code(secret)
        remaining_after = time_remaining()
        log(f"[mfa_browser] entering code={code} window_remaining={remaining_after}s (attempt {attempt})")

        # Fill OTP input
        try:
            otp_input = page.locator(otp_sel).first
            await otp_input.wait_for(state="visible", timeout=5000)
            await otp_input.click(timeout=2000)
            await otp_input.fill("")
            await otp_input.fill(code)
        except Exception as exc:
            raise MfaBrowserError(f"Không fill được OTP input: {exc}")

        # Click Verify button
        verify_clicked = False
        for btn_sel in (
            "button.btn-primary",
            f"{modal_sel} button:has-text('Xác minh')",
            f"{modal_sel} button:has-text('Verify')",
            f"{modal_sel} button[type='submit']",
        ):
            try:
                btn = page.locator(btn_sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click(timeout=3000)
                    log(f"[mfa_browser] clicked Verify ({btn_sel})")
                    verify_clicked = True
                    break
            except Exception:
                continue

        if not verify_clicked:
            raise MfaBrowserError("Không tìm thấy nút Verify")

        await asyncio.sleep(2.5)

        # Kiểm tra kết quả: modal đóng = thành công
        try:
            modal_visible = await page.locator(modal_sel).is_visible(timeout=1500)
        except Exception:
            modal_visible = False

        if not modal_visible:
            log(f"[mfa_browser] TOTP verified — modal đã đóng (attempt {attempt})")
            return

        # Modal vẫn mở → đọc error message
        err_msg = ""
        for err_sel in (
            f"{modal_sel} [class*='error']",
            f"{modal_sel} [role='alert']",
            f"{modal_sel} p[class*='text-red']",
        ):
            try:
                err_el = page.locator(err_sel).first
                if await err_el.is_visible(timeout=800):
                    err_msg = (await err_el.inner_text(timeout=800)).strip()
                    break
            except Exception:
                continue

        log(f"[mfa_browser] code rejected attempt {attempt}: {err_msg or '(no error msg)'}")

        if attempt < max_attempts:
            wait = time_remaining() + 1
            log(f"[mfa_browser] đợi {wait}s sang window mới rồi retry...")
            await asyncio.sleep(wait)
            # Clear input
            try:
                await page.locator(otp_sel).fill("")
            except Exception:
                pass
            continue

        raise MfaBrowserError(f"TOTP bị reject sau {max_attempts} attempts: {err_msg}")


async def enable_2fa_browser(
    *,
    cookies: list[dict[str, Any]],
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0",
    proxy: str | None = None,
    headless: bool = True,
    settings=None,
    log=print,
) -> dict[str, Any]:
    """Enable 2FA TOTP qua browser UI của ChatGPT.

    Args:
        cookies: Danh sách cookies chatgpt.com từ SignupResult.cookies
        user_agent: UA string
        proxy: HTTP/HTTPS proxy string
        headless: True = ẩn browser, False = hiện browser (debug)
        settings: Settings object (load từ config nếu None)
        log: callable để ghi log

    Returns:
        {
            "secret": "B2P3OQCCXINLHGPUDIS55DHQDW5MENK5",
            "first_code": "763657",
            "provisioning_uri": "otpauth://totp/ChatGPT?secret=...",
            "activated": True,
        }
    """
    from .config import load_settings, ensure_runtime_dirs

    if settings is None:
        settings = load_settings()
    ensure_runtime_dirs(settings)

    from camoufox.async_api import AsyncCamoufox
    from camoufox.utils import Screen as _Screen

    job_id = f"mfa2_{uuid.uuid4().hex[:8]}"
    profile_dir = settings.profiles_dir / f"camoufox_{job_id}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Copy template profile nếu có
    template_dir = settings.browser_camoufox_profile_dir
    if template_dir.exists():
        import shutil
        for item in template_dir.iterdir():
            dst = profile_dir / item.name
            if not dst.exists():
                try:
                    if item.is_dir():
                        shutil.copytree(str(item), str(dst))
                    else:
                        shutil.copy2(str(item), str(dst))
                except Exception:
                    pass

    w, h = settings.browser_viewport_width, settings.browser_viewport_height
    chrome_h = 85
    fixed_screen = _Screen(
        min_width=w, max_width=w,
        min_height=h + chrome_h, max_height=h + chrome_h,
    )

    proxy_kwargs: dict[str, Any] = {}
    if proxy:
        proxy_kwargs["proxy"] = {"server": proxy}

    log(f"[mfa_browser] launching browser (headless={headless})...")

    cf = AsyncCamoufox(
        headless=headless,
        persistent_context=True,
        user_data_dir=str(profile_dir),
        viewport={"width": w, "height": h},
        screen=fixed_screen,
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
    ctx = await cf.__aenter__()

    try:
        # Inject chatgpt.com cookies vào context
        chatgpt_cookies = []
        for c in (cookies or []):
            domain = (c.get("domain") or "").lower()
            if "chatgpt.com" in domain or "openai.com" in domain:
                cookie_entry: dict[str, Any] = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain") or ".chatgpt.com",
                    "path": c.get("path") or "/",
                }
                if c.get("httpOnly") is not None:
                    cookie_entry["httpOnly"] = bool(c["httpOnly"])
                if c.get("secure") is not None:
                    cookie_entry["secure"] = bool(c["secure"])
                same_site = c.get("sameSite", "Lax")
                if same_site in ("Strict", "Lax", "None"):
                    cookie_entry["sameSite"] = same_site
                chatgpt_cookies.append(cookie_entry)

        if chatgpt_cookies:
            await ctx.add_cookies(chatgpt_cookies)
            log(f"[mfa_browser] injected {len(chatgpt_cookies)} cookies")
        else:
            raise MfaBrowserError("Không có chatgpt.com cookies để inject")

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        log("[mfa_browser] navigating to chatgpt.com...")
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2.0)
        log(f"[mfa_browser] page loaded: {page.url}")

        # Dismiss dialogs
        await _dismiss_dialogs(page, log=log)

        # Settings → Security
        await _open_settings_security(page, log=log)

        # Authenticator toggle → modal → extract secret
        secret_raw = await _get_secret_from_modal(page, log=log)
        secret = normalize_secret(secret_raw)

        # Submit TOTP code để activate
        await _verify_totp_in_modal(page, secret=secret, log=log)

        first_code = generate_code(secret)
        log(f"[mfa_browser] 2FA enabled OK secret={secret[:8]}...")

        return {
            "secret": secret,
            "first_code": first_code,
            "provisioning_uri": f"otpauth://totp/ChatGPT?secret={secret}&issuer=ChatGPT",
            "activated": True,
            "mfa_info": None,
        }

    finally:
        try:
            await cf.__aexit__(None, None, None)
        except Exception:
            pass
        # Cleanup profile dir
        try:
            import shutil
            shutil.rmtree(str(profile_dir), ignore_errors=True)
        except Exception:
            pass
