"""Import + parse browser_phase.py để verify thay đổi không break."""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    targets = [
        root / "browser_phase.py",
        root / "signup.py",
        root / "config.py",
    ]
    for path in targets:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            print(f"OK ast: {path.name} ({len(tree.body)} top-level nodes)")
        except SyntaxError as exc:
            print(f"FAIL syntax {path.name}: {exc}")
            return 1

    # Thử import để bắt NameError, undefined symbol
    sys.path.insert(0, str(root.parent))
    try:
        from gpt_signup_hybrid import browser_phase  # noqa: F401
        print("OK import: browser_phase")
        from gpt_signup_hybrid import signup  # noqa: F401
        print("OK import: signup")
        # Verify symbols mới tồn tại
        from gpt_signup_hybrid.browser_phase import (
            _is_driver_dead_error,
            _DRIVER_DEAD_MARKERS,
            _LAUNCH_RETRY_MAX,
            BrowserPhaseError,
        )
        # Test _is_driver_dead_error
        class _E(Exception):
            pass
        cases = [
            ("Page.goto: Connection closed while reading from the driver", True),
            ("Target closed", True),
            ("Browser has been closed", True),
            ("OTP wrong code: invalid", False),
            ("regular error", False),
        ]
        for msg, expected in cases:
            got = _is_driver_dead_error(_E(msg))
            status = "OK" if got == expected else "FAIL"
            print(f"  {status} _is_driver_dead_error({msg!r}) = {got} (want {expected})")
            if got != expected:
                return 1
        print(f"OK markers: _LAUNCH_RETRY_MAX={_LAUNCH_RETRY_MAX} markers={len(_DRIVER_DEAD_MARKERS)}")
    except Exception as exc:
        print(f"FAIL import: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
