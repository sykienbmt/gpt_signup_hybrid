"""Pool Gmail rented email.

Format pool file (1 dòng = 1 combo):
    email|otp_api_url
    email|otp_api_url
    ...

State tracker: runtime/gmail_state/<email>.json
    used_for_signup: true|false
    last_error:      str | null
    last_used_at:    ISO timestamp
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .mail_providers import GmailRentedCombo, GmailRentedComboError


_TERMINAL_ERRORS = (
    "registration_disallowed",
    "wrong password — account already exists",
    "TimeoutError",  # OTP timeout → skip permanently
)


class GmailPoolError(Exception):
    """Pool fail."""


def _state_file(state_dir: Path, email: str) -> Path:
    return state_dir / f"{email.replace('/', '_')}.json"


def _read_state(state_dir: Path, email: str) -> dict:
    path = _state_file(state_dir, email)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state_dir: Path, email: str, state: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = _state_file(state_dir, email)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_pool_file(path: Path) -> list[GmailRentedCombo]:
    """Đọc pool file, return list combo. Skip dòng trống / comment (#)."""
    if not path.exists():
        raise GmailPoolError(f"pool file không tồn tại: {path}")
    combos: list[GmailRentedCombo] = []
    seen: set[str] = set()
    for line_num, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            combo = GmailRentedCombo.parse(line)
        except GmailRentedComboError as exc:
            raise GmailPoolError(f"pool file dòng {line_num}: {exc}") from exc
        if combo.email.lower() in seen:
            raise GmailPoolError(f"pool file dòng {line_num}: email trùng {combo.email}")
        seen.add(combo.email.lower())
        combos.append(combo)
    if not combos:
        raise GmailPoolError(f"pool file rỗng: {path}")
    return combos


def iter_available(
    pool: list[GmailRentedCombo], *, state_dir: Path, log,
) -> Iterator[GmailRentedCombo]:
    for combo in pool:
        state = _read_state(state_dir, combo.email)
        if state.get("used_for_signup"):
            log(f"[gmail_pool] skip {combo.email} — đã signup (used_for_signup=true)")
            continue
        last_error = state.get("last_error")
        if last_error and any(err in last_error for err in _TERMINAL_ERRORS):
            log(f"[gmail_pool] skip {combo.email} — terminal error: {last_error[:80]}")
            continue
        yield combo


def pick_first_available(
    pool: list[GmailRentedCombo], *, state_dir: Path, log,
) -> GmailRentedCombo:
    for combo in iter_available(pool, state_dir=state_dir, log=log):
        log(f"[gmail_pool] picked {combo.email}")
        return combo
    raise GmailPoolError(
        f"hết combo khả dụng trong pool ({len(pool)} combo total). "
        "Tất cả đã used_for_signup hoặc terminal error."
    )


def mark_signup_success(*, state_dir: Path, email: str) -> None:
    state = _read_state(state_dir, email)
    state["used_for_signup"] = True
    state["used_at"] = datetime.now(timezone.utc).isoformat()
    state.pop("last_error", None)
    _write_state(state_dir, email, state)


def mark_signup_failure(
    *, state_dir: Path, email: str, error: str, registered_password: str | None = None,
) -> None:
    state = _read_state(state_dir, email)
    state["last_error"] = error
    state["last_failed_at"] = datetime.now(timezone.utc).isoformat()
    if registered_password:
        state["registered_password"] = registered_password
    _write_state(state_dir, email, state)


def get_registered_password(*, state_dir: Path, email: str) -> str | None:
    return _read_state(state_dir, email).get("registered_password")


def status_summary(pool: list[GmailRentedCombo], *, state_dir: Path) -> dict:
    used = available = terminal = unknown = 0
    for combo in pool:
        state = _read_state(state_dir, combo.email)
        if state.get("used_for_signup"):
            used += 1
            continue
        last_error = state.get("last_error", "")
        if last_error and any(err in last_error for err in _TERMINAL_ERRORS):
            terminal += 1
            continue
        if state:
            available += 1
        else:
            unknown += 1
    return {
        "total": len(pool),
        "used_for_signup": used,
        "available": available + unknown,
        "terminal_error": terminal,
    }
