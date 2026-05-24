"""Verify DatabaseEngine — syntax, pragmas, transaction behavior."""

import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.engine import DatabaseEngine


def test_init_creates_dirs_and_file():
    """Constructor tạo directories + DB file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "sub" / "deep" / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        assert db_path.exists(), f"DB file should exist: {db_path}"
        assert not engine.is_closed
        print("✓ init creates dirs and file")


def test_wal_mode_enabled():
    """WAL mode phải enabled sau init."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        conn = engine.raw_connection()
        result = conn.execute("PRAGMA journal_mode").fetchone()
        assert result[0] == "wal", f"Expected WAL, got: {result[0]}"
        print("✓ WAL mode enabled")


def test_busy_timeout():
    """busy_timeout = 5000."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        conn = engine.raw_connection()
        result = conn.execute("PRAGMA busy_timeout").fetchone()
        assert result[0] == 5000, f"Expected 5000, got: {result[0]}"
        print("✓ busy_timeout = 5000")


def test_foreign_keys_on():
    """foreign_keys ON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        conn = engine.raw_connection()
        result = conn.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1, f"Expected 1, got: {result[0]}"
        print("✓ foreign_keys ON")


def test_permission_error_non_writable_dir():
    """Raise PermissionError nếu dir không writable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        read_only_dir = Path(tmpdir) / "readonly"
        read_only_dir.mkdir()
        os.chmod(read_only_dir, 0o555)
        try:
            db_path = read_only_dir / "test.db"
            try:
                DatabaseEngine(db_path=db_path)
                assert False, "Should have raised PermissionError"
            except PermissionError as e:
                assert "not writable" in str(e)
                print("✓ PermissionError on non-writable dir")
        finally:
            os.chmod(read_only_dir, 0o755)


def test_get_connection_commit():
    """get_connection() auto-commit on success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        conn = engine.raw_connection()
        conn.execute("CREATE TABLE t (val TEXT)")

        with engine.get_connection() as c:
            c.execute("INSERT INTO t (val) VALUES (?)", ("hello",))

        # Verify committed
        row = conn.execute("SELECT val FROM t").fetchone()
        assert row[0] == "hello", f"Expected 'hello', got: {row[0]}"
        print("✓ get_connection commits on success")


def test_get_connection_rollback():
    """get_connection() rollback on exception, re-raise original type."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        conn = engine.raw_connection()
        conn.execute("CREATE TABLE t (val TEXT)")

        try:
            with engine.get_connection() as c:
                c.execute("INSERT INTO t (val) VALUES (?)", ("should_rollback",))
                raise ValueError("test error")
        except ValueError as e:
            assert str(e) == "test error"

        # Verify rolled back
        row = conn.execute("SELECT val FROM t").fetchone()
        assert row is None, f"Should be None after rollback, got: {row}"
        print("✓ get_connection rollback on exception, preserves exception type")


def test_raw_connection_read():
    """raw_connection() trả connection cho read operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        conn = engine.raw_connection()
        conn.execute("CREATE TABLE t (val TEXT)")
        conn.execute("INSERT INTO t (val) VALUES ('data')")

        # Read via raw_connection
        result = conn.execute("SELECT val FROM t").fetchone()
        assert result[0] == "data"
        print("✓ raw_connection works for reads")


def test_db_path_property():
    """db_path property trả Path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        assert engine.db_path == db_path
        print("✓ db_path property correct")


if __name__ == "__main__":
    test_init_creates_dirs_and_file()
    test_wal_mode_enabled()
    test_busy_timeout()
    test_foreign_keys_on()
    test_permission_error_non_writable_dir()
    test_get_connection_commit()
    test_get_connection_rollback()
    test_raw_connection_read()
    test_db_path_property()
    print("\n✅ All engine checks passed!")
