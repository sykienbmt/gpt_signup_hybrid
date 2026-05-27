"""UPI Watch Mode — opens up to 3 browsers to UPI QR step, keeps alive.

Each slot:
  - Opens a small headed browser window, positioned vertically on screen
  - Auto-navigates → fills UPI billing → clicks subscribe → waits for QR
  - Injects Done / Fail / Off overlay buttons into the page
  - Takes periodic screenshots (served via /api/link/watch/slot/{n}/screenshot)
  - Waits until the user presses Done / Fail / Off (in browser or web UI)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

SlotStatus = Literal[
    "idle", "opening", "navigating", "filling", "submitting",
    "waiting_qr", "qr_visible", "done", "failed", "off", "error",
]

_SHOT_DIR = Path(__file__).resolve().parent.parent / "runtime" / "upi_watch_shots"

# JS injected into the page — uses expose_function "watchAction"
_OVERLAY_JS = r"""
() => {
  if (document.getElementById('_gw_ov')) return;
  const d = document.createElement('div');
  d.id = '_gw_ov';
  d.style.cssText = 'position:fixed;top:8px;right:8px;z-index:2147483647;display:flex;gap:6px;font-family:system-ui,sans-serif;';
  const mk = (t, bg) => {
    const b = document.createElement('button');
    b.textContent = t;
    b.style.cssText = `background:${bg};color:#fff;border:none;padding:8px 14px;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.4)`;
    return b;
  };
  const done = mk('✅ Done','#22c55e'), fail = mk('❌ Fail','#ef4444'), off = mk('🔴 Off','#6b7280');
  done.onclick = () => { done.disabled=true; window.watchAction('done'); };
  fail.onclick = () => { fail.disabled=true; window.watchAction('fail'); };
  off.onclick  = () => { off.disabled=true;  window.watchAction('off');  };
  d.append(done, fail, off);
  document.body.appendChild(d);
}
"""

_OFF_ONLY_JS = r"""
() => {
  if (document.getElementById('_gw_ov')) return;
  const d = document.createElement('div');
  d.id = '_gw_ov';
  d.style.cssText = 'position:fixed;top:8px;right:8px;z-index:2147483647;font-family:system-ui,sans-serif;';
  const b = document.createElement('button');
  b.textContent = '🔴 Off';
  b.style.cssText = 'background:#6b7280;color:#fff;border:none;padding:8px 14px;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.4)';
  b.onclick = () => { b.disabled=true; window.watchAction('off'); };
  d.appendChild(b);
  document.body.appendChild(d);
}
"""


@dataclass
class WatchSlot:
    slot_idx: int
    job_id: str
    email: str
    payment_url: str
    publishable_key: str | None = None
    checkout_session_id: str | None = None
    status: SlotStatus = "idle"
    status_msg: str = ""
    screenshot_path: str | None = None
    screenshot_ts: float = 0.0
    # Runtime (not serialised)
    _action_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _pending_action: str | None = field(default=None, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)


class UpiWatchManager:
    """Manages up to 3 concurrent UPI watch browser sessions."""

    MAX_SLOTS = 3

    def __init__(self, on_job_action: Callable[[str, str], None] | None = None) -> None:
        """on_job_action(job_id, action) called when Done/Fail is selected."""
        self.slots: dict[int, WatchSlot] = {}
        self._on_job_action = on_job_action
        _SHOT_DIR.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────

    async def start(self, configs: list[dict]) -> dict:
        """Start watch sessions. Each config: {slot_idx, job_id, email, payment_url, ...}"""
        for cfg in configs[: self.MAX_SLOTS]:
            idx = int(cfg["slot_idx"])
            old = self.slots.get(idx)
            if old and old._task and not old._task.done():
                old._pending_action = "off"
                old._action_event.set()
                try:
                    await asyncio.wait_for(asyncio.shield(old._task), timeout=3.0)
                except Exception:
                    old._task.cancel()

            slot = WatchSlot(
                slot_idx=idx,
                job_id=cfg["job_id"],
                email=cfg["email"],
                payment_url=cfg["payment_url"],
                publishable_key=cfg.get("publishable_key"),
                checkout_session_id=cfg.get("checkout_session_id"),
                status="opening",
                status_msg="Opening browser…",
            )
            self.slots[idx] = slot
            slot._task = asyncio.create_task(self._run_slot(slot))
        return self.get_status()

    async def slot_action(self, slot_idx: int, action: str) -> None:
        slot = self.slots.get(slot_idx)
        if slot:
            slot._pending_action = action
            slot._action_event.set()

    async def stop_all(self) -> None:
        for slot in self.slots.values():
            slot._pending_action = "off"
            slot._action_event.set()

    def get_status(self) -> dict:
        return {
            "slots": [
                {
                    "slot_idx": s.slot_idx,
                    "job_id": s.job_id,
                    "email": s.email,
                    "status": s.status,
                    "status_msg": s.status_msg,
                    "screenshot_ts": s.screenshot_ts,
                    "has_screenshot": bool(
                        s.screenshot_path and Path(s.screenshot_path).exists()
                    ),
                }
                for s in sorted(self.slots.values(), key=lambda x: x.slot_idx)
            ]
        }

    def get_screenshot_path(self, slot_idx: int) -> Path | None:
        slot = self.slots.get(slot_idx)
        if not slot or not slot.screenshot_path:
            return None
        p = Path(slot.screenshot_path)
        return p if p.exists() else None

    # ── Internal ─────────────────────────────────────────────────────────

    async def _take_screenshot(self, slot: WatchSlot, page) -> None:
        try:
            fname = f"watch_{slot.slot_idx}_{int(time.time())}.png"
            fpath = _SHOT_DIR / fname
            await page.screenshot(path=str(fpath), full_page=False)
            slot.screenshot_path = str(fpath)
            slot.screenshot_ts = time.time()
        except Exception:
            pass

    async def _run_slot(self, slot: WatchSlot) -> None:
        from camoufox.async_api import AsyncCamoufox
        from .upi_automation import (
            _wait_for_upi_accordion,
            _click_upi_accordion,
            _fill_upi_billing,
            _check_required_checkboxes,
            _click_subscribe,
            generate_indian_address,
        )

        WIN_W, WIN_H = 500, 540
        WIN_X = 40
        WIN_Y = slot.slot_idx * (WIN_H + 30)

        cf = AsyncCamoufox(headless=False, window=(WIN_W, WIN_H))
        try:
            browser_or_ctx = await cf.__aenter__()
            if hasattr(browser_or_ctx, "new_context"):
                ctx = await browser_or_ctx.new_context(
                    viewport={"width": WIN_W, "height": WIN_H},
                    ignore_https_errors=True,
                )
            else:
                ctx = browser_or_ctx

            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                await page.set_viewport_size({"width": WIN_W, "height": WIN_H})
            except Exception:
                pass

            # Position window on screen (Firefox supports window.moveTo)
            try:
                await page.evaluate(f"window.moveTo({WIN_X},{WIN_Y}); window.resizeTo({WIN_W},{WIN_H})")
            except Exception:
                pass

            # Expose action callback so overlay buttons can signal Python
            async def _overlay_action(action: str) -> None:
                slot._pending_action = action
                slot._action_event.set()

            await page.expose_function("watchAction", _overlay_action)

            # Navigate
            slot.status = "navigating"
            slot.status_msg = "Navigating to payment page…"
            await page.goto(slot.payment_url, wait_until="domcontentloaded", timeout=60_000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass
            await asyncio.sleep(2)

            if slot._action_event.is_set():
                await self._finish(slot)
                return

            # Fill UPI
            slot.status = "filling"
            slot.status_msg = "Waiting for UPI accordion…"
            appeared = await _wait_for_upi_accordion(page, timeout_ms=20_000)

            if not appeared or slot._action_event.is_set():
                slot.status = "error"
                slot.status_msg = "UPI accordion not found"
                try:
                    await page.evaluate(_OFF_ONLY_JS)
                except Exception:
                    pass
                await slot._action_event.wait()
            else:
                await _click_upi_accordion(page)
                await asyncio.sleep(1.5)
                addr = generate_indian_address()
                await _fill_upi_billing(page, addr)
                await asyncio.sleep(0.8)
                await _check_required_checkboxes(page)
                await asyncio.sleep(0.5)

                slot.status = "submitting"
                slot.status_msg = "Clicking subscribe…"
                await _click_subscribe(page)

                # Wait for QR
                slot.status = "waiting_qr"
                slot.status_msg = "Waiting for QR code… (up to 40s)"
                qr_sel = (
                    'img[data-testid="QRCode-image"], img.QRCode-image,'
                    ' img[src*="qr.stripe.com"]'
                )
                qr_found = False
                deadline = time.monotonic() + 40.0
                while time.monotonic() < deadline and not slot._action_event.is_set():
                    for frame in page.frames:
                        try:
                            el = await frame.query_selector(qr_sel)
                            if el:
                                qr_found = True
                                break
                        except Exception:
                            continue
                    if qr_found:
                        break
                    await asyncio.sleep(0.5)

                if slot._action_event.is_set():
                    pass  # early exit
                elif qr_found:
                    slot.status = "qr_visible"
                    slot.status_msg = "QR visible — waiting for payment"
                    await asyncio.sleep(5.0)  # let QR fully render
                    try:
                        await page.evaluate(_OVERLAY_JS)
                    except Exception:
                        pass
                    await self._take_screenshot(slot, page)
                    # Keep alive + periodic screenshots
                    while not slot._action_event.is_set():
                        await asyncio.sleep(3.0)
                        if not slot._action_event.is_set():
                            await self._take_screenshot(slot, page)
                else:
                    slot.status = "error"
                    slot.status_msg = "QR not found within 40s"
                    try:
                        await page.evaluate(_OFF_ONLY_JS)
                    except Exception:
                        pass
                    await self._take_screenshot(slot, page)
                    await slot._action_event.wait()

            await self._finish(slot)

        except asyncio.CancelledError:
            slot.status = "off"
            slot.status_msg = "Cancelled"
        except Exception as exc:
            slot.status = "error"
            slot.status_msg = str(exc)[:120]
        finally:
            try:
                await cf.__aexit__(None, None, None)
            except Exception:
                pass

    async def _finish(self, slot: WatchSlot) -> None:
        action = slot._pending_action or "off"
        slot.status = {"done": "done", "fail": "failed"}.get(action, "off")
        slot.status_msg = {
            "done": "✅ Marked as paid",
            "fail": "❌ Marked as failed",
            "off": "Closed",
        }.get(action, "Closed")
        if self._on_job_action and action in ("done", "fail"):
            try:
                self._on_job_action(slot.job_id, action)
            except Exception:
                pass
