"""Smoke test: launch Camoufox + bootstrap authorize URL + page.goto(authorize).

Mục tiêu: reproduce đúng đoạn fail trong browser_phase.py (chatgpt.com loaded
→ bootstrap NextAuth → page.goto(authorize_url)) để khẳng định stack
playwright 1.49 + Python 3.13 có còn lỗi `Connection closed while reading
from the driver` không.

Chạy:
    .venv313/bin/python test/smoke_camoufox_authorize.py
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path


def _ensure_root_on_path() -> None:
    here = Path(__file__).resolve().parent.parent  # gpt_signup_hybrid/
    root = here.parent  # gpt_reg/
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_root_on_path()


from gpt_signup_hybrid._nextauth_bootstrap import bootstrap_authorize_url
from gpt_signup_hybrid.config import (
    ensure_runtime_dirs,
    load_settings,
    prepare_profile_dir,
)


async def main() -> int:
    settings = load_settings()
    job_id = f"smoke_{uuid.uuid4().hex[:8]}"

    if (settings.browser_engine or "").lower() != "camoufox":
        print(
            f"[smoke] BROWSER_ENGINE={settings.browser_engine!r} (expected 'camoufox')",
            file=sys.stderr,
        )
        return 2

    profile_dir = settings.profiles_dir / f"camoufox_{job_id}"
    template_dir = settings.browser_camoufox_profile_dir
    ensure_runtime_dirs(settings, extra=(profile_dir,))
    prepare_profile_dir(
        profile_dir=profile_dir,
        template_dir=template_dir,
        use_template=settings.browser_use_profile_template,
    )

    from camoufox.async_api import AsyncCamoufox
    from camoufox.utils import Screen as _Screen

    w, h = settings.browser_viewport_width, settings.browser_viewport_height
    chrome_h = 85
    extra_config: dict = {
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
        min_width=w, max_width=w, min_height=h + chrome_h, max_height=h + chrome_h,
    )

    cf = AsyncCamoufox(
        headless=True,
        persistent_context=True,
        user_data_dir=str(profile_dir),
        viewport={"width": w, "height": h},
        screen=fixed_screen,
        config=extra_config,
    )

    device_id = str(uuid.uuid4())
    logging_id = str(uuid.uuid4())
    email = "smoke_test@example.com"

    ctx = await cf.__aenter__()
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print("[smoke] step 1: page.goto chatgpt.com")
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        print("[smoke] step 1 ok — chatgpt.com loaded")

        print("[smoke] step 2: bootstrap NextAuth (csrf + signin)")
        authorize_url = await bootstrap_authorize_url(
            page,
            email=email,
            device_id=device_id,
            logging_id=logging_id,
        )
        print(f"[smoke] step 2 ok — authorize URL: {authorize_url[:120]}...")

        print("[smoke] step 3: page.goto(authorize_url) — đoạn fail trước đây")
        await page.goto(authorize_url, wait_until="domcontentloaded")
        print(f"[smoke] step 3 ok — landed at: {page.url[:120]}")

        await asyncio.sleep(1.0)
        print("[smoke] PASS")
        return 0
    finally:
        try:
            await cf.__aexit__(None, None, None)
        except Exception as exc:
            print(f"[smoke] cleanup warning: {type(exc).__name__}: {exc}", file=sys.stderr)
        import shutil
        shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
