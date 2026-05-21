"""Property tests cho JobRepository — Properties 6, 7, 8, 9.

Property 6: Job status transition updates correct fields
  When status transitions to "running", started_at SHALL be set to a valid unix timestamp
  AND finished_at SHALL remain NULL. When status transitions to any terminal state
  ("success", "error", "cancelled"), finished_at SHALL be set to a valid unix timestamp.

Property 7: Job log append round-trip
  For any valid job_id and any non-empty log line string, after append_log(job_id, line),
  the log line is retrievable in the job's log list in insertion order.

Property 8: Reset running to queued preserves other statuses
  For any set of jobs with mixed statuses, after recover_interrupted(), all previously-running
  jobs have status "queued" with started_at=None, while jobs with status "queued", "success",
  "error", "cancelled" remain unchanged (except queued jobs which stay queued).

Property 9: Delete finished removes only success/error jobs
  For any set of jobs with mixed statuses, after delete_finished(), no jobs with status
  "success" or "error" exist, and all jobs with status "queued", "running", or "cancelled"
  remain intact with their log entries.

**Validates: Requirements 3.3, 3.4, 3.5, 3.6, 3.7, 10.2, 10.6**
"""

import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from db.engine import DatabaseEngine
from db.repositories import JobRepository


# --- Strategies ---

st_email = st.from_regex(r"[a-z]{3,10}@[a-z]{3,8}\.(com|net|org)", fullmatch=True)

st_combo = st.from_regex(
    r"[a-z]{3,8}@[a-z]{3,6}\.com\|pass[0-9]{3}\|M\.C[a-z0-9]{10}\|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    fullmatch=True,
)

st_job_type = st.sampled_from(["signup", "session", "link"])

st_mail_mode = st.sampled_from(["outlook", "worker", "gmail_advanced"])

st_terminal_status = st.sampled_from(["success", "error", "cancelled"])

st_any_status = st.sampled_from(["queued", "running", "success", "error", "cancelled"])

st_log_line = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=200,
)


# --- Helpers ---


def _make_engine_and_repo(tmp_path: Path) -> tuple[DatabaseEngine, JobRepository]:
    """Create a DatabaseEngine + JobRepository in a temp directory."""
    db_path = tmp_path / "test_prop6789.db"
    engine = DatabaseEngine(db_path=db_path)
    repo = JobRepository(engine)
    return engine, repo


def _create_job(
    repo: JobRepository,
    email: str | None = None,
    combo: str | None = None,
    status: str = "queued",
    mail_mode: str = "outlook",
    job_type: str = "signup",
    created_at: float | None = None,
) -> str:
    """Helper to create a job and return its ID."""
    job_id = str(uuid.uuid4())
    job_data = {
        "id": job_id,
        "email": email or f"test-{job_id[:8]}@example.com",
        "combo": combo or f"test@ex.com|pass|M.Ctoken0000|00000000-0000-0000-0000-000000000000",
        "mail_mode": mail_mode,
        "status": status,
        "created_at": created_at or time.time(),
        "job_type": job_type,
    }
    repo.create(job_data)
    return job_id


# --- Property 6: Job status transition updates correct fields ---


@settings(max_examples=100)
@given(
    email=st_email,
    combo=st_combo,
    mail_mode=st_mail_mode,
    job_type=st_job_type,
)
def test_prop6_transition_to_running_sets_started_at(email, combo, mail_mode, job_type):
    """Property 6 (running): When status transitions to "running", started_at SHALL be set
    to a valid unix timestamp AND finished_at SHALL remain NULL.

    **Validates: Requirements 3.3**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, repo = _make_engine_and_repo(Path(tmpdir))

        # Create job with "queued" status
        job_id = _create_job(repo, email=email, combo=combo, mail_mode=mail_mode, job_type=job_type)

        before = time.time()
        repo.update_status(job_id, "running")
        after = time.time()

        job = repo.get_by_id(job_id)
        assert job is not None
        assert job["status"] == "running"
        assert job["started_at"] is not None, "started_at should be set when transitioning to running"
        assert isinstance(job["started_at"], (int, float)), (
            f"started_at should be numeric unix timestamp, got {type(job['started_at'])}"
        )
        assert before <= job["started_at"] <= after, (
            f"started_at={job['started_at']} should be between {before} and {after}"
        )
        assert job["finished_at"] is None, (
            f"finished_at should remain None when transitioning to running, got {job['finished_at']}"
        )


@settings(max_examples=100)
@given(
    email=st_email,
    combo=st_combo,
    terminal_status=st_terminal_status,
    mail_mode=st_mail_mode,
    job_type=st_job_type,
)
def test_prop6_transition_to_terminal_sets_finished_at(email, combo, terminal_status, mail_mode, job_type):
    """Property 6 (terminal): When status transitions to any terminal state ("success",
    "error", "cancelled"), finished_at SHALL be set to a valid unix timestamp.

    **Validates: Requirements 3.4**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, repo = _make_engine_and_repo(Path(tmpdir))

        # Create job and transition to running first
        job_id = _create_job(repo, email=email, combo=combo, mail_mode=mail_mode, job_type=job_type)
        repo.update_status(job_id, "running")

        before = time.time()
        repo.update_status(job_id, terminal_status)
        after = time.time()

        job = repo.get_by_id(job_id)
        assert job is not None
        assert job["status"] == terminal_status
        assert job["finished_at"] is not None, (
            f"finished_at should be set when transitioning to {terminal_status}"
        )
        assert isinstance(job["finished_at"], (int, float)), (
            f"finished_at should be numeric unix timestamp, got {type(job['finished_at'])}"
        )
        assert before <= job["finished_at"] <= after, (
            f"finished_at={job['finished_at']} should be between {before} and {after}"
        )


# --- Property 7: Job log append round-trip ---


@settings(max_examples=100)
@given(
    email=st_email,
    combo=st_combo,
    log_lines=st.lists(st_log_line, min_size=1, max_size=10),
)
def test_prop7_log_append_roundtrip(email, combo, log_lines):
    """Property 7: For any valid job_id and any non-empty log line string,
    after append_log(job_id, line), the log line is retrievable in the job's
    log list in insertion order.

    **Validates: Requirements 3.5**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        _, repo = _make_engine_and_repo(Path(tmpdir))

        job_id = _create_job(repo, email=email, combo=combo)

        # Append all log lines
        for line in log_lines:
            repo.append_log(job_id, line)

        # Retrieve logs
        logs = repo.get_logs(job_id)

        # Verify count matches
        assert len(logs) == len(log_lines), (
            f"Expected {len(log_lines)} logs, got {len(logs)}"
        )

        # Verify insertion order and content
        for i, (expected_line, log_entry) in enumerate(zip(log_lines, logs)):
            assert log_entry["line"] == expected_line, (
                f"Log entry {i}: expected '{expected_line}', got '{log_entry['line']}'"
            )
            assert log_entry["job_id"] == job_id
            assert log_entry["created_at"] is not None, (
                f"Log entry {i}: created_at should be set"
            )


# --- Property 8: Reset running to queued preserves other statuses ---


@st.composite
def st_job_with_status(draw):
    """Generate a job spec with a random status."""
    status = draw(st_any_status)
    email = draw(st_email)
    return {"email": email, "status": status}


@settings(max_examples=100)
@given(
    jobs_spec=st.lists(
        st_job_with_status(),
        min_size=1,
        max_size=15,
        unique_by=lambda j: j["email"],
    ),
)
def test_prop8_recover_interrupted_resets_running_preserves_others(jobs_spec):
    """Property 8: For any set of jobs with mixed statuses, after recover_interrupted(),
    all previously-running jobs have status "queued" with started_at=None, while jobs
    with status "queued", "success", "error", "cancelled" remain unchanged.

    **Validates: Requirements 3.6, 10.2**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Create jobs with specific statuses
        job_ids = {}
        for i, spec in enumerate(jobs_spec):
            job_id = str(uuid.uuid4())
            created_at = 1700000000.0 + i  # Deterministic ordering
            job_data = {
                "id": job_id,
                "email": spec["email"],
                "combo": f"{spec['email']}|pass|M.Ctoken|00000000-0000-0000-0000-000000000000",
                "mail_mode": "outlook",
                "status": "queued",  # Always create as queued first
                "created_at": created_at,
                "job_type": "signup",
            }
            repo.create(job_data)

            # Transition to target status
            target_status = spec["status"]
            if target_status == "running":
                repo.update_status(job_id, "running")
            elif target_status in ("success", "error", "cancelled"):
                repo.update_status(job_id, "running")
                repo.update_status(job_id, target_status)
            # If "queued", nothing to do

            job_ids[job_id] = spec["status"]

        # Snapshot state before recover for non-running/non-queued jobs
        snapshots_before = {}
        for jid, status in job_ids.items():
            if status not in ("running", "queued"):
                snapshots_before[jid] = repo.get_by_id(jid)

        # Act
        repo.recover_interrupted()

        # Assert
        for jid, original_status in job_ids.items():
            job = repo.get_by_id(jid)
            assert job is not None, f"Job {jid} should still exist"

            if original_status == "running":
                # Running jobs should be reset to queued with started_at cleared
                assert job["status"] == "queued", (
                    f"Previously-running job should be 'queued', got '{job['status']}'"
                )
                assert job["started_at"] is None, (
                    f"Previously-running job should have started_at=None, got {job['started_at']}"
                )
            elif original_status == "queued":
                # Queued jobs remain queued
                assert job["status"] == "queued", (
                    f"Queued job should remain 'queued', got '{job['status']}'"
                )
            else:
                # success, error, cancelled — completely unchanged
                before = snapshots_before[jid]
                assert job["status"] == original_status, (
                    f"Job with status '{original_status}' should remain unchanged, got '{job['status']}'"
                )
                assert job["started_at"] == before["started_at"], (
                    f"started_at should be unchanged for {original_status} job"
                )
                assert job["finished_at"] == before["finished_at"], (
                    f"finished_at should be unchanged for {original_status} job"
                )


# --- Property 9: Delete finished removes only success/error jobs ---


@settings(max_examples=100)
@given(
    jobs_spec=st.lists(
        st_job_with_status(),
        min_size=1,
        max_size=15,
        unique_by=lambda j: j["email"],
    ),
    log_lines_per_job=st.lists(st_log_line, min_size=0, max_size=3),
)
def test_prop9_delete_finished_removes_only_success_error(jobs_spec, log_lines_per_job):
    """Property 9: For any set of jobs with mixed statuses, after delete_finished(),
    no jobs with status "success" or "error" exist, and all jobs with status "queued",
    "running", or "cancelled" remain intact with their log entries.

    **Validates: Requirements 3.7, 10.6**
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        engine, repo = _make_engine_and_repo(Path(tmpdir))

        # Create jobs with specific statuses and add logs
        job_ids = {}
        for i, spec in enumerate(jobs_spec):
            job_id = str(uuid.uuid4())
            created_at = 1700000000.0 + i
            job_data = {
                "id": job_id,
                "email": spec["email"],
                "combo": f"{spec['email']}|pass|M.Ctoken|00000000-0000-0000-0000-000000000000",
                "mail_mode": "outlook",
                "status": "queued",
                "created_at": created_at,
                "job_type": "signup",
            }
            repo.create(job_data)

            # Transition to target status
            target_status = spec["status"]
            if target_status == "running":
                repo.update_status(job_id, "running")
            elif target_status in ("success", "error", "cancelled"):
                repo.update_status(job_id, "running")
                repo.update_status(job_id, target_status)

            # Append some logs to each job
            for line in log_lines_per_job:
                repo.append_log(job_id, line)

            job_ids[job_id] = target_status

        # Snapshot logs for non-finished jobs
        preserved_jobs = {
            jid: status for jid, status in job_ids.items()
            if status not in ("success", "error")
        }
        logs_before = {jid: repo.get_logs(jid) for jid in preserved_jobs}

        # Act
        deleted_count = repo.delete_finished()

        # Count expected deletions
        expected_deleted = sum(
            1 for s in job_ids.values() if s in ("success", "error")
        )
        assert deleted_count == expected_deleted, (
            f"Expected {expected_deleted} deletions, got {deleted_count}"
        )

        # Assert: no success/error jobs exist
        all_jobs = repo.list_all()
        for job in all_jobs:
            assert job["status"] not in ("success", "error"), (
                f"Found job with status '{job['status']}' after delete_finished"
            )

        # Assert: queued, running, cancelled jobs still intact with logs
        for jid, status in preserved_jobs.items():
            job = repo.get_by_id(jid)
            assert job is not None, (
                f"Job {jid} with status '{status}' should still exist after delete_finished"
            )
            assert job["status"] == status

            # Verify log entries are intact
            logs_after = repo.get_logs(jid)
            expected_logs = logs_before[jid]
            assert len(logs_after) == len(expected_logs), (
                f"Job {jid}: expected {len(expected_logs)} logs, got {len(logs_after)}"
            )
            for log_before, log_after in zip(expected_logs, logs_after):
                assert log_before["line"] == log_after["line"], (
                    f"Log content mismatch for job {jid}"
                )

        # Assert: finished jobs' logs are also deleted (cascade)
        finished_jobs = {
            jid for jid, status in job_ids.items()
            if status in ("success", "error")
        }
        for jid in finished_jobs:
            logs = repo.get_logs(jid)
            assert len(logs) == 0, (
                f"Logs for deleted job {jid} should be cascade-deleted, found {len(logs)}"
            )


if __name__ == "__main__":
    print("Running Property 6, 7, 8, 9: JobRepository tests...")

    print("\n  Property 6a: Transition to running sets started_at...")
    test_prop6_transition_to_running_sets_started_at()
    print("  ✓ Property 6a passed (100 examples)")

    print("\n  Property 6b: Transition to terminal sets finished_at...")
    test_prop6_transition_to_terminal_sets_finished_at()
    print("  ✓ Property 6b passed (100 examples)")

    print("\n  Property 7: Log append round-trip...")
    test_prop7_log_append_roundtrip()
    print("  ✓ Property 7 passed (100 examples)")

    print("\n  Property 8: Recover interrupted resets running, preserves others...")
    test_prop8_recover_interrupted_resets_running_preserves_others()
    print("  ✓ Property 8 passed (100 examples)")

    print("\n  Property 9: Delete finished removes only success/error jobs...")
    test_prop9_delete_finished_removes_only_success_error()
    print("  ✓ Property 9 passed (100 examples)")

    print("\n✅ Properties 6, 7, 8, 9: All JobRepository tests passed!")
