"""Persist proxy URL ra disk — survive cả server restart.

Proxy là global app setting (single source of truth), lưu JSON ở
`runtime/app-settings.json`. Trước đây proxy chỉ giữ in-memory trong các
manager → mất sau mỗi lần restart web server. Module này load lúc manager
init và ghi lại mỗi lần `set_proxy`.

Atomic write (write tmp → replace) để tránh file rỗng khi crash giữa chừng.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import load_settings

_SETTINGS_FILENAME = "app-settings.json"


def _settings_path() -> Path:
    return load_settings().runtime_dir / _SETTINGS_FILENAME


def _read_all() -> dict:
    """Đọc toàn bộ settings dict. Trả {} nếu thiếu file / JSON hỏng."""
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_proxy(default: str | None = None) -> str | None:
    """Đọc proxy đã persist.

    - File tồn tại + có key "proxy" → trả giá trị đó (kể cả None khi user
      đã chủ động clear) → tôn trọng lựa chọn "direct" của user.
    - File thiếu / không có key → trả `default` (thường là env default).
    """
    data = _read_all()
    if "proxy" not in data:
        return default
    return data.get("proxy") or None


def save_proxy(value: str | None) -> None:
    """Ghi proxy (merge với settings hiện có). Atomic write."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_all()
    data["proxy"] = value or None
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic trên cùng filesystem


def load_proxy_enabled(default: bool = True) -> bool:
    data = _read_all()
    if "proxy_enabled" not in data:
        return default
    return bool(data.get("proxy_enabled", default))


def save_proxy_enabled(value: bool) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_all()
    data["proxy_enabled"] = bool(value)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
