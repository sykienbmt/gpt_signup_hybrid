"""Repro kịch bản: stop_all → clear_finished → add_jobs → kỳ vọng worker pick lên.

Mock _run_job_with_timeout = sleep dài để có gì cancel.
Quan trọng: dùng JobManager thật từ web.manager để test đúng pattern thực tế.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gpt_signup_hybrid.web.manager import JobManager  # noqa: E402


async def fake_run(self, job):  # noqa: ANN001
    """Stub thay cho _run_job_with_timeout — sleep 60s để kịp cancel."""
    job.status = "running"
    job.started_at = time.time()
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.finished_at = time.time()
        raise
    job.status = "success"
    job.finished_at = time.time()


async def main() -> int:
    JobManager._run_job_with_timeout = fake_run  # type: ignore[assignment]

    mgr = JobManager(max_concurrent=2)
    # Disable stagger để test chạy nhanh
    mgr._stagger_min_seconds = 0.0
    mgr._stagger_max_seconds = 0.0

    # Round 1: add 3 jobs (combo format outlook hợp lệ)
    valid = "{e}|pwd|M.C123_FAKE|11111111-2222-3333-4444-555555555555"
    jobs1 = mgr.add_jobs(
        [valid.format(e=f"a{i}@hotmail.com") for i in range(3)],
        mail_mode="outlook",
    )
    print(f"[round1] added {len(jobs1)} jobs")

    # Cho worker pick xong
    await asyncio.sleep(0.2)
    running1 = sum(1 for j in mgr.jobs.values() if j.status == "running")
    queued1 = sum(1 for j in mgr.jobs.values() if j.status == "queued")
    print(f"[round1] running={running1} queued={queued1}")
    assert running1 == 2, f"expected 2 running, got {running1}"

    # Stop all
    stopped = mgr.stop_all()
    print(f"[stop_all] cancelled {stopped}")

    # Yield event loop để cancel propagate
    await asyncio.sleep(0.1)

    # Worker phải còn sống
    alive_workers = sum(1 for w in mgr._workers if not w.done())
    print(f"[after stop] alive workers={alive_workers}/{len(mgr._workers)}")
    assert alive_workers == 2, f"workers chết sau stop_all: {alive_workers}"

    # Clear done
    removed = mgr.clear_finished()
    print(f"[clear_finished] removed {removed}")

    # Round 2: add 3 jobs mới — phải tự pick lên
    jobs2 = mgr.add_jobs(
        [valid.format(e=f"b{i}@hotmail.com") for i in range(3)],
        mail_mode="outlook",
    )
    print(f"[round2] added {len(jobs2)} jobs")

    await asyncio.sleep(0.3)
    running2 = sum(1 for j in mgr.jobs.values() if j.status == "running")
    queued2 = sum(1 for j in mgr.jobs.values() if j.status == "queued")
    print(f"[round2] running={running2} queued={queued2}")
    assert running2 == 2, f"BUG VẪN CÒN: round2 running={running2}, expected 2"

    # Cleanup
    mgr.stop_all()
    for w in mgr._workers:
        w.cancel()
    await asyncio.sleep(0.05)

    print("\n[PASS] stop_all → clear_finished → add_jobs hoạt động bình thường")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
