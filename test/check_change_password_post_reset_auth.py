"""Verify post-reset auth handles MFA after Continue before session fetch."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

import gpt_signup_hybrid.change_password_phase as cp  # noqa: E402


class FakePage:
    url = "https://auth.openai.com/mfa-challenge/post-reset"


async def _check() -> None:
    page = FakePage()
    events: list[str] = []
    logs: list[str] = []

    original_fill = cp._fill_mfa_if_needed
    original_click = cp._click_post_reset_continue_if_present
    original_sleep = cp.asyncio.sleep

    async def fake_sleep(_seconds: float) -> None:
        return None

    async def fake_fill(page_arg, secret, log, step="") -> None:
        assert page_arg is page
        assert secret == "SECRET"
        assert step == "reset"
        events.append("mfa")
        page.url = "https://auth.openai.com/post-reset-continue"

    async def fake_click(page_arg, log) -> bool:
        assert page_arg is page
        events.append("continue")
        page.url = "https://chatgpt.com/"
        return True

    cp._fill_mfa_if_needed = fake_fill
    cp._click_post_reset_continue_if_present = fake_click
    cp.asyncio.sleep = fake_sleep
    try:
        await cp._complete_post_reset_auth(page, "SECRET", logs.append)
    finally:
        cp._fill_mfa_if_needed = original_fill
        cp._click_post_reset_continue_if_present = original_click
        cp.asyncio.sleep = original_sleep

    assert events == ["mfa", "continue"]
    assert page.url == "https://chatgpt.com/"
    assert cp._REAUTH_FORM_NAV_WAIT_SECONDS <= 4.0


def main() -> int:
    asyncio.run(_check())
    print("OK change password post-reset auth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
