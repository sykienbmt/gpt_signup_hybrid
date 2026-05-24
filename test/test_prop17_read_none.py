"""Property 17: Read non-existent identifiers return None.

For any random string identifier that does not exist as a primary key in the database,
get_by_email(), get_by_id() return None without raising an exception.

**Validates: Requirements 7.7**
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, settings
from hypothesis import strategies as st

from db.engine import DatabaseEngine
from db.repositories import ComboRepository, JobRepository, SessionResultRepository


# --- Strategies ---

# Random strings guaranteed NOT to exist in an empty database
st_random_email = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=80,
).map(lambda s: f"{s}@nonexistent.test")

st_random_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=100,
)


def _make_repos(tmp_path: Path):
    """Create DatabaseEngine + all 3 repositories on a fresh SQLite file."""
    db_path = tmp_path / "test_prop17.db"
    engine = DatabaseEngine(db_path=db_path)
    combo_repo = ComboRepository(engine)
    job_repo = JobRepository(engine)
    session_repo = SessionResultRepository(engine)
    return combo_repo, job_repo, session_repo


# --- Property Tests ---


@settings(max_examples=100)
@given(email=st_random_email)
def test_combo_get_by_email_returns_none(email):
    """Property 17 — ComboRepository.get_by_email(random_email) returns None
    for non-existent email without raising an exception.

    **Validates: Requirements 7.7**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        combo_repo, _, _ = _make_repos(Path(tmpdir))
        result = combo_repo.get_by_email(email)
        assert result is None, f"Expected None for non-existent email '{email}', got {result}"


@settings(max_examples=100)
@given(job_id=st_random_id)
def test_job_get_by_id_returns_none(job_id):
    """Property 17 — JobRepository.get_by_id(random_job_id) returns None
    for non-existent job_id without raising an exception.

    **Validates: Requirements 7.7**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, job_repo, _ = _make_repos(Path(tmpdir))
        result = job_repo.get_by_id(job_id)
        assert result is None, f"Expected None for non-existent job_id '{job_id}', got {result}"


@settings(max_examples=100)
@given(email=st_random_email)
def test_session_get_by_email_returns_none(email):
    """Property 17 — SessionResultRepository.get_by_email(random_email) returns None
    for non-existent email without raising an exception.

    **Validates: Requirements 7.7**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, _, session_repo = _make_repos(Path(tmpdir))
        result = session_repo.get_by_email(email)
        assert result is None, f"Expected None for non-existent email '{email}', got {result}"


@settings(max_examples=100)
@given(email=st_random_email)
def test_session_export_json_returns_none(email):
    """Property 17 — SessionResultRepository.export_json(random_email) returns None
    for non-existent email without raising an exception.

    **Validates: Requirements 7.7**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, _, session_repo = _make_repos(Path(tmpdir))
        result = session_repo.export_json(email)
        assert result is None, f"Expected None for non-existent email '{email}', got {result}"


if __name__ == "__main__":
    print("Running Property 17: Read non-existent identifiers return None...")
    print("  Testing ComboRepository.get_by_email...")
    test_combo_get_by_email_returns_none()
    print("  ✓ ComboRepository.get_by_email passed (100 examples)")
    print("  Testing JobRepository.get_by_id...")
    test_job_get_by_id_returns_none()
    print("  ✓ JobRepository.get_by_id passed (100 examples)")
    print("  Testing SessionResultRepository.get_by_email...")
    test_session_get_by_email_returns_none()
    print("  ✓ SessionResultRepository.get_by_email passed (100 examples)")
    print("  Testing SessionResultRepository.export_json...")
    test_session_export_json_returns_none()
    print("  ✓ SessionResultRepository.export_json passed (100 examples)")
    print("\n✅ Property 17: All read non-existent tests passed!")
