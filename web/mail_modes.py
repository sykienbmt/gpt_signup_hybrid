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

from ..mail_providers import OutlookCombo, OutlookComboError
from ..models import SignupRequest


# ─── Errors ───────────────────────────────────────────────────────────


class MailModeParseError(Exception):
    """Parse line fail cho 1 mail mode."""


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
    return SignupRequest(
        email=parsed.email,
        mail_provider="outlook",
        outlook_combo=parsed.raw,
        headless=headless,
        keep_browser_open=keep_browser_open,
        password=password,
        proxy=proxy,
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
    return SignupRequest(
        email=parsed.email,
        mail_provider="worker",
        email_logs_url=cfg.get("logs_url", "https://icloud-cf-mail.n5pskgzs9g.workers.dev/logs"),
        email_api_key=cfg.get("api_key", ""),
        email_insecure_tls=True,
        otp_timeout_seconds=200.0,
        otp_poll_interval_seconds=15.0,
        headless=headless,
        keep_browser_open=keep_browser_open,
        password=password,
        proxy=proxy,
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


# ─── Registry ─────────────────────────────────────────────────────────


_REGISTRY: dict[str, MailModeSpec] = {
    OUTLOOK_MODE.id: OUTLOOK_MODE,
    WORKER_MODE.id: WORKER_MODE,
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
