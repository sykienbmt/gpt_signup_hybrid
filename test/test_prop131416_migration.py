"""Property tests cho Migration — Properties 13, 14, 16.

Property 13: Migration preserves data from JSON files to SQLite
  For any valid outlook state JSON file (containing email, refresh_token, client_id)
  or valid session result JSON file, after migration the corresponding database row
  contains field values equivalent to the source JSON file content.

Property 14: Migration skips duplicates without error
  For any combo email or session result that already exists in the database,
  re-running migration on the same source files produces no duplicate rows
  and does not raise an error.

Property 16: Parse resilience — valid items processed despite invalid siblings
  For any mix of valid and invalid input items (JSON files with invalid content),
  processing the batch successfully imports/migrates all valid items and skips
  invalid ones without aborting.

**Validates: Requirements 6.1, 6.2, 6.3, 6.6**
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from db.engine import DatabaseEngine
from db.migrate import MigrationTool, MigrationSummary
from db.repositories import ComboRepository, SessionResultRepository


# --- Strategies ---

# Valid email-like strings (used as filenames for outlook_state)
st_email = st.from_regex(r"[a-z]{3,10}@[a-z]{3,8}\.(com|net|org)", fullmatch=True)

# Valid refresh tokens starting with "M.C"
st_refresh_token = st.from_regex(r"M\.C[A-Za-z0-9_\-]{10,40}", fullmatch=True)

# Client IDs (UUID-like)
st_client_id = st.from_regex(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    fullmatch=True,
)

# Generic password strings
st_password = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=4,
    max_size=30,
)

# used_for_signup values (0 or 1)
st_used_for_signup = st.sampled_from([0, 1])


@st.composite
def st_outlook_state_data(draw):
    """Generate valid outlook state JSON content dict."""
    return {
        "refresh_token": draw(st_refresh_token),
        "client_id": draw(st_client_id),
        "password": draw(st_password),
        "used_for_signup": draw(st.booleans()),
        "last_error": draw(st.one_of(st.none(), st.text(min_size=1, max_size=30))),
        "last_failed_at": draw(st.one_of(st.none(), st.just("2024-06-15T10:30:00+00:00"))),
        "used_at": draw(st.one_of(st.none(), st.just("2024-05-01T08:00:00+00:00"))),
        "last_refresh_at": draw(st.one_of(st.none(), st.just("2024-06-20T12:00:00+00:00"))),
    }


@st.composite
def st_session_result_data(draw):
    """Generate valid session result JSON content dict."""
    email = draw(st_email)
    return {
        "email": email,
        "password": draw(st_password),
        "name": draw(st.text(alphabet=st.characters(whitelist_categories=("L",)), min_size=2, max_size=15)),
        "age": draw(st.integers(min_value=18, max_value=80)),
        "user_id": draw(st.from_regex(r"user-[a-z0-9]{8}", fullmatch=True)),
        "account_id": draw(st.from_regex(r"acct-[a-z0-9]{8}", fullmatch=True)),
        "session_token": draw(st.from_regex(r"sess_[A-Za-z0-9]{20}", fullmatch=True)),
        "access_token": draw(st.from_regex(r"eyJ[A-Za-z0-9]{20}", fullmatch=True)),
        "cookies": draw(st.lists(
            st.fixed_dictionaries({"name": st.text(min_size=1, max_size=8), "value": st.text(min_size=1, max_size=16)}),
            min_size=0, max_size=3,
        )),
        "two_factor": draw(st.one_of(st.none(), st.fixed_dictionaries({
            "secret": st.from_regex(r"[A-Z2-7]{16}", fullmatch=True),
            "type": st.just("totp"),
        }))),
        "phase1_seconds": draw(st.floats(min_value=0.1, max_value=60.0, allow_nan=False)),
        "phase2_seconds": draw(st.floats(min_value=0.1, max_value=60.0, allow_nan=False)),
        "otp_seconds": draw(st.one_of(st.none(), st.floats(min_value=0.1, max_value=30.0, allow_nan=False))),
    }


# --- Helpers ---


def _make_engine_and_tools(tmp_path: Path):
    """Create DatabaseEngine + MigrationTool + repos in a temp directory."""
    db_path = tmp_path / "test_migration.db"
    engine = DatabaseEngine(db_path=db_path)
    combo_repo = ComboRepository(engine)
    session_repo = SessionResultRepository(engine)
    tool = MigrationTool(engine, combo_repo, session_repo)
    return engine, combo_repo, session_repo, tool


def _write_outlook_state_file(state_dir: Path, email: str, content: dict) -> Path:
    """Write a JSON file to state_dir with email as filename."""
    filepath = state_dir / f"{email}.json"
    filepath.write_text(json.dumps(content), encoding="utf-8")
    return filepath


def _write_session_file(sessions_dir: Path, email: str, content: dict, index: int = 0) -> Path:
    """Write a session JSON file with proper naming convention."""
    # Format: signup-YYYYMMDD-HHMMSS-<email>.json
    timestamp = f"20240601-{120000 + index:06d}"
    filename = f"signup-{timestamp}-{email.replace('@', '_at_')}.json"
    filepath = sessions_dir / filename
    filepath.write_text(json.dumps(content), encoding="utf-8")
    return filepath


# --- Property 13: Migration preserves data from JSON files to SQLite ---


@settings(max_examples=100)
@given(
    email=st_email,
    state_data=st_outlook_state_data(),
)
def test_prop13_migration_preserves_outlook_state(email, state_data):
    """Property 13 (outlook_state part): For any valid outlook state JSON file,
    after migration the corresponding database row contains field values equivalent
    to the source JSON file content.

    **Validates: Requirements 6.1**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        engine, combo_repo, session_repo, tool = _make_engine_and_tools(tmp_path)

        # Setup: create outlook_state directory with JSON file
        state_dir = tmp_path / "outlook_state"
        state_dir.mkdir()
        _write_outlook_state_file(state_dir, email, state_data)

        # Act: run migration
        summary = tool.migrate_outlook_state(state_dir)

        # Assert: summary reports 1 inserted
        assert summary.inserted == 1, f"Expected 1 inserted, got {summary.inserted}"
        assert summary.skipped_error == 0, f"Unexpected errors: {summary.errors}"

        # Assert: DB row matches source JSON content
        record = combo_repo.get_by_email(email)
        assert record is not None, f"Record for {email} not found in DB"
        assert record["email"] == email
        assert record["refresh_token"] == state_data["refresh_token"]
        assert record["client_id"] == state_data["client_id"]
        assert record["password"] == state_data["password"]
        expected_used = 1 if state_data["used_for_signup"] else 0
        assert record["used_for_signup"] == expected_used, (
            f"Expected used_for_signup={expected_used}, got {record['used_for_signup']}"
        )
        assert record["last_error"] == state_data["last_error"]
        assert record["last_failed_at"] == state_data["last_failed_at"]
        assert record["used_at"] == state_data["used_at"]
        assert record["last_refresh_at"] == state_data["last_refresh_at"]


@settings(max_examples=100)
@given(
    session_data=st_session_result_data(),
)
def test_prop13_migration_preserves_session_results(session_data):
    """Property 13 (session_results part): For any valid session result JSON file,
    after migration the corresponding database row contains field values equivalent
    to the source JSON file content.

    **Validates: Requirements 6.2**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        engine, combo_repo, session_repo, tool = _make_engine_and_tools(tmp_path)

        # Setup: create sessions directory with JSON file
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        email = session_data["email"]
        _write_session_file(sessions_dir, email, session_data)

        # Act: run migration
        summary = tool.migrate_sessions(sessions_dir)

        # Assert: summary reports 1 inserted
        assert summary.inserted == 1, f"Expected 1 inserted, got {summary.inserted}"
        assert summary.skipped_error == 0, f"Unexpected errors: {summary.errors}"

        # Assert: DB row matches source JSON content
        record = session_repo.get_by_email(email)
        assert record is not None, f"Record for {email} not found in DB"
        assert record["email"] == email
        assert record["password"] == session_data["password"]
        assert record["name"] == session_data["name"]
        assert record["age"] == session_data["age"]
        assert record["user_id"] == session_data["user_id"]
        assert record["account_id"] == session_data["account_id"]
        assert record["session_token"] == session_data["session_token"]
        assert record["access_token"] == session_data["access_token"]

        # cookies stored as JSON string
        if session_data["cookies"] is not None:
            stored_cookies = json.loads(record["cookies"])
            assert stored_cookies == session_data["cookies"], (
                f"Cookies mismatch: {stored_cookies} != {session_data['cookies']}"
            )

        # two_factor stored as JSON string
        if session_data["two_factor"] is not None:
            stored_2fa = json.loads(record["two_factor"])
            assert stored_2fa == session_data["two_factor"], (
                f"two_factor mismatch: {stored_2fa} != {session_data['two_factor']}"
            )

        # Timing fields
        assert record["phase1_seconds"] == session_data["phase1_seconds"]
        assert record["phase2_seconds"] == session_data["phase2_seconds"]
        assert record["otp_seconds"] == session_data["otp_seconds"]


# --- Property 14: Migration skips duplicates without error ---


@settings(max_examples=100)
@given(
    email=st_email,
    state_data=st_outlook_state_data(),
)
def test_prop14_migration_skips_duplicate_outlook_state(email, state_data):
    """Property 14 (outlook_state part): For any combo email that already exists
    in the database, re-running migration on the same source files produces no
    duplicate rows and does not raise an error.

    **Validates: Requirements 6.3**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        engine, combo_repo, session_repo, tool = _make_engine_and_tools(tmp_path)

        # Setup: create outlook_state directory with JSON file
        state_dir = tmp_path / "outlook_state"
        state_dir.mkdir()
        _write_outlook_state_file(state_dir, email, state_data)

        # Act: run migration TWICE
        summary1 = tool.migrate_outlook_state(state_dir)
        summary2 = tool.migrate_outlook_state(state_dir)

        # Assert: first run inserts, second run skips
        assert summary1.inserted == 1
        assert summary2.inserted == 0
        assert summary2.skipped_duplicate == 1

        # Assert: no duplicate rows — only 1 row in DB
        all_combos = combo_repo.list_all()
        matching = [c for c in all_combos if c["email"] == email]
        assert len(matching) == 1, f"Expected 1 row for {email}, got {len(matching)}"


@settings(max_examples=100)
@given(
    session_data=st_session_result_data(),
)
def test_prop14_migration_skips_duplicate_session_results(session_data):
    """Property 14 (session_results part): For any session result that already
    exists in the database (duplicate email + created_at), re-running migration
    produces no duplicate rows and does not raise an error.

    **Validates: Requirements 6.3**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        engine, combo_repo, session_repo, tool = _make_engine_and_tools(tmp_path)

        # Setup: create sessions directory with JSON file
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        email = session_data["email"]
        _write_session_file(sessions_dir, email, session_data)

        # Act: run migration TWICE
        summary1 = tool.migrate_sessions(sessions_dir)
        summary2 = tool.migrate_sessions(sessions_dir)

        # Assert: first run inserts, second run skips
        assert summary1.inserted == 1
        assert summary2.inserted == 0
        assert summary2.skipped_duplicate == 1

        # Assert: no duplicate rows — only 1 row for this email
        all_sessions = session_repo.list_all()
        matching = [s for s in all_sessions if s["email"] == email]
        assert len(matching) == 1, f"Expected 1 row for {email}, got {len(matching)}"


# --- Property 16: Parse resilience — valid items processed despite invalid siblings ---


@st.composite
def st_mixed_outlook_files(draw):
    """Generate a mix of valid and invalid outlook state file specs.

    Returns: list of (email, content_or_None) tuples.
    content_or_None = dict for valid, None for 'write invalid content'.
    """
    # At least 1 valid file
    valid_count = draw(st.integers(min_value=1, max_value=5))
    invalid_count = draw(st.integers(min_value=1, max_value=5))

    files = []
    emails_used = set()

    for _ in range(valid_count):
        email = draw(st_email.filter(lambda e: e not in emails_used))
        emails_used.add(email)
        content = draw(st_outlook_state_data())
        files.append((email, content, "valid"))

    for i in range(invalid_count):
        email = draw(st_email.filter(lambda e: e not in emails_used))
        emails_used.add(email)
        # Decide type of invalidity
        invalid_type = draw(st.sampled_from(["bad_json", "not_dict", "missing_fields"]))
        files.append((email, None, invalid_type))

    # Shuffle to interleave valid/invalid
    shuffled = draw(st.permutations(files))
    return list(shuffled)


@settings(max_examples=100)
@given(
    file_specs=st_mixed_outlook_files(),
)
def test_prop16_parse_resilience_outlook_state(file_specs):
    """Property 16 (outlook_state): For any mix of valid and invalid JSON files,
    processing the batch successfully imports all valid items and skips invalid
    ones without aborting.

    **Validates: Requirements 6.6**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        engine, combo_repo, session_repo, tool = _make_engine_and_tools(tmp_path)

        # Setup: create outlook_state directory with mixed files
        state_dir = tmp_path / "outlook_state"
        state_dir.mkdir()

        valid_emails = []
        for email, content, file_type in file_specs:
            filepath = state_dir / f"{email}.json"
            if file_type == "valid":
                filepath.write_text(json.dumps(content), encoding="utf-8")
                valid_emails.append(email)
            elif file_type == "bad_json":
                filepath.write_text("{{invalid json content!!!", encoding="utf-8")
            elif file_type == "not_dict":
                filepath.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            elif file_type == "missing_fields":
                # Dict without required refresh_token and client_id
                filepath.write_text(json.dumps({"some_key": "value"}), encoding="utf-8")

        # Act: run migration — should NOT raise
        summary = tool.migrate_outlook_state(state_dir)

        # Assert: all valid files were migrated
        assert summary.inserted == len(valid_emails), (
            f"Expected {len(valid_emails)} inserted, got {summary.inserted}. "
            f"Errors: {summary.errors}"
        )

        # Assert: invalid files were skipped
        expected_errors = len(file_specs) - len(valid_emails)
        assert summary.skipped_error == expected_errors, (
            f"Expected {expected_errors} skipped_error, got {summary.skipped_error}"
        )

        # Assert: each valid email exists in DB
        for email in valid_emails:
            record = combo_repo.get_by_email(email)
            assert record is not None, f"Valid email {email} not found in DB after migration"


@st.composite
def st_mixed_session_files(draw):
    """Generate a mix of valid and invalid session result file specs."""
    valid_count = draw(st.integers(min_value=1, max_value=5))
    invalid_count = draw(st.integers(min_value=1, max_value=5))

    files = []
    emails_used = set()

    for _ in range(valid_count):
        data = draw(st_session_result_data().filter(
            lambda d: d["email"] not in emails_used
        ))
        emails_used.add(data["email"])
        files.append((data["email"], data, "valid"))

    for _ in range(invalid_count):
        email = draw(st_email.filter(lambda e: e not in emails_used))
        emails_used.add(email)
        invalid_type = draw(st.sampled_from(["bad_json", "not_dict", "missing_email"]))
        files.append((email, None, invalid_type))

    shuffled = draw(st.permutations(files))
    return list(shuffled)


@settings(max_examples=100)
@given(
    file_specs=st_mixed_session_files(),
)
def test_prop16_parse_resilience_session_results(file_specs):
    """Property 16 (session_results): For any mix of valid and invalid session JSON files,
    processing the batch successfully imports all valid items and skips invalid
    ones without aborting.

    **Validates: Requirements 6.6**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        engine, combo_repo, session_repo, tool = _make_engine_and_tools(tmp_path)

        # Setup: create sessions directory with mixed files
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        valid_emails = []
        for i, (email, content, file_type) in enumerate(file_specs):
            timestamp = f"20240601-{120000 + i:06d}"
            filename = f"signup-{timestamp}-{email.replace('@', '_at_')}.json"
            filepath = sessions_dir / filename

            if file_type == "valid":
                filepath.write_text(json.dumps(content), encoding="utf-8")
                valid_emails.append(email)
            elif file_type == "bad_json":
                filepath.write_text("not valid json {{{", encoding="utf-8")
            elif file_type == "not_dict":
                filepath.write_text(json.dumps("just a string"), encoding="utf-8")
            elif file_type == "missing_email":
                # Dict without required 'email' field
                filepath.write_text(json.dumps({"password": "abc", "name": "Test"}), encoding="utf-8")

        # Act: run migration — should NOT raise
        summary = tool.migrate_sessions(sessions_dir)

        # Assert: all valid files were migrated
        assert summary.inserted == len(valid_emails), (
            f"Expected {len(valid_emails)} inserted, got {summary.inserted}. "
            f"Errors: {summary.errors}"
        )

        # Assert: invalid files were skipped
        expected_errors = len(file_specs) - len(valid_emails)
        assert summary.skipped_error == expected_errors, (
            f"Expected {expected_errors} skipped_error, got {summary.skipped_error}"
        )

        # Assert: each valid email exists in DB
        for email in valid_emails:
            record = session_repo.get_by_email(email)
            assert record is not None, f"Valid email {email} not found in DB after migration"


if __name__ == "__main__":
    print("Running Property 13, 14, 16: Migration tests...")

    print("\n  Property 13a: Migration preserves outlook state data...")
    test_prop13_migration_preserves_outlook_state()
    print("  ✓ Property 13a passed (100 examples)")

    print("\n  Property 13b: Migration preserves session results data...")
    test_prop13_migration_preserves_session_results()
    print("  ✓ Property 13b passed (100 examples)")

    print("\n  Property 14a: Migration skips duplicate outlook state...")
    test_prop14_migration_skips_duplicate_outlook_state()
    print("  ✓ Property 14a passed (100 examples)")

    print("\n  Property 14b: Migration skips duplicate session results...")
    test_prop14_migration_skips_duplicate_session_results()
    print("  ✓ Property 14b passed (100 examples)")

    print("\n  Property 16a: Parse resilience — outlook state...")
    test_prop16_parse_resilience_outlook_state()
    print("  ✓ Property 16a passed (100 examples)")

    print("\n  Property 16b: Parse resilience — session results...")
    test_prop16_parse_resilience_session_results()
    print("  ✓ Property 16b passed (100 examples)")

    print("\n✅ Properties 13, 14, 16: All Migration tests passed!")
