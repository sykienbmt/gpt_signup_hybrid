"""UPI payment automation using Playwright — fills billing info and clicks subscribe."""
from __future__ import annotations

import asyncio
import glob
import os
import random
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.async_api import Page


def _cleanup_playwright_artifacts() -> None:
    """Delete leftover playwright-artifacts-* temp dirs."""
    tmp = tempfile.gettempdir()
    for entry in glob.glob(os.path.join(tmp, "playwright-artifacts-*")):
        try:
            shutil.rmtree(entry, ignore_errors=True)
        except Exception:
            pass


def _stamp_email(fpath: Path, label: str) -> None:
    """Overlay a red label centered at the top of the screenshot."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(fpath).convert("RGBA")
        draw = ImageDraw.Draw(img)
        font_size = max(18, img.width // 40)
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except Exception:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        pad_x, pad_y = 14, 6
        x = (img.width - text_w) // 2 - pad_x
        y = 0
        draw.rectangle([x, y, x + text_w + pad_x * 2, y + text_h + pad_y * 2], fill=(30, 100, 220, 220))
        draw.text((x + pad_x, y + pad_y), label, font=font, fill=(255, 255, 255, 255))
        img = img.convert("RGB")
        img.save(fpath, format="PNG")
    except Exception:
        pass


_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "runtime" / "upi_screenshots"


# ─── Indian address data ───

_CITIES = [
    ("Mumbai", "MH", "400001"),
    ("Delhi", "DL", "110001"),
    ("Bangalore", "KA", "560001"),
    ("Hyderabad", "TG", "500001"),
    ("Chennai", "TN", "600001"),
    ("Kolkata", "WB", "700001"),
    ("Pune", "MH", "411001"),
    ("Ahmedabad", "GJ", "380001"),
    ("Jaipur", "RJ", "302001"),
    ("Surat", "GJ", "395001"),
    ("Lucknow", "UP", "226001"),
    ("Kanpur", "UP", "208001"),
    ("Nagpur", "MH", "440001"),
    ("Indore", "MP", "452001"),
]

_FIRST_NAMES = [
    "Aarav", "Arjun", "Rohan", "Vikram", "Rahul", "Amit", "Sanjay", "Priya",
    "Anjali", "Neha", "Pooja", "Sunita", "Ramesh", "Suresh", "Mahesh",
    "Rajesh", "Deepak", "Pankaj", "Manish", "Vikas",
]

_LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Singh", "Kumar", "Patel", "Shah", "Mehta",
    "Joshi", "Nair", "Iyer", "Reddy", "Rao", "Pillai", "Mishra",
    "Tiwari", "Pandey", "Yadav", "Desai", "Bose",
]

_STREET_PATTERNS = [
    "{num} MG Road", "{num} Gandhi Nagar", "{num} Nehru Street",
    "{num} Park Avenue", "{num} Station Road", "Flat {num}, Shanti Apartments",
    "{num} Market Street", "{num} Temple Road", "{num} Metro Colony",
    "Plot {num}, Sector 12", "{num} Cross Street", "{num} Main Road",
]


@dataclass
class IndianAddress:
    name: str
    address1: str
    address2: str
    city: str
    state: str
    pin: str


def generate_indian_address() -> IndianAddress:
    city, state, base_pin = random.choice(_CITIES)
    name = f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"
    num = random.randint(1, 999)
    address1 = random.choice(_STREET_PATTERNS).format(num=num)
    address2 = ""
    # Slightly vary PIN (last 2 digits)
    pin_num = int(base_pin) + random.randint(0, 99)
    pin = str(pin_num)
    return IndianAddress(name=name, address1=address1, address2=address2, city=city, state=state, pin=pin)


# ─── Playwright helper ───

_JS_SIMULATE_TYPING = """
(el, value) => {
    el.focus();
    const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    )?.set;
    el.value = '';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    for (const char of value) {
        if (nativeSetter) nativeSetter.call(el, el.value + char);
        else el.value += char;
        el.dispatchEvent(new Event('input', { bubbles: true }));
    }
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.blur();
}
"""

_JS_SET_SELECT = """
(el, value) => {
    const option = Array.from(el.options).find(
        o => o.value === value || o.value.toUpperCase() === value.toUpperCase()
    );
    if (!option) return false;
    const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLSelectElement.prototype, 'value'
    )?.set;
    if (nativeSetter) nativeSetter.call(el, option.value);
    else el.value = option.value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
}
"""


async def _fill_input(page: Page, selector: str, value: str) -> bool:
    """Fill input by CSS selector using JS simulate typing. Returns True if found."""
    try:
        el = page.locator(selector).first
        await el.evaluate(_JS_SIMULATE_TYPING, value)
        return True
    except Exception:
        return False


async def _fill_by_id(page: Page, element_id: str, value: str) -> bool:
    return await _fill_input(page, f"#{element_id}", value)


async def _set_select_by_id(page: Page, element_id: str, value: str) -> bool:
    try:
        el = page.locator(f"#{element_id}").first
        result = await el.evaluate(_JS_SET_SELECT, value)
        return bool(result)
    except Exception:
        return False


async def _wait_for_upi_accordion(page: Page, timeout_ms: int = 20_000) -> bool:
    """Wait until the UPI accordion appears in DOM."""
    selectors = [
        '[data-testid="upi-accordion-item-button"]',
        '[data-testid="upi-accordion-item"]',
        'input[type="radio"][value="upi"]',
    ]
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=timeout_ms, state="attached")
            return True
        except Exception:
            continue
    return False


async def _click_upi_accordion(page: Page) -> bool:
    """Try multiple strategies to expand the UPI section."""
    selectors = [
        '[data-testid="upi-accordion-item-button"]',
        'input[type="radio"][value="upi"]',
        '[data-testid="upi-accordion-item"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(force=True)
                return True
        except Exception:
            continue

    # Text-based fallback
    try:
        elements = page.locator("div, span, label, button")
        count = await elements.count()
        for i in range(count):
            el = elements.nth(i)
            text = (await el.text_content() or "").strip()
            if text == "UPI":
                await el.click(force=True)
                return True
    except Exception:
        pass

    return False


async def _fill_upi_billing(page: Page, addr: IndianAddress) -> None:
    """Fill UPI billing fields."""
    await _fill_by_id(page, "billingName", addr.name)
    await _fill_by_id(page, "billingAddressLine1", addr.address1)
    if addr.address2:
        await _fill_by_id(page, "billingAddressLine2", addr.address2)
    await _fill_by_id(page, "billingLocality", addr.city)
    await _fill_by_id(page, "billingPostalCode", addr.pin)
    await _set_select_by_id(page, "billingCountry", "IN")
    if addr.state:
        await _set_select_by_id(page, "billingAdministrativeArea", addr.state)

    # Autocomplete fallbacks
    autocomplete_map = [
        (["cc-name", "billing-name", "cardholder"], addr.name),
        (["address-line1", "street-address"], addr.address1),
        (["address-level2", "locality", "city"], addr.city),
        (["postal-code", "zip"], addr.pin),
    ]
    inputs = page.locator("input")
    count = await inputs.count()
    for i in range(count):
        inp = inputs.nth(i)
        try:
            el_id = (await inp.get_attribute("id") or "").lower()
            name = (await inp.get_attribute("name") or "").lower()
            auto = (await inp.get_attribute("autocomplete") or "").lower()
            combined = f"{el_id} {name} {auto}"
            for patterns, value in autocomplete_map:
                if any(p in combined for p in patterns):
                    await inp.evaluate(_JS_SIMULATE_TYPING, value)
                    break
        except Exception:
            continue


async def _check_required_checkboxes(page: Page) -> None:
    # 1. Stripe terms of service consent (the "You'll be charged..." checkbox)
    try:
        terms = page.locator("#termsOfServiceConsentCheckbox").first
        if await terms.count() > 0 and not await terms.is_checked():
            await terms.click(force=True)
            await terms.evaluate(
                "el => el.dispatchEvent(new Event('change', { bubbles: true }))"
            )
    except Exception:
        pass

    # 2. Generic: required / aria-required + any visible unchecked checkbox in the form
    checkboxes = page.locator('input[type="checkbox"]')
    count = await checkboxes.count()
    for i in range(count):
        cb = checkboxes.nth(i)
        try:
            if await cb.is_checked():
                continue
            required = await cb.get_attribute("required")
            aria_required = await cb.get_attribute("aria-required")
            cb_id = (await cb.get_attribute("id") or "").lower()
            name = (await cb.get_attribute("name") or "").lower()
            is_terms_like = any(
                kw in (cb_id + " " + name)
                for kw in ("terms", "consent", "charge", "agree", "subscription")
            )
            if required is not None or aria_required == "true" or is_terms_like:
                try:
                    await cb.click(force=True)
                except Exception:
                    await cb.check(force=True)
        except Exception:
            continue


async def _click_subscribe(page: Page) -> bool:
    """Click the subscribe/pay button."""
    selectors = [
        '.SubmitButton',
        '[data-testid="hosted-payment-submit-button"]',
        'button[type="submit"]',
        'button.SubmitButton--complete',
        'button.SubmitButton--incomplete',
        '.checkout-button',
        'button:not([type="button"]):not([type="reset"])',
        'input[type="submit"]',
    ]
    pattern = re.compile(
        r'submit|pay|subscribe|confirm|complete|buy|purchase|start|upgrade|checkout',
        re.IGNORECASE,
    )
    for sel in selectors:
        try:
            btns = page.locator(sel)
            count = await btns.count()
            for i in range(count):
                btn = btns.nth(i)
                if not await btn.is_visible():
                    continue
                text = (await btn.text_content() or "").strip()
                if pattern.search(text) or "SubmitButton" in sel or "submit" in sel:
                    # Force-enable if disabled
                    await btn.evaluate("""
                        el => {
                            if (el.disabled) {
                                el.disabled = false;
                                el.removeAttribute('disabled');
                            }
                        }
                    """)
                    await btn.click()
                    return True
        except Exception:
            continue
    return False


# ─── Main public function ───

async def run_upi_automation(
    payment_url: str,
    *,
    proxy: str | None = None,
    headless: bool = True,
    log: Callable[[str], None] | None = None,
    job_id: str | None = None,
    email: str | None = None,
) -> dict:
    """
    Open payment_url in a browser, select UPI, fill Indian billing info, click subscribe.
    Returns {"ok": bool, "error": str | None}.
    """
    from camoufox.async_api import AsyncCamoufox

    _log = log or (lambda msg: None)

    addr = generate_indian_address()
    _log(f"[upi] address: {addr.name}, {addr.city}, {addr.state} {addr.pin}")

    proxy_kwargs: dict = {}
    if proxy:
        from urllib.parse import urlparse
        parsed = urlparse(proxy)
        server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        proxy_cfg: dict = {"server": server}
        if parsed.username:
            proxy_cfg["username"] = parsed.username
        if parsed.password:
            proxy_cfg["password"] = parsed.password
        proxy_kwargs["proxy"] = proxy_cfg

    win_w, win_h = 1100, 800
    cf = AsyncCamoufox(
        headless=headless,
        window=(win_w, win_h),
        **proxy_kwargs,
    )
    browser_or_ctx = await cf.__aenter__()
    try:
        # In non-persistent mode camoufox returns a Browser; create a context.
        if hasattr(browser_or_ctx, "new_context"):
            ctx = await browser_or_ctx.new_context(
                viewport={"width": win_w, "height": win_h},
                ignore_https_errors=True,
            )
        else:
            ctx = browser_or_ctx
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.set_viewport_size({"width": win_w, "height": win_h})
        except Exception:
            pass

        _log(f"[upi] navigating to {payment_url}")
        await page.goto(payment_url, wait_until="domcontentloaded", timeout=60_000)

        # Resize Firefox window from page-side (covers non-headless mode)
        try:
            await page.evaluate(f"window.resizeTo({win_w}, {win_h})")
        except Exception:
            pass

        # Wait for Stripe checkout to fully render
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(2)

        _log("[upi] waiting for UPI accordion to appear")
        appeared = await _wait_for_upi_accordion(page, timeout_ms=20_000)
        if not appeared:
            _log("[upi] ⚠️ UPI accordion not detected after 20s")

        _log("[upi] clicking UPI accordion")
        clicked = await _click_upi_accordion(page)
        if not clicked:
            _log("[upi] ⚠️ UPI accordion not found, attempting fill anyway")
        await asyncio.sleep(1.5)

        _log("[upi] filling billing fields")
        await _fill_upi_billing(page, addr)
        await asyncio.sleep(0.8)

        _log("[upi] checking required checkboxes")
        await _check_required_checkboxes(page)
        await asyncio.sleep(0.5)

        _log("[upi] clicking subscribe")
        submitted = await _click_subscribe(page)
        if not submitted:
            _log("[upi] ❌ subscribe button not found")
            return {"ok": False, "error": "subscribe button not found"}

        _log("[upi] ✅ subscribe clicked, waiting for QR / response")

        screenshots: list[str] = []
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (email or job_id or "unknown")).strip("_")

        async def _take_screenshot(reason: str) -> str | None:
            ts = int(time.time() * 1000)
            fname = f"{slug}_{ts}.png"
            fpath = _SCREENSHOT_DIR / fname
            try:
                await page.screenshot(path=str(fpath), full_page=True)
                _stamp_email(fpath, email or slug)
                _log(f"[upi] 📸 screenshot ({reason}): {fpath}")
                screenshots.append(str(fpath))
                return str(fpath)
            except Exception as exc:
                _log(f"[upi] ⚠️ screenshot failed: {exc}")
                return None

        # Wait up to 40s for Stripe UPI QR popup — retry subscribe every 10s if no QR
        qr_selector = (
            'img[data-testid="QRCode-image"], img.QRCode-image, '
            'img[src*="qr.stripe.com"]'
        )
        qr_element = None
        _retry_interval = 10.0
        _max_retries = 3
        _retry_count = 0
        qr_deadline = time.monotonic() + 40.0
        _next_retry_at = time.monotonic() + _retry_interval
        while time.monotonic() < qr_deadline:
            for frame in page.frames:
                try:
                    el = await frame.query_selector(qr_selector)
                    if el:
                        qr_element = el
                        break
                except Exception:
                    continue
            if qr_element is not None:
                break
            # Retry subscribe if button was unresponsive
            if time.monotonic() >= _next_retry_at and _retry_count < _max_retries:
                _retry_count += 1
                _log(f"[upi] ⟳ no QR after {int(_retry_interval)}s — retrying subscribe ({_retry_count}/{_max_retries})")
                await _click_subscribe(page)
                _next_retry_at = time.monotonic() + _retry_interval
            await asyncio.sleep(0.5)

        if qr_element is not None:
            _log("[upi] ✅ UPI QR detected — waiting 5s for full render")
            await asyncio.sleep(5.0)

            # Find the popup container (closest visible card / dialog ancestor)
            popup_handle = None
            try:
                popup_handle = await qr_element.evaluate_handle(
                    """
                    el => el.closest(
                        '.Chrome--stripejs, .ContentCard, [role="dialog"], .Modal, .ModalOverlay'
                    ) || el.parentElement
                    """
                )
            except Exception:
                popup_handle = None

            ts = int(time.time() * 1000)
            fname = f"{slug}_{ts}.png"
            fpath = _SCREENSHOT_DIR / fname
            captured = False
            try:
                target = popup_handle.as_element() if popup_handle else None
                if target is not None:
                    await target.screenshot(path=str(fpath))
                    _stamp_email(fpath, email or slug)
                    captured = True
                    _log(f"[upi] 📸 popup screenshot: {fpath}")
            except Exception as exc:
                _log(f"[upi] ⚠️ popup screenshot failed: {exc}")

            if not captured:
                try:
                    await qr_element.screenshot(path=str(fpath))
                    _stamp_email(fpath, email or slug)
                    captured = True
                    _log(f"[upi] 📸 QR-only screenshot: {fpath}")
                except Exception as exc:
                    _log(f"[upi] ⚠️ QR element screenshot failed: {exc}")

            if captured:
                screenshots.append(str(fpath))
            else:
                await _take_screenshot("qr_fallback_fullpage")

            return {
                "ok": True,
                "error": None,
                "screenshots": screenshots,
                "payment_link": payment_url,
                "qr_captured": True,
            }

        _log("[upi] ❌ UPI QR not detected within 40s — failing")
        return {
            "ok": False,
            "error": "UPI QR not detected within 40s",
            "screenshots": screenshots,
            "payment_link": payment_url,
            "qr_captured": False,
        }

    except Exception as exc:
        _log(f"[upi] ❌ error: {exc}")
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            await cf.__aexit__(None, None, None)
        except Exception:
            pass
        _cleanup_playwright_artifacts()
