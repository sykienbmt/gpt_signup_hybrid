"""Check ChatGPT account plan status (Plus / Free / etc.) via accessToken."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from curl_cffi.requests import AsyncSession


_ACCOUNTS_CHECK_URL = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
_IMPERSONATE = "chrome136"

_PLAN_LABELS = {
    "chatgptplusplan": "Plus",
    "chatgptteamplan": "Team",
    "chatgptenterpriseplan": "Enterprise",
    "chatgptproplan": "Pro",
    "chatgptfreeplan": "Free",
}


@dataclass
class CheckResult:
    email: str
    status: str           # "plus" | "free" | "team" | "pro" | "enterprise" | "expired" | "error"
    plan: str             # human label
    is_plus: bool
    error: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "status": self.status,
            "plan": self.plan,
            "is_plus": self.is_plus,
            "error": self.error,
        }


def _extract_access_token(session_field: str) -> str | None:
    """Accept raw JWT OR session JSON OR JSON-ish string and return accessToken."""
    s = (session_field or "").strip()
    if not s:
        return None

    # Try JSON parse
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            token = obj.get("accessToken") or obj.get("access_token")
            if token:
                return token
        except Exception:
            pass

    # Look for "accessToken":"..." inside arbitrary text
    m = re.search(r'"access[_]?[Tt]oken"\s*:\s*"([^"]+)"', s)
    if m:
        return m.group(1)

    # Otherwise treat as raw JWT if it looks like one
    if s.count(".") >= 2 and len(s) > 50:
        return s

    return None


def _classify_plan(data: dict[str, Any]) -> tuple[str, str, bool]:
    """Return (status, label, is_plus) from accounts/check response."""
    accounts = data.get("accounts") or {}
    default = accounts.get("default") or {}
    entitlement = default.get("entitlement") or {}

    plan_id = (
        entitlement.get("subscription_plan")
        or default.get("plan_type")
        or ""
    )
    has_sub = bool(entitlement.get("has_active_subscription"))
    label = _PLAN_LABELS.get(plan_id, plan_id or ("Unknown" if not has_sub else "Subscribed"))

    if plan_id == "chatgptplusplan":
        return "plus", label, True
    if plan_id == "chatgptproplan":
        return "pro", label, True
    if plan_id == "chatgptteamplan":
        return "team", label, True
    if plan_id == "chatgptenterpriseplan":
        return "enterprise", label, True
    if plan_id == "chatgptfreeplan" or not has_sub:
        return "free", label or "Free", False
    return "unknown", label, has_sub


async def _check_one(
    session: AsyncSession,
    email: str,
    token: str,
    *,
    timeout: float = 20.0,
) -> CheckResult:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "*/*",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }
    try:
        resp = await session.get(_ACCOUNTS_CHECK_URL, headers=headers, timeout=timeout)
    except Exception as exc:
        return CheckResult(email=email, status="error", plan="—", is_plus=False, error=str(exc))

    if resp.status_code == 401:
        return CheckResult(email=email, status="expired", plan="—", is_plus=False,
                           error="HTTP 401 — token expired / invalid")
    if resp.status_code >= 400:
        return CheckResult(email=email, status="error", plan="—", is_plus=False,
                           error=f"HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        data = resp.json()
    except Exception as exc:
        return CheckResult(email=email, status="error", plan="—", is_plus=False,
                           error=f"JSON parse: {exc}")

    status, label, is_plus = _classify_plan(data)
    return CheckResult(email=email, status=status, plan=label, is_plus=is_plus, raw=data)


def parse_line(line: str) -> tuple[str, str | None]:
    """Return (email, access_token | None) from a `email|pass|2fa|session` line."""
    parts = [p.strip() for p in line.strip().split("|")]
    if not parts or not parts[0]:
        return "", None
    email = parts[0]
    session_field = parts[3] if len(parts) >= 4 else ""
    token = _extract_access_token(session_field)
    return email, token


async def check_accounts(
    lines: list[str],
    *,
    proxy: str | None = None,
    max_concurrent: int = 5,
    timeout: float = 20.0,
) -> list[CheckResult]:
    """Check Plus status for each input line. Returns one CheckResult per non-empty line."""
    items: list[tuple[str, str | None]] = []
    for raw in lines:
        if not raw or raw.lstrip().startswith("#"):
            continue
        email, token = parse_line(raw)
        if not email:
            continue
        items.append((email, token))

    sess_kwargs: dict[str, Any] = {"impersonate": _IMPERSONATE}
    if proxy:
        sess_kwargs["proxy"] = proxy

    sem = asyncio.Semaphore(max(1, min(max_concurrent, 10)))
    results: list[CheckResult] = []

    async with AsyncSession(**sess_kwargs) as session:
        async def _run(email: str, token: str | None) -> CheckResult:
            if not token:
                return CheckResult(
                    email=email, status="error", plan="—", is_plus=False,
                    error="no access token in 4th column (need session/JWT)",
                )
            async with sem:
                return await _check_one(session, email, token, timeout=timeout)

        tasks = [_run(e, t) for (e, t) in items]
        for coro in asyncio.as_completed(tasks):
            res = await coro
            results.append(res)

    # Preserve input order
    order = {e: i for i, (e, _) in enumerate(items)}
    results.sort(key=lambda r: order.get(r.email, 999_999))
    return results
