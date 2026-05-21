"""Syntax + import check cho db/repositories.py."""

import ast
import sys
from pathlib import Path

repo_file = Path(__file__).parent.parent / "db" / "repositories.py"

# 1) Parse AST — kiểm tra syntax hợp lệ
with open(repo_file) as f:
    source = f.read()

try:
    tree = ast.parse(source)
    print(f"✓ AST parse OK: {repo_file.name}")
except SyntaxError as e:
    print(f"✗ SyntaxError: {e}")
    sys.exit(1)

# 2) Import module — kiểm tra runtime import chain
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.repositories import (
    ComboRepository,
    RepositoryError,
    TERMINAL_ERROR_SUBSTRINGS,
)
from db.engine import DatabaseError

# 3) Kiểm tra exception hierarchy
assert issubclass(RepositoryError, DatabaseError), "RepositoryError phải kế thừa DatabaseError"

# 4) Kiểm tra RepositoryError attributes
err = RepositoryError("test_op", ValueError("inner"))
assert err.operation == "test_op"
assert isinstance(err.cause, ValueError)
assert "test_op failed:" in str(err)

# 5) Kiểm tra TERMINAL_ERROR_SUBSTRINGS
assert len(TERMINAL_ERROR_SUBSTRINGS) == 4
assert "registration_disallowed" in TERMINAL_ERROR_SUBSTRINGS
assert "invalid_grant" in TERMINAL_ERROR_SUBSTRINGS
assert "AADSTS50173" in TERMINAL_ERROR_SUBSTRINGS
assert "AADSTS70008" in TERMINAL_ERROR_SUBSTRINGS

# 6) Kiểm tra ComboRepository có đầy đủ methods
methods = [
    "get_by_email",
    "upsert",
    "mark_success",
    "mark_failure",
    "pick_available",
    "update_refresh_token",
    "list_all",
]
for m in methods:
    assert hasattr(ComboRepository, m), f"Missing method: {m}"

print("✓ Import + structure checks passed")
print("✓ RepositoryError hierarchy correct")
print("✓ All ComboRepository methods present")
print("\nAll checks passed!")
