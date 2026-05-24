"""Verify import_pool_file logic: parse, upsert, error handling."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.engine import DatabaseEngine
from db.migrate import ImportSummary, MigrationTool
from db.repositories import ComboRepository, SessionResultRepository


def make_tool(tmp_dir: Path) -> tuple[MigrationTool, DatabaseEngine]:
    db_path = tmp_dir / "test.db"
    engine = DatabaseEngine(db_path)
    combo_repo = ComboRepository(engine)
    session_repo = SessionResultRepository(engine)
    tool = MigrationTool(engine, combo_repo, session_repo)
    return tool, engine


def test_basic_import():
    """Import 2 valid lines → inserted=2."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        pool_file = tmp_dir / "pool.txt"
        pool_file.write_text(
            "test@example.com|pass123|M.C_token1|client_1\n"
            "user2@hotmail.com|pw2|M.C_token2|client_2\n"
        )

        tool, engine = make_tool(tmp_dir)
        result = tool.import_pool_file(pool_file)

        assert result.total_lines == 2, f"Expected 2, got {result.total_lines}"
        assert result.inserted == 2, f"Expected 2 inserted, got {result.inserted}"
        assert result.updated == 0
        assert result.skipped == 0

        # Verify data in DB
        conn = engine.raw_connection()
        row = conn.execute(
            "SELECT * FROM outlook_combos WHERE email = ?", ("test@example.com",)
        ).fetchone()
        assert row is not None
        assert row["password"] == "pass123"
        assert row["refresh_token"] == "M.C_token1"
        assert row["client_id"] == "client_1"

    print("✓ test_basic_import")


def test_upsert_preserves_state():
    """Existing email → update credentials, preserve tracking state."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        tool, engine = make_tool(tmp_dir)

        # Pre-insert a combo with used_for_signup=1 and last_error set
        with engine.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO outlook_combos (email, password, refresh_token, client_id,
                    used_for_signup, used_at, last_error, last_failed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("exist@test.com", "old_pw", "old_token", "old_client",
                 1, "2025-01-01T00:00:00", "some_error", "2025-01-02T00:00:00"),
            )

        # Import with new credentials for same email
        pool_file = tmp_dir / "pool.txt"
        pool_file.write_text("exist@test.com|new_pw|new_token|new_client\n")

        result = tool.import_pool_file(pool_file)
        assert result.total_lines == 1
        assert result.updated == 1
        assert result.inserted == 0

        # Verify: credentials updated, state preserved
        conn = engine.raw_connection()
        row = conn.execute(
            "SELECT * FROM outlook_combos WHERE email = ?", ("exist@test.com",)
        ).fetchone()
        assert row["password"] == "new_pw"
        assert row["refresh_token"] == "new_token"
        assert row["client_id"] == "new_client"
        # Preserved fields
        assert row["used_for_signup"] == 1
        assert row["used_at"] == "2025-01-01T00:00:00"
        assert row["last_error"] == "some_error"
        assert row["last_failed_at"] == "2025-01-02T00:00:00"

    print("✓ test_upsert_preserves_state")


def test_skip_blank_and_comments():
    """Blank lines and # comments are skipped, not counted as total_lines."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        pool_file = tmp_dir / "pool.txt"
        pool_file.write_text(
            "# This is a comment\n"
            "\n"
            "   \n"
            "valid@test.com|pw|token|client\n"
            "# Another comment\n"
        )

        tool, _ = make_tool(tmp_dir)
        result = tool.import_pool_file(pool_file)

        assert result.total_lines == 1, f"Expected 1, got {result.total_lines}"
        assert result.inserted == 1

    print("✓ test_skip_blank_and_comments")


def test_parse_errors():
    """Invalid lines → skipped with error, valid lines still imported."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        pool_file = tmp_dir / "pool.txt"
        pool_file.write_text(
            "good@test.com|pw|token|client\n"
            "bad_line_no_pipes\n"
            "empty@test.com||token|client\n"
            "also_good@test.com|pw2|token2|client2\n"
        )

        tool, _ = make_tool(tmp_dir)
        result = tool.import_pool_file(pool_file)

        assert result.total_lines == 4
        assert result.inserted == 2
        assert result.skipped == 2
        assert len(result.errors) == 2

    print("✓ test_parse_errors")


def test_file_not_found():
    """Non-existent file → SystemExit(1)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        tool, _ = make_tool(tmp_dir)

        try:
            tool.import_pool_file(Path("/nonexistent/pool.txt"))
            assert False, "Should have raised SystemExit"
        except SystemExit as e:
            assert e.code == 1

    print("✓ test_file_not_found")


if __name__ == "__main__":
    test_basic_import()
    test_upsert_preserves_state()
    test_skip_blank_and_comments()
    test_parse_errors()
    test_file_not_found()
    print("\nAll import_pool_file checks passed!")
