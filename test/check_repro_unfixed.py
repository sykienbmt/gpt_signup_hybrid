"""Repro bug ở pattern CŨ (không fix) để chứng minh test có khả năng phát hiện bug.

Tạo JobManager phẳng tại chỗ với worker_loop kiểu cũ — `await self._run_job_with_timeout(job)`.
Mong đợi: round 2 không có job nào running.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from gpt_signup_hybrid.web.manager import JobManager  # noqa: E402


async def fake_run(self, job):  # noqa: ANN001
    self._tasks[job.id] = asyncio.current_task()
    job.status = "running"
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError:
        job.status = "cancelled"
        raise


async def buggy_loop(self):
    """Pattern cũ — await trực tiếp, không tách inner task."""
    try:
        while True:
            job_id = await self._job_queue.get()
            job = self.jobs.get(job_id)
            if job is None or job.status != "queued":
                continue
            await self._run_job_with_timeout(job)
    except asyncio.CancelledError:
        pass


async def main() -> int:
    JobManager._run_job_with_timeout = fake_run  # type: ignore[assignment]
    JobManager._worker_loop = buggy_loop  # type: ignore[assignment]
    # Tắt prune để repro chính xác bug gốc
    original_ensure = JobManager._ensure_workers

    def no_prune(self):
        if not self._worker_started:
            self._worker_started = True
        while len(self._workers) < self._max:
            w = asyncio.create_task(self._worker_loop())
            self._workers.append(w)
        while len(self._workers) > self._max:
            w = self._workers.pop()
            w.cancel()

    JobManager._ensure_workers = no_prune  # type: ignore[assignment]

    mgr = JobManager(max_concurrent=2)
    mgr._stagger_min_seconds = 0.0
    mgr._stagger_max_seconds = 0.0

    valid = "{e}|pwd|M.C123_FAKE|11111111-2222-3333-4444-555555555555"
    mgr.add_jobs([valid.format(e=f"a{i}@hotmail.com") for i in range(3)], mail_mode="outlook")
    await asyncio.sleep(0.2)

    mgr.stop_all()
    await asyncio.sleep(0.1)

    alive = sum(1 for w in mgr._workers if not w.done())
    print(f"[unfixed] sau stop_all alive workers={alive}/2 (kỳ vọng <2 = bug)")

    mgr.clear_finished()
    mgr.add_jobs([valid.format(e=f"b{i}@hotmail.com") for i in range(3)], mail_mode="outlook")
    await asyncio.sleep(0.3)

    running = sum(1 for j in mgr.jobs.values() if j.status == "running")
    queued = sum(1 for j in mgr.jobs.values() if j.status == "queued")
    print(f"[unfixed] round2 running={running} queued={queued}")

    JobManager._ensure_workers = original_ensure  # restore
    if running == 0 and queued > 0:
        print("[OK] test repro được bug (running=0, queued>0)")
        return 0
    print("[ERR] test KHÔNG repro được bug — test bị sai")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
