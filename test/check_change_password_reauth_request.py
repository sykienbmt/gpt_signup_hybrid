"""Verify reauth redirect fallback uses external HTTP with browser cookies."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

import curl_cffi.requests as curl_requests  # noqa: E402
from gpt_signup_hybrid.change_password_phase import _fetch_reauth_redirect_url  # noqa: E402


class FakeResponse:
    status_code = 200
    text = '{"url":"https://auth.openai.com/u/login/password"}'

    def json(self) -> dict:
        return {"url": "https://auth.openai.com/u/login/password"}


class FakeAsyncSession:
    calls: list[dict] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, "session_kwargs": self.kwargs, **kwargs})
        return FakeResponse()


class FakeContext:
    async def cookies(self, url: str) -> list[dict]:
        assert url == "https://chatgpt.com/"
        return [
            {"name": "__Secure-next-auth.session-token", "value": "token", "domain": ".chatgpt.com"},
            {"name": "ignored", "value": "x", "domain": ".example.com"},
        ]


async def _check() -> None:
    original = curl_requests.AsyncSession
    curl_requests.AsyncSession = FakeAsyncSession
    try:
        logs: list[str] = []
        url = await _fetch_reauth_redirect_url(
            FakeContext(),
            "/api/auth/signin/openai?reauth=password",
            "csrf-token",
            logs.append,
            "http://user:pass@127.0.0.1:8080",
        )
    finally:
        curl_requests.AsyncSession = original

    call = FakeAsyncSession.calls[0]
    assert url == "https://auth.openai.com/u/login/password"
    assert call["url"] == "https://chatgpt.com/api/auth/signin/openai?reauth=password"
    assert call["data"]["csrfToken"] == "csrf-token"
    assert call["data"]["json"] == "true"
    assert "__Secure-next-auth.session-token=token" in call["headers"]["Cookie"]
    assert "ignored=x" not in call["headers"]["Cookie"]
    assert call["session_kwargs"]["proxies"]["http"] == "http://user:pass@127.0.0.1:8080"
    assert logs == ["[chpwd] reauth request HTTP 200"]


def main() -> int:
    asyncio.run(_check())
    print("OK change password reauth external request")
    return 0


if __name__ == "__main__":
    sys.exit(main())
