"""Verify proxy rotate command parsing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from gpt_signup_hybrid.web.proxy_rotate import (  # noqa: E402
    extract_proxy_from_response,
    parse_rotate_command,
)


def main() -> int:
    direct = parse_rotate_command("https://provider.example/rotate")
    assert direct.method == "GET"
    assert direct.url == "https://provider.example/rotate"

    curl = parse_rotate_command(
        "curl -X POST https://provider.example/rotate "
        "-H Authorization:token --data-raw mode=rotate"
    )
    assert curl.method == "POST"
    assert curl.url == "https://provider.example/rotate"
    assert curl.headers["Authorization"] == "token"
    assert curl.data == "mode=rotate"

    win_curl = parse_rotate_command(
        'curl ^"https://proxyxoay.example/api/change^" ^\n'
        '  -H ^"accept: application/json, text/plain, */*^" ^\n'
        '  -H ^"authorization: Bearer 123^|abc^" ^\n'
        '  -b ^"laravel_session=abc^%^3D; XSRF-TOKEN=xyz^"'
    )
    assert win_curl.method == "GET"
    assert win_curl.url == "https://proxyxoay.example/api/change"
    assert win_curl.headers["accept"] == "application/json, text/plain, */*"
    assert win_curl.headers["authorization"] == "Bearer 123|abc"
    assert win_curl.headers["Cookie"].startswith("laravel_session=")

    proxy = extract_proxy_from_response(
        "",
        {"proxy": "http://user:pass@127.0.0.1:8080"},
    )
    assert proxy == "http://user:pass@127.0.0.1:8080"

    text_proxy = extract_proxy_from_response(
        "rotated socks5://127.0.0.1:1080 ok",
        None,
    )
    assert text_proxy == "socks5://127.0.0.1:1080"

    print("OK proxy rotate parser")
    return 0


if __name__ == "__main__":
    sys.exit(main())
