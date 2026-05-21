"""Repro bug: stop_all → clear_finished → add_jobs lại không chạy.

Pattern: monkey-patch _run_job để giả lập signup (sleep + status update),
sau đó:
  1. add 2 jobs → đợi ít chạy
  2. stop_all → cancel
  3. clear_finished → clear
  4. add 2 jobs mới
  5. assert: 2 job mới chuyển từ queued → running trong vòng 5s

Nếu bug → job stuck "queued" forever, test fail.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))


async def main() -> int:
    from gpt_signup_hybrid.web.manager import JobManager, Job

    mgr = JobManager(max_concurrent=2)
    mgr.set_headless(True)
    mgr.set_job_timeout(60)

    # Monkey-patch: thay _run_job bằng fake để khỏi launch browser thật.
    async def fake_run_job(job: Job) -> None:
        if job.id not in mgr.jobs:
            return
        job.status = "running"
        mgr._broadcast_job(job)
        try:
            await asyncio.sleep(30)  # long sleep — đủ để bị cancel
            job.status = "success"
        except asyncio.CancelledError:
            job.status = "cancelled"
            mgr._broadcast_job(job)
            raise
        finally:
            mgr._broadcast_job(job)

    mgr._run_job = fake_run_job  # type: ignore[assignment]

    def snapshot(label: str) -> None:
        for j in mgr.jobs.values():
            print(f"  [{label}] {j.email}: status={j.status}")

    print("== round 1: add 2 jobs ==")
    combos1 = [
        "user1@icloud.com",
        "user2@icloud.com",
    ]
    jobs1 = mgr.add_jobs(combos1, mail_mode="worker")
    print(f"added {len(jobs1)} jobs")

    # Chờ workers pick up
    await asyncio.sleep(0.5)
    snapshot("round1")

    print("== stop_all ==")
    stopped = mgr.stop_all()
    print(f"stopped {stopped} jobs")
    await asyncio.sleep(0.5)
    snapshot("after-stop")

    print("== clear_finished ==")
    removed = mgr.clear_finished()
    print(f"removed {removed} jobs")
    snapshot("after-clear")

    print("== round 2: add 2 new jobs ==")
    combos2 = [
        "user3@icloud.com",
        "user4@icloud.com",
    ]
    jobs2 = mgr.add_jobs(combos2, mail_mode="worker")
    print(f"added {len(jobs2)} jobs")

    # Đợi tối đa 8s cho worker pick up + start
    deadline = asyncio.get_event_loop().time() + 8
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        statuses = {j.id: j.status for j in jobs2}
        if all(s == "running" for s in statuses.values()):
            break

    snapshot("round2-final")
    statuses = [j.status for j in jobs2]
    if not all(s == "running" for s in statuses):
        print(f"❌ FAIL round 2: statuses={statuses}")
        return 1
    print("✅ round 2 OK")

    # ── Bonus: stop_all giữa stagger sleep → bail nhanh < 1s ──
    print("== bonus: stop interrupts stagger sleep ==")
    mgr.stop_all()
    await asyncio.sleep(0.3)
    mgr.clear_finished()
    # Force stagger debt: chạy 1 job xong rồi stop để giả lập state
    combos3 = [
        "user5@icloud.com",
        "user6@icloud.com",
        "user7@icloud.com",
    ]
    jobs3 = mgr.add_jobs(combos3, mail_mode="worker")
    await asyncio.sleep(0.3)
    # Lúc này: 2 jobs chạy (user5 running, user6 stagger sleeping ~5s),
    # user7 trong queue chưa pick.
    t0 = asyncio.get_event_loop().time()
    mgr.stop_all()
    # Đợi tất cả worker rời stagger (status flip cancelled → bail trong 0.25s)
    deadline = t0 + 2.0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.1)
        if all(j.status == "cancelled" for j in jobs3):
            break
    t1 = asyncio.get_event_loop().time()
    elapsed = t1 - t0
    if all(j.status == "cancelled" for j in jobs3) and elapsed < 1.5:
        print(f"✅ stop interrupts stagger trong {elapsed:.2f}s")
    else:
        statuses3 = [j.status for j in jobs3]
        print(f"❌ FAIL bonus: elapsed={elapsed:.2f}s statuses={statuses3}")
        return 1

    print("\n✅ ALL PASS")
    return 0
    print(f"❌ FAIL: jobs mới statuses={statuses} (expect all running)")
    print(f"  workers count: {len(mgr._workers)}")
    for i, w in enumerate(mgr._workers):
        st = "done" if w.done() else "alive"
        exc = None
        if w.done() and not w.cancelled():
            try:
                exc = w.exception()
            except Exception:
                exc = "??"
        print(f"    w{i}: {st} exc={exc}")
    print(f"  queue size: {mgr._job_queue.qsize()}")
    print(f"  tasks: {list(mgr._tasks.keys())}")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
