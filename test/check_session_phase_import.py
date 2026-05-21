"""Verify session_phase + browser_phase import + retry helpers."""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    targets = [
        root / "_browser_retry.py",
        root / "browser_phase.py",
        root / "session_phase.py",
        root / "signup.py",
    ]
    for path in targets:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            print(f"OK ast: {path.name} ({len(tree.body)} top-level nodes)")
        except SyntaxError as exc:
            print(f"FAIL syntax {path.name}: {exc}")
            return 1

    sys.path.insert(0, str(root.parent))
    try:
        from gpt_signup_hybrid import _browser_retry, browser_phase, session_phase  # noqa: F401
        from gpt_signup_hybrid._browser_retry import (
            DRIVER_DEAD_MARKERS,
            LAUNCH_RETRY_BACKOFF,
            LAUNCH_RETRY_MAX,
            is_driver_dead_error,
        )
        print(f"OK shared: markers={len(DRIVER_DEAD_MARKERS)} retry_max={LAUNCH_RETRY_MAX} backoff={LAUNCH_RETRY_BACKOFF}")

        # Verify cả 2 phase đều ref tới shared marker
        from gpt_signup_hybrid.browser_phase import _is_driver_dead_error as bp_fn
        from gpt_signup_hybrid.session_phase import _is_driver_dead_error as sp_fn
        assert bp_fn is is_driver_dead_error, "browser_phase phải dùng shared fn"
        assert sp_fn is is_driver_dead_error, "session_phase phải dùng shared fn"
        print("OK shared fn reused by both phases")

        # Test cases
        class _E(Exception):
            pass
        cases = [
            ("Page.goto: Connection closed while reading from the driver", True),
            ("Target page, context or browser has been closed", True),
            ("Browser has been closed", True),
            ("BrowserContext has been closed", True),
            ("regular OTP error", False),
            ("password incorrect", False),
            ("", False),
        ]
        for msg, expected in cases:
            got = is_driver_dead_error(_E(msg) if msg else None)
            status = "OK" if got == expected else "FAIL"
            print(f"  {status} {msg!r:60} → {got} (want {expected})")
            if got != expected:
                return 1

        # None case
        got = is_driver_dead_error(None)
        if got is not False:
            print(f"FAIL None case: {got}")
            return 1
        print("  OK None → False")

    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
