"""Smoke check proxy rotate API is reachable."""
from __future__ import annotations

import json
import sys
import urllib.request


def main() -> int:
    with urllib.request.urlopen("http://127.0.0.1:8083/api/proxy/rotate/config", timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    assert resp.status == 200
    assert "enabled" in data
    assert "command" in data
    assert "interval_seconds" in data
    print("OK proxy rotate API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
