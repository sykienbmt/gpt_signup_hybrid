"""Property test cho Pool Import — Property 15.

Property 15: Pool import upsert preserves state, updates credentials
  For any existing combo in the database (with any values of used_for_signup,
  used_at, last_error, last_failed_at), importing a pool line with the same email
  overwrites password, refresh_token, client_id from the pool line while preserving
  used_for_signup, used_at, last_error, last_failed_at unchanged.

**Validates: Requirements 8.1, 8.2, 8.3**
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, settings
from hypothesis import strategies as st

from db.engine import DatabaseEngine
from db.migrate import MigrationTool
from db.repositories import ComboRepository, SessionResultRepository


# --- Strategies ---

st_email = st.from_regex(r"[a-z]{3,10}@[a-z]{3,8}\.(com|net|org)", fullmatch=True)

st_password = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=4,
    max_size=30,
)

st_refresh_token = st.from_regex(r"M\.C[A-Za-z0-9_\-]{10,40}", fullmatch=True)

st_client_id = st.from_regex(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    fullmatch=True,
)

st_iso_timestamp = st.one_of(
    st.none(),
    st.just("2024-06-15T10:30:00+00:00"),
    st.just("2025-01-01T00:00:00+00:00"),
)

st_error_string = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P")),
        min_size=1,
        max_size=50,
    ),
)


@st.composite
def st_existing_combo_state(draw):
    """Generate tracking state for an existing combo in the DB."""
    return {
        "used_for_signup": draw(st.sampled_from([0, 1])),
        "used_at": draw(st_iso_timestamp),
        "last_error": draw(st_error_string),
        "last_failed_at": draw(st_iso_timestamp),
    }


@st.composite
def st_pool_credentials(draw):
    """Generate new credentials from a pool line (different from original)."""
    return {
        "password": draw(st_password),
        "refresh_token": draw(st_refresh_token),
        "client_id": draw(st_client_id),
    }


# --- Helpers ---


def _make_engine_and_tools(tmp_path: Path):
    """Create DatabaseEngine + MigrationTool + repos in a temp directory."""
    db_path = tmp_path / "test_pool_import.db"
    engine = DatabaseEngine(db_path=db_path)
    combo_repo = ComboRepository(engine)
    session_repo = SessionResultRepository(engine)
    tool = MigrationTool(engine, combo_repo, session_repo)
    return engine, combo_repo, tool


def _insert_combo_with_state(engine: DatabaseEngine, email: str,
                             password: str, refresh_token: str, client_id: str,
                             state: dict) -> None:
    """Insert a combo row with specific tracking state values."""
    with engine.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO outlook_combos
                (email, password, refresh_token, client_id,
                 used_for_signup, used_at, last_error, last_failed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                email,
                password,
                refresh_token,
                client_id,
                state["used_for_signup"],
                state["used_at"],
                state["last_error"],
                state["last_failed_at"],
            ),
        )


def _write_pool_file(pool_path: Path, email: str, password: str,
                     refresh_token: str, client_id: str) -> None:
    """Write a single-line pool file."""
    pool_path.write_text(
        f"{email}|{password}|{refresh_token}|{client_id}\n",
        encoding="utf-8",
    )


# --- Property 15: Pool import upsert preserves state, updates credentials ---


@settings(max_examples=100)
@given(
    email=st_email,
    original_password=st_password,
    original_refresh_token=st_refresh_token,
    original_client_id=st_client_id,
    existing_state=st_existing_combo_state(),
    new_credentials=st_pool_credentials(),
)
def test_prop15_pool_import_upsert_preserves_state_updates_credentials(
    email,
    original_password,
    original_refresh_token,
    original_client_id,
    existing_state,
    new_credentials,
):
    """Property 15: For any existing combo in the database (with any values of
    used_for_signup, used_at, last_error, last_failed_at), importing a pool line
    with the same email overwrites password, refresh_token, client_id from the
    pool line while preserving used_for_signup, used_at, last_error, last_failed_at
    unchanged.

    **Validates: Requirements 8.1, 8.2, 8.3**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        engine, combo_repo, tool = _make_engine_and_tools(tmp_path)

        # Step 1: Insert combo with tracking state
        _insert_combo_with_state(
            engine, email,
            original_password, original_refresh_token, original_client_id,
            existing_state,
        )

        # Verify initial state is correct
        record_before = combo_repo.get_by_email(email)
        assert record_before is not None

        # Step 2: Create pool file with same email but different credentials
        pool_path = tmp_path / "pool.txt"
        _write_pool_file(
            pool_path, email,
            new_credentials["password"],
            new_credentials["refresh_token"],
            new_credentials["client_id"],
        )

        # Step 3: Run import_pool_file()
        summary = tool.import_pool_file(pool_path)

        # Verify import succeeded as an update
        assert summary.updated == 1, (
            f"Expected 1 updated, got {summary.updated}. "
            f"Inserted: {summary.inserted}, Skipped: {summary.skipped}, "
            f"Errors: {summary.errors}"
        )

        # Step 4: Read back and verify
        record_after = combo_repo.get_by_email(email)
        assert record_after is not None, f"Record for {email} not found after import"

        # Credentials MUST be updated to new values
        assert record_after["password"] == new_credentials["password"], (
            f"password not updated: expected {new_credentials['password']!r}, "
            f"got {record_after['password']!r}"
        )
        assert record_after["refresh_token"] == new_credentials["refresh_token"], (
            f"refresh_token not updated: expected {new_credentials['refresh_token']!r}, "
            f"got {record_after['refresh_token']!r}"
        )
        assert record_after["client_id"] == new_credentials["client_id"], (
            f"client_id not updated: expected {new_credentials['client_id']!r}, "
            f"got {record_after['client_id']!r}"
        )

        # Tracking state MUST be preserved (unchanged)
        assert record_after["used_for_signup"] == existing_state["used_for_signup"], (
            f"used_for_signup changed: expected {existing_state['used_for_signup']}, "
            f"got {record_after['used_for_signup']}"
        )
        assert record_after["used_at"] == existing_state["used_at"], (
            f"used_at changed: expected {existing_state['used_at']!r}, "
            f"got {record_after['used_at']!r}"
        )
        assert record_after["last_error"] == existing_state["last_error"], (
            f"last_error changed: expected {existing_state['last_error']!r}, "
            f"got {record_after['last_error']!r}"
        )
        assert record_after["last_failed_at"] == existing_state["last_failed_at"], (
            f"last_failed_at changed: expected {existing_state['last_failed_at']!r}, "
            f"got {record_after['last_failed_at']!r}"
        )


if __name__ == "__main__":
    print("Running Property 15: Pool import upsert semantics...")
    test_prop15_pool_import_upsert_preserves_state_updates_credentials()
    print("✅ Property 15 passed (100 examples)")
