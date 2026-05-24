"""Parse AST mọi file Python trong package để chắc chắn không lỗi syntax."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    fails = 0
    for p in ROOT.rglob("*.py"):
        # Bỏ venv, build artifact
        rel = p.relative_to(ROOT)
        first = rel.parts[0]
        if first in {".venv", "__pycache__", "codex-security-scan-20260520-184456"}:
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError as exc:
            fails += 1
            print(f"[FAIL] {rel}: {exc}")
    if fails == 0:
        print("[OK] toàn bộ file parse sạch")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
