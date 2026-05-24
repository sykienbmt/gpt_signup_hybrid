"""Fix race condition trong worker loop của SessionJobManager + LinkJobManager.

Bug: stop_all() cancel `self._tasks[job.id]` mà thực ra giá trị đó là task của
worker (do _run_job dùng current_task()). Cancel = kill worker. Cancel là async
nên `worker.done()` chưa True ngay → `_ensure_workers` không prune kịp → job
mới enqueue nhưng kẹt vĩnh viễn.

Fix: tách inner task → cancel chỉ giết job, worker còn sống.

Idempotent: skip nếu đã có "create_task(self._run_job(job))".
"""
from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "web" / "manager.py"

OLD_LOOP = """    async def _worker_loop(self) -> None:
        try:
            while True:
                job_id = await self._job_queue.get()
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                # Stagger start nếu max_concurrent > 1
                if self._max > 1:
                    async with self._stagger_lock:
                        now = time.monotonic()
                        wait_min = self._last_start_ts + self._stagger_min_seconds - now
                        if wait_min > 0:
                            jitter = random.uniform(
                                self._stagger_min_seconds, self._stagger_max_seconds,
                            )
                            wait = max(wait_min, jitter)
                            self._job_log(job, f"[stagger] đợi {wait:.1f}s trước khi start")
                            await asyncio.sleep(wait)
                        self._last_start_ts = time.monotonic()
                await self._run_job(job)
        except asyncio.CancelledError:
            pass
"""

NEW_LOOP = """    async def _worker_loop(self) -> None:
        # Job execution wrap trong inner task để stop_all cancel job mà không
        # kill luôn worker. Nếu worker bị kill, các job add lại sau đó sẽ
        # kẹt trong queue vì không ai pick lên.
        try:
            while True:
                job_id = await self._job_queue.get()
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                # Stagger start nếu max_concurrent > 1
                if self._max > 1:
                    async with self._stagger_lock:
                        now = time.monotonic()
                        wait_min = self._last_start_ts + self._stagger_min_seconds - now
                        if wait_min > 0:
                            jitter = random.uniform(
                                self._stagger_min_seconds, self._stagger_max_seconds,
                            )
                            wait = max(wait_min, jitter)
                            self._job_log(job, f"[stagger] đợi {wait:.1f}s trước khi start")
                            await asyncio.sleep(wait)
                        self._last_start_ts = time.monotonic()
                inner = asyncio.create_task(self._run_job(job))
                self._tasks[job_id] = inner
                try:
                    await inner
                except asyncio.CancelledError:
                    if inner.cancelled():
                        # job bị cancel — worker tiếp tục vòng kế
                        continue
                    raise
                finally:
                    self._tasks.pop(job_id, None)
        except asyncio.CancelledError:
            pass
"""


def main() -> int:
    src = PATH.read_text(encoding="utf-8")
    count = src.count(OLD_LOOP)
    if count == 0:
        print("[skip] không tìm thấy block cũ — đã fix hoặc text khác")
        return 0
    new_src = src.replace(OLD_LOOP, NEW_LOOP)
    PATH.write_text(new_src, encoding="utf-8")
    print(f"[ok] thay thế {count} _worker_loop block")
    return count


if __name__ == "__main__":
    main()
