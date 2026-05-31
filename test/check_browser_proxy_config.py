"""Verify browser proxy config normalization."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from gpt_signup_hybrid.config import browser_proxy_config  # noqa: E402


def main() -> int:
    cfg = browser_proxy_config("http://admin:admin@hndc41.proxyxoay.net:58196")
    assert cfg == {
        "server": "http://hndc41.proxyxoay.net:58196",
        "username": "admin",
        "password": "admin",
    }

    cfg2 = browser_proxy_config("socks5://127.0.0.1:1080")
    assert cfg2 == {"server": "socks5://127.0.0.1:1080"}

    assert browser_proxy_config("") is None
    print("OK browser proxy config")
    return 0


if __name__ == "__main__":
    sys.exit(main())
