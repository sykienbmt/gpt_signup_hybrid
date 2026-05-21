"""Config + utilities — self-contained (không import signup_runner)."""
from __future__ import annotations

import os
import shutil
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# ─── Env parsing helpers ──────────────────────────────────────────────


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse .env file đơn giản (KEY=VALUE, bỏ comment #)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        values[key] = val
    return values


def _lookup(env: dict[str, str], key: str, default: str) -> str:
    return os.environ.get(key) or env.get(key) or default


def _parse_bool(val: str, *, default: bool) -> bool:
    if not val:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _parse_int(val: str, *, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _parse_float(val: str, *, default: float) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ─── Settings ─────────────────────────────────────────────────────────


@dataclass
class Settings:
    root_dir: Path
    runtime_dir: Path
    browser_engine: str = "camoufox"
    browser_channel: str = "chrome"
    browser_headless: bool = False
    browser_viewport_width: int = 1440
    browser_viewport_height: int = 800
    browser_use_profile_template: bool = True
    browser_profile_template_dir: Path = Path("runtime/profiles/template")
    browser_camoufox_profile_dir: Path = Path("runtime/profiles/camoufox_template")

    @property
    def profiles_dir(self) -> Path:
        return self.runtime_dir / "profiles"

    def profile_dir_for(self, job_id: str) -> Path:
        return self.profiles_dir / job_id


def load_settings(root_dir: Path | None = None, env_file: str | Path = ".env") -> Settings:
    root = Path(root_dir or os.environ.get("GPT_REG_ROOT") or Path.cwd()).resolve()
    env_path = Path(env_file)
    if not env_path.is_absolute():
        env_path = root / env_path
    env = _load_env_file(env_path)

    runtime_dir = Path(_lookup(env, "RUNTIME_DIR", "runtime"))
    if not runtime_dir.is_absolute():
        runtime_dir = root / runtime_dir

    profile_template_dir = Path(_lookup(env, "BROWSER_PROFILE_TEMPLATE_DIR", "runtime/profiles/template"))
    if not profile_template_dir.is_absolute():
        profile_template_dir = root / profile_template_dir

    camoufox_profile_dir = Path(_lookup(env, "BROWSER_CAMOUFOX_PROFILE_DIR", "runtime/profiles/camoufox_template"))
    if not camoufox_profile_dir.is_absolute():
        camoufox_profile_dir = root / camoufox_profile_dir

    return Settings(
        root_dir=root,
        runtime_dir=runtime_dir,
        browser_engine=_lookup(env, "BROWSER_ENGINE", "camoufox"),
        browser_channel=_lookup(env, "BROWSER_CHANNEL", "chrome"),
        browser_headless=_parse_bool(_lookup(env, "BROWSER_HEADLESS", "false"), default=False),
        browser_viewport_width=_parse_int(_lookup(env, "BROWSER_VIEWPORT_WIDTH", "1440"), default=1440),
        browser_viewport_height=_parse_int(_lookup(env, "BROWSER_VIEWPORT_HEIGHT", "800"), default=800),
        browser_use_profile_template=_parse_bool(
            _lookup(env, "BROWSER_USE_PROFILE_TEMPLATE", "true"), default=True,
        ),
        browser_profile_template_dir=profile_template_dir,
        browser_camoufox_profile_dir=camoufox_profile_dir,
    )


def ensure_runtime_dirs(settings: Settings, extra: Iterable[Path] = ()) -> None:
    for path in (
        settings.profiles_dir,
        settings.browser_profile_template_dir,
        settings.browser_camoufox_profile_dir,
        *extra,
    ):
        path.mkdir(parents=True, exist_ok=True)


def runtime_session_dir(settings: Settings) -> Path:
    out = settings.runtime_dir / "sessions"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ─── Profile dir management ──────────────────────────────────────────


_PROFILE_COPY_IGNORE = (
    "BrowserMetrics",
    "Crashpad",
    "DevToolsActivePort",
    "LOCK",
    "RunningChromeVersion",
    "SingletonLock",
    "SingletonCookie",
    "SingletonSocket",
)


def _directory_has_contents(path: Path) -> bool:
    try:
        next(path.iterdir())
        return True
    except (FileNotFoundError, StopIteration):
        return False


def prepare_profile_dir(*, profile_dir: Path, template_dir: Path, use_template: bool) -> bool:
    """Clone profile template → profile_dir. Return True nếu đã clone."""
    if profile_dir.resolve() == template_dir.resolve():
        raise ValueError("Run profile clone path must be different from template profile path")
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.parent.mkdir(parents=True, exist_ok=True)
    if use_template and _directory_has_contents(template_dir):
        shutil.copytree(
            template_dir,
            profile_dir,
            ignore=shutil.ignore_patterns(*_PROFILE_COPY_IGNORE),
        )
        return True
    profile_dir.mkdir(parents=True, exist_ok=True)
    return False


# ─── TLS security helpers ────────────────────────────────────────────


_INSECURE_TLS_ENV = "GPT_SIGNUP_INSECURE_TLS"
_warned_scopes: set[str] = set()


def env_insecure_tls() -> bool:
    """Đọc env GPT_SIGNUP_INSECURE_TLS → bool. Default False (secure).

    Bật qua env (1/true/yes/on) hoặc CLI flag truyền tay. Không có default
    insecure ở bất cứ đâu — chỉ opt-in.
    """
    raw = os.environ.get(_INSECURE_TLS_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def warn_insecure_tls(scope: str) -> None:
    """In cảnh báo loud khi 1 phase đang chạy với TLS verify off.

    Idempotent per-process per-scope: chỉ log lần đầu mỗi scope để không spam.
    """
    if scope in _warned_scopes:
        return
    _warned_scopes.add(scope)
    msg = (
        f"[security] TLS verification DISABLED for {scope!r} — "
        f"debug/local-dev only. Set {_INSECURE_TLS_ENV}=0 or remove --insecure-tls "
        f"to restore secure default."
    )
    print(msg, file=sys.stderr)
    warnings.warn(msg, RuntimeWarning, stacklevel=2)
