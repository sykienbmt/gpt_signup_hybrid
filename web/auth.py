"""Token-based auth cho web control plane.

Singleton token được sinh khi process start (hoặc đọc từ env), dùng để gate
toàn bộ /api/*. Token có thể đi qua:
  - Header   "X-API-Token: <token>"
  - Query    "?token=<token>"  (cho EventSource — JS EventSource không set header)
  - Cookie   "gsh_token=<token>"

CORS — server bind 127.0.0.1 default; khi user opt-in non-loopback bind, vẫn
yêu cầu token nên không cần SOP exception. Không bật CORS *.
"""
from __future__ import annotations

import os
import secrets
from typing import Final

from fastapi import HTTPException, Request


_ENV_KEY: Final[str] = "GPT_SIGNUP_WEB_TOKEN"
_HEADER_NAME: Final[str] = "X-API-Token"
_COOKIE_NAME: Final[str] = "gsh_token"
_QUERY_NAME: Final[str] = "token"


_token_singleton: str | None = None


def _generate() -> str:
    return secrets.token_urlsafe(24)


def get_token() -> str:
    """Lấy token hiện tại (lazy init).

    Nếu env GPT_SIGNUP_WEB_TOKEN được set → dùng giá trị đó (cho automation).
    Ngược lại → sinh ngẫu nhiên 1 lần per-process.
    """
    global _token_singleton  # noqa: PLW0603 — singleton hợp lý
    if _token_singleton is None:
        env_val = os.environ.get(_ENV_KEY, "").strip()
        _token_singleton = env_val or _generate()
    return _token_singleton


def reset_token_for_tests(value: str | None = None) -> str:
    """Test-only helper: reset singleton. Không gọi từ runtime code."""
    global _token_singleton  # noqa: PLW0603
    _token_singleton = value
    return get_token()


def _extract_token(request: Request) -> str | None:
    header_val = request.headers.get(_HEADER_NAME)
    if header_val:
        return header_val.strip()
    query_val = request.query_params.get(_QUERY_NAME)
    if query_val:
        return query_val.strip()
    cookie_val = request.cookies.get(_COOKIE_NAME)
    if cookie_val:
        return cookie_val.strip()
    return None


def require_token(request: Request) -> None:
    """FastAPI dependency: raise 401 nếu token sai/thiếu.

    Constant-time compare để tránh timing oracle.
    """
    expected = get_token()
    provided = _extract_token(request)
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid auth token",
            headers={"WWW-Authenticate": "Token"},
        )
