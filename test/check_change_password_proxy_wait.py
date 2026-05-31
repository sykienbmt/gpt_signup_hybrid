"""Verify change_password proxy preflight helper imports."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from gpt_signup_hybrid.change_password_phase import _wait_proxy_ready  # noqa: E402


def main() -> int:
    assert callable(_wait_proxy_ready)
    print("OK change password proxy preflight import")
    return 0


if __name__ == "__main__":
    sys.exit(main())
