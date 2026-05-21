"""Property tests cho SessionResultRepository — Properties 10, 11, 12.

Property 10: Session result create round-trip
  For any valid session result data dict, after create(data), reading via
  get_by_email(email) returns a record with all fields matching the input.

Property 11: Session result export_json deserialization
  For any session result with cookies stored as JSON string (list) and two_factor
  stored as JSON string (dict), export_json(email) returns a dictionary where
  cookies is a Python list and two_factor is a Python dict.

Property 12: 2FA update targets only the most recent record
  For any email with multiple session result rows (different created_at timestamps),
  update_2fa(email, mfa_data) modifies only the row with the latest created_at,
  leaving all older rows' two_factor column unchanged.

**Validates: Requirements 4.2, 4.3, 4.5**
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from db.engine import DatabaseEngine
from db.repositories import SessionResultRepository


# --- Strategies ---

# Valid email-like strings
st_email = st.from_regex(r"[a-z]{3,10}@[a-z]{3,8}\.(com|net|org)", fullmatch=True)

# Generic password strings
st_password = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=4,
    max_size=30,
)

# Name strings
st_name = st.text(
    alphabet=st.characters(whitelist_categories=("L",)),
    min_size=2,
    max_size=20,
)

# Age (reasonable range)
st_age = st.integers(min_value=13, max_value=99)

# Token-like strings
st_token = st.from_regex(r"[A-Za-z0-9_\-]{10,40}", fullmatch=True)

# UUID-like IDs
st_uuid = st.from_regex(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    fullmatch=True,
)

# Cookie: list of simple dicts
st_cookie_entry = st.fixed_dictionaries({
    "name": st.from_regex(r"[a-z_]{3,10}", fullmatch=True),
    "value": st.from_regex(r"[A-Za-z0-9]{5,20}", fullmatch=True),
    "domain": st.from_regex(r"\.[a-z]{3,8}\.(com|net)", fullmatch=True),
})

st_cookies = st.lists(st_cookie_entry, min_size=1, max_size=5)

# Two-factor: dict with typical 2FA fields
st_two_factor = st.fixed_dictionaries({
    "secret": st.from_regex(r"[A-Z2-7]{16,32}", fullmatch=True),
    "backup_codes": st.lists(
        st.from_regex(r"[0-9]{6}", fullmatch=True),
        min_size=1,
        max_size=5,
    ),
})

# Phase timings (seconds)
st_phase_seconds = st.floats(min_value=0.1, max_value=300.0, allow_nan=False, allow_infinity=False)


# --- Composite strategy for session result data ---

@st.composite
def st_session_result(draw):
    """Generate a valid session result data dict."""
    return {
        "email": draw(st_email),
        "password": draw(st_password),
        "name": draw(st_name),
        "age": draw(st_age),
        "user_id": draw(st_uuid),
        "account_id": draw(st_uuid),
        "session_token": draw(st_token),
        "access_token": draw(st_token),
        "cookies": draw(st_cookies),
        "two_factor": draw(st_two_factor),
        "phase1_seconds": draw(st_phase_seconds),
        "phase2_seconds": draw(st_phase_seconds),
        "otp_seconds": draw(st_phase_seconds),
    }


# --- Helpers ---


def _make_engine_and_repo(tmp_path: Path) -> tuple[DatabaseEngine, SessionResultRepository]:
    """Create a DatabaseEngine + SessionResultRepository in a temp directory."""
    db_path = tmp_path / "test_prop101112.db"
    engine = DatabaseEngine(db_path=db_path)
    repo = SessionResultRepository(engine)
    return engine, repo


# --- Property 10: Session result create round-trip ---


@settings(max_examples=100)
@given(data=st_session_result())
def test_prop10_session_result_create_roundtrip(data):
    """Property 10: After create(data), get_by_email(email) returns a record
    with all scalar fields matching the input. cookies and two_factor are stored
    as JSON strings (raw read).

    **Validates: Requirements 4.2**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Act: create session result
        row_id = repo.create(data)
        assert row_id is not None and row_id > 0

        # Assert: read back via get_by_email
        record = repo.get_by_email(data["email"])
        assert record is not None, f"Session result for {data['email']} should exist"

        # Verify scalar fields match
        assert record["email"] == data["email"]
        assert record["password"] == data["password"]
        assert record["name"] == data["name"]
        assert record["age"] == data["age"]
        assert record["user_id"] == data["user_id"]
        assert record["account_id"] == data["account_id"]
        assert record["session_token"] == data["session_token"]
        assert record["access_token"] == data["access_token"]
        assert record["phase1_seconds"] == data["phase1_seconds"]
        assert record["phase2_seconds"] == data["phase2_seconds"]
        assert record["otp_seconds"] == data["otp_seconds"]

        # cookies and two_factor are stored as JSON strings in raw read
        assert json.loads(record["cookies"]) == data["cookies"]
        assert json.loads(record["two_factor"]) == data["two_factor"]


# --- Property 11: Session result export_json deserialization ---


@settings(max_examples=100)
@given(data=st_session_result())
def test_prop11_export_json_deserialization(data):
    """Property 11: export_json(email) returns a dictionary where cookies is a Python
    list and two_factor is a Python dict, with values matching the original input.

    **Validates: Requirements 4.5**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Setup: create session result
        repo.create(data)

        # Act: export_json
        exported = repo.export_json(data["email"])
        assert exported is not None, f"export_json for {data['email']} should not be None"

        # Assert: cookies is a Python list
        assert isinstance(exported["cookies"], list), (
            f"cookies should be list, got {type(exported['cookies'])}"
        )
        assert exported["cookies"] == data["cookies"]

        # Assert: two_factor is a Python dict
        assert isinstance(exported["two_factor"], dict), (
            f"two_factor should be dict, got {type(exported['two_factor'])}"
        )
        assert exported["two_factor"] == data["two_factor"]


# --- Property 12: 2FA update targets only the most recent record ---


@st.composite
def st_timestamps(draw, count):
    """Generate `count` distinct ISO 8601 timestamps in ascending order."""
    # Use fixed date with increasing seconds for determinism
    base_seconds = draw(st.lists(
        st.integers(min_value=0, max_value=59),
        min_size=count,
        max_size=count,
        unique=True,
    ))
    base_seconds.sort()
    return [f"2024-01-01T12:{s:02d}:00" for s in base_seconds]


@settings(max_examples=100)
@given(
    email=st_email,
    password=st_password,
    num_rows=st.integers(min_value=2, max_value=5),
    mfa_data=st_two_factor,
    data=st.data(),
)
def test_prop12_2fa_update_targets_most_recent(email, password, num_rows, mfa_data, data):
    """Property 12: For any email with multiple session result rows, update_2fa(email, mfa_data)
    modifies only the row with the latest created_at, leaving all older rows' two_factor unchanged.

    **Validates: Requirements 4.3**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Generate distinct timestamps
        timestamps = data.draw(st_timestamps(num_rows))

        # Insert multiple rows with different created_at directly via SQL
        initial_two_factors = []
        for i, ts in enumerate(timestamps):
            initial_2fa = {"initial": f"row_{i}"}
            initial_two_factors.append(initial_2fa)
            with engine.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO session_results
                        (email, password, name, age, user_id, account_id,
                         session_token, access_token, cookies, two_factor,
                         phase1_seconds, phase2_seconds, otp_seconds, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email, password, f"name_{i}", 20 + i,
                        f"uid_{i}", f"aid_{i}",
                        f"st_{i}", f"at_{i}",
                        json.dumps([{"c": i}]),
                        json.dumps(initial_2fa),
                        1.0, 2.0, 3.0,
                        ts,
                    ),
                )

        # Act: update_2fa — should only affect the most recent row
        repo.update_2fa(email, mfa_data)

        # Assert: query all rows for this email ordered by created_at ASC
        conn = engine.raw_connection()
        rows = conn.execute(
            "SELECT two_factor, created_at FROM session_results WHERE email = ? ORDER BY created_at ASC",
            (email,),
        ).fetchall()

        assert len(rows) == num_rows, f"Expected {num_rows} rows, got {len(rows)}"

        # All rows except the last should have their original two_factor
        for i, row in enumerate(rows[:-1]):
            stored_2fa = json.loads(row["two_factor"])
            assert stored_2fa == initial_two_factors[i], (
                f"Row {i} (older) should be unchanged. "
                f"Expected {initial_two_factors[i]}, got {stored_2fa}"
            )

        # The last row (most recent) should have the new mfa_data
        latest_2fa = json.loads(rows[-1]["two_factor"])
        assert latest_2fa == mfa_data, (
            f"Most recent row should have updated 2FA. "
            f"Expected {mfa_data}, got {latest_2fa}"
        )


if __name__ == "__main__":
    print("Running Property 10, 11, 12: SessionResultRepository tests...")

    print("\n  Property 10: Session result create round-trip...")
    test_prop10_session_result_create_roundtrip()
    print("  ✓ Property 10 passed (100 examples)")

    print("\n  Property 11: Session result export_json deserialization...")
    test_prop11_export_json_deserialization()
    print("  ✓ Property 11 passed (100 examples)")

    print("\n  Property 12: 2FA update targets only the most recent record...")
    test_prop12_2fa_update_targets_most_recent()
    print("  ✓ Property 12 passed (100 examples)")

    print("\n✅ Properties 10, 11, 12: All SessionResultRepository tests passed!")
