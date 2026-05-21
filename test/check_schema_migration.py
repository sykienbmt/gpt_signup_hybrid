"""Verify task 1.3: schema migration trong DatabaseEngine.__init__."""

import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.engine import DatabaseEngine, DatabaseError, SchemaError
from db.schema import CURRENT_VERSION


def test_migration_creates_all_tables():
    """Engine init phải tạo tất cả tables qua migration."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = DatabaseEngine(db_path)

        conn = engine.raw_connection()

        # Kiểm tra tất cả tables tồn tại (exclude internal sqlite tables)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        table_names = sorted(row[0] for row in tables)

        expected = sorted([
            "_schema_version",
            "outlook_combos",
            "jobs",
            "job_logs",
            "session_results",
        ])
        assert table_names == expected, f"Tables mismatch: {table_names} != {expected}"
        print("[PASS] All tables created")


def test_schema_version_recorded():
    """Migration phải ghi CURRENT_VERSION vào _schema_version."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = DatabaseEngine(db_path)

        conn = engine.raw_connection()
        row = conn.execute("SELECT MAX(version) FROM _schema_version").fetchone()
        assert row[0] == CURRENT_VERSION, f"Version mismatch: {row[0]} != {CURRENT_VERSION}"
        print("[PASS] Schema version recorded correctly")


def test_migration_idempotent():
    """Tạo engine lần 2 trên cùng DB không lỗi (skip migration nếu version đã đúng)."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine1 = DatabaseEngine(db_path)
        # Đóng connection cũ (giả lập restart)
        engine1.raw_connection().close()

        # Tạo engine lần 2
        engine2 = DatabaseEngine(db_path)
        conn = engine2.raw_connection()
        row = conn.execute("SELECT MAX(version) FROM _schema_version").fetchone()
        assert row[0] == CURRENT_VERSION
        print("[PASS] Migration idempotent (skip khi version đã đúng)")


def test_schema_error_is_subclass():
    """SchemaError phải là subclass của DatabaseError."""
    assert issubclass(SchemaError, DatabaseError)
    assert issubclass(SchemaError, Exception)
    print("[PASS] SchemaError hierarchy correct")


def test_indexes_created():
    """Migration phải tạo indexes."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = DatabaseEngine(db_path)

        conn = engine.raw_connection()
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = sorted(row[0] for row in indexes)

        expected = sorted([
            "idx_jobs_status",
            "idx_jobs_email",
            "idx_job_logs_job_id",
            "idx_session_results_email",
        ])
        assert index_names == expected, f"Indexes mismatch: {index_names} != {expected}"
        print("[PASS] All indexes created")


def test_migration_rollback_on_failure():
    """Nếu DDL fail → rollback toàn bộ, raise SchemaError."""
    import sqlite3 as _sqlite3
    from unittest.mock import patch
    from db.schema import ALL_DDL

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"

        # Patch ALL_DDL để inject invalid SQL
        bad_ddl = list(ALL_DDL) + ["THIS IS NOT VALID SQL;"]
        with patch("db.engine.ALL_DDL", bad_ddl):
            try:
                engine = DatabaseEngine(db_path)
                assert False, "Should have raised SchemaError"
            except SchemaError as e:
                assert "failed" in str(e).lower()
                print(f"[PASS] SchemaError raised: {e}")

        # Verify rollback: tables should NOT exist
        conn = _sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        # _schema_version might or might not exist depending on rollback scope
        # But the key check: không có full set of tables
        table_names = [row[0] for row in tables]
        assert "jobs" not in table_names, f"Rollback failed — jobs table exists: {table_names}"
        print("[PASS] Rollback confirmed — no tables after failed migration")
        conn.close()


if __name__ == "__main__":
    test_schema_error_is_subclass()
    test_migration_creates_all_tables()
    test_schema_version_recorded()
    test_migration_idempotent()
    test_indexes_created()
    test_migration_rollback_on_failure()
    print("\n✅ All migration tests passed!")
