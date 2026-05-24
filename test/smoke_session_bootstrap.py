"""Smoke: bootstrap NextAuth and navigate to auth.openai.com for session flow."""
from __future__ import annotations

import asyncio
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from gpt_signup_hybrid._browser_retry import is_driver_dead_error
from gpt_signup_hybrid._nextauth_bootstrap import bootstrap_authorize_url
from gpt_signup_hybrid.config import ensure_runtime_dirs, load_settings, prepare_profile_dir


async def _probe_camoufox(profile_dir: Path, *, headless: bool) -> tuple[str, str]:
    from camoufox.async_api import AsyncCamoufox
    from camoufox.utils import Screen as _Screen

    settings = load_settings(ROOT)
    w, h = settings.browser_viewport_width, settings.browser_viewport_height
    chrome_h = 85
    extra_config: dict[str, Any] = {
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
        viewport={"width": w, "height": h},
        screen=fixed_screen,
        ignore_https_errors=False,
        config=extra_config,
    )
    ctx = await cf.__aenter__()
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        url = await bootstrap_authorize_url(
            page,
            email="probe@example.com",
            device_id=str(uuid.uuid4()),
            logging_id=str(uuid.uuid4()),
        )
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(2.0)
        return url.split("?")[0], page.url.split("?")[0]
    finally:
        try:
            await cf.__aexit__(None, None, None)
        except Exception:
            pass


async def _probe_chromium(profile_dir: Path, *, headless: bool) -> tuple[str, str]:
    from playwright.async_api import async_playwright

    settings = load_settings(ROOT)
    playwright = await async_playwright().start()
    try:
        ctx = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            channel=settings.browser_channel or None,
            viewport={
                "width": settings.browser_viewport_width,
                "height": settings.browser_viewport_height,
            },
            ignore_https_errors=False,
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
            url = await bootstrap_authorize_url(
                page,
                email="probe@example.com",
                device_id=str(uuid.uuid4()),
                logging_id=str(uuid.uuid4()),
            )
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(2.0)
            return url.split("?")[0], page.url.split("?")[0]
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
    finally:
        await playwright.stop()


async def main() -> int:
    settings = load_settings(ROOT)
    engines = (
        ["camoufox", "chromium"]
        if (settings.browser_engine or "camoufox").lower() == "camoufox"
        else ["chromium"]
    )
    failures = 0

    for engine in engines:
        if engine == "camoufox":
            profile_dir = settings.profiles_dir / f"smoke_camoufox_{uuid.uuid4().hex[:8]}"
            template_dir = settings.browser_camoufox_profile_dir
        else:
            profile_dir = settings.profiles_dir / f"smoke_chromium_{uuid.uuid4().hex[:8]}"
            template_dir = settings.browser_profile_template_dir

        ensure_runtime_dirs(settings, extra=(profile_dir,))
        prepare_profile_dir(
            profile_dir=profile_dir,
            template_dir=template_dir,
            use_template=settings.browser_use_profile_template,
        )
        try:
            probe = _probe_camoufox if engine == "camoufox" else _probe_chromium
            auth_url, landed_url = await probe(profile_dir, headless=True)
            print(f"OK engine={engine} authorize={auth_url} landed={landed_url}")
        except Exception as exc:
            failures += 1
            print(
                f"FAIL engine={engine} "
                f"driver_dead={is_driver_dead_error(exc)} "
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
