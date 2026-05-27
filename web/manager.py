"""Job manager: queue + concurrency control + broadcast events."""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..config import load_settings, runtime_session_dir
from ..mail_providers import OutlookCombo, OutlookComboError
from ..mfa_phase import MfaError, enable_2fa
from ..models import SignupRequest, SignupResult
from ..signup import run_signup
from .mail_modes import MailModeParseError, get_spec


# ── Load .env riêng của gpt_signup_hybrid ─────────────────────────────
def _load_hybrid_env() -> dict[str, str]:
    """Đọc gpt_signup_hybrid/.env (cùng thư mục package root)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return values


_HYBRID_ENV = _load_hybrid_env()


def _env(key: str, default: str) -> str:
    """Ưu tiên: os.environ > .env file > default."""
    return os.environ.get(key) or _HYBRID_ENV.get(key) or default


# Parsed constants
_MAX_CONCURRENT = min(max(int(_env("HYBRID_MAX_CONCURRENT", "2")), 1), 10)
_DEFAULT_JOB_TIMEOUT = min(max(float(_env("HYBRID_JOB_TIMEOUT", "240")), 30), 600)
_DEFAULT_PROXY = _env("HYBRID_OUTLOOK_PROXY", "") or None


def _mask_proxy(proxy: str | None) -> str:
    """Mask user:pass trong proxy URL cho log. None/empty → 'direct'."""
    if not proxy:
        return "direct"
    if "@" in proxy:
        scheme_split = proxy.split("://", 1)
        if len(scheme_split) == 2:
            scheme, rest = scheme_split
            _, _, host = rest.partition("@")
            return f"{scheme}://***@{host}"
    return proxy


JobStatus = str  # queued | running | success | error | cancelled


@dataclass
class Job:
    id: str
    email: str
    combo: str  # raw combo line
    mail_mode: str = "outlook"
    status: JobStatus = "queued"
    log_lines: list[str] = field(default_factory=list)
    error: str | None = None
    # Output sau khi success
    password: str | None = None
    secret: str | None = None
    first_code: str | None = None
    user_id: str | None = None
    session_path: str | None = None
    # Post-reg optional results
    session_data: dict[str, Any] | None = None  # post-reg session JSON
    payment_link: str | None = None  # post-reg payment URL
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "mail_mode": self.mail_mode,
            "status": self.status,
            "error": self.error,
            "user_id": self.user_id,
            "has_password": bool(self.password),
            "has_secret": bool(self.secret),
            "has_first_code": bool(self.first_code),
            "has_session_path": bool(self.session_path),
            "has_session": self.session_data is not None,
            "session_data": self.session_data,
            "session_access_token": (self.session_data or {}).get("accessToken") if self.session_data else None,
            "payment_link": self.payment_link,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": (
                (self.finished_at or time.time()) - self.started_at if self.started_at else None
            ),
            "log_count": len(self.log_lines),
        }

    def to_dict_secrets(self) -> dict[str, Any]:
        """Chỉ password + secret + first_code + session_path. Dùng cho /api/jobs/secrets."""
        return {
            "password": self.password,
            "secret": self.secret,
            "first_code": self.first_code,
            "session_path": self.session_path,
        }

    def to_dict_full(self) -> dict[str, Any]:
        """Detail endpoint — bao gồm cả secrets + session_data + log_lines."""
        d = self.to_dict()
        d.update(self.to_dict_secrets())
        d["log_lines"] = list(self.log_lines)
        d["session_data"] = self.session_data
        return d


_PLUS_TAGS = [
    "mail", "inbox", "acc", "dev", "web", "hub", "box", "app",
    "gpt", "ai", "bot", "user", "me", "hi", "ok", "go", "id",
    "x1", "x2", "x3", "a1", "a2", "b1", "c1", "info", "reg",
]


def _generate_gmail_aliases(local: str, domain: str, count: int) -> list[str]:
    """Tạo list alias Gmail random mix giữa +tag và dot trick."""
    results: list[str] = []
    used: set[str] = set()

    def _dot_variants(name: str) -> list[str]:
        if len(name) <= 2:
            return []
        variants = []
        for i in range(1, len(name)):
            v = name[:i] + "." + name[i:]
            if ".." not in v:
                variants.append(v)
        for i in range(1, len(name)):
            for j in range(i + 2, len(name)):
                v = name[:i] + "." + name[i:j] + "." + name[j:]
                if ".." not in v:
                    variants.append(v)
        return variants

    dot_pool = _dot_variants(local)
    random.shuffle(dot_pool)
    tag_pool = _PLUS_TAGS[:]
    random.shuffle(tag_pool)

    dot_iter = iter(dot_pool)
    tag_iter = iter(tag_pool)

    attempts = 0
    while len(results) < count and attempts < count * 6:
        attempts += 1
        strategy = random.choice(["plus", "dot", "plus_dot"])
        email = None

        if strategy == "plus":
            tag = next(tag_iter, None)
            if tag is None:
                tag = f"r{random.randint(10, 99)}"
            email = f"{local}+{tag}@{domain}"
        elif strategy == "dot":
            dv = next(dot_iter, None)
            if dv:
                email = f"{dv}@{domain}"
        else:
            dv = next(dot_iter, None) or local
            tag = next(tag_iter, None) or f"r{random.randint(10, 99)}"
            email = f"{dv}+{tag}@{domain}"

        if email and email not in used:
            used.add(email)
            results.append(email)

    return results


class JobManager:
    """Quản lý jobs + concurrency thông qua worker pool pattern.

    Thay vì spawn task riêng cho mỗi job (race condition khi thay đổi
    max_concurrent giữa chừng), dùng N worker coroutine lấy job từ queue.
    Khi thay đổi max_concurrent → scale worker pool lên/xuống.
    """

    def __init__(self, *, max_concurrent: int = 1):
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []  # giữ thứ tự tạo
        self._max = max_concurrent
        self._headless = True
        self._debug = False
        self._job_timeout = _DEFAULT_JOB_TIMEOUT
        self._proxy: str | None = _DEFAULT_PROXY
        self._tasks: dict[str, asyncio.Task] = {}  # job_id → running task (for cancel)
        self._subscribers: set[asyncio.Queue] = set()
        # Worker pool
        self._job_queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._worker_started = False
        # Post-reg optional toggles
        self._post_reg_get_session: bool = False
        self._post_reg_get_link: bool = False
        # Stagger: tránh nhiều browser khởi tạo cùng 1 lúc → random 5-10s giữa các start
        self._stagger_lock = asyncio.Lock()
        self._last_start_ts: float = 0.0
        self._stagger_min_seconds = 5.0
        self._stagger_max_seconds = 10.0

    def _ensure_workers(self) -> None:
        """Đảm bảo đủ worker theo max_concurrent. Gọi mỗi khi thêm job hoặc đổi config."""
        if not self._worker_started:
            self._worker_started = True
        # Prune worker đã chết (bị cancel khi stop_all hoặc exit do exception).
        # Nếu không prune, len(self._workers) vẫn = _max → không spawn worker mới
        # → job enqueue sau stop_all sẽ nằm yên trong queue mãi.
        self._workers = [w for w in self._workers if not w.done()]
        # Scale lên nếu cần thêm worker
        while len(self._workers) < self._max:
            w = asyncio.create_task(self._worker_loop())
            self._workers.append(w)
        while len(self._workers) > self._max:
            w = self._workers.pop()
            w.cancel()

    async def _worker_loop(self) -> None:
        """Worker lấy job từ queue, chạy tuần tự từng cái một.

        Stagger: trước mỗi start, đợi tới ít nhất `_stagger_min_seconds` sau lần
        start gần nhất + random jitter — tránh spawn nhiều browser cùng tick.

        Job execution wrap trong inner task để `stop_all` cancel job mà không
        kill luôn worker. Nếu worker bị kill, các job add lại sau đó sẽ kẹt
        trong queue vì không ai pick lên.
        """
        try:
            while True:
                job_id = await self._job_queue.get()
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue  # job đã bị remove/cancel trước khi tới lượt
                # Stagger start nếu max_concurrent > 1 (single mode không cần).
                # Reserve slot trong lock (fast), sleep ngoài lock + poll job
                # status mỗi 0.25s — bail nhanh nếu job bị cancel/remove giữa
                # chừng (đảm bảo stop_all + add lại không kẹt vì stagger debt).
                if self._max > 1:
                    async with self._stagger_lock:
                        now = time.monotonic()
                        wait_min = self._last_start_ts + self._stagger_min_seconds - now
                        if wait_min > 0:
                            jitter = random.uniform(
                                self._stagger_min_seconds, self._stagger_max_seconds,
                            )
                            wait = max(wait_min, jitter)
                            self._last_start_ts = now + wait
                        else:
                            wait = 0.0
                            self._last_start_ts = now
                    if wait > 0:
                        self._job_log(job, f"[stagger] đợi {wait:.1f}s trước khi start")
                        deadline = time.monotonic() + wait
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            await asyncio.sleep(min(0.25, remaining))
                            cur = self.jobs.get(job_id)
                            if cur is None or cur.status != "queued":
                                break
                    cur = self.jobs.get(job_id)
                    if cur is None or cur.status != "queued":
                        continue
                inner = asyncio.create_task(self._run_job_with_timeout(job))
                self._tasks[job_id] = inner
                try:
                    await inner
                except asyncio.CancelledError:
                    # Inner job bị cancel (stop_all/remove_job) — worker đi tiếp.
                    # Nếu chính worker bị cancel → re-raise.
                    if inner.cancelled():
                        continue
                    raise
                finally:
                    self._tasks.pop(job_id, None)
        except asyncio.CancelledError:
            pass

    @property
    def max_concurrent(self) -> int:
        return self._max

    def set_max_concurrent(self, n: int) -> None:
        if n < 1 or n > 10:
            raise ValueError("max_concurrent phải trong [1, 10]")
        self._max = n
        self._ensure_workers()

    @property
    def headless(self) -> bool:
        return self._headless

    def set_headless(self, value: bool) -> None:
        self._headless = bool(value)

    @property
    def debug(self) -> bool:
        return self._debug

    def set_debug(self, value: bool) -> None:
        self._debug = bool(value)

    @property
    def job_timeout(self) -> float:
        return self._job_timeout

    def set_job_timeout(self, seconds: float) -> None:
        if seconds < 30 or seconds > 600:
            raise ValueError("job_timeout phải trong [30, 600]")
        self._job_timeout = float(seconds)

    @property
    def proxy(self) -> str | None:
        return self._proxy

    def set_proxy(self, value: str | None) -> None:
        """Set proxy chung cho tất cả jobs sau này. None/'' = direct."""
        if value is None:
            self._proxy = None
            return
        v = str(value).strip()
        self._proxy = v or None

    @property
    def post_reg_get_session(self) -> bool:
        return self._post_reg_get_session

    def set_post_reg_get_session(self, value: bool) -> None:
        self._post_reg_get_session = bool(value)

    @property
    def post_reg_get_link(self) -> bool:
        return self._post_reg_get_link

    def set_post_reg_get_link(self, value: bool) -> None:
        self._post_reg_get_link = bool(value)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # drop event nếu queue đầy (subscriber chậm)
                pass

    def _job_log(self, job: Job, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        job.log_lines.append(line)
        if len(job.log_lines) > 500:
            job.log_lines = job.log_lines[-500:]
        self._broadcast({"type": "log", "job_id": job.id, "line": line})

    def _broadcast_job(self, job: Job) -> None:
        self._broadcast({"type": "job", "job": job.to_dict()})

    def _safe_proxy_log(self) -> str:
        return _mask_proxy(self._proxy)

    def add_jobs(self, combos: list[str], *, default_password: str | None = None, mail_mode: str = "outlook", worker_config: dict[str, str] | None = None, gmail_alias_expand: bool = False, gmail_alias_count: int = 1) -> list[Job]:
        """Thêm jobs từ list combo/email strings. Skip đã có trong list (dedup theo email)."""
        spec = get_spec(mail_mode)  # KeyError nếu mode lạ — server chặn trước

        # Expand aliases với round-robin interleaving để tránh 2 alias cùng mail chạy song song
        # Hỗ trợ: gmail_advanced (sep=|) và smsbower (sep=----)
        if gmail_alias_expand and mail_mode in ("gmail_advanced", "smsbower"):
            alias_count = max(1, min(int(gmail_alias_count), 10))
            # Chọn separator phù hợp với mode
            _sep = "----" if mail_mode == "smsbower" else "|"
            groups: list[list[str]] = []
            for raw in combos:
                group = [raw]
                line = raw.strip()
                # gmail_advanced cho phép URL-only (bắt đầu http) — skip expand cho dạng này
                if not (not line or line.startswith("#") or line.startswith(("http://", "https://"))):
                    parts = line.split(_sep, 1)
                    if len(parts) == 2:
                        base_email = parts[0].strip()
                        api_url = parts[1].strip()
                        if "@" in base_email:
                            local_part = base_email.split("+")[0].split("@")[0]
                            domain = base_email.split("@", 1)[1]
                            aliases = _generate_gmail_aliases(local_part, domain, alias_count)
                            group.extend(f"{a}{_sep}{api_url}" for a in aliases)
                groups.append(group)
            # Round-robin: [orig1,orig2,...] → [alias1_1,alias2_1,...] → [alias1_2,alias2_2,...]
            max_len = max((len(g) for g in groups), default=0)
            combos = [g[i] for i in range(max_len) for g in groups if i < len(g)]

        existing_emails = {j.email.lower() for j in self.jobs.values() if j.status != "cancelled"}
        out: list[Job] = []
        for raw in combos:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                parsed = spec.parse_line(line)
            except (OutlookComboError, MailModeParseError) as exc:
                jid = uuid.uuid4().hex[:12]
                job = Job(
                    id=jid,
                    email="<invalid>",
                    combo=line[:80],
                    mail_mode=spec.id,
                    status="error",
                    error=f"parse fail: {exc}",
                    finished_at=time.time(),
                )
                self.jobs[jid] = job
                self.order.append(jid)
                self._broadcast_job(job)
                out.append(job)
                continue

            if parsed.email.lower() in existing_emails:
                continue  # dedup
            existing_emails.add(parsed.email.lower())

            jid = uuid.uuid4().hex[:12]
            job = Job(id=jid, email=parsed.email, combo=line, mail_mode=spec.id)
            job._default_password = default_password  # type: ignore[attr-defined]
            job._worker_config = worker_config  # type: ignore[attr-defined]
            self.jobs[jid] = job
            self.order.append(jid)
            self._broadcast_job(job)
            out.append(job)
        # Enqueue jobs → workers sẽ pick lên theo thứ tự
        self._ensure_workers()
        for j in out:
            if j.status == "queued":
                self._job_queue.put_nowait(j.id)
        return out

    def remove_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        # Cancel task nếu đang chạy
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            job.status = "cancelled"
            job.finished_at = time.time()
        # Cleanup references
        self.jobs.pop(job_id, None)
        if job_id in self.order:
            self.order.remove(job_id)
        self._tasks.pop(job_id, None)
        self._broadcast({"type": "remove", "job_id": job_id})
        return True

    def stop_all(self) -> int:
        """Cancel tất cả jobs đang running/queued. Return số job đã cancel."""
        # Drain queue trước — tránh worker pick job mới
        while not self._job_queue.empty():
            try:
                self._job_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        count = 0
        for job_id, job in list(self.jobs.items()):
            if job.status in ("running", "queued"):
                task = self._tasks.get(job_id)
                if task and not task.done():
                    task.cancel()
                job.status = "cancelled"
                job.finished_at = time.time()
                self._broadcast_job(job)
                count += 1
        # Reset stagger debt — batch jobs mới sau stop_all không phải đợi
        # khoảng cách stagger tính từ batch cũ.
        self._last_start_ts = 0.0
        return count

    def clear_finished(self) -> int:
        """Xoá tất cả jobs đã xong (success/error) khỏi memory. Giữ running/queued/cancelled."""
        removed = 0
        for jid in list(self.order):
            job = self.jobs.get(jid)
            if job and job.status in ("success", "error"):
                self.jobs.pop(jid, None)
                self.order.remove(jid)
                self._tasks.pop(jid, None)
                removed += 1
        if removed:
            self._broadcast({"type": "clear_finished", "removed": removed})
        return removed

    def clear_all(self) -> int:
        """Cancel running/queued rồi xoá toàn bộ jobs khỏi memory."""
        self.stop_all()
        removed = len(self.jobs)
        self.jobs.clear()
        self.order.clear()
        self._tasks.clear()
        if removed:
            self._broadcast({"type": "clear_finished", "removed": removed})
        return removed

    def retry_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        # Cancel task hiện tại nếu running
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()

        # Nếu signup đã thành công (có session_path) nhưng 2FA fail,
        # retry chỉ Phase 2 — không signup lại (tránh trigger login flow + duplicate).
        retry_2fa_only = bool(job.session_path and not job.secret)

        # Reset state — giữ password đã gen (không gen lại khi retry)
        job.status = "queued"
        job.error = None
        job.secret = None
        job.first_code = None
        # KHÔNG reset job.password — dùng lại password đã gen ban đầu
        if not retry_2fa_only:
            job.user_id = None
            job.session_path = None
        job.started_at = None
        job.finished_at = None
        retry_label = "retry-2fa" if retry_2fa_only else "retry"
        job.log_lines.append(f"[{datetime.now():%H:%M:%S}] -- {retry_label} --")
        self._broadcast_job(job)
        self._broadcast({"type": "log", "job_id": job_id, "line": job.log_lines[-1]})
        # Mark để worker biết cần chạy 2fa-only
        job._retry_2fa_only = retry_2fa_only  # type: ignore[attr-defined]
        self._ensure_workers()
        self._job_queue.put_nowait(job_id)
        return True

    async def fetch_session_for_job(self, job_id: str) -> bool:
        """Fetch /api/auth/session cho 1 job dùng cookies đã lưu ở session_path."""
        job = self.jobs.get(job_id)
        if not job or job.status != "success" or not job.session_path:
            return False
        # Đã có rồi → skip
        if job.session_data and job.session_data.get("accessToken"):
            return True
        try:
            sdata = json.loads(Path(job.session_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        cookies = sdata.get("cookies", [])
        if not cookies:
            return False
        from ..session_phase import SessionError, fetch_session_via_http
        try:
            self._job_log(job, "[fetch-session] calling /api/auth/session...")
            session_data = await fetch_session_via_http(
                cookies=cookies,
                proxy=self._proxy,
                timeout=30.0,
            )
            job.session_data = session_data
            self._job_log(job, "[fetch-session] OK")
            self._broadcast_job(job)
            return True
        except Exception as exc:
            self._job_log(job, f"[fetch-session] failed: {exc}")
            return False

    def list_jobs(self) -> list[dict[str, Any]]:
        return [self.jobs[jid].to_dict() for jid in self.order if jid in self.jobs]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        return job.to_dict_full() if job else None

    def get_log(self, job_id: str) -> list[str]:
        job = self.jobs.get(job_id)
        return list(job.log_lines) if job else []

    def get_secrets_map(self) -> dict[str, dict[str, Any]]:
        """Trả map job_id → {password, secret, first_code, session_path} cho mọi job.

        Dùng cho UI render Success pane một lần thay vì fetch detail từng job.
        Endpoint gọi method này phải có auth gate.
        """
        return {
            jid: self.jobs[jid].to_dict_secrets()
            for jid in self.order
            if jid in self.jobs
        }

    def _spawn(self, job: Job) -> None:
        """Legacy: chỉ dùng cho internal retry trực tiếp (không qua queue)."""
        task = asyncio.create_task(self._run_job_with_timeout(job))
        self._tasks[job.id] = task

    async def _run_job_with_timeout(self, job: Job) -> None:
        """Wrap _run_job với timeout. Kill nếu vượt job_timeout."""
        try:
            # Kiểm tra nếu là retry-2fa-only
            retry_2fa_only = getattr(job, '_retry_2fa_only', False)
            if hasattr(job, '_retry_2fa_only'):
                del job._retry_2fa_only  # type: ignore[attr-defined]

            # Debug mode + headed → không timeout (chờ user cancel)
            timeout = None if (self._debug and not self._headless) else self._job_timeout

            if retry_2fa_only:
                await asyncio.wait_for(self._run_2fa_only_inner(job), timeout=timeout)
            else:
                await asyncio.wait_for(self._run_job(job), timeout=timeout)
        except asyncio.TimeoutError:
            job.status = "error"
            job.error = f"timeout {self._job_timeout:.0f}s exceeded — killed"
            job.finished_at = time.time()
            self._job_log(job, f"[fatal] job timeout {self._job_timeout:.0f}s — killed")
            self._broadcast_job(job)
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.finished_at = time.time()
            self._broadcast_job(job)
            raise
        finally:
            self._tasks.pop(job.id, None)

    def _spawn_2fa_only(self, job: Job) -> None:
        """Legacy — không dùng trực tiếp nữa, retry qua queue."""
        job._retry_2fa_only = True  # type: ignore[attr-defined]
        self._ensure_workers()
        self._job_queue.put_nowait(job.id)

    async def _run_2fa_only_inner(self, job: Job) -> None:
        """Chạy Phase 2 (enable 2FA) khi signup đã có session_path."""
        try:
            if job.id not in self.jobs:
                return
            job.status = "running"
            job.started_at = time.time()
            self._broadcast_job(job)

            def log(msg: str) -> None:
                self._job_log(job, msg)

            # Đọc access_token từ session.json đã save
            if not job.session_path or not Path(job.session_path).exists():
                job.status = "error"
                job.error = "session file mất, không retry 2FA được"
                job.finished_at = time.time()
                self._broadcast_job(job)
                return

            try:
                sdata = json.loads(Path(job.session_path).read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                job.status = "error"
                job.error = f"session file corrupt: {exc}"
                job.finished_at = time.time()
                self._broadcast_job(job)
                return

            access_token = sdata.get("access_token")
            if not access_token:
                job.status = "error"
                job.error = "session file thiếu access_token"
                job.finished_at = time.time()
                self._broadcast_job(job)
                return

            log("[2fa] retry-only: dùng session đã có")
            try:
                mfa_result = await enable_2fa(
                    access_token=access_token,
                    cookies=sdata.get("cookies"),
                    proxy=self._proxy,
                    log=log,
                )
            except MfaError as exc:
                job.status = "error"
                job.error = f"2fa: {exc}"
                job.finished_at = time.time()
                self._broadcast_job(job)
                return

            two_fa_path = Path(job.session_path).with_suffix(".2fa.json")
            two_fa_path.write_text(
                json.dumps({
                    "email": job.email,
                    "user_id": job.user_id,
                    "two_factor": mfa_result,
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            job.secret = mfa_result.get("secret")
            job.first_code = mfa_result.get("first_code")
            job.status = "success"
            job.finished_at = time.time()
            self._broadcast_job(job)

        except asyncio.CancelledError:
            job.status = "cancelled"
            job.finished_at = time.time()
            self._broadcast_job(job)
            raise
        except Exception as exc:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
            self._job_log(job, f"[fatal] {job.error}")
            self._broadcast_job(job)

    async def _run_job(self, job: Job) -> None:
        try:
            if job.id not in self.jobs:
                return  # đã bị remove trước khi tới lượt
            job.status = "running"
            job.started_at = time.time()
            self._broadcast_job(job)

            def log(msg: str) -> None:
                self._job_log(job, msg)

            log(f"[mode] {job.mail_mode}")
            if self._proxy:
                log(f"[proxy] using {self._safe_proxy_log()}")

            # Build SignupRequest qua registry spec
            spec = get_spec(job.mail_mode)
            parsed = spec.parse_line(job.combo)
            request = spec.build_request(
                parsed,
                worker_config=getattr(job, '_worker_config', None),
                password=job.password or getattr(job, '_default_password', None),
                headless=self._headless,
                keep_browser_open=self._debug and not self._headless,
                proxy=self._proxy,
            )
            # Extra fields (e.g. smsbower_max_all_codes cho recheck jobs)
            extra = getattr(job, '_extra_req_fields', None)
            if extra:
                request = request.model_copy(update=extra)
            result: SignupResult = await run_signup(request, log=log)

            # Update job email nếu đã resolve từ API (URL-only gmail_advanced)
            if result.email and result.email != job.email:
                job.email = result.email
                self._broadcast_job(job)

            # Auto-retry 1 lần nếu browser crash
            if not result.success and result.error and result.error.startswith("browser_crash:"):
                log(f"[retry] browser crashed, retrying once...")
                await asyncio.sleep(3.0)
                result = await run_signup(request, log=log)
                if result.email and result.email != job.email:
                    job.email = result.email
                    self._broadcast_job(job)

            if not result.success:
                job.status = "error"
                job.error = result.error or "signup failed"
                job.finished_at = time.time()
                self._broadcast_job(job)
                return

            # Lưu session JSON
            settings = load_settings()
            session_path = (
                runtime_session_dir(settings)
                / f"signup-{datetime.now():%Y%m%d-%H%M%S}-{job.email.replace('@', '_at_')}.json"
            )
            session_path.write_text(
                json.dumps(result.model_dump(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            job.session_path = str(session_path)
            job.user_id = result.user_id
            job.password = result.password

            # Phase 2: enable 2FA
            log("[2fa] enabling…")
            if not result.access_token:
                job.status = "error"
                job.error = "missing access_token, không thể enable 2FA"
                job.finished_at = time.time()
                self._broadcast_job(job)
                return

            try:
                mfa_result = await enable_2fa(
                    access_token=result.access_token,
                    cookies=result.cookies,
                    proxy=self._proxy,
                    log=log,
                )
            except MfaError as exc:
                job.status = "error"
                job.error = f"2fa: {exc}"
                job.finished_at = time.time()
                self._broadcast_job(job)
                return

            # Lưu .2fa.json kèm
            two_fa_path = session_path.with_suffix(".2fa.json")
            two_fa_path.write_text(
                json.dumps({
                    "email": job.email,
                    "user_id": job.user_id,
                    "two_factor": mfa_result,
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            job.secret = mfa_result.get("secret")
            job.first_code = mfa_result.get("first_code")

            # Post-reg optional steps
            if self._post_reg_get_session or self._post_reg_get_link:
                await self._post_reg_steps(job, result)

            job.status = "success"
            job.finished_at = time.time()
            self._broadcast_job(job)

        except asyncio.CancelledError:
            job.status = "cancelled"
            job.finished_at = time.time()
            self._broadcast_job(job)
            raise
        except Exception as exc:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
            self._job_log(job, f"[fatal] {job.error}")
            self._broadcast_job(job)

    async def _post_reg_steps(self, job: Job, result: SignupResult) -> None:
        """Execute enabled post-reg toggles.

        Mỗi step độc lập: lỗi 1 step không ảnh hưởng step khác và không làm fail
        overall job (reg+2FA đã thành công).
        """
        access_token = result.access_token
        cookies = result.cookies  # list[dict] from Playwright

        # Get Session (HTTP only, dùng cookies đã có — không re-login)
        if self._post_reg_get_session:
            try:
                self._job_log(job, "[post-reg] fetching session...")
                from ..session_phase import fetch_session_via_http
                data = await fetch_session_via_http(
                    cookies=cookies,
                    proxy=self._proxy,
                    timeout=30.0,
                )
                job.session_data = data
                user_email = (data.get("user") or {}).get("email", "?")
                self._job_log(job, f"[post-reg] session OK — user: {user_email}")
            except Exception as exc:
                self._job_log(job, f"[post-reg] get-session failed: {exc}")

        # Get Link (dùng access_token có sẵn — không re-login)
        if self._post_reg_get_link:
            try:
                self._job_log(job, "[post-reg] fetching payment link...")
                if not access_token:
                    raise RuntimeError("access_token rỗng từ SignupResult")
                from ..payment_link import get_checkout_url
                url = await get_checkout_url(access_token, proxy=self._proxy)
                job.payment_link = url
                self._job_log(job, f"[post-reg] payment link: {url}")
            except Exception as exc:
                self._job_log(job, f"[post-reg] get-link failed: {exc}")


# Singleton
_manager: JobManager | None = None


def get_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager(max_concurrent=1)
    return _manager


# ─────────────────────────────────────────────────────────────────────
# SessionJobManager — Get Session feature
# ─────────────────────────────────────────────────────────────────────

from ..session_phase import SessionError, get_session  # noqa: E402


@dataclass
class SessionJob:
    id: str
    email: str
    password: str
    secret: str | None = None
    status: JobStatus = "queued"
    log_lines: list[str] = field(default_factory=list)
    error: str | None = None
    session_data: dict[str, Any] | None = None  # full /api/auth/session JSON
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "status": self.status,
            "error": self.error,
            "has_session": self.session_data is not None,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": (
                (self.finished_at or time.time()) - self.started_at if self.started_at else None
            ),
            "log_count": len(self.log_lines),
        }

    def to_dict_full(self) -> dict[str, Any]:
        d = self.to_dict()
        d["log_lines"] = list(self.log_lines)
        d["session_data"] = self.session_data
        d["password"] = self.password
        d["secret"] = self.secret
        return d


class SessionJobManager:
    """Quản lý Get Session jobs — worker pool pattern tương tự JobManager."""

    def __init__(self, *, max_concurrent: int = 1):
        self.jobs: dict[str, SessionJob] = {}
        self.order: list[str] = []
        self._max = max_concurrent
        self._headless = True
        self._job_timeout = _DEFAULT_JOB_TIMEOUT
        self._proxy: str | None = _DEFAULT_PROXY
        self._tasks: dict[str, asyncio.Task] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._job_queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._worker_started = False
        # Stagger: random 5-10s giữa các start
        self._stagger_lock = asyncio.Lock()
        self._last_start_ts: float = 0.0
        self._stagger_min_seconds = 5.0
        self._stagger_max_seconds = 10.0

    @property
    def headless(self) -> bool:
        return self._headless

    def set_headless(self, value: bool) -> None:
        self._headless = bool(value)

    def _ensure_workers(self) -> None:
        if not self._worker_started:
            self._worker_started = True
        # Prune worker đã chết (bị cancel khi stop_all hoặc exit do exception).
        # Không prune → len(self._workers) vẫn = _max → add jobs sau stop_all
        # sẽ enqueue nhưng không worker nào pick lên.
        self._workers = [w for w in self._workers if not w.done()]
        while len(self._workers) < self._max:
            w = asyncio.create_task(self._worker_loop())
            self._workers.append(w)
        while len(self._workers) > self._max:
            w = self._workers.pop()
            w.cancel()

    async def _worker_loop(self) -> None:
        # Job execution wrap trong inner task để stop_all cancel job mà không
        # kill luôn worker. Nếu worker bị kill, các job add lại sau đó sẽ
        # kẹt trong queue vì không ai pick lên.
        try:
            while True:
                job_id = await self._job_queue.get()
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                # Stagger reserve+sleep tách rời (xem JobManager._worker_loop).
                if self._max > 1:
                    async with self._stagger_lock:
                        now = time.monotonic()
                        wait_min = self._last_start_ts + self._stagger_min_seconds - now
                        if wait_min > 0:
                            jitter = random.uniform(
                                self._stagger_min_seconds, self._stagger_max_seconds,
                            )
                            wait = max(wait_min, jitter)
                            self._last_start_ts = now + wait
                        else:
                            wait = 0.0
                            self._last_start_ts = now
                    if wait > 0:
                        self._job_log(job, f"[stagger] đợi {wait:.1f}s trước khi start")
                        deadline = time.monotonic() + wait
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            await asyncio.sleep(min(0.25, remaining))
                            cur = self.jobs.get(job_id)
                            if cur is None or cur.status != "queued":
                                break
                    cur = self.jobs.get(job_id)
                    if cur is None or cur.status != "queued":
                        continue
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

    @property
    def max_concurrent(self) -> int:
        return self._max

    def set_max_concurrent(self, n: int) -> None:
        if n < 1 or n > 10:
            raise ValueError("max_concurrent phải trong [1, 10]")
        self._max = n
        self._ensure_workers()

    @property
    def job_timeout(self) -> float:
        return self._job_timeout

    def set_job_timeout(self, seconds: float) -> None:
        if seconds < 30 or seconds > 600:
            raise ValueError("job_timeout phải trong [30, 600]")
        self._job_timeout = float(seconds)

    @property
    def proxy(self) -> str | None:
        return self._proxy

    def set_proxy(self, value: str | None) -> None:
        if value is None:
            self._proxy = None
            return
        v = str(value).strip()
        self._proxy = v or None

    def _safe_proxy_log(self) -> str:
        return _mask_proxy(self._proxy)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _job_log(self, job: SessionJob, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        job.log_lines.append(line)
        if len(job.log_lines) > 500:
            job.log_lines = job.log_lines[-500:]
        self._broadcast({"type": "log", "job_id": job.id, "line": line})

    def _broadcast_job(self, job: SessionJob) -> None:
        self._broadcast({"type": "job", "job": job.to_dict()})

    def add_jobs(self, combos: list[str]) -> list[SessionJob]:
        """Parse input lines: email|password|secret. Dedup theo email."""
        existing_emails = {j.email.lower() for j in self.jobs.values() if j.status != "cancelled"}
        out: list[SessionJob] = []
        for raw in combos:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 2:
                jid = uuid.uuid4().hex[:12]
                job = SessionJob(
                    id=jid, email="<invalid>", password="",
                    status="error", error=f"format sai, cần email|password|secret: {line[:60]}",
                    finished_at=time.time(),
                )
                self.jobs[jid] = job
                self.order.append(jid)
                self._broadcast_job(job)
                out.append(job)
                continue

            email = parts[0].strip()
            password = parts[1].strip()
            secret = parts[2].strip() if len(parts) >= 3 else None

            if email.lower() in existing_emails:
                continue
            existing_emails.add(email.lower())

            jid = uuid.uuid4().hex[:12]
            job = SessionJob(id=jid, email=email, password=password, secret=secret)
            self.jobs[jid] = job
            self.order.append(jid)
            self._broadcast_job(job)
            out.append(job)

        self._ensure_workers()
        for j in out:
            if j.status == "queued":
                self._job_queue.put_nowait(j.id)
        return out

    def stop_all(self) -> int:
        while not self._job_queue.empty():
            try:
                self._job_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        count = 0
        for job_id, job in list(self.jobs.items()):
            if job.status in ("running", "queued"):
                task = self._tasks.get(job_id)
                if task and not task.done():
                    task.cancel()
                job.status = "cancelled"
                job.finished_at = time.time()
                self._broadcast_job(job)
                count += 1
        # Reset stagger debt — batch jobs mới sau stop_all không phải đợi
        # khoảng cách stagger tính từ batch cũ.
        self._last_start_ts = 0.0
        return count

    def clear_finished(self) -> int:
        """Xoá jobs đã xong (success/error). Giữ cancelled để user retry."""
        removed = 0
        for jid in list(self.order):
            job = self.jobs.get(jid)
            if job and job.status in ("success", "error"):
                self.jobs.pop(jid, None)
                self.order.remove(jid)
                self._tasks.pop(jid, None)
                removed += 1
        if removed:
            self._broadcast({"type": "clear_finished", "removed": removed})
        return removed

    def clear_all(self) -> int:
        """Cancel running/queued rồi xoá toàn bộ jobs khỏi memory."""
        self.stop_all()
        removed = len(self.jobs)
        self.jobs.clear()
        self.order.clear()
        self._tasks.clear()
        if removed:
            self._broadcast({"type": "clear_finished", "removed": removed})
        return removed

    def remove_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            job.status = "cancelled"
            job.finished_at = time.time()
        self.jobs.pop(job_id, None)
        if job_id in self.order:
            self.order.remove(job_id)
        self._tasks.pop(job_id, None)
        self._broadcast({"type": "remove", "job_id": job_id})
        return True

    def retry_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job.status = "queued"
        job.error = None
        job.session_data = None
        job.started_at = None
        job.finished_at = None
        job.log_lines.append(f"[{datetime.now():%H:%M:%S}] -- retry --")
        self._broadcast_job(job)
        self._ensure_workers()
        self._job_queue.put_nowait(job_id)
        return True

    def list_jobs(self) -> list[dict[str, Any]]:
        return [self.jobs[jid].to_dict() for jid in self.order if jid in self.jobs]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        return job.to_dict_full() if job else None

    def get_log(self, job_id: str) -> list[str]:
        job = self.jobs.get(job_id)
        return list(job.log_lines) if job else []

    async def _run_job(self, job: SessionJob) -> None:
        try:
            if job.id not in self.jobs:
                return
            job.status = "running"
            job.started_at = time.time()
            self._broadcast_job(job)

            def log(msg: str) -> None:
                self._job_log(job, msg)

            if self._proxy:
                log(f"[proxy] using {self._safe_proxy_log()}")

            from ..config import env_insecure_tls
            session_data = await asyncio.wait_for(
                get_session(
                    email=job.email,
                    password=job.password,
                    secret=job.secret,
                    headless=self._headless,
                    proxy=self._proxy,
                    tls_insecure=env_insecure_tls(),
                    log=log,
                ),
                timeout=self._job_timeout,
            )

            job.session_data = session_data
            job.status = "success"
            job.finished_at = time.time()
            self._broadcast_job(job)

        except asyncio.TimeoutError:
            job.status = "error"
            job.error = f"timeout {self._job_timeout:.0f}s exceeded"
            job.finished_at = time.time()
            self._job_log(job, f"[fatal] timeout {self._job_timeout:.0f}s")
            self._broadcast_job(job)
        except asyncio.CancelledError:
            job.status = "cancelled"
            job.finished_at = time.time()
            self._broadcast_job(job)
            raise
        except SessionError as exc:
            job.status = "error"
            job.error = str(exc)
            job.finished_at = time.time()
            self._job_log(job, f"[error] {exc}")
            self._broadcast_job(job)
        except Exception as exc:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
            self._job_log(job, f"[fatal] {job.error}")
            self._broadcast_job(job)
        finally:
            self._tasks.pop(job.id, None)


# Singleton
_session_manager: SessionJobManager | None = None


def get_session_manager() -> SessionJobManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionJobManager(max_concurrent=1)
    return _session_manager

# ─────────────────────────────────────────────────────────────────────
# LinkJobManager — Get Link feature
# ─────────────────────────────────────────────────────────────────────

from ..payment_link import get_checkout_url, get_checkout_info, PaymentLinkError, REGION_BILLING, DEFAULT_REGION  # noqa: E402


LinkMode = Literal["combo", "session_json", "access_token"]


@dataclass
class LinkJob:
    id: str
    email: str
    password: str
    secret: str | None = None
    mode: LinkMode = "combo"
    region: str = DEFAULT_REGION
    # Pre-provided token (dùng cho mode session_json / access_token)
    _access_token: str | None = field(default=None, repr=False)
    status: JobStatus = "queued"
    log_lines: list[str] = field(default_factory=list)
    error: str | None = None
    payment_link: str | None = None
    user_id: str | None = None
    screenshot_urls: list[str] = field(default_factory=list)
    # Trial / payment data
    is_trial: bool | None = None
    trial_days: int = 0
    amount_due: int = -1
    currency: str = ""
    checkout_session_id: str | None = None
    publishable_key: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "mode": self.mode,
            "region": self.region,
            "status": self.status,
            "error": self.error,
            "payment_link": self.payment_link,
            "user_id": self.user_id,
            "screenshot_urls": list(self.screenshot_urls),
            "is_trial": self.is_trial,
            "trial_days": self.trial_days,
            "amount_due": self.amount_due,
            "currency": self.currency,
            "checkout_session_id": self.checkout_session_id,
            "publishable_key": self.publishable_key,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": (
                (self.finished_at or time.time()) - self.started_at if self.started_at else None
            ),
            "log_count": len(self.log_lines),
        }

    def to_dict_full(self) -> dict[str, Any]:
        d = self.to_dict()
        d["log_lines"] = list(self.log_lines)
        return d


class LinkJobManager:
    """Quản lý Get Link jobs — login via browser → get payment link."""

    def __init__(self, *, max_concurrent: int = 1):
        self.jobs: dict[str, LinkJob] = {}
        self.order: list[str] = []
        self._max = max_concurrent
        self._headless = True
        self._job_timeout = 180.0
        self._proxy: str | None = _DEFAULT_PROXY
        self._region: str = DEFAULT_REGION
        self._tasks: dict[str, asyncio.Task] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._job_queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._worker_started = False
        # Stagger: random 5-10s giữa các start
        self._stagger_lock = asyncio.Lock()
        self._last_start_ts: float = 0.0
        self._stagger_min_seconds = 5.0
        self._stagger_max_seconds = 10.0

    @property
    def headless(self) -> bool:
        return self._headless

    def set_headless(self, value: bool) -> None:
        self._headless = bool(value)

    def _ensure_workers(self) -> None:
        if not self._worker_started:
            self._worker_started = True
        # Prune worker đã chết (bị cancel khi stop_all hoặc exit do exception).
        # Không prune → len(self._workers) vẫn = _max → add jobs sau stop_all
        # sẽ enqueue nhưng không worker nào pick lên.
        self._workers = [w for w in self._workers if not w.done()]
        while len(self._workers) < self._max:
            w = asyncio.create_task(self._worker_loop())
            self._workers.append(w)
        while len(self._workers) > self._max:
            w = self._workers.pop()
            w.cancel()

    async def _worker_loop(self) -> None:
        # Job execution wrap trong inner task để stop_all cancel job mà không
        # kill luôn worker. Nếu worker bị kill, các job add lại sau đó sẽ
        # kẹt trong queue vì không ai pick lên.
        try:
            while True:
                job_id = await self._job_queue.get()
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                # Stagger reserve+sleep tách rời (xem JobManager._worker_loop).
                if self._max > 1:
                    async with self._stagger_lock:
                        now = time.monotonic()
                        wait_min = self._last_start_ts + self._stagger_min_seconds - now
                        if wait_min > 0:
                            jitter = random.uniform(
                                self._stagger_min_seconds, self._stagger_max_seconds,
                            )
                            wait = max(wait_min, jitter)
                            self._last_start_ts = now + wait
                        else:
                            wait = 0.0
                            self._last_start_ts = now
                    if wait > 0:
                        self._job_log(job, f"[stagger] đợi {wait:.1f}s trước khi start")
                        deadline = time.monotonic() + wait
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                break
                            await asyncio.sleep(min(0.25, remaining))
                            cur = self.jobs.get(job_id)
                            if cur is None or cur.status != "queued":
                                break
                    cur = self.jobs.get(job_id)
                    if cur is None or cur.status != "queued":
                        continue
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

    @property
    def max_concurrent(self) -> int:
        return self._max

    def set_max_concurrent(self, n: int) -> None:
        if n < 1 or n > 10:
            raise ValueError("max_concurrent phải trong [1, 10]")
        self._max = n
        self._ensure_workers()

    @property
    def job_timeout(self) -> float:
        return self._job_timeout

    def set_job_timeout(self, seconds: float) -> None:
        if seconds < 30 or seconds > 600:
            raise ValueError("job_timeout phải trong [30, 600]")
        self._job_timeout = float(seconds)

    @property
    def proxy(self) -> str | None:
        return self._proxy

    def set_proxy(self, value: str | None) -> None:
        if value is None:
            self._proxy = None
            return
        v = str(value).strip()
        self._proxy = v or None

    @property
    def region(self) -> str:
        return self._region

    def set_region(self, value: str) -> None:
        if value not in REGION_BILLING:
            raise ValueError(f"region phải là một trong: {list(REGION_BILLING.keys())}")
        self._region = value

    def _safe_proxy_log(self) -> str:
        return _mask_proxy(self._proxy)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _job_log(self, job: LinkJob, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        job.log_lines.append(line)
        if len(job.log_lines) > 500:
            job.log_lines = job.log_lines[-500:]
        self._broadcast({"type": "log", "job_id": job.id, "line": line})

    def _broadcast_job(self, job: LinkJob) -> None:
        self._broadcast({"type": "job", "job": job.to_dict()})

    def add_jobs(self, lines: list[str], *, mode: LinkMode = "combo", region: str | None = None) -> list[LinkJob]:
        """Parse input based on mode. Dedup theo email."""
        resolved_region = region or self._region
        existing_emails = {j.email.lower() for j in self.jobs.values() if j.status != "cancelled"}
        out: list[LinkJob] = []

        if mode == "combo":
            out = self._parse_combo(lines, existing_emails, resolved_region)
        elif mode == "session_json":
            out = self._parse_session_json(lines, existing_emails, resolved_region)
        elif mode == "access_token":
            out = self._parse_access_token(lines, existing_emails, resolved_region)
        else:
            return out

        self._ensure_workers()
        for j in out:
            if j.status == "queued":
                self._job_queue.put_nowait(j.id)
        return out

    def _parse_combo(self, lines: list[str], existing_emails: set[str], region: str) -> list[LinkJob]:
        """Mode combo: email|password|secret per line."""
        out: list[LinkJob] = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 2:
                jid = uuid.uuid4().hex[:12]
                job = LinkJob(
                    id=jid, email="<invalid>", password="",
                    mode="combo", region=region,
                    status="error", error=f"format sai, cần email|password|secret: {line[:60]}",
                    finished_at=time.time(),
                )
                self.jobs[jid] = job
                self.order.append(jid)
                self._broadcast_job(job)
                out.append(job)
                continue

            email = parts[0].strip()
            password = parts[1].strip()
            secret = parts[2].strip() if len(parts) >= 3 else None

            if email.lower() in existing_emails:
                continue
            existing_emails.add(email.lower())

            jid = uuid.uuid4().hex[:12]
            job = LinkJob(id=jid, email=email, password=password, secret=secret, mode="combo", region=region)
            self.jobs[jid] = job
            self.order.append(jid)
            self._broadcast_job(job)
            out.append(job)
        return out

    def _parse_session_json(self, lines: list[str], existing_emails: set[str], region: str) -> list[LinkJob]:
        """Mode session_json: hỗ trợ 1 JSON object trải nhiều dòng HOẶC nhiều JSON
        objects, mỗi dòng = 1 account.
        """
        out: list[LinkJob] = []
        raw_lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        if not raw_lines:
            return out

        # Detect format: thử parse toàn bộ làm 1 JSON; nếu fail → parse từng dòng
        full_text = "\n".join(raw_lines).strip()
        json_blobs: list[str] = []
        try:
            single = json.loads(full_text)
            if isinstance(single, list):
                json_blobs = [json.dumps(obj) for obj in single]
            elif isinstance(single, dict):
                json_blobs = [full_text]
            else:
                json_blobs = []
        except (json.JSONDecodeError, ValueError):
            json_blobs = [ln.strip() for ln in raw_lines]

        for blob in json_blobs:
            blob = blob.strip()
            if not blob:
                continue

            try:
                data = json.loads(blob)
            except (json.JSONDecodeError, ValueError) as exc:
                jid = uuid.uuid4().hex[:12]
                job = LinkJob(
                    id=jid, email="<invalid>", password="",
                    mode="session_json",
                    status="error", error=f"invalid JSON: {exc}",
                    finished_at=time.time(),
                )
                self.jobs[jid] = job
                self.order.append(jid)
                self._broadcast_job(job)
                out.append(job)
                continue

            if not isinstance(data, dict):
                jid = uuid.uuid4().hex[:12]
                job = LinkJob(
                    id=jid, email="<invalid>", password="",
                    mode="session_json",
                    status="error", error="JSON phải là object",
                    finished_at=time.time(),
                )
                self.jobs[jid] = job
                self.order.append(jid)
                self._broadcast_job(job)
                out.append(job)
                continue

            token = data.get("accessToken") or data.get("access_token") or ""
            user = data.get("user") or {}
            email = user.get("email") or f"token_{uuid.uuid4().hex[:6]}"
            user_id = user.get("id") or data.get("userId") or data.get("user_id")

            if not token:
                jid = uuid.uuid4().hex[:12]
                job = LinkJob(
                    id=jid, email=email, password="",
                    mode="session_json",
                    status="error", error="thiếu accessToken",
                    finished_at=time.time(),
                )
                self.jobs[jid] = job
                self.order.append(jid)
                self._broadcast_job(job)
                out.append(job)
                continue

            if email.lower() in existing_emails:
                continue
            existing_emails.add(email.lower())

            jid = uuid.uuid4().hex[:12]
            job = LinkJob(
                id=jid, email=email, password="", mode="session_json",
                region=region, _access_token=token, user_id=user_id,
            )
            self.jobs[jid] = job
            self.order.append(jid)
            self._broadcast_job(job)
            out.append(job)

        return out

    def _parse_access_token(self, lines: list[str], existing_emails: set[str], region: str) -> list[LinkJob]:
        """Mode access_token: mỗi line là 1 raw JWT."""
        out: list[LinkJob] = []
        for raw in lines:
            token = raw.strip()
            if not token or token.startswith("#"):
                continue

            # Tạo label từ token (email nếu decode được, hoặc token prefix)
            email = self._extract_email_from_jwt(token) or f"token_...{token[-8:]}"

            if email.lower() in existing_emails:
                continue
            existing_emails.add(email.lower())

            jid = uuid.uuid4().hex[:12]
            job = LinkJob(
                id=jid, email=email, password="", mode="access_token",
                region=region, _access_token=token,
            )
            self.jobs[jid] = job
            self.order.append(jid)
            self._broadcast_job(job)
            out.append(job)
        return out

    @staticmethod
    def _extract_email_from_jwt(token: str) -> str | None:
        """Decode JWT payload (no verify) để lấy email."""
        import base64
        parts = token.split(".")
        if len(parts) < 2:
            return None
        try:
            payload_b64 = parts[1]
            # Fix padding
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes)
            return payload.get("email") or payload.get("https://api.openai.com/auth", {}).get("email")
        except Exception:
            return None

    def stop_all(self) -> int:
        while not self._job_queue.empty():
            try:
                self._job_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        count = 0
        for job_id, job in list(self.jobs.items()):
            if job.status in ("running", "queued"):
                task = self._tasks.get(job_id)
                if task and not task.done():
                    task.cancel()
                job.status = "cancelled"
                job.finished_at = time.time()
                self._broadcast_job(job)
                count += 1
        # Reset stagger debt — batch jobs mới sau stop_all không phải đợi
        # khoảng cách stagger tính từ batch cũ.
        self._last_start_ts = 0.0
        return count

    def clear_finished(self) -> int:
        """Xoá jobs đã xong (success/error). Giữ cancelled để user retry."""
        removed = 0
        for jid in list(self.order):
            job = self.jobs.get(jid)
            if job and job.status in ("success", "error"):
                self.jobs.pop(jid, None)
                self.order.remove(jid)
                self._tasks.pop(jid, None)
                removed += 1
        if removed:
            self._broadcast({"type": "clear_finished", "removed": removed})
        return removed

    def clear_all(self) -> int:
        """Cancel running/queued rồi xoá toàn bộ jobs khỏi memory."""
        self.stop_all()
        removed = len(self.jobs)
        self.jobs.clear()
        self.order.clear()
        self._tasks.clear()
        if removed:
            self._broadcast({"type": "clear_finished", "removed": removed})
        return removed

    def remove_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            job.status = "cancelled"
            job.finished_at = time.time()
        # Delete associated screenshot files
        _screenshot_dir = Path(__file__).resolve().parent.parent / "runtime" / "upi_screenshots"
        for url in (job.screenshot_urls or []):
            fname = url.split("/")[-1]
            fpath = _screenshot_dir / fname
            try:
                if fpath.exists():
                    fpath.unlink()
            except Exception:
                pass
        self.jobs.pop(job_id, None)
        if job_id in self.order:
            self.order.remove(job_id)
        self._tasks.pop(job_id, None)
        self._broadcast({"type": "remove", "job_id": job_id})
        return True

    def retry_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job.status = "queued"
        job.error = None
        job.payment_link = None
        job.started_at = None
        job.finished_at = None
        job.log_lines.append(f"[{datetime.now():%H:%M:%S}] -- retry --")
        self._broadcast_job(job)
        self._ensure_workers()
        self._job_queue.put_nowait(job_id)
        return True

    def list_jobs(self) -> list[dict[str, Any]]:
        return [self.jobs[jid].to_dict() for jid in self.order if jid in self.jobs]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        return job.to_dict_full() if job else None

    def get_log(self, job_id: str) -> list[str]:
        job = self.jobs.get(job_id)
        return list(job.log_lines) if job else []

    async def _run_job(self, job: LinkJob) -> None:
        try:
            if job.id not in self.jobs:
                return
            job.status = "running"
            job.started_at = time.time()
            self._broadcast_job(job)

            def log(msg: str) -> None:
                self._job_log(job, msg)

            # ── Resolve access_token theo mode ──
            access_token: str | None = None

            if job.mode == "combo":
                # Login via browser → obtain token
                log("[login] starting")
                if self._proxy:
                    log(f"[login] via proxy {self._safe_proxy_log()}")
                from ..config import env_insecure_tls
                try:
                    session_data = await asyncio.wait_for(
                        get_session(
                            email=job.email,
                            password=job.password,
                            secret=job.secret,
                            headless=self._headless,
                            proxy=self._proxy,
                            tls_insecure=env_insecure_tls(),
                            log=log,
                        ),
                        timeout=self._job_timeout,
                    )
                except asyncio.TimeoutError:
                    job.status = "error"
                    job.error = f"timeout {self._job_timeout:.0f}s exceeded (login phase)"
                    job.finished_at = time.time()
                    self._job_log(job, f"[fatal] timeout {self._job_timeout:.0f}s")
                    self._broadcast_job(job)
                    return
                except SessionError as exc:
                    job.status = "error"
                    job.error = f"login: {exc}"
                    job.finished_at = time.time()
                    self._job_log(job, f"[login] failed: {exc}")
                    self._broadcast_job(job)
                    return

                access_token = session_data.get("accessToken") if session_data else None
                if not access_token:
                    job.status = "error"
                    job.error = "login: missing accessToken in session"
                    job.finished_at = time.time()
                    self._job_log(job, "[login] failed: no accessToken in response")
                    self._broadcast_job(job)
                    return
                log("[login] success")

            elif job.mode in ("session_json", "access_token"):
                # Token đã được parse sẵn
                access_token = job._access_token
                if not access_token:
                    job.status = "error"
                    job.error = "no access_token provided"
                    job.finished_at = time.time()
                    self._job_log(job, "[token] missing — nothing to do")
                    self._broadcast_job(job)
                    return
                log(f"[token] using pre-provided token (mode={job.mode})")

            # ── Get payment link + trial info ──
            log(f"[link] fetching payment URL (region={job.region})")
            if self._proxy:
                log(f"[link] via proxy {self._safe_proxy_log()}")
            try:
                info = await asyncio.wait_for(
                    get_checkout_info(access_token, region=job.region, proxy=self._proxy),
                    timeout=60.0,
                )
                url = info.payment_url
                job.is_trial = info.is_trial
                job.trial_days = info.trial_days
                job.amount_due = info.amount_due
                job.currency = info.currency
                job.checkout_session_id = info.checkout_session_id
                job.publishable_key = info.publishable_key
                trial_str = f"trial={info.trial_days}d" if info.is_trial else f"paid={info.amount_due}"
                log(f"[link] trial_check: {trial_str}")
            except asyncio.TimeoutError:
                job.status = "error"
                job.error = "payment_link: timeout 60s exceeded"
                job.finished_at = time.time()
                self._job_log(job, "[link] timeout 60s")
                self._broadcast_job(job)
                return
            except PaymentLinkError as exc:
                job.status = "error"
                job.error = f"payment_link: {exc}"
                job.finished_at = time.time()
                self._job_log(job, f"[link] failed: {exc}")
                self._broadcast_job(job)
                return

            job.payment_link = url
            job.status = "success"
            job.finished_at = time.time()
            log(f"[link] success: {url}")
            self._broadcast_job(job)

        except asyncio.CancelledError:
            job.status = "cancelled"
            job.finished_at = time.time()
            self._broadcast_job(job)
            raise
        except Exception as exc:
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
            self._job_log(job, f"[fatal] {job.error}")
            self._broadcast_job(job)
        finally:
            self._tasks.pop(job.id, None)


# Singleton
_link_manager: LinkJobManager | None = None


def get_link_manager() -> LinkJobManager:
    global _link_manager
    if _link_manager is None:
        _link_manager = LinkJobManager(max_concurrent=1)
    return _link_manager
