"""Shared NextAuth bootstrap helpers for chatgpt.com auth flows."""
from __future__ import annotations

from typing import Any


async def bootstrap_authorize_url_http(
    *,
    proxy: str | None = None,
    email: str | None = None,
    device_id: str,
    logging_id: str | None = None,
    callback_url: str = "https://chatgpt.com/",
) -> tuple[str, list[dict]]:
    """Fetch the auth.openai.com authorize URL via HTTP — no browser needed.

    Returns (authorize_url, chatgpt_cookies) where chatgpt_cookies must be
    injected into the browser context before navigating to the authorize URL,
    so that NextAuth can validate the state/CSRF when auth.openai.com redirects
    back to chatgpt.com/api/auth/callback.
    """
    from curl_cffi.requests import AsyncSession

    proxies = {"http": proxy, "https": proxy} if proxy else None
    async with AsyncSession(impersonate="firefox135", proxies=proxies) as sess:
        csrf_resp = await sess.get(
            "https://chatgpt.com/api/auth/csrf",
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        if not csrf_resp.ok:
            raise ValueError(f"csrf HTTP {csrf_resp.status_code}")
        csrf_token = (csrf_resp.json() or {}).get("csrfToken")
        if not csrf_token:
            raise ValueError("csrf token missing from response")

        params: dict[str, str] = {
            "prompt": "login",
            "ext-oai-did": device_id,
            "ext-passkey-client-capabilities": "0100",
            "screen_hint": "login_or_signup",
        }
        if logging_id:
            params["auth_session_logging_id"] = logging_id
        if email:
            params["login_hint"] = email

        sign_resp = await sess.post(
            "https://chatgpt.com/api/auth/signin/openai",
            params=params,
            data=f"callbackUrl={callback_url}&csrfToken={csrf_token}&json=true",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Origin": "https://chatgpt.com",
                "Referer": "https://chatgpt.com/",
            },
            timeout=15.0,
        )
        if not sign_resp.ok:
            raise ValueError(f"signin HTTP {sign_resp.status_code}")
        url = (sign_resp.json() or {}).get("url") or ""
        if "auth.openai.com" not in url:
            raise ValueError(f"signin returned unexpected URL: {url!r}")

        # Collect chatgpt.com cookies so caller can inject them into the browser.
        # Without these (next-auth.csrf-token, next-auth.callback-url, etc.) the
        # OAuth callback will fail with /auth/error because NextAuth can't validate
        # the state parameter.
        raw_cookies = sess.cookies.get_dict(domain="chatgpt.com") or {}
        browser_cookies = [
            {
                "name": name,
                "value": value,
                "domain": ".chatgpt.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
            for name, value in raw_cookies.items()
        ]
        return url, browser_cookies


BOOTSTRAP_JS = r"""
async ({email, deviceId, loggingId, callbackUrl}) => {
    const params = new URLSearchParams({
        'prompt': 'login',
        'ext-oai-did': deviceId,
        'ext-passkey-client-capabilities': '0100',
        'screen_hint': 'login_or_signup',
    });
    if (loggingId) params.set('auth_session_logging_id', loggingId);
    if (email) params.set('login_hint', email);

    const csrfRes = await fetch('/api/auth/csrf', {credentials: 'include'});
    if (!csrfRes.ok) throw new Error('csrf HTTP ' + csrfRes.status);
    const csrfData = await csrfRes.json();
    const csrfToken = csrfData.csrfToken;
    if (!csrfToken) throw new Error('csrf token missing');

    const body = new URLSearchParams({
        callbackUrl: callbackUrl || 'https://chatgpt.com/',
        csrfToken,
        json: 'true',
    }).toString();
    const signRes = await fetch('/api/auth/signin/openai?' + params.toString(), {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body,
    });
    if (!signRes.ok) throw new Error('signin HTTP ' + signRes.status);
    const signData = await signRes.json();
    if (!signData.url) {
        throw new Error('signin missing url: ' + JSON.stringify(signData));
    }
    return signData.url;
}
"""


async def bootstrap_authorize_url(
    page: Any,
    *,
    device_id: str,
    email: str | None = None,
    logging_id: str | None = None,
    callback_url: str = "https://chatgpt.com/",
) -> str:
    """Return the auth.openai.com authorize URL bootstrapped from chatgpt.com."""
    url = await page.evaluate(
        BOOTSTRAP_JS,
        {
            "email": email or "",
            "deviceId": device_id,
            "loggingId": logging_id or "",
            "callbackUrl": callback_url,
        },
    )
    if not isinstance(url, str) or "auth.openai.com" not in url:
        raise ValueError(f"bootstrap returned bad URL: {url!r}")
    return url
