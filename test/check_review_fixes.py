"""Smoke check cho 3 bug fix sau review:

1. warn_insecure_tls idempotent per-scope
2. _mask_proxy helper module-level
3. _safe_proxy_log delegate đúng cho cả 3 manager
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from gpt_signup_hybrid.config import warn_insecure_tls  # noqa: E402
from gpt_signup_hybrid.web.manager import (  # noqa: E402
    _mask_proxy,
    get_link_manager,
    get_manager,
    get_session_manager,
)


def expect(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        sys.exit(1)


def main() -> None:
    # 1. warn_insecure_tls idempotent per-scope
    # Reset state bằng import lại module — đơn giản hơn nhiều
    import gpt_signup_hybrid.config as cfg_mod
    cfg_mod._warned_scopes.clear()
    buf = io.StringIO()
    with redirect_stderr(buf):
        warn_insecure_tls("scope-a")
        warn_insecure_tls("scope-a")
        warn_insecure_tls("scope-a")
    out_a = buf.getvalue()
    # 1 lần warn → 2 line stderr (print + warnings.warn). 3 call idempotent
    # → vẫn chỉ 2 line. Nếu đếm > 2 → idempotent fail.
    sec_count = out_a.count("[security] TLS verification DISABLED")
    expect(
        sec_count == 2,
        f"scope-a 3 call → 1 lần warn (2 line stderr, got {sec_count})",
    )
    expect(
        cfg_mod._warned_scopes == {"scope-a"},
        f"_warned_scopes = {{'scope-a'}} (got {cfg_mod._warned_scopes})",
    )

    # Scope khác → log lại
    buf2 = io.StringIO()
    with redirect_stderr(buf2):
        warn_insecure_tls("scope-b")
    expect(
        "scope-b" in buf2.getvalue(),
        "scope-b log riêng (không bị block bởi scope-a)",
    )
    expect(
        cfg_mod._warned_scopes == {"scope-a", "scope-b"},
        "_warned_scopes giờ có cả 2 scope",
    )

    # 2. _mask_proxy
    expect(_mask_proxy(None) == "direct", "mask None → 'direct'")
    expect(_mask_proxy("") == "direct", "mask '' → 'direct'")
    expect(
        _mask_proxy("http://host:8080") == "http://host:8080",
        "no-cred → giữ nguyên",
    )
    expect(
        _mask_proxy("http://user:pass@host:8080") == "http://***@host:8080",
        "user:pass@ → ***@",
    )
    expect(
        _mask_proxy("socks5://u:p@1.2.3.4:1080") == "socks5://***@1.2.3.4:1080",
        "socks5 user:pass → ***",
    )
    expect(_mask_proxy("weird-no-scheme@host") == "weird-no-scheme@host", "no-scheme giữ nguyên")

    # 3. 3 manager đều có _safe_proxy_log delegate đúng
    for name, m in (("JobManager", get_manager()), ("SessionJobManager", get_session_manager()), ("LinkJobManager", get_link_manager())):
        m.set_proxy("http://u:p@host:8080")
        out = m._safe_proxy_log()
        expect(
            out == "http://***@host:8080",
            f"{name}._safe_proxy_log() mask đúng (got {out})",
        )
        m.set_proxy(None)
        expect(
            m._safe_proxy_log() == "direct",
            f"{name}._safe_proxy_log() None → direct",
        )

    print("OK — review fixes verified")


if __name__ == "__main__":
    main()
