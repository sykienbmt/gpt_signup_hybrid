"""Phase 2: replay request OTP validate → create_account → callback bằng curl_cffi.

Sequence (đúng theo HAR):
    1. POST  https://auth.openai.com/api/accounts/email-otp/validate      body {"code":"..."}
    2. GET   https://auth.openai.com/api/accounts/client_auth_session_dump (optional, browser warmup)
    3. POST  https://auth.openai.com/api/accounts/create_account          body {"name":..,"birthdate":..}
       → trả {"continue_url": "https://chatgpt.com/api/auth/callback/openai?code=..&state=.."}
    4. GET   continue_url                                                  (NextAuth callback, set session-token)

Sau bước 4 ta có cookie `__Secure-next-auth.session-token` trên chatgpt.com.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from curl_cffi import requests as curl_requests

from .models import BrowserHandoff, SignupRequest, SignupResult


class HttpPhaseError(Exception):
    """Phase 2 failed."""


_AUTH_OPENAI_BASE = "https://auth.openai.com"
_CHATGPT_BASE = "https://chatgpt.com"


def _cookies_for_domain(handoff: BrowserHandoff, domain: str) -> dict[str, str]:
    """Lọc cookies theo domain (case-insensitive, suffix match)."""
    target = domain.lower().lstrip(".")
    out: dict[str, str] = {}
    for c in handoff.cookies:
        cd = (c.get("domain") or "").lower().lstrip(".")
        if cd == target or target.endswith(f".{cd}"):
            out[c["name"]] = c["value"]
    return out


def _build_session(*, request: SignupRequest, handoff: BrowserHandoff) -> curl_requests.Session:
    """Tạo curl_cffi Session với cookies + impersonate Firefox 135."""
    session = curl_requests.Session(impersonate=request.impersonate)
    if request.proxy:
        session.proxies = {"http": request.proxy, "https": request.proxy}
    # Inject cookies từ handoff. curl_cffi accept dict cookie nhưng để cookie domain riêng,
    # ta set thẳng vào jar qua header đầu tiên.
    return session


def _common_headers(*, referer: str, user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": referer,
        "Origin": _AUTH_OPENAI_BASE,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def _cookies_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _validate_otp(
    *,
    session: curl_requests.Session,
    request: SignupRequest,
    handoff: BrowserHandoff,
    code: str,
    log,
) -> dict[str, str]:
    """POST /email-otp/validate. Trả về cookies update (dict)."""
    url = f"{_AUTH_OPENAI_BASE}/api/accounts/email-otp/validate"
    cookies = _cookies_for_domain(handoff, "auth.openai.com")
    headers = _common_headers(
        referer=f"{_AUTH_OPENAI_BASE}/email-verification",
        user_agent=request.user_agent,
    )
    headers["Content-Type"] = "application/json"
    headers["Cookie"] = _cookies_header(cookies)

    log(f"[http] POST email-otp/validate code={code}")
    response = session.post(url, headers=headers, data=json.dumps({"code": code}), timeout=30)
    if response.status_code != 200:
        raise HttpPhaseError(
            f"validate OTP failed: HTTP {response.status_code}: {response.text[:300]}"
        )
    data = response.json()
    if data.get("continue_url") != f"{_AUTH_OPENAI_BASE}/about-you":
        log(f"[http] WARNING continue_url={data.get('continue_url')} (expected /about-you)")

    # Lấy cookies update từ response (set-cookie)
    new_cookies: dict[str, str] = {}
    for cookie in session.cookies.jar:
        if "openai.com" in (cookie.domain or ""):
            new_cookies[cookie.name] = cookie.value
    return new_cookies


def _create_account(
    *,
    session: curl_requests.Session,
    request: SignupRequest,
    handoff: BrowserHandoff,
    cookies: dict[str, str],
    log,
) -> str:
    """POST /create_account. Trả về continue_url (callback URL)."""
    url = f"{_AUTH_OPENAI_BASE}/api/accounts/create_account"
    headers = _common_headers(
        referer=f"{_AUTH_OPENAI_BASE}/about-you",
        user_agent=request.user_agent,
    )
    headers["Content-Type"] = "application/json"
    headers["Cookie"] = _cookies_header(cookies)

    body = {"name": request.name, "birthdate": request.birthdate}
    log(f"[http] POST create_account name={request.name!r} birthdate={request.birthdate}")
    response = session.post(url, headers=headers, data=json.dumps(body), timeout=30)
    if response.status_code != 200:
        raise HttpPhaseError(
            f"create_account failed: HTTP {response.status_code}: {response.text[:500]}"
        )
    data = response.json()
    continue_url = data.get("continue_url") or (data.get("page", {}).get("payload", {}) or {}).get("url")
    if not continue_url:
        raise HttpPhaseError(f"create_account missing continue_url: {data}")
    if "/api/auth/callback/openai" not in continue_url:
        raise HttpPhaseError(f"unexpected continue_url: {continue_url}")
    return continue_url


def _callback_openai(
    *,
    session: curl_requests.Session,
    request: SignupRequest,
    handoff: BrowserHandoff,
    callback_url: str,
    log,
) -> dict[str, Any]:
    """GET callback URL → set session-token. Follow 302 về /."""
    cookies = _cookies_for_domain(handoff, "chatgpt.com")
    headers = {
        "User-Agent": request.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": f"{_AUTH_OPENAI_BASE}/",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Cookie": _cookies_header(cookies),
        "Upgrade-Insecure-Requests": "1",
    }
    log(f"[http] GET callback {callback_url[:100]}...")
    response = session.get(callback_url, headers=headers, timeout=30, allow_redirects=False)
    if response.status_code not in (302, 303, 307):
        raise HttpPhaseError(
            f"callback expected redirect, got HTTP {response.status_code}: {response.text[:300]}"
        )

    # Parse cookies set bởi callback
    out_cookies: list[dict[str, Any]] = []
    session_token: str | None = None
    account_id: str | None = None
    for cookie in session.cookies.jar:
        cd = (cookie.domain or "").lower()
        if "chatgpt.com" not in cd:
            continue
        out_cookies.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
        })
        if cookie.name == "__Secure-next-auth.session-token":
            session_token = cookie.value
        elif cookie.name == "_account":
            account_id = cookie.value

    if not session_token:
        raise HttpPhaseError("callback không set __Secure-next-auth.session-token")

    return {
        "cookies": out_cookies,
        "session_token": session_token,
        "account_id": account_id,
    }


def _fetch_access_token(
    *,
    session: curl_requests.Session,
    request: SignupRequest,
    log,
) -> tuple[str | None, str | None]:
    """Optional: gọi /api/auth/session để lấy access token + user_id sau callback."""
    url = f"{_CHATGPT_BASE}/api/auth/session"
    headers = {
        "User-Agent": request.user_agent,
        "Accept": "application/json",
        "Referer": f"{_CHATGPT_BASE}/",
    }
    try:
        response = session.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            log(f"[http] WARN /api/auth/session HTTP {response.status_code}")
            return None, None
        data = response.json()
        access = data.get("accessToken")
        user = data.get("user", {}) or {}
        return access, user.get("id")
    except Exception as exc:
        log(f"[http] WARN fetch access_token failed: {exc}")
        return None, None


def _extract_session_from_handoff(handoff: BrowserHandoff) -> dict[str, Any]:
    """Đọc session-token + cookies chatgpt.com từ handoff (browser đã set sẵn).

    NextAuth có thể split token thành nhiều chunk: `.session-token.0`, `.session-token.1`.
    Phase 2 client (browser auth state) cần đầy đủ cả 2 chunk để decode đúng.
    """
    out_cookies: list[dict[str, Any]] = []
    session_token: str | None = None
    session_token_chunks: dict[str, str] = {}
    account_id: str | None = None
    for c in handoff.cookies:
        domain = (c.get("domain") or "").lower()
        if "chatgpt.com" not in domain:
            continue
        out_cookies.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain"),
            "path": c.get("path"),
            "secure": c.get("secure", False),
        })
        name = c["name"]
        if name == "__Secure-next-auth.session-token":
            session_token = c["value"]
        elif name.startswith("__Secure-next-auth.session-token."):
            # Chunk: .0, .1, ...
            idx = name.rsplit(".", 1)[-1]
            session_token_chunks[idx] = c["value"]
        elif name == "_account":
            account_id = c["value"]

    # Nếu có chunks, ghép lại theo thứ tự index
    if session_token is None and session_token_chunks:
        ordered = "".join(session_token_chunks[k] for k in sorted(session_token_chunks))
        session_token = ordered

    if not session_token:
        raise HttpPhaseError("handoff cookies không có __Secure-next-auth.session-token")
    return {
        "cookies": out_cookies,
        "session_token": session_token,
        "account_id": account_id,
    }


async def run_http_phase(
    *,
    request: SignupRequest,
    handoff: BrowserHandoff,
    log,
) -> dict[str, Any]:
    """Phase 2: extract session-token từ handoff cookies + fetch access_token.

    Browser đã đi qua callback và set cookies session-token sẵn ở Phase 1.
    Chỉ cần đọc lại + optionally gọi /api/auth/session để lấy access_token JWT.
    """
    def _sync() -> dict[str, Any]:
        result = _extract_session_from_handoff(handoff)
        log(f"[http] session-token from handoff ({len(result['session_token'])} bytes)")
        # Build curl_cffi session với cookies handoff để gọi /api/auth/session
        session = _build_session(request=request, handoff=handoff)
        # Inject cookies chatgpt.com
        try:
            for c in result["cookies"]:
                session.cookies.set(
                    c["name"], c["value"],
                    domain=c.get("domain") or "chatgpt.com",
                    path=c.get("path") or "/",
                )
            access_token, user_id = _fetch_access_token(session=session, request=request, log=log)
            return {**result, "access_token": access_token, "user_id": user_id}
        finally:
            try:
                session.close()
            except Exception:
                pass

    return await asyncio.to_thread(_sync)
