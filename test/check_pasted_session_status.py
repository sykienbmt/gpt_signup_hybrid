"""Check pasted exported session lines without printing secrets."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from gpt_signup_hybrid.web.check_account import check_accounts  # noqa: E402
from gpt_signup_hybrid.web.manager import get_link_manager  # noqa: E402


async def _run() -> int:
    input_path = os.environ.get("PASTED_SESSION_FILE", "").strip()
    if not input_path:
        print("Missing PASTED_SESSION_FILE", file=sys.stderr)
        return 2
    path = Path(input_path)
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    results = await check_accounts(lines, proxy=get_link_manager().proxy, max_concurrent=1)
    for item in results:
        print(f"{item.email}\t{item.status}\t{item.plan}\t{item.error or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
