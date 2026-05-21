"""Syntax check: verify all modified Python files parse correctly."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    ROOT / "payment_link.py",
    ROOT / "web" / "server.py",
    ROOT / "web" / "manager.py",
]

errors = []
for f in FILES:
    try:
        ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        print(f"OK  {f.relative_to(ROOT)}")
    except SyntaxError as exc:
        errors.append(f"{f.relative_to(ROOT)}: {exc}")
        print(f"ERR {f.relative_to(ROOT)}: {exc}")

if errors:
    print(f"\n{len(errors)} file(s) with syntax errors")
    sys.exit(1)
else:
    print(f"\nAll {len(FILES)} files OK")
