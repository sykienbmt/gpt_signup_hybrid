"""Mail Mode Registry — extension point cho mail provider dispatch.

Mỗi MailModeSpec khai báo:
- parse_line: parse 1 dòng input → ParsedLine
- build_request: build SignupRequest từ parsed + config
- config_schema: mô tả trường config (UI render + persist localStorage)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..mail_providers import (
    OutlookCombo, OutlookComboError,
    GmailAdvancedProvider, GmailAdvancedParseError,
    SmsBowerProvider, SmsBowerParseError,
    smsbower_dot_alias,
    SmsBowerDirectProvider,
)
from ..models import SignupRequest


# ─── Errors ───────────────────────────────────────────────────────────


class MailModeParseError(Exception):
    """Parse line fail cho 1 mail mode."""


class GmailAdvancedModeParseError(MailModeParseError):
    """Parse line fail cho Gmail Advanced mode."""


class SmsBowerModeParseError(MailModeParseError):
    """Parse line fail cho SmsBower mode."""


# ─── Data types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedLine:
    email: str
    raw: str


@dataclass(frozen=True)
class MailModeSpec:
    id: str
    label: str
    input_placeholder: str
    input_help: str
    config_schema: list[dict[str, Any]]
    parse_line: Callable[[str], ParsedLine]
    build_request: Callable[..., SignupRequest]
    input_type: str = "text"  # "text" (default) hoặc "count" (auto-acquire mode)


# ─── Outlook mode ─────────────────────────────────────────────────────


def _parse_outlook_line(line: str) -> ParsedLine:
    combo = OutlookCombo.parse(line)
    return ParsedLine(email=combo.email, raw=line)


def _build_outlook_request(
    parsed: ParsedLine,
    *,
    worker_config: dict[str, str] | None = None,
    password: str | None = None,
    headless: bool = True,
    keep_browser_open: bool = False,
    proxy: str | None = None,
) -> SignupRequest:
    from ..config import env_insecure_tls
    return SignupRequest(
        email=parsed.email,
        mail_provider="outlook",
        outlook_combo=parsed.raw,
        headless=headless,
        keep_browser_open=keep_browser_open,
        password=password,
        proxy=proxy,
        tls_insecure=env_insecure_tls(),
    )


OUTLOOK_MODE = MailModeSpec(
    id="outlook",
    label="Hotmail (combo)",
    input_placeholder="email|password|refresh_token|client_id",
    input_help="Mỗi dòng 1 combo Outlook 4 phần.",
    config_schema=[],
    parse_line=_parse_outlook_line,
    build_request=_build_outlook_request,
)


# ─── Worker mode (iCloud) ────────────────────────────────────────────


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_worker_line(line: str) -> ParsedLine:
    email = line.strip()
    if not _EMAIL_RE.match(email):
        raise MailModeParseError(f"invalid icloud email: {line[:80]}")
    return ParsedLine(email=email, raw=line)


def _build_worker_request(
    parsed: ParsedLine,
    *,
    worker_config: dict[str, str] | None = None,
    password: str | None = None,
    headless: bool = True,
    keep_browser_open: bool = False,
    proxy: str | None = None,
) -> SignupRequest:
    cfg = worker_config or {}
    # insecure_tls chỉ bật qua opt-in: env GPT_SIGNUP_INSECURE_TLS=1 hoặc
    # worker_config["insecure_tls"]. Default = secure.
    from ..config import env_insecure_tls
    raw_flag = str(cfg.get("insecure_tls", "")).strip().lower()
    cfg_insecure = raw_flag in ("1", "true", "yes", "on")
    insecure = env_insecure_tls() or cfg_insecure
    return SignupRequest(
        email=parsed.email,
        mail_provider="worker",
        email_logs_url=cfg.get("logs_url", "https://icloud-cf-mail.n5pskgzs9g.workers.dev/logs"),
        email_api_key=cfg.get("api_key", ""),
        email_insecure_tls=insecure,
        otp_timeout_seconds=200.0,
        otp_poll_interval_seconds=15.0,
        headless=headless,
        keep_browser_open=keep_browser_open,
        password=password,
        proxy=proxy,
        tls_insecure=insecure,
    )


WORKER_MODE = MailModeSpec(
    id="worker",
    label="iCloud Mail (Worker API)",
    input_placeholder="user@icloud.com",
    input_help="Mỗi dòng 1 email iCloud nhận OTP qua Worker.",
    config_schema=[
        {
            "key": "logs_url",
            "label": "Worker API URL",
            "type": "text",
            "default": "https://icloud-cf-mail.n5pskgzs9g.workers.dev/logs",
            "required": True,
            "validate_prefix": ["http://", "https://"],
        },
        {
            "key": "api_key",
            "label": "VIEW_TOKEN",
            "type": "text",
            "default": "12345678@",
            "required": False,
        },
    ],
    parse_line=_parse_worker_line,
    build_request=_build_worker_request,
)


# ─── Gmail Advanced mode ──────────────────────────────────────────────


def _parse_gmail_advanced_line(line: str) -> ParsedLine:
    """Parse line `email|api_url` hoặc chỉ `api_url` cho Gmail Advanced."""
    try:
        email, api_url = GmailAdvancedProvider.parse_line(line)
    except GmailAdvancedParseError as exc:
        raise MailModeParseError(str(exc)) from exc
    # Nếu URL-only → email rỗng, dùng placeholder (sẽ resolve từ API pre_check)
    display_email = email if email else f"(pending) {api_url[:50]}..."
    return ParsedLine(email=display_email, raw=line)


def _build_gmail_advanced_request(
    parsed: ParsedLine,
    *,
    worker_config: dict[str, str] | None = None,
    password: str | None = None,
    headless: bool = True,
    keep_browser_open: bool = False,
    proxy: str | None = None,
) -> SignupRequest:
    raw = parsed.raw.strip()
    # Detect format: URL-only hoặc email|url
    if raw.startswith(("http://", "https://")):
        api_url = raw
        email = ""  # sẽ fill từ pre_check
    else:
        parts = raw.split("|", 1)
        email = parts[0].strip()
        api_url = parts[1].strip() if len(parts) == 2 else ""

    # Nếu email rỗng → dùng placeholder, pre_check sẽ resolve
    signup_email = email if email else "pending@gmail-advanced.local"
    from ..config import env_insecure_tls
    return SignupRequest(
        email=signup_email,
        mail_provider="gmail_advanced",
        gmail_api_url=api_url,
        otp_timeout_seconds=30.0,
        otp_poll_interval_seconds=3.0,
        headless=headless,
        keep_browser_open=keep_browser_open,
        password=password,
        proxy=proxy,
        tls_insecure=env_insecure_tls(),
    )


GMAIL_ADVANCED_MODE = MailModeSpec(
    id="gmail_advanced",
    label="Gmail Advanced (API)",
    input_placeholder="https://checkotpgmail.live/otp/2605201652376818498?t=...\nbrandonspencer7424@gmail.com|https://checkotpgmail.live/otp/...",
    input_help="Mỗi dòng: api_url hoặc email|api_url. Pre-check mail_status=live trước khi chạy.",
    config_schema=[],
    parse_line=_parse_gmail_advanced_line,
    build_request=_build_gmail_advanced_request,
)


# ─── SmsBower mode ────────────────────────────────────────────────────


def _parse_smsbower_line(line: str) -> ParsedLine:
    """Parse line `email----api_url` cho SmsBower.

    Tự động apply 1-dot Gmail alias ngay khi import để mỗi lần signup dùng
    một biến thể khác inbox-mapping (cùng inbox vật lý nhưng OpenAI thấy email khác).
    """
    try:
        email, api_url = SmsBowerProvider.parse_line(line)
    except SmsBowerParseError as exc:
        raise SmsBowerModeParseError(str(exc)) from exc
    aliased = smsbower_dot_alias(email)
    aliased_line = f"{aliased}{SmsBowerProvider.SEPARATOR}{api_url}"
    return ParsedLine(email=aliased, raw=aliased_line)


def _build_smsbower_request(
    parsed: ParsedLine,
    *,
    worker_config: dict[str, str] | None = None,
    password: str | None = None,
    headless: bool = True,
    keep_browser_open: bool = False,
    proxy: str | None = None,
) -> SignupRequest:
    raw = parsed.raw.strip()
    parts = raw.split(SmsBowerProvider.SEPARATOR, 1)
    email = parts[0].strip()
    api_url = parts[1].strip() if len(parts) == 2 else ""

    from ..config import env_insecure_tls
    return SignupRequest(
        email=email,
        mail_provider="smsbower",
        smsbower_api_url=api_url,
        otp_timeout_seconds=70.0,        # 7s delay + poll code1 + chờ code2 (~60s tổng)
        otp_initial_delay_seconds=7.0,   # chờ 7s trước khi gọi API lần đầu
        otp_max_resends=0,               # không click Resend dù timeout
        otp_resend_on_reject=False,      # không click Resend khi code bị reject — chờ code 2 tự về
        otp_poll_interval_seconds=3.0,
        headless=headless,
        keep_browser_open=keep_browser_open,
        password=password,
        proxy=proxy,
        tls_insecure=env_insecure_tls(),
    )


SMSBOWER_MODE = MailModeSpec(
    id="smsbower",
    label="SmsBower (API)",
    input_placeholder="gaylelauro9452100@gmail.com----smsbower.page/api/mail/getCodeBySignature?s=...",
    input_help="Mỗi dòng: email----api_url (phân cách bằng 4 dấu gạch ngang). Pre-check status=1 trước khi chạy.",
    config_schema=[],
    parse_line=_parse_smsbower_line,
    build_request=_build_smsbower_request,
)


# ─── SmsBower Direct mode (auto acquire + auto poll) ──────────────────

def _parse_smsbower_direct_line(line: str) -> ParsedLine:
    """Parse line — chỉ cần số lượng (count), email tự acquire từ API.

    Format: empty line hoặc just a number = create 1 account per line.
    Tức mỗi dòng = 1 account, không cần nhập gì cả.
    """
    return ParsedLine(email=f"(auto-acquire)", raw=line.strip())


def _build_smsbower_direct_request(
    parsed: ParsedLine,
    *,
    worker_config: dict[str, str] | None = None,
    password: str | None = None,
    headless: bool = True,
    keep_browser_open: bool = False,
    proxy: str | None = None,
) -> SignupRequest:
    cfg = worker_config or {}
    # Default values từ user config
    api_key = cfg.get("api_key", "V7JuZljb0RQDEzawWc6IO4LPAV3x71vo")
    service = cfg.get("service", "dr")
    domain = cfg.get("domain", "gmail.com")
    max_price_str = cfg.get("max_price", "")
    max_price = float(max_price_str) if max_price_str else 0.01  # Auto lowest price

    from ..config import env_insecure_tls
    return SignupRequest(
        email="pending@smsbower-direct.local",  # Placeholder — sẽ resolve từ acquire_email()
        mail_provider="smsbower_direct",
        smsbower_api_key=api_key,
        smsbower_service=service,
        smsbower_domain=domain,
        smsbower_max_price=max_price,
        otp_timeout_seconds=100.0,  # Poll OTP 100s (10s interval = 10 attempts)
        otp_poll_interval_seconds=10.0,  # Check API mỗi 10s
        otp_max_resends=0,  # Không reload/resend, chỉ tiếp tục poll
        otp_resend_on_reject=False,  # Không click "Resend" — tiếp tục poll để xem có code lần 2
        headless=headless,
        keep_browser_open=keep_browser_open,
        password=password,
        proxy=proxy,
        tls_insecure=env_insecure_tls(),
    )


SMSBOWER_DIRECT_MODE = MailModeSpec(
    id="smsbower_direct",
    label="SMSBower Direct (Auto)",
    input_placeholder="\n\n",
    input_help="Mỗi dòng (trống): 1 account. Email tự acquire từ API + apply dot alias.",
    input_type="count",  # Special mode: chỉ nhập số lượng, không cần combo
    config_schema=[
        {
            "key": "api_key",
            "label": "API Key",
            "type": "text",
            "default": "V7JuZljb0RQDEzawWc6IO4LPAV3x71vo",
            "required": True,
            "secret": True,
        },
        {
            "key": "service",
            "label": "Service Code",
            "type": "select",
            "options": [
                {"value": "dr", "label": "OpenAI/ChatGPT (dr)"},
                {"value": "oi", "label": "Tinder"},
                {"value": "ig", "label": "Instagram"},
                {"value": "fb", "label": "Facebook"},
            ],
            "default": "dr",
        },
        {
            "key": "domain",
            "label": "Domain",
            "type": "select",
            "options": [
                {"value": "gmail.com", "label": "Gmail"},
                {"value": "mailnestpro.com", "label": "Mailnestpro"},
                {"value": "hihinail.com", "label": "Hihinail"},
                {"value": "flytempbox.com", "label": "Flytempbox"},
            ],
            "default": "gmail.com",
        },
        {
            "key": "max_price",
            "label": "Max Price ($)",
            "type": "text",
            "default": "",
            "required": False,
            "placeholder": "Leave blank for lowest price (0.01)",
        },
    ],
    parse_line=_parse_smsbower_direct_line,
    build_request=_build_smsbower_direct_request,
)


# ─── Registry ─────────────────────────────────────────────────────────


_REGISTRY: dict[str, MailModeSpec] = {
    OUTLOOK_MODE.id: OUTLOOK_MODE,
    WORKER_MODE.id: WORKER_MODE,
    GMAIL_ADVANCED_MODE.id: GMAIL_ADVANCED_MODE,
    SMSBOWER_MODE.id: SMSBOWER_MODE,
    SMSBOWER_DIRECT_MODE.id: SMSBOWER_DIRECT_MODE,
}


def get_registry() -> dict[str, MailModeSpec]:
    return _REGISTRY


def get_spec(mail_mode: str) -> MailModeSpec:
    """Lấy spec theo id. Raise KeyError nếu không tồn tại."""
    return _REGISTRY[mail_mode]


def serialize_for_api() -> list[dict[str, Any]]:
    """Trả list dict cho endpoint GET /api/mail-modes."""
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "input_placeholder": spec.input_placeholder,
            "input_help": spec.input_help,
            "input_type": spec.input_type,
            "config_schema": spec.config_schema,
        }
        for spec in _REGISTRY.values()
    ]
