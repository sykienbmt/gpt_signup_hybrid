"""Check camoufox + playwright + python version."""
from __future__ import annotations

import sys


def main() -> None:
    print(f"python: {sys.version}")
    try:
        from camoufox.__version__ import __version__ as cf_ver
        print(f"camoufox: {cf_ver}")
    except Exception as exc:
        print(f"camoufox: FAILED {exc}")

    try:
        from importlib.metadata import version as _ver
        print(f"playwright: {_ver('playwright')}")
    except Exception as exc:
        print(f"playwright: FAILED {exc}")

    try:
        from importlib.metadata import version as _ver
        print(f"camoufox (pkg): {_ver('camoufox')}")
    except Exception as exc:
        print(f"camoufox (pkg): FAILED {exc}")


if __name__ == "__main__":
    main()
