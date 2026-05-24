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
    """Parse line `email----api_url` cho SmsBower."""
    try:
        email, api_url = SmsBowerProvider.parse_line(line)
    except SmsBowerParseError as exc:
        raise SmsBowerModeParseError(str(exc)) from exc
    return ParsedLine(email=email, raw=line)


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
        otp_timeout_seconds=70.0,       # 7s delay + 30s poll + resend + 30s poll
        otp_initial_delay_seconds=7.0,  # chờ 7s trước khi gọi API lần đầu
        otp_max_resends=1,              # resend tối đa 1 lần, sau đó skip job
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


# ─── Registry ─────────────────────────────────────────────────────────


_REGISTRY: dict[str, MailModeSpec] = {
    OUTLOOK_MODE.id: OUTLOOK_MODE,
    WORKER_MODE.id: WORKER_MODE,
    GMAIL_ADVANCED_MODE.id: GMAIL_ADVANCED_MODE,
    SMSBOWER_MODE.id: SMSBOWER_MODE,
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
            "config_schema": spec.config_schema,
        }
        for spec in _REGISTRY.values()
    ]
