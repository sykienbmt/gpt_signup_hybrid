"""Verify Change Password live log buffer behavior."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from gpt_signup_hybrid.web.server import (  # noqa: E402
    _append_change_password_log,
    _change_password_logs,
    _finish_change_password_log,
    change_password_log,
)


async def _check() -> None:
    request_id = "test_live_logs"
    _change_password_logs.pop(request_id, None)

    _append_change_password_log(request_id, "first")
    _append_change_password_log(request_id, "second")

    response = await change_password_log(request_id)
    data = json.loads(response.body.decode("utf-8"))
    assert data["request_id"] == request_id
    assert data["logs"] == ["first", "second"]
    assert data["done"] is False

    _finish_change_password_log(request_id)
    response = await change_password_log(request_id)
    data = json.loads(response.body.decode("utf-8"))
    assert data["logs"] == ["first", "second"]
    assert data["done"] is True

    _change_password_logs.pop(request_id, None)


def main() -> int:
    asyncio.run(_check())
    print("OK change password live logs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
