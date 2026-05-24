"""Property tests cho ComboRepository — Properties 3, 4, 5.

Property 3: Refresh token rotation round-trip
  For any combo email and any valid refresh token string (starting with "M.C"),
  after calling update_refresh_token(email, token), reading back via get_by_email(email)
  should return the new token and a valid ISO 8601 last_refresh_at timestamp.

Property 4: Combo state mutation preserves invariants
  mark_success(email) sets used_for_signup=1, used_at to a valid timestamp, and last_error=None;
  mark_failure(email, error) sets last_error and last_failed_at without modifying used_for_signup
  regardless of its prior value.

Property 5: Pick available returns correct combo under filtering rules
  For any set of combos with mixed states, pick_available() returns the earliest-created combo
  where used_for_signup=0 AND last_error does not contain any terminal error substring, or None
  if no such combo exists.

**Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6**
"""

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from db.engine import DatabaseEngine
from db.repositories import ComboRepository, TERMINAL_ERROR_SUBSTRINGS


# --- Strategies ---

# Valid email-like strings (simplified for testing)
st_email = st.from_regex(r"[a-z]{3,10}@[a-z]{3,8}\.(com|net|org)", fullmatch=True)

# Valid refresh tokens starting with "M.C"
st_refresh_token = st.from_regex(r"M\.C[A-Za-z0-9_\-]{10,40}", fullmatch=True)

# Generic password strings
st_password = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=4,
    max_size=30,
)

# Client IDs (UUID-like)
st_client_id = st.from_regex(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    fullmatch=True,
)

# Non-terminal error strings (must NOT contain terminal substrings)
st_non_terminal_error = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=50,
).filter(
    lambda s: all(term not in s for term in TERMINAL_ERROR_SUBSTRINGS)
)

# Terminal error strings (MUST contain at least one terminal substring)
st_terminal_error = st.sampled_from(TERMINAL_ERROR_SUBSTRINGS).flatmap(
    lambda term: st.tuples(
        st.text(min_size=0, max_size=10),
        st.just(term),
        st.text(min_size=0, max_size=10),
    ).map(lambda parts: parts[0] + parts[1] + parts[2])
)

# Any error string for mark_failure
st_error_string = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=80,
)

# used_for_signup values (0 or 1)
st_used_for_signup = st.sampled_from([0, 1])


# --- Helpers ---


def _make_engine_and_repo(tmp_path: Path) -> tuple[DatabaseEngine, ComboRepository]:
    """Create a DatabaseEngine + ComboRepository in a temp directory (WAL mode needs file)."""
    db_path = tmp_path / "test_prop345.db"
    engine = DatabaseEngine(db_path=db_path)
    repo = ComboRepository(engine)
    return engine, repo


def _insert_combo(
    repo: ComboRepository,
    engine: DatabaseEngine,
    email: str,
    password: str = "pass123",
    refresh_token: str = "M.Cdefault0000000000",
    client_id: str = "00000000-0000-0000-0000-000000000000",
    used_for_signup: int = 0,
    last_error: str | None = None,
    last_failed_at: str | None = None,
    created_at: str | None = None,
) -> None:
    """Insert a combo row directly for test setup."""
    with engine.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO outlook_combos
                (email, password, refresh_token, client_id, used_for_signup,
                 last_error, last_failed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))
            """,
            (email, password, refresh_token, client_id, used_for_signup,
             last_error, last_failed_at, created_at),
        )


def _is_valid_iso8601(ts: str) -> bool:
    """Check if a string is a valid ISO 8601 timestamp."""
    try:
        datetime.fromisoformat(ts)
        return True
    except (ValueError, TypeError):
        return False


# --- Property 3: Refresh token rotation round-trip ---


@settings(max_examples=100)
@given(
    email=st_email,
    password=st_password,
    initial_token=st_refresh_token,
    new_token=st_refresh_token,
    client_id=st_client_id,
)
def test_prop3_refresh_token_rotation_roundtrip(email, password, initial_token, new_token, client_id):
    """Property 3: After update_refresh_token(email, token), get_by_email returns
    the new token and a valid ISO 8601 last_refresh_at timestamp.

    **Validates: Requirements 2.2**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Setup: insert a combo
        _insert_combo(repo, engine, email, password, initial_token, client_id)

        # Act: rotate refresh token
        repo.update_refresh_token(email, new_token)

        # Assert: read back and verify
        record = repo.get_by_email(email)
        assert record is not None, f"Combo {email} should exist after update"
        assert record["refresh_token"] == new_token, (
            f"Expected refresh_token={new_token}, got {record['refresh_token']}"
        )
        assert record["last_refresh_at"] is not None, "last_refresh_at should be set"
        assert _is_valid_iso8601(record["last_refresh_at"]), (
            f"last_refresh_at should be valid ISO 8601, got: {record['last_refresh_at']}"
        )


# --- Property 4: Combo state mutation preserves invariants ---


@settings(max_examples=100)
@given(
    email=st_email,
    password=st_password,
    token=st_refresh_token,
    client_id=st_client_id,
    initial_used=st_used_for_signup,
    initial_error=st.one_of(st.none(), st_error_string),
)
def test_prop4_mark_success_sets_correct_state(
    email, password, token, client_id, initial_used, initial_error
):
    """Property 4 (mark_success part): mark_success(email) sets used_for_signup=1,
    used_at to a valid timestamp, and last_error=None regardless of initial state.

    **Validates: Requirements 2.3**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Setup: insert combo with arbitrary initial state
        _insert_combo(
            repo, engine, email, password, token, client_id,
            used_for_signup=initial_used,
            last_error=initial_error,
        )

        # Act
        repo.mark_success(email)

        # Assert
        record = repo.get_by_email(email)
        assert record is not None
        assert record["used_for_signup"] == 1, (
            f"Expected used_for_signup=1, got {record['used_for_signup']}"
        )
        assert record["used_at"] is not None, "used_at should be set"
        assert _is_valid_iso8601(record["used_at"]), (
            f"used_at should be valid ISO 8601, got: {record['used_at']}"
        )
        assert record["last_error"] is None, (
            f"last_error should be None after mark_success, got: {record['last_error']}"
        )


@settings(max_examples=100)
@given(
    email=st_email,
    password=st_password,
    token=st_refresh_token,
    client_id=st_client_id,
    initial_used=st_used_for_signup,
    error_msg=st_error_string,
)
def test_prop4_mark_failure_preserves_signup_status(
    email, password, token, client_id, initial_used, error_msg
):
    """Property 4 (mark_failure part): mark_failure(email, error) sets last_error and
    last_failed_at without modifying used_for_signup regardless of its prior value.

    **Validates: Requirements 2.4**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Setup: insert combo with specific used_for_signup
        _insert_combo(
            repo, engine, email, password, token, client_id,
            used_for_signup=initial_used,
        )

        # Act
        repo.mark_failure(email, error_msg)

        # Assert
        record = repo.get_by_email(email)
        assert record is not None
        assert record["used_for_signup"] == initial_used, (
            f"used_for_signup should remain {initial_used}, got {record['used_for_signup']}"
        )
        assert record["last_error"] == error_msg, (
            f"last_error should be '{error_msg}', got '{record['last_error']}'"
        )
        assert record["last_failed_at"] is not None, "last_failed_at should be set"
        assert _is_valid_iso8601(record["last_failed_at"]), (
            f"last_failed_at should be valid ISO 8601, got: {record['last_failed_at']}"
        )


# --- Property 5: Pick available returns correct combo under filtering rules ---


# Strategy for a single combo state
@st.composite
def st_combo_state(draw):
    """Generate a combo with random state for pick_available testing."""
    email = draw(st_email)
    used = draw(st_used_for_signup)
    # Decide error type: None, non-terminal, or terminal
    error_type = draw(st.sampled_from(["none", "non_terminal", "terminal"]))
    if error_type == "none":
        error = None
    elif error_type == "non_terminal":
        error = draw(st_non_terminal_error)
    else:
        error = draw(st_terminal_error)
    return {"email": email, "used_for_signup": used, "last_error": error}


@settings(max_examples=100)
@given(
    combos=st.lists(st_combo_state(), min_size=1, max_size=15, unique_by=lambda c: c["email"]),
)
def test_prop5_pick_available_filtering(combos):
    """Property 5: pick_available() returns the earliest-created combo where
    used_for_signup=0 AND last_error does not contain any terminal error substring,
    or None if no such combo exists.

    **Validates: Requirements 2.5, 2.6**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Insert combos with sequential created_at to ensure deterministic ordering
        for i, combo in enumerate(combos):
            created_at = f"2024-01-01T00:00:{i:02d}"
            _insert_combo(
                repo, engine,
                email=combo["email"],
                used_for_signup=combo["used_for_signup"],
                last_error=combo["last_error"],
                created_at=created_at,
            )

        # Act
        result = repo.pick_available()

        # Compute expected result using Python logic
        def is_available(c: dict) -> bool:
            if c["used_for_signup"] != 0:
                return False
            err = c["last_error"]
            if err is None:
                return True
            return all(term not in err for term in TERMINAL_ERROR_SUBSTRINGS)

        available = [c for c in combos if is_available(c)]

        if not available:
            assert result is None, (
                f"Expected None (no available combos), got: {result}"
            )
        else:
            # The expected pick is the first available in insertion order
            expected_email = available[0]["email"]
            assert result is not None, "Expected a combo but got None"
            assert result["email"] == expected_email, (
                f"Expected email={expected_email}, got {result['email']}"
            )
            # Verify the result itself satisfies the filtering criteria
            assert result["used_for_signup"] == 0
            err = result["last_error"]
            if err is not None:
                assert all(term not in err for term in TERMINAL_ERROR_SUBSTRINGS), (
                    f"Picked combo has terminal error: {err}"
                )


if __name__ == "__main__":
    print("Running Property 3, 4, 5: ComboRepository tests...")
    print("\n  Property 3: Refresh token rotation round-trip...")
    test_prop3_refresh_token_rotation_roundtrip()
    print("  ✓ Property 3 passed (100 examples)")

    print("\n  Property 4a: mark_success sets correct state...")
    test_prop4_mark_success_sets_correct_state()
    print("  ✓ Property 4a passed (100 examples)")

    print("\n  Property 4b: mark_failure preserves signup status...")
    test_prop4_mark_failure_preserves_signup_status()
    print("  ✓ Property 4b passed (100 examples)")

    print("\n  Property 5: pick_available filtering correctness...")
    test_prop5_pick_available_filtering()
    print("  ✓ Property 5 passed (100 examples)")

    print("\n✅ Properties 3, 4, 5: All ComboRepository tests passed!")
