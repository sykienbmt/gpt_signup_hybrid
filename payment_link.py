"""Payment Link: lấy checkout URL pay.openai.com từ ChatGPT + Stripe API.

Flow:
    1. POST chatgpt.com/backend-api/payments/checkout (hosted mode)
       → CheckoutResponse (session_id, publishable_key, optional url)
    2. Nếu response có url chứa checkout.stripe.com/c/pay/ → replace host → return
    3. Nếu không → POST api.stripe.com/v1/payment_pages/{session_id}/init
       → stripe_hosted_url → replace host → return

Dùng curl_cffi AsyncSession impersonate="chrome136" cho TLS fingerprint.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from curl_cffi.requests import AsyncSession


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PaymentLinkError(Exception):
    """Base error for payment link operations."""
    pass


class SessionExpiredError(PaymentLinkError):
    """HTTP 401 from Checkout API — access token expired/revoked."""
    pass


class CloudflareBlockedError(PaymentLinkError):
    """HTTP 403 with Cloudflare challenge markers."""
    pass


class StripeInitError(PaymentLinkError):
    """Stripe init API failed or missing hosted_url."""
    pass


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class CheckoutResponse:
    """Parsed response from chatgpt.com/backend-api/payments/checkout."""

    checkout_session_id: str
    publishable_key: str
    client_secret: str | None = None
    url: str | None = None
    checkout_ui_mode: str | None = None


@dataclass
class CheckoutInfo:
    """Rich result from get_checkout_info() — URL + trial data + Stripe IDs."""

    payment_url: str
    checkout_session_id: str
    publishable_key: str
    is_trial: bool
    trial_days: int
    amount_due: int          # smallest currency unit; -1 if unknown
    currency: str
    payment_intent_id: str | None = None   # pi_xxx (for polling)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHECKOUT_URL = "https://chatgpt.com/backend-api/payments/checkout"
_STRIPE_INIT_URL_TPL = "https://api.stripe.com/v1/payment_pages/{session_id}/init"
_CF_MARKERS = ("cf-chl", "just a moment", "cloudflare")
_IMPERSONATE = "chrome136"

# Region → billing_details mapping
REGION_BILLING: dict[str, dict[str, str]] = {
    "VN": {"country": "VN", "currency": "VND"},
    "ID": {"country": "ID", "currency": "IDR"},
    "IN": {"country": "IN", "currency": "INR"},
    "BR": {"country": "BR", "currency": "BRL"},
    "US": {"country": "US", "currency": "USD"},
}
DEFAULT_REGION = "VN"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _replace_stripe_host(url: str) -> str:
    """Replace checkout.stripe.com → pay.openai.com, preserve path/query."""
    parsed = urlparse(url)
    if parsed.hostname == "checkout.stripe.com":
        replaced = parsed._replace(netloc="pay.openai.com")
        return urlunparse(replaced)
    return url


def _generate_stripe_js_id() -> str:
    """UUID v4 string for stripe_js_id parameter."""
    return str(uuid.uuid4())


def _check_response_error(status_code: int, body: str) -> None:
    """Raise appropriate error based on HTTP status code and body content.

    - 401 → SessionExpiredError
    - 403 + CF markers → CloudflareBlockedError
    - Other non-2xx → PaymentLinkError with status + first 300 chars body
    """
    if 200 <= status_code < 300:
        return

    if status_code == 401:
        raise SessionExpiredError(f"HTTP 401: session expired — {body[:300]}")

    if status_code == 403:
        body_lower = body.lower()
        if any(marker in body_lower for marker in _CF_MARKERS):
            raise CloudflareBlockedError(
                f"HTTP 403: Cloudflare block detected — {body[:300]}"
            )

    raise PaymentLinkError(f"HTTP {status_code}: {body[:300]}")


# ---------------------------------------------------------------------------
# Internal API calls
# ---------------------------------------------------------------------------


async def _call_chatgpt_checkout(
    session: AsyncSession,
    access_token: str,
    *,
    region: str = DEFAULT_REGION,
    timeout: float = 30.0,
) -> CheckoutResponse:
    """POST chatgpt.com/backend-api/payments/checkout with hosted mode payload."""
    billing = REGION_BILLING.get(region, REGION_BILLING[DEFAULT_REGION])

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/?promo_campaign=plus-1-month-free",
        "x-openai-target-path": "/backend-api/payments/checkout",
        "x-openai-target-route": "/backend-api/payments/checkout",
    }
    payload = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {
            "country": billing["country"],
            "currency": billing["currency"],
        },
        "promo_campaign": {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        },
        "checkout_ui_mode": "hosted",
    }

    try:
        resp = await session.post(
            _CHECKOUT_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        raise PaymentLinkError(f"checkout request failed: {exc}") from exc

    body = resp.text
    _check_response_error(resp.status_code, body)

    try:
        data = resp.json()
    except Exception as exc:
        raise PaymentLinkError(f"checkout JSON parse failed: {exc} — body: {body[:300]}") from exc

    session_id = data.get("checkout_session_id")
    pub_key = data.get("publishable_key")
    if not session_id or not pub_key:
        raise PaymentLinkError(
            f"checkout response missing required fields — "
            f"checkout_session_id={session_id!r}, publishable_key={pub_key!r}"
        )

    return CheckoutResponse(
        checkout_session_id=session_id,
        publishable_key=pub_key,
        client_secret=data.get("client_secret"),
        url=data.get("url"),
        checkout_ui_mode=data.get("checkout_ui_mode"),
    )


async def _call_stripe_init(
    session: AsyncSession,
    checkout_session_id: str,
    publishable_key: str,
    *,
    timeout: float = 30.0,
) -> str:
    """POST api.stripe.com/v1/payment_pages/{session_id}/init → hosted_url.

    Uses form-encoded data as Stripe expects.
    """
    url = _STRIPE_INIT_URL_TPL.format(session_id=checkout_session_id)
    stripe_js_id = _generate_stripe_js_id()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Origin": "https://js.stripe.com",
        "Referer": "https://js.stripe.com/",
    }
    # Form data y hệt Rust checkout.rs (urlencoded vẫn dùng được dict)
    form_data = {
        "browser_locale": "en-US",
        "browser_timezone": "Asia/Saigon",
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": stripe_js_id,
        "elements_session_client[locale]": "en-US",
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[stripe_js_locale]": "auto",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "key": publishable_key,
        "_stripe_version": "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1",
    }

    try:
        resp = await session.post(
            url,
            headers=headers,
            data=form_data,
            timeout=timeout,
        )
    except Exception as exc:
        raise PaymentLinkError(f"stripe init request failed: {exc}") from exc

    body = resp.text
    _check_response_error(resp.status_code, body)

    try:
        data = resp.json()
    except Exception as exc:
        raise StripeInitError(
            f"stripe init JSON parse failed: {exc} — body: {body[:300]}"
        ) from exc

    hosted_url = data.get("stripe_hosted_url")
    if not hosted_url:
        raise StripeInitError(
            f"stripe init response missing stripe_hosted_url — keys: {list(data.keys())}"
        )

    return hosted_url


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_checkout_url(
    access_token: str,
    *,
    region: str = DEFAULT_REGION,
    proxy: str | None = None,
    timeout: float = 30.0,
) -> str:
    """Main entry: access_token → pay.openai.com URL.

    Flow:
        1. POST checkout API → CheckoutResponse
        2. If response.url has checkout.stripe.com/c/pay/ → replace host → return
        3. Otherwise POST stripe init → get hosted_url → replace host → return

    Args:
        access_token: Bearer JWT from ChatGPT session.
        region: Region code (VN, ID, IN, US). Determines country + currency.
        proxy: HTTP/HTTPS proxy URL (optional).
        timeout: Per-request timeout in seconds (default 30s).

    Returns:
        Payment URL with pay.openai.com host.

    Raises:
        SessionExpiredError: HTTP 401
        CloudflareBlockedError: HTTP 403 + CF markers
        PaymentLinkError: other HTTP errors, timeout, parse errors
        StripeInitError: Stripe init failed or missing hosted_url
    """
    proxies = {"http": proxy, "https": proxy} if proxy else None

    async with AsyncSession(impersonate=_IMPERSONATE, proxies=proxies) as session:
        # Step 1: call checkout API
        checkout = await _call_chatgpt_checkout(
            session, access_token, region=region, timeout=timeout,
        )

        # Step 2: check if hosted URL is already available
        if checkout.url:
            replaced = _replace_stripe_host(checkout.url)
            parsed = urlparse(replaced)
            if "/c/pay/" in (parsed.path or ""):
                return replaced

        # Step 3: fallback — call Stripe init
        hosted_url = await _call_stripe_init(
            session,
            checkout.checkout_session_id,
            checkout.publishable_key,
            timeout=timeout,
        )
        return _replace_stripe_host(hosted_url)


def _stripe_init_form(publishable_key: str) -> dict[str, str]:
    """Minimal Stripe payment_pages/init form data (no elements_options_client)."""
    return {
        "browser_locale": "en-US",
        "browser_timezone": "Asia/Saigon",
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": str(uuid.uuid4()),
        "elements_session_client[locale]": "en-US",
        "elements_session_client[is_aggregation_expected]": "false",
        "key": publishable_key,
        "_stripe_version": "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1",
    }


_STRIPE_INIT_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
    "Origin": "https://js.stripe.com",
    "Referer": "https://js.stripe.com/",
}


async def _call_stripe_init_full(
    session: AsyncSession,
    checkout_session_id: str,
    publishable_key: str,
    *,
    timeout: float = 30.0,
) -> dict:
    """Like _call_stripe_init but returns the full JSON response dict."""
    url = _STRIPE_INIT_URL_TPL.format(session_id=checkout_session_id)
    resp = await session.post(
        url,
        headers=_STRIPE_INIT_HEADERS,
        data=_stripe_init_form(publishable_key),
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise StripeInitError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except Exception as exc:
        raise StripeInitError(f"JSON parse error: {exc}") from exc


def _parse_stripe_trial(data: dict) -> tuple[bool, int, int, str]:
    """Parse (is_trial, trial_days, amount_due, currency) from Stripe init response."""
    amount_due: int = (data.get("invoice") or {}).get("amount_due", -1)
    trial_days: int = (data.get("subscription_data") or {}).get("trial_period_days") or 0
    is_trial = amount_due == 0 or trial_days > 0
    currency = data.get("currency", "").upper()
    return is_trial, trial_days, amount_due, currency


async def get_checkout_info(
    access_token: str,
    *,
    region: str = DEFAULT_REGION,
    proxy: str | None = None,
    timeout: float = 30.0,
) -> CheckoutInfo:
    """Like get_checkout_url() but returns CheckoutInfo with trial data.

    Raises same errors as get_checkout_url().
    """
    proxies = {"http": proxy, "https": proxy} if proxy else None

    async with AsyncSession(impersonate=_IMPERSONATE, proxies=proxies) as session:
        checkout = await _call_chatgpt_checkout(
            session, access_token, region=region, timeout=timeout,
        )

        # Payment URL — prefer checkout.url when it has /c/pay/
        payment_url = ""
        if checkout.url:
            replaced = _replace_stripe_host(checkout.url)
            if "/c/pay/" in (urlparse(replaced).path or ""):
                payment_url = replaced

        # Call Stripe init for full data (trial, amount, payment_intent)
        try:
            stripe_data = await _call_stripe_init_full(
                session,
                checkout.checkout_session_id,
                checkout.publishable_key,
                timeout=timeout,
            )
        except StripeInitError:
            # Fallback: use checkout.url or _call_stripe_init for URL only
            if not payment_url:
                hosted = await _call_stripe_init(
                    session,
                    checkout.checkout_session_id,
                    checkout.publishable_key,
                    timeout=timeout,
                )
                payment_url = _replace_stripe_host(hosted)
            return CheckoutInfo(
                payment_url=payment_url,
                checkout_session_id=checkout.checkout_session_id,
                publishable_key=checkout.publishable_key,
                is_trial=False,
                trial_days=0,
                amount_due=-1,
                currency="",
            )

        # Extract payment URL if not yet found
        if not payment_url:
            hosted = stripe_data.get("stripe_hosted_url") or ""
            payment_url = _replace_stripe_host(hosted) if hosted else ""

        is_trial, trial_days, amount_due, currency = _parse_stripe_trial(stripe_data)

        # Payment intent ID (for polling)
        pi = stripe_data.get("payment_intent") or {}
        payment_intent_id = pi.get("id") or None

        return CheckoutInfo(
            payment_url=payment_url,
            checkout_session_id=checkout.checkout_session_id,
            publishable_key=checkout.publishable_key,
            is_trial=is_trial,
            trial_days=trial_days,
            amount_due=amount_due,
            currency=currency,
            payment_intent_id=payment_intent_id,
        )
