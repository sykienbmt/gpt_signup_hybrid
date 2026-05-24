"""Property 2: Connection context manager commit/rollback.

For any sequence of write operations within a `get_connection()` context manager,
if the block completes without exception then all writes are committed and readable;
if any exception is raised, then no writes from that block persist and the original
exception type is preserved.

**Validates: Requirements 1.6, 5.1, 5.2**
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from db.engine import DatabaseEngine


# --- Strategies ---

# Random text for insert values (printable, non-empty)
st_value = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=50,
)

# Random number of rows to insert per transaction (1..10)
st_row_count = st.integers(min_value=1, max_value=10)

# Random exception types to raise inside transaction block
# Exclude KeyError because str(KeyError("x")) == "'x'" (adds quotes), which is
# Python's KeyError repr behavior, not relevant to our property under test.
st_exception_type = st.sampled_from([
    ValueError,
    TypeError,
    RuntimeError,
    IOError,
    ZeroDivisionError,
    AttributeError,
    IndexError,
    OSError,
])


def _make_engine(tmp_path: Path) -> DatabaseEngine:
    """Create a DatabaseEngine with a test table in a temp directory."""
    db_path = tmp_path / "test_prop2.db"
    engine = DatabaseEngine(db_path=db_path)
    # Create a simple test table outside of get_connection (raw)
    conn = engine.raw_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS prop2_test (id INTEGER PRIMARY KEY AUTOINCREMENT, val TEXT NOT NULL)")
    return engine


# --- Property Test: Normal completion → data persists ---


@settings(max_examples=100)
@given(values=st.lists(st_value, min_size=1, max_size=10))
def test_commit_on_success(values):
    """Property 2 — commit path: all writes within get_connection() persist
    when block completes without exception.

    **Validates: Requirements 1.6, 5.1, 5.2**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = _make_engine(Path(tmpdir))

        # Write all values inside a single transaction
        with engine.get_connection() as conn:
            for v in values:
                conn.execute("INSERT INTO prop2_test (val) VALUES (?)", (v,))

        # Verify all writes persisted (readable after commit)
        raw = engine.raw_connection()
        rows = raw.execute("SELECT val FROM prop2_test ORDER BY id").fetchall()
        persisted = [r[0] for r in rows]

        assert persisted == values, (
            f"Expected {values}, got {persisted}"
        )


# --- Property Test: Exception raised → rollback + exception type preserved ---


@settings(max_examples=100)
@given(
    values=st.lists(st_value, min_size=1, max_size=10),
    exc_type=st_exception_type,
    exc_msg=st.text(min_size=1, max_size=30),
)
def test_rollback_on_exception(values, exc_type, exc_msg):
    """Property 2 — rollback path: no writes persist when exception is raised,
    and the original exception type is preserved (re-raised).

    **Validates: Requirements 1.6, 5.1, 5.2**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine = _make_engine(Path(tmpdir))

        # Attempt writes then raise exception
        caught_exc = None
        try:
            with engine.get_connection() as conn:
                for v in values:
                    conn.execute("INSERT INTO prop2_test (val) VALUES (?)", (v,))
                raise exc_type(exc_msg)
        except Exception as e:
            caught_exc = e

        # 1) Original exception type is preserved
        assert caught_exc is not None, "Exception should have been re-raised"
        assert type(caught_exc) is exc_type, (
            f"Expected {exc_type.__name__}, got {type(caught_exc).__name__}"
        )

        # 2) No writes persisted (all rolled back)
        raw = engine.raw_connection()
        rows = raw.execute("SELECT val FROM prop2_test").fetchall()
        assert len(rows) == 0, (
            f"Expected 0 rows after rollback, got {len(rows)}: {[r[0] for r in rows]}"
        )


if __name__ == "__main__":
    print("Running Property 2: Transaction safety tests...")
    print("  Testing commit on success...")
    test_commit_on_success()
    print("  ✓ Commit path passed (100 examples)")
    print("  Testing rollback on exception...")
    test_rollback_on_exception()
    print("  ✓ Rollback path passed (100 examples)")
    print("\n✅ Property 2: All transaction safety tests passed!")
