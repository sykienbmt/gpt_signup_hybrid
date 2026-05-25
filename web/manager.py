"""Job manager: queue + concurrency control + broadcast events (Get Link only)."""
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

from ..payment_link import get_checkout_url, PaymentLinkError, REGION_BILLING, DEFAULT_REGION
from ..session_phase import SessionError, get_session


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

LinkMode = Literal["combo", "session_json", "access_token"]


# ─────────────────────────────────────────────────────────────────────
# LinkJobManager — Get Link feature
# ─────────────────────────────────────────────────────────────────────


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
        self._workers = [w for w in self._workers if not w.done()]
        while len(self._workers) < self._max:
            w = asyncio.create_task(self._worker_loop())
            self._workers.append(w)
        while len(self._workers) > self._max:
            w = self._workers.pop()
            w.cancel()

    async def _worker_loop(self) -> None:
        try:
            while True:
                job_id = await self._job_queue.get()
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
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
        """Mode session_json: hỗ trợ 1 JSON object trải nhiều dòng HOẶC nhiều JSON objects."""
        out: list[LinkJob] = []
        raw_lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        if not raw_lines:
            return out

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
        self._last_start_ts = 0.0
        return count

    def clear_finished(self) -> int:
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

            access_token: str | None = None

            if job.mode == "combo":
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
                access_token = job._access_token
                if not access_token:
                    job.status = "error"
                    job.error = "no access_token provided"
                    job.finished_at = time.time()
                    self._job_log(job, "[token] missing — nothing to do")
                    self._broadcast_job(job)
                    return
                log(f"[token] using pre-provided token (mode={job.mode})")

            log(f"[link] fetching payment URL (region={job.region})")
            if self._proxy:
                log(f"[link] via proxy {self._safe_proxy_log()}")
            try:
                url = await asyncio.wait_for(
                    get_checkout_url(access_token, region=job.region, proxy=self._proxy),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                job.status = "error"
                job.error = "payment_link: timeout 30s exceeded"
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
