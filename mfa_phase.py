"""Enable 2FA (TOTP) cho account đã đăng ký.

Flow theo HAR:
    1. POST /backend-api/accounts/mfa/enroll body {"factor_type":"totp"}
       → trả {secret, session_id, factor: {id, factor_type:"totp", ...}}
    2. (Optional) POST /backend-api/accounts/mfa/user/activate_enrollment
       body {"factor_id":..., "session_id":..., "code":"<6-digit TOTP>"}
       → confirm enrollment, mfa_enabled=true.

Secret base32 tương thích Google Authenticator. Lưu để gen code mỗi lần login.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from curl_cffi import requests as curl_requests

from .totp_helper import generate_code, normalize_secret


_BASE = "https://chatgpt.com/backend-api"

# Account mới create_account → server-side cần thời gian để propagate sang
# /backend-api. Retry với backoff cho cả enroll + activate.
_HTTP_TIMEOUT = 60.0
_MAX_ATTEMPTS = 4
_BACKOFF_SECONDS = (3.0, 6.0, 10.0)  # delay sau attempt 1, 2, 3


class MfaError(Exception):
    """MFA enable fail."""


def _build_session(*, user_agent: str, impersonate: str = "firefox135", proxy: str | None = None):
    session = curl_requests.Session(impersonate=impersonate)
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    })
    return session


def _refresh_access_token(
    session, *, cookies: list[dict[str, Any]], log,
) -> str | None:
    """Gọi /api/auth/session với session cookies để lấy access_token mới."""
    # Inject cookies chatgpt.com vào session
    for c in cookies:
        domain = (c.get("domain") or "").lower()
        if "chatgpt.com" in domain:
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain") or "chatgpt.com",
                path=c.get("path") or "/",
            )
    url = "https://chatgpt.com/api/auth/session"
    try:
        r = session.get(url, timeout=30)
        if r.status_code != 200:
            log(f"[mfa] refresh token: HTTP {r.status_code}")
            return None
        data = r.json()
        token = data.get("accessToken")
        if token:
            log("[mfa] access_token refreshed OK")
        else:
            log("[mfa] refresh response missing accessToken")
        return token
    except Exception as exc:
        log(f"[mfa] refresh token error: {exc}")
        return None


def _post_with_retry(
    session, *, url: str, headers: dict, body: dict, log, label: str,
):
    """POST với retry exponential backoff khi timeout/5xx/connection error."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            r = session.post(
                url, headers=headers, data=json.dumps(body), timeout=_HTTP_TIMEOUT,
            )
            if r.status_code in (502, 503, 504):
                log(f"[mfa] {label} HTTP {r.status_code} attempt {attempt} — retry")
                last_exc = MfaError(f"{label} HTTP {r.status_code}")
            else:
                return r
        except Exception as exc:
            last_exc = exc
            log(f"[mfa] {label} attempt {attempt} error: {exc}")

        if attempt < _MAX_ATTEMPTS:
            backoff = _BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)]
            log(f"[mfa] retry in {backoff:.0f}s...")
            time.sleep(backoff)

    raise MfaError(f"{label} failed sau {_MAX_ATTEMPTS} attempts: {last_exc}")


def _enroll_totp(session, *, access_token: str, cookies: list[dict[str, Any]] | None, log) -> tuple[dict[str, Any], str]:
    """POST /mfa/enroll → trả (enroll_data, access_token_used).

    Nếu 401 token_revoked + có cookies → refresh access_token rồi retry.
    """
    url = f"{_BASE}/accounts/mfa/enroll"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    log("[mfa] POST mfa/enroll factor_type=totp")
    r = _post_with_retry(
        session, url=url, headers=headers, body={"factor_type": "totp"},
        log=log, label="enroll",
    )
    # 401 token_revoked → refresh access_token rồi retry 1 lần
    if r.status_code == 401 and cookies:
        body_text = r.text[:500]
        if "token_revoked" in body_text or "invalidated" in body_text:
            log("[mfa] token revoked — refreshing access_token...")
            new_token = _refresh_access_token(session, cookies=cookies, log=log)
            if new_token:
                access_token = new_token
                headers["Authorization"] = f"Bearer {access_token}"
                time.sleep(2.0)  # small delay cho server propagate
                r = _post_with_retry(
                    session, url=url, headers=headers, body={"factor_type": "totp"},
                    log=log, label="enroll-retry",
                )
            else:
                raise MfaError(f"enroll failed HTTP 401 + refresh failed: {body_text}")

    if r.status_code != 200:
        raise MfaError(f"enroll failed HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if "secret" not in data:
        raise MfaError(f"enroll response missing secret: {data}")
    log(f"[mfa] enroll OK factor_id={data.get('factor', {}).get('id', '?')[:20]} secret_len={len(data['secret'])}")
    return data, access_token


def _activate_enrollment(
    session,
    *,
    access_token: str,
    factor_id: str,
    session_id: str,
    code: str,
    log,
) -> dict[str, Any]:
    """POST /mfa/user/activate_enrollment để confirm 6-digit TOTP."""
    url = f"{_BASE}/accounts/mfa/user/activate_enrollment"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = {
        "factor_id": factor_id,
        "factor_type": "totp",
        "session_id": session_id,
        "code": code,
    }
    log(f"[mfa] POST activate_enrollment factor_id={factor_id[:20]} code={code}")
    r = _post_with_retry(
        session, url=url, headers=headers, body=body, log=log, label="activate",
    )
    if r.status_code != 200:
        raise MfaError(f"activate failed HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    log(f"[mfa] activate OK")
    return data


def _check_mfa_info(session, *, access_token: str, log) -> dict[str, Any]:
    """GET /mfa_info để verify trạng thái sau khi activate."""
    url = f"{_BASE}/accounts/mfa_info"
    headers = {"Authorization": f"Bearer {access_token}"}
    r = session.get(url, headers=headers, timeout=30)
    if r.status_code != 200:
        log(f"[mfa] WARN mfa_info HTTP {r.status_code}")
        return {}
    return r.json()


async def enable_2fa(
    *,
    access_token: str,
    cookies: list[dict[str, Any]] | None = None,
    user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0",
    impersonate: str = "firefox135",
    proxy: str | None = None,
    activate: bool = True,
    log=print,
) -> dict[str, Any]:
    """Enable 2FA TOTP cho account hiện tại.

    Args:
        access_token: Bearer JWT của account (lấy từ SignupResult).
        cookies: Session cookies chatgpt.com — dùng để refresh token nếu bị revoke.
        user_agent / impersonate: phải khớp với phiên browser nếu lo Sentinel.
        proxy: HTTP/HTTPS proxy.
        activate: True = gọi activate_enrollment với code TOTP đầu tiên (bật 2FA luôn).
                  False = chỉ enroll, lưu secret để mày confirm sau.
        log: callable.

    Returns:
        {
            "secret": "B2P3OQCCXINLHGPUDIS55DHQDW5MENK5",
            "factor_id": "6a0beb...",
            "session_id": "6a0beb...",
            "provisioning_uri": "otpauth://totp/...",
            "first_code": "763657",  # code TOTP gen từ secret tại t=now
            "activated": True / False,
            "mfa_info": {...}  # nếu activate=True, response /mfa_info sau khi enable
        }
    """
    def _sync() -> dict[str, Any]:
        session = _build_session(user_agent=user_agent, impersonate=impersonate, proxy=proxy)
        try:
            enroll, active_token = _enroll_totp(
                session, access_token=access_token, cookies=cookies, log=log,
            )
            secret = normalize_secret(enroll["secret"])
            factor_id = enroll["factor"]["id"]
            session_id = enroll["session_id"]
            first_code = generate_code(secret)

            result: dict[str, Any] = {
                "secret": secret,
                "factor_id": factor_id,
                "session_id": session_id,
                "provisioning_uri": f"otpauth://totp/ChatGPT?secret={secret}&issuer=ChatGPT",
                "first_code": first_code,
                "activated": False,
                "mfa_info": None,
            }

            if activate:
                _activate_enrollment(
                    session,
                    access_token=active_token,
                    factor_id=factor_id,
                    session_id=session_id,
                    code=first_code,
                    log=log,
                )
                result["activated"] = True
                result["mfa_info"] = _check_mfa_info(session, access_token=active_token, log=log)

            return result
        finally:
            try:
                session.close()
            except Exception:
                pass

    return await asyncio.to_thread(_sync)
