"""Proxy auto-rotation config and runner."""
from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..config import load_settings

SETTINGS_FILENAME = "app-settings.json"
PROXY_RE = re.compile(r"^(https?|socks4|socks5)://\S+$", re.IGNORECASE)


@dataclass
class RotateRequestSpec:
    method: str
    url: str
    headers: dict[str, str]
    data: str | None = None


def _normalize_curl_text(text: str) -> str:
    """Normalize pasted curl from Unix shells or Windows cmd.exe."""
    normalized = text.strip()
    normalized = re.sub(r"\^\s*\r?\n\s*", " ", normalized)
    normalized = re.sub(r"\^(.)", r"\1", normalized)
    return normalized


def _settings_path() -> Path:
    return load_settings().runtime_dir / SETTINGS_FILENAME


def _read_all() -> dict[str, Any]:
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict[str, Any]) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_proxy_rotate() -> dict[str, Any]:
    data = _read_all()
    cfg = data.get("proxy_rotate") if isinstance(data.get("proxy_rotate"), dict) else {}
    interval = cfg.get("interval_seconds", 300)
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        interval = 300
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "command": str(cfg.get("command") or ""),
        "interval_seconds": max(10, min(interval, 86400)),
        "last_run_at": cfg.get("last_run_at"),
        "last_ok": bool(cfg.get("last_ok", False)),
        "last_message": str(cfg.get("last_message") or ""),
    }


def save_proxy_rotate(config: dict[str, Any]) -> dict[str, Any]:
    current = load_proxy_rotate()
    current.update(config)
    try:
        current["interval_seconds"] = int(current.get("interval_seconds") or 300)
    except (TypeError, ValueError):
        current["interval_seconds"] = 300
    current["interval_seconds"] = max(10, min(current["interval_seconds"], 86400))
    current["enabled"] = bool(current.get("enabled"))
    current["command"] = str(current.get("command") or "").strip()

    data = _read_all()
    data["proxy_rotate"] = current
    _write_all(data)
    return current


def parse_rotate_command(raw: str) -> RotateRequestSpec:
    text = _normalize_curl_text(raw or "")
    if not text:
        raise ValueError("rotate URL/curl is required")
    if text.startswith(("http://", "https://")):
        return RotateRequestSpec(method="GET", url=text, headers={})
    if not text.lower().startswith("curl "):
        raise ValueError("must be a URL or curl command")

    try:
        parts = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid curl syntax: {exc}") from exc
    if not parts or parts[0].lower() not in ("curl", "curl.exe"):
        raise ValueError("invalid curl command")

    method = "GET"
    headers: dict[str, str] = {}
    data: str | None = None
    url = ""
    i = 1
    while i < len(parts):
        part = parts[i]
        lower = part.lower()
        if part == "-x" or lower == "--proxy":
            i += 2
            continue
        if lower in ("-k", "--insecure", "-s", "--silent", "-l", "--location"):
            i += 1
            continue
        if part.startswith("-X") and len(part) > 2:
            method = part[2:].upper()
            i += 1
            continue
        if (part == "-X" or lower == "--request") and i + 1 < len(parts):
            method = parts[i + 1].upper()
            i += 2
            continue
        if lower in ("-h", "--header") and i + 1 < len(parts):
            name, _, value = parts[i + 1].partition(":")
            if name and value:
                headers[name.strip()] = value.strip()
            i += 2
            continue
        if lower in ("-b", "--cookie", "--cookie-jar") and i + 1 < len(parts):
            if lower != "--cookie-jar":
                headers.setdefault("Cookie", parts[i + 1])
            i += 2
            continue
        if lower in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode") and i + 1 < len(parts):
            data = parts[i + 1]
            if method == "GET":
                method = "POST"
            i += 2
            continue
        if part.startswith(("http://", "https://")):
            url = part
        i += 1

    if not url:
        raise ValueError("curl command has no http/https URL")
    return RotateRequestSpec(method=method, url=url, headers=headers, data=data)


def extract_proxy_from_response(text: str, data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("proxy", "url", "http", "https", "socks5"):
            val = data.get(key)
            if isinstance(val, str) and PROXY_RE.match(val.strip()):
                return val.strip()
    stripped = (text or "").strip()
    if PROXY_RE.match(stripped):
        return stripped
    match = re.search(r"(https?|socks4|socks5)://[^\s\"'<>]+", stripped, re.IGNORECASE)
    return match.group(0) if match else None


async def call_rotate_command(raw: str) -> tuple[str | None, str]:
    spec = parse_rotate_command(raw)
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.request(spec.method, spec.url, headers=spec.headers, content=spec.data)
    text = resp.text or ""
    parsed: Any = None
    try:
        parsed = resp.json()
    except Exception:
        parsed = None
    proxy = extract_proxy_from_response(text, parsed)
    detail = f"HTTP {resp.status_code}"
    if proxy:
        detail += " -> proxy returned"
    elif resp.status_code < 400:
        detail += " -> rotate OK, no proxy in response"
    else:
        detail += f": {text[:200]}"
    if resp.status_code >= 400:
        raise ValueError(detail)
    return proxy, detail


class ProxyRotateService:
    def __init__(self, apply_proxy):
        self._apply_proxy = apply_proxy
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def restart(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._loop())

    async def rotate_once(self) -> dict[str, Any]:
        async with self._lock:
            cfg = load_proxy_rotate()
            proxy, message = await call_rotate_command(cfg.get("command") or "")
            if proxy:
                self._apply_proxy(proxy)
            updated = save_proxy_rotate({
                "last_run_at": time.time(),
                "last_ok": True,
                "last_message": message,
            })
            updated["proxy"] = proxy
            return updated

    async def _loop(self) -> None:
        while True:
            cfg = load_proxy_rotate()
            if not cfg["enabled"] or not cfg["command"]:
                await asyncio.sleep(2.0)
                continue
            try:
                await self.rotate_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                save_proxy_rotate({
                    "last_run_at": time.time(),
                    "last_ok": False,
                    "last_message": f"{type(exc).__name__}: {exc}",
                })
            await asyncio.sleep(load_proxy_rotate()["interval_seconds"])
