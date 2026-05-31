"""FastAPI server cho web UI gpt_signup_hybrid."""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import get_token  # legacy — token auth disabled
from .manager import get_manager, get_session_manager, get_link_manager, _mask_proxy
from .mail_modes import get_registry, serialize_for_api
from .proxy_rotate import ProxyRotateService, load_proxy_rotate, save_proxy_rotate
from .upi_automation import run_upi_automation
from .upi_watch import UpiWatchManager
from .check_account import check_accounts
from ..mail_providers import smsbower_dot_alias, smsbower_existing_dot_position


_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """Build a lightweight cache-busting token from static file mtimes."""
    latest_mtime = 0
    for path in _STATIC_DIR.glob("*"):
        if path.is_file():
            latest_mtime = max(latest_mtime, path.stat().st_mtime_ns)
    return str(latest_mtime or 1)


app = FastAPI(title="gpt_signup_hybrid web UI", version="0.1.0")


def _apply_global_proxy(value: str | None) -> str | None:
    manager = get_manager()
    manager.set_proxy(value)
    get_session_manager().set_proxy(value)
    get_link_manager().set_proxy(value)
    return manager.proxy


def _apply_global_proxy_enabled(value: bool) -> bool:
    get_manager().set_proxy_enabled(value)
    get_session_manager().set_proxy_enabled(value)
    get_link_manager().set_proxy_enabled(value)
    return value


_proxy_rotate_service = ProxyRotateService(_apply_global_proxy)
_CHANGE_PASSWORD_LOG_TTL_SECONDS = 3600
_CHANGE_PASSWORD_LOG_MAX_ITEMS = 200
_CHANGE_PASSWORD_LOG_MAX_LINES = 700
_change_password_logs: dict[str, dict[str, Any]] = {}


def _cleanup_change_password_logs(now: float | None = None) -> None:
    now = now or time.time()
    expired = [
        request_id
        for request_id, entry in _change_password_logs.items()
        if now - float(entry.get("updated_at") or 0) > _CHANGE_PASSWORD_LOG_TTL_SECONDS
    ]
    for request_id in expired:
        _change_password_logs.pop(request_id, None)

    overflow = len(_change_password_logs) - _CHANGE_PASSWORD_LOG_MAX_ITEMS
    if overflow > 0:
        oldest = sorted(
            _change_password_logs,
            key=lambda key: float(_change_password_logs[key].get("updated_at") or 0),
        )
        for request_id in oldest[:overflow]:
            _change_password_logs.pop(request_id, None)


def _append_change_password_log(request_id: str, msg: str) -> None:
    if not request_id:
        return
    now = time.time()
    entry = _change_password_logs.setdefault(
        request_id,
        {"logs": [], "done": False, "updated_at": now},
    )
    logs = entry.setdefault("logs", [])
    logs.append(msg)
    if len(logs) > _CHANGE_PASSWORD_LOG_MAX_LINES:
        del logs[:-_CHANGE_PASSWORD_LOG_MAX_LINES]
    entry["updated_at"] = now
    _cleanup_change_password_logs(now)


def _finish_change_password_log(request_id: str) -> None:
    if not request_id:
        return
    entry = _change_password_logs.setdefault(
        request_id,
        {"logs": [], "done": False, "updated_at": time.time()},
    )
    entry["done"] = True
    entry["updated_at"] = time.time()


@app.on_event("startup")
async def _start_proxy_rotate_service() -> None:
    _proxy_rotate_service.start()


# ─── Auth middleware (disabled) ───────────────────────────────────────
# Token auth đã tắt — truy cập trực tiếp không cần token.


# ─────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────


class AddJobsRequest(BaseModel):
    combos: str = Field(..., description="Textarea content, nhiều combo cách nhau bằng newline.")
    default_password: str | None = Field(
        default=None,
        description="Password mặc định cho tất cả job. Nếu null → random.",
    )
    mail_mode: str = Field(
        default="outlook",
        description="Mail mode: 'outlook', 'worker', hoặc 'gmail_advanced'.",
    )
    email_logs_url: str | None = Field(
        default=None,
        description="[worker] Worker API URL.",
    )
    email_api_key: str | None = Field(
        default=None,
        description="[worker] Bearer token (VIEW_TOKEN).",
    )
    gmail_alias_expand: bool = Field(
        default=False,
        description="[gmail_advanced] Tạo alias +gshN cho mỗi email|api_url.",
    )
    gmail_alias_count: int = Field(
        default=1, ge=1, le=10,
        description="[gmail_advanced] Số alias cần tạo (1-10).",
    )


class SetConfigRequest(BaseModel):
    max_concurrent: int | None = Field(default=None, ge=1, le=10)
    headless: bool | None = Field(default=None)
    debug: bool | None = Field(default=None)
    job_timeout: float | None = Field(default=None, ge=30, le=600)
    proxy: str | None = Field(
        default=None,
        description="HTTP/HTTPS proxy URL. Empty string = direct (clear).",
    )
    proxy_enabled: bool | None = None
    post_reg_get_session: bool | None = None
    post_reg_get_link: bool | None = None


class ProxyRotateConfigRequest(BaseModel):
    enabled: bool | None = None
    command: str | None = Field(
        default=None,
        description="Rotate URL or curl command.",
    )
    interval_seconds: int | None = Field(default=None, ge=10, le=86400)


@app.get("/api/jobs")
async def list_jobs() -> JSONResponse:
    manager = get_manager()
    return JSONResponse({
        "max_concurrent": manager.max_concurrent,
        "headless": manager.headless,
        "debug": manager.debug,
        "job_timeout": manager.job_timeout,
        "proxy": manager.proxy,
        "proxy_enabled": manager.proxy_enabled,
        "proxy_rotate": load_proxy_rotate(),
        "jobs": manager.list_jobs(),
    })


@app.get("/api/jobs/secrets")
async def get_jobs_secrets() -> JSONResponse:
    """Trả secrets (password/secret/first_code/session_path) cho mọi job.

    Auth gate đã cover bởi middleware. Endpoint riêng để list jobs default
    không leak secrets nếu caller chỉ subscribe SSE.
    """
    manager = get_manager()
    return JSONResponse({"secrets": manager.get_secrets_map()})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    manager = get_manager()
    data = manager.get_job(job_id)
    if data is None:
        raise HTTPException(404, "job not found")
    return JSONResponse(data)


@app.get("/api/jobs/{job_id}/log")
async def get_job_log(job_id: str) -> JSONResponse:
    manager = get_manager()
    if job_id not in manager.jobs:
        raise HTTPException(404, "job not found")
    return JSONResponse({"job_id": job_id, "log": manager.get_log(job_id)})


@app.post("/api/jobs")
async def add_jobs(payload: AddJobsRequest) -> JSONResponse:
    # Validate mail_mode
    if payload.mail_mode not in get_registry():
        raise HTTPException(422, f"unknown mail_mode: {payload.mail_mode}")

    # Build worker_config nếu mode = worker
    worker_config = None
    if payload.mail_mode == "worker":
        url = (payload.email_logs_url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(422, "email_logs_url must start with http:// or https://")
        worker_config = {"logs_url": url, "api_key": (payload.email_api_key or "").strip()}

    combos = payload.combos.splitlines()
    manager = get_manager()
    jobs = manager.add_jobs(
        combos,
        default_password=payload.default_password,
        mail_mode=payload.mail_mode,
        worker_config=worker_config,
        gmail_alias_expand=payload.gmail_alias_expand,
        gmail_alias_count=payload.gmail_alias_count,
    )
    return JSONResponse({"added": len(jobs), "jobs": [j.to_dict() for j in jobs]})


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str) -> JSONResponse:
    manager = get_manager()
    ok = manager.retry_job(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return JSONResponse({"ok": True})


class RecheckBody(BaseModel):
    combos: str


@app.post("/api/smsbower/recheck")
async def smsbower_recheck(body: RecheckBody) -> JSONResponse:
    """Check api_url từ combos input, queue alias mới cho những url còn OTP capacity.

    Logic:
    - Parse combos input (email----api_url per line)
    - Deduplicate theo api_url, gọi API check all_codes
    - Nếu len(all_codes) >= 2 → skip (inbox đã full)
    - Nếu len(all_codes) < 2 → tạo dot-alias email, queue job mới với smsbower_max_all_codes=2
    """
    import httpx as _httpx

    manager = get_manager()

    # Parse combos từ input — mỗi dòng là email----api_url
    url_to_email: dict[str, str] = {}
    for line in body.combos.splitlines():
        raw = line.strip()
        if not raw or "----" not in raw:
            continue
        parts = raw.split("----", 1)
        email = parts[0].strip()
        url = parts[1].strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if url not in url_to_email:
            url_to_email[url] = email

    if not url_to_email:
        return JSONResponse({"requeued": 0, "skipped": 0, "message": "No valid SmsBower combos in input"})

    # Check từng api_url concurrently
    async def _check(url: str, email: str) -> tuple[str, str, int | None, str | None]:
        try:
            async with _httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                r = await c.get(url)
            if r.status_code != 200:
                return url, email, None, f"HTTP {r.status_code}"
            data = r.json()
            codes = data.get("all_codes") or []
            return url, email, len(codes), None
        except Exception as exc:
            return url, email, None, str(exc)[:120]

    results = await asyncio.gather(*[_check(u, e) for u, e in url_to_email.items()])

    requeued: list[dict] = []
    skipped:  list[dict] = []

    combos_to_add: list[str] = []
    extra_fields_map: dict[str, dict] = {}  # combo → extra_req_fields

    for url, email, codes_count, err in results:
        if err:
            skipped.append({"email": email, "reason": err})
            continue
        if codes_count is None or codes_count >= 2:
            skipped.append({"email": email, "reason": f"all_codes={codes_count} — inbox full"})
            continue

        # Còn capacity → strip dot hiện tại, chèn 1 dot mới ở vị trí khác
        current_pos = smsbower_existing_dot_position(email)
        alias_email = smsbower_dot_alias(email, avoid_position=current_pos)
        combo = f"{alias_email}----{url}"
        combos_to_add.append(combo)
        extra_fields_map[combo] = {"smsbower_max_all_codes": 2}
        requeued.append({"email": alias_email, "original": email, "codes_used": codes_count})

    # Queue jobs mới
    if combos_to_add:
        added = manager.add_jobs(combos_to_add, mail_mode="smsbower")
        # Gán extra_req_fields cho từng job vừa tạo
        for job in added:
            extra = extra_fields_map.get(job.combo)
            if extra:
                job._extra_req_fields = extra  # type: ignore[attr-defined]

    return JSONResponse({
        "requeued": len(requeued),
        "skipped": len(skipped),
        "details": requeued,
        "skipped_details": skipped,
    })


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> JSONResponse:
    manager = get_manager()
    ok = manager.remove_job(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return JSONResponse({"ok": True})


@app.post("/api/jobs/stop-all")
async def stop_all_jobs() -> JSONResponse:
    """Cancel tất cả jobs đang running/queued."""
    manager = get_manager()
    stopped = manager.stop_all()
    return JSONResponse({"stopped": stopped})


@app.post("/api/jobs/fetch-all-sessions")
async def fetch_all_sessions() -> JSONResponse:
    """Fetch /api/auth/session cho tất cả jobs success dùng cookies đã có."""
    manager = get_manager()
    job_ids = [jid for jid in list(manager.order)
               if manager.jobs.get(jid) and manager.jobs[jid].status == "success"]
    results = await asyncio.gather(*[manager.fetch_session_for_job(jid) for jid in job_ids])
    fetched = sum(1 for r in results if r)
    return JSONResponse({"fetched": fetched, "total": len(job_ids)})


@app.post("/api/jobs/clear-finished")
async def clear_finished_jobs() -> JSONResponse:
    """Xoá tất cả jobs đã xong khỏi memory (giải phóng RAM)."""
    manager = get_manager()
    removed = manager.clear_finished()
    return JSONResponse({"removed": removed})


@app.post("/api/jobs/clear-all")
async def clear_all_jobs() -> JSONResponse:
    """Cancel running/queued và xoá toàn bộ jobs khỏi memory."""
    manager = get_manager()
    removed = manager.clear_all()
    return JSONResponse({"removed": removed})


@app.get("/api/config")
async def get_config() -> JSONResponse:
    manager = get_manager()
    return JSONResponse({
        "max_concurrent": manager.max_concurrent,
        "headless": manager.headless,
        "debug": manager.debug,
        "job_timeout": manager.job_timeout,
        "proxy": manager.proxy,
        "proxy_enabled": manager.proxy_enabled,
        "proxy_rotate": load_proxy_rotate(),
        "post_reg_get_session": manager.post_reg_get_session,
        "post_reg_get_link": manager.post_reg_get_link,
    })


@app.post("/api/config")
async def set_config(payload: SetConfigRequest) -> JSONResponse:
    manager = get_manager()
    if payload.max_concurrent is not None:
        try:
            manager.set_max_concurrent(payload.max_concurrent)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if payload.headless is not None:
        manager.set_headless(payload.headless)
    if payload.debug is not None:
        manager.set_debug(payload.debug)
    if payload.job_timeout is not None:
        try:
            manager.set_job_timeout(payload.job_timeout)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if payload.proxy is not None:
        _apply_global_proxy(payload.proxy)
    if payload.proxy_enabled is not None:
        _apply_global_proxy_enabled(payload.proxy_enabled)
    if payload.post_reg_get_session is not None:
        manager.set_post_reg_get_session(payload.post_reg_get_session)
    if payload.post_reg_get_link is not None:
        manager.set_post_reg_get_link(payload.post_reg_get_link)
    return JSONResponse({
        "max_concurrent": manager.max_concurrent,
        "headless": manager.headless,
        "debug": manager.debug,
        "job_timeout": manager.job_timeout,
        "proxy": manager.proxy,
        "proxy_enabled": manager.proxy_enabled,
        "proxy_rotate": load_proxy_rotate(),
        "post_reg_get_session": manager.post_reg_get_session,
        "post_reg_get_link": manager.post_reg_get_link,
    })


@app.get("/api/mail-modes")
async def list_mail_modes() -> JSONResponse:
    """Trả danh sách mail modes cho UI render selector + config panels."""
    return JSONResponse({"modes": serialize_for_api()})


# ─────────────────────────────────────────────────────────────────────
# Proxy test
# ─────────────────────────────────────────────────────────────────────


class TestProxyRequest(BaseModel):
    proxy: str | None = Field(
        default=None,
        description="Proxy URL cần test. Empty/null = test direct.",
    )


@app.post("/api/proxy/test")
async def test_proxy(payload: TestProxyRequest) -> JSONResponse:
    """Verify proxy đến Microsoft login + Graph endpoint thật.

    Test 2 endpoint quan trọng nhất cho luồng Outlook OTP:
      - login.microsoftonline.com (refresh token)
      - graph.microsoft.com (list mail)

    Cả 2 endpoint chỉ cần check reachability — gọi GET không auth, expect HTTP
    200/4xx (không phải connect error). 5xx và timeout = proxy fail.
    """
    import time as _time
    import httpx as _httpx

    proxy = (payload.proxy or "").strip() or None
    timeout = _httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)
    targets = [
        ("microsoft_login", "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"),
        ("microsoft_graph", "https://graph.microsoft.com/v1.0/me"),
        ("ip_check", "https://api.ipify.org?format=json"),
    ]
    results: list[dict[str, Any]] = []
    overall_ok = True
    public_ip: str | None = None

    client_kwargs: dict[str, Any] = {"timeout": timeout, "follow_redirects": False}
    if proxy:
        client_kwargs["proxy"] = proxy

    try:
        async with _httpx.AsyncClient(**client_kwargs) as client:
            for label, url in targets:
                t0 = _time.monotonic()
                ok = False
                detail = ""
                try:
                    r = await client.get(url)
                    elapsed = (_time.monotonic() - t0) * 1000
                    # 2xx/3xx/4xx đều OK về reachability — chỉ 5xx mới là server fail
                    ok = r.status_code < 500
                    detail = f"HTTP {r.status_code} in {elapsed:.0f}ms"
                    if label == "ip_check" and ok:
                        try:
                            public_ip = r.json().get("ip")
                        except Exception:
                            public_ip = None
                except _httpx.HTTPError as exc:
                    elapsed = (_time.monotonic() - t0) * 1000
                    detail = f"{type(exc).__name__}: {exc!r} (after {elapsed:.0f}ms)"
                    ok = False
                except Exception as exc:  # noqa: BLE001
                    detail = f"{type(exc).__name__}: {exc!r}"
                    ok = False
                results.append({"target": label, "ok": ok, "detail": detail})
                if not ok:
                    overall_ok = False
    except Exception as exc:  # noqa: BLE001 — proxy URL invalid (httpx raise lúc tạo client)
        return JSONResponse(
            {
                "ok": False,
                "proxy": proxy,
                "error": f"{type(exc).__name__}: {exc!r}",
                "results": [],
                "public_ip": None,
            },
            status_code=200,
        )

    return JSONResponse({
        "ok": overall_ok,
        "proxy": proxy,
        "public_ip": public_ip,
        "results": results,
        "ms_reachable": all(
            r["ok"] for r in results if r["target"].startswith("microsoft_")
        ),
    })


@app.get("/api/proxy/rotate/config")
async def get_proxy_rotate_config() -> JSONResponse:
    cfg = load_proxy_rotate()
    cfg["proxy"] = get_manager().proxy
    return JSONResponse(cfg)


@app.post("/api/proxy/rotate/config")
async def set_proxy_rotate_config(payload: ProxyRotateConfigRequest) -> JSONResponse:
    patch: dict[str, Any] = {}
    if payload.enabled is not None:
        patch["enabled"] = payload.enabled
    if payload.command is not None:
        patch["command"] = payload.command
    if payload.interval_seconds is not None:
        patch["interval_seconds"] = payload.interval_seconds
    cfg = save_proxy_rotate(patch)
    _proxy_rotate_service.restart()
    cfg["proxy"] = get_manager().proxy
    return JSONResponse(cfg)


@app.post("/api/proxy/rotate-now")
async def rotate_proxy_now() -> JSONResponse:
    try:
        cfg = await _proxy_rotate_service.rotate_once()
    except Exception as exc:
        cfg = save_proxy_rotate({
            "last_run_at": time.time(),
            "last_ok": False,
            "last_message": f"{type(exc).__name__}: {exc}",
        })
        return JSONResponse({**cfg, "proxy": get_manager().proxy}, status_code=200)
    return JSONResponse({**cfg, "proxy": get_manager().proxy})


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """SSE stream cho realtime updates."""
    manager = get_manager()
    queue = manager.subscribe()

    async def gen():
        try:
            # Initial snapshot
            snapshot = {
                "type": "snapshot",
                "max_concurrent": manager.max_concurrent,
                "headless": manager.headless,
                "debug": manager.debug,
                "job_timeout": manager.job_timeout,
                "proxy": manager.proxy,
        "proxy_enabled": manager.proxy_enabled,
                "proxy_rotate": load_proxy_rotate(),
                "post_reg_get_session": manager.post_reg_get_session,
                "post_reg_get_link": manager.post_reg_get_link,
                "jobs": manager.list_jobs(),
            }
            yield f"data: {json.dumps(snapshot)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # heartbeat
                    yield ": ping\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    break
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            manager.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.on_event("shutdown")
async def on_shutdown():
    """Force close tất cả SSE subscriber queues khi server shutdown."""
    manager = get_manager()
    for q in list(manager._subscribers):
        try:
            q.put_nowait(None)
        except Exception:
            pass
    manager._subscribers.clear()
    # Session manager cleanup
    sm = get_session_manager()
    for q in list(sm._subscribers):
        try:
            q.put_nowait(None)
        except Exception:
            pass
    sm._subscribers.clear()
    # Link manager cleanup
    lm = get_link_manager()
    for q in list(lm._subscribers):
        try:
            q.put_nowait(None)
        except Exception:
            pass
    lm._subscribers.clear()


# ─────────────────────────────────────────────────────────────────────
# Session API (Get Session feature)
# ─────────────────────────────────────────────────────────────────────


class AddSessionJobsRequest(BaseModel):
    combos: str = Field(..., description="email|password|secret per line")


class SetSessionConfigRequest(BaseModel):
    max_concurrent: int | None = Field(default=None, ge=1, le=10)
    job_timeout: float | None = Field(default=None, ge=30, le=600)
    proxy: str | None = Field(
        default=None,
        description="HTTP/HTTPS proxy URL. Empty string = direct.",
    )


@app.get("/api/session/jobs")
async def list_session_jobs() -> JSONResponse:
    sm = get_session_manager()
    return JSONResponse({
        "max_concurrent": sm.max_concurrent,
        "job_timeout": sm.job_timeout,
        "proxy": sm.proxy,
        "jobs": sm.list_jobs(),
    })


@app.get("/api/session/jobs/{job_id}")
async def get_session_job(job_id: str) -> JSONResponse:
    sm = get_session_manager()
    data = sm.get_job(job_id)
    if data is None:
        raise HTTPException(404, "job not found")
    return JSONResponse(data)


@app.get("/api/session/jobs/{job_id}/log")
async def get_session_job_log(job_id: str) -> JSONResponse:
    sm = get_session_manager()
    if job_id not in sm.jobs:
        raise HTTPException(404, "job not found")
    return JSONResponse({"job_id": job_id, "log": sm.get_log(job_id)})


@app.post("/api/session/jobs")
async def add_session_jobs(payload: AddSessionJobsRequest) -> JSONResponse:
    combos = payload.combos.splitlines()
    sm = get_session_manager()
    jobs = sm.add_jobs(combos)
    return JSONResponse({"added": len(jobs), "jobs": [j.to_dict() for j in jobs]})


@app.post("/api/session/jobs/{job_id}/retry")
async def retry_session_job(job_id: str) -> JSONResponse:
    sm = get_session_manager()
    ok = sm.retry_job(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return JSONResponse({"ok": True})


@app.delete("/api/session/jobs/{job_id}")
async def delete_session_job(job_id: str) -> JSONResponse:
    sm = get_session_manager()
    ok = sm.remove_job(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return JSONResponse({"ok": True})


@app.post("/api/session/jobs/stop-all")
async def stop_all_session_jobs() -> JSONResponse:
    sm = get_session_manager()
    stopped = sm.stop_all()
    return JSONResponse({"stopped": stopped})


@app.post("/api/session/jobs/clear-finished")
async def clear_finished_session_jobs() -> JSONResponse:
    sm = get_session_manager()
    removed = sm.clear_finished()
    return JSONResponse({"removed": removed})


@app.post("/api/session/jobs/clear-all")
async def clear_all_session_jobs() -> JSONResponse:
    sm = get_session_manager()
    removed = sm.clear_all()
    return JSONResponse({"removed": removed})


@app.get("/api/session/config")
async def get_session_config() -> JSONResponse:
    sm = get_session_manager()
    return JSONResponse({
        "max_concurrent": sm.max_concurrent,
        "job_timeout": sm.job_timeout,
        "proxy": sm.proxy,
    })


@app.post("/api/session/config")
async def set_session_config(payload: SetSessionConfigRequest) -> JSONResponse:
    sm = get_session_manager()
    if payload.max_concurrent is not None:
        try:
            sm.set_max_concurrent(payload.max_concurrent)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if payload.job_timeout is not None:
        try:
            sm.set_job_timeout(payload.job_timeout)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if payload.proxy is not None:
        sm.set_proxy(payload.proxy)
    return JSONResponse({
        "max_concurrent": sm.max_concurrent,
        "job_timeout": sm.job_timeout,
        "proxy": sm.proxy,
    })


@app.get("/api/session/events")
async def session_events(request: Request) -> StreamingResponse:
    """SSE stream cho session jobs."""
    sm = get_session_manager()
    queue = sm.subscribe()

    async def gen():
        try:
            snapshot = {
                "type": "snapshot",
                "max_concurrent": sm.max_concurrent,
                "job_timeout": sm.job_timeout,
                "proxy": sm.proxy,
                "jobs": sm.list_jobs(),
            }
            yield f"data: {json.dumps(snapshot)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    break
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            sm.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Link API (Get Payment Link feature)
# ─────────────────────────────────────────────────────────────────────


class AddLinkJobsRequest(BaseModel):
    combos: str = Field(..., description="Input text — format depends on mode")
    mode: str = Field(default="combo", description="combo | session_json | access_token")
    region: str = Field(default="VN", description="Region: VN | ID | IN | US")


class SetLinkConfigRequest(BaseModel):
    max_concurrent: int | None = Field(default=None, ge=1, le=10)
    job_timeout: float | None = Field(default=None, ge=30, le=600)
    proxy: str | None = Field(
        default=None,
        description="HTTP/HTTPS proxy URL. Empty string = direct.",
    )
    region: str | None = Field(
        default=None,
        description="Region: VN | ID | IN | US",
    )


@app.post("/api/link/jobs")
async def add_link_jobs(payload: AddLinkJobsRequest) -> JSONResponse:
    mode = payload.mode
    if mode not in ("combo", "session_json", "access_token"):
        raise HTTPException(400, f"invalid mode: {mode}")
    region = payload.region.upper()
    from ..payment_link import REGION_BILLING
    if region not in REGION_BILLING:
        raise HTTPException(400, f"invalid region: {payload.region}. Must be one of: {list(REGION_BILLING.keys())}")
    lines = payload.combos.splitlines()
    lm = get_link_manager()
    jobs = lm.add_jobs(lines, mode=mode, region=region)  # type: ignore[arg-type]
    return JSONResponse({"added": len(jobs), "jobs": [j.to_dict() for j in jobs]})


@app.get("/api/link/jobs")
async def list_link_jobs() -> JSONResponse:
    lm = get_link_manager()
    return JSONResponse({
        "max_concurrent": lm.max_concurrent,
        "job_timeout": lm.job_timeout,
        "proxy": lm.proxy,
        "region": lm.region,
        "jobs": lm.list_jobs(),
    })


@app.get("/api/link/config")
async def get_link_config() -> JSONResponse:
    lm = get_link_manager()
    return JSONResponse({
        "max_concurrent": lm.max_concurrent,
        "job_timeout": lm.job_timeout,
        "proxy": lm.proxy,
        "region": lm.region,
    })


@app.post("/api/link/config")
async def set_link_config(payload: SetLinkConfigRequest) -> JSONResponse:
    lm = get_link_manager()
    if payload.max_concurrent is not None:
        try:
            lm.set_max_concurrent(payload.max_concurrent)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if payload.job_timeout is not None:
        try:
            lm.set_job_timeout(payload.job_timeout)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if payload.proxy is not None:
        lm.set_proxy(payload.proxy)
    if payload.region is not None:
        try:
            lm.set_region(payload.region.upper())
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return JSONResponse({
        "max_concurrent": lm.max_concurrent,
        "job_timeout": lm.job_timeout,
        "proxy": lm.proxy,
        "region": lm.region,
    })


@app.post("/api/link/jobs/stop-all")
async def stop_all_link_jobs() -> JSONResponse:
    lm = get_link_manager()
    cancelled = lm.stop_all()
    return JSONResponse({"cancelled": cancelled})


@app.post("/api/link/jobs/clear-finished")
async def clear_finished_link_jobs() -> JSONResponse:
    lm = get_link_manager()
    removed = lm.clear_finished()
    return JSONResponse({"removed": removed})


@app.post("/api/link/jobs/clear-all")
async def clear_all_link_jobs() -> JSONResponse:
    lm = get_link_manager()
    removed = lm.clear_all()
    return JSONResponse({"removed": removed})


@app.get("/api/link/jobs/{job_id}")
async def get_link_job(job_id: str) -> JSONResponse:
    lm = get_link_manager()
    data = lm.get_job(job_id)
    if data is None:
        raise HTTPException(404, "job not found")
    return JSONResponse(data)


@app.post("/api/link/jobs/{job_id}/retry")
async def retry_link_job(job_id: str) -> JSONResponse:
    lm = get_link_manager()
    job = lm.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "error":
        raise HTTPException(400, "job is not in error status")
    lm.retry_job(job_id)
    return JSONResponse({"ok": True})


@app.delete("/api/link/jobs/{job_id}")
async def delete_link_job(job_id: str) -> JSONResponse:
    lm = get_link_manager()
    ok = lm.remove_job(job_id)
    if not ok:
        raise HTTPException(404, "job not found")
    return JSONResponse({"ok": True})


@app.get("/api/link/events")
async def link_events(request: Request) -> StreamingResponse:
    """SSE stream cho link jobs."""
    lm = get_link_manager()
    queue = lm.subscribe()

    async def gen():
        try:
            snapshot = {
                "type": "snapshot",
                "max_concurrent": lm.max_concurrent,
                "job_timeout": lm.job_timeout,
                "proxy": lm.proxy,
                "region": lm.region,
                "jobs": lm.list_jobs(),
            }
            yield f"data: {json.dumps(snapshot)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    break
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            lm.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/link/jobs/{job_id}/upi-fill")
async def upi_fill_job(job_id: str) -> JSONResponse:
    """Mở browser, chọn UPI, điền billing info và click subscribe cho job India."""
    lm = get_link_manager()
    job = lm.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "success" or not job.payment_link:
        raise HTTPException(400, "job chưa có payment link")
    if job.region != "IN":
        raise HTTPException(400, "chỉ hỗ trợ UPI fill cho region IN (India)")

    result = await run_upi_automation(
        job.payment_link,
        proxy=lm.proxy,
        headless=True,
        log=lambda msg: lm._job_log(job, msg),
        job_id=job_id,
        email=job.email,
    )

    # Attach screenshot URLs to the job & broadcast update
    shots = result.get("screenshots") or []
    urls = [f"/upi-shots/{Path(p).name}" for p in shots]
    if urls:
        job.screenshot_urls = list(dict.fromkeys((job.screenshot_urls or []) + urls))
        lm._broadcast_job(job)
    result["screenshot_urls"] = urls
    return JSONResponse(result)


@app.post("/api/link/jobs/{job_id}/check-plus")
async def check_plus(job_id: str) -> JSONResponse:
    """Check xem account đã có ChatGPT Plus chưa, dùng access_token của job."""
    from curl_cffi.requests import AsyncSession as CurlSession

    lm = get_link_manager()
    job = lm.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    token = job._access_token
    if not token:
        raise HTTPException(422, "no access_token for this job")

    _proxy = lm.proxy
    _proxies = {"http": _proxy, "https": _proxy} if _proxy else None
    try:
        async with CurlSession(impersonate="chrome136", proxies=_proxies) as s:
            r = await s.get(
                "https://chatgpt.com/backend-api/me",
                headers={"Authorization": f"Bearer {token}", "Accept": "*/*"},
                timeout=15.0,
            )
        if r.status_code != 200:
            return JSONResponse({"is_plus": False, "error": f"HTTP {r.status_code}"})
        data = r.json()
        plan = data.get("plan_type") or ""
        is_plus = plan.lower() in ("plus", "team", "pro")
        if not is_plus:
            for org in (data.get("orgs") or {}).get("data", []):
                if org.get("plan_type", "").lower() in ("plus", "team", "pro"):
                    is_plus = True
                    plan = org["plan_type"]
                    break
        return JSONResponse({"is_plus": is_plus, "plan": plan, "email": data.get("email", "")})
    except Exception as e:
        return JSONResponse({"is_plus": False, "error": str(e)})


@app.post("/api/link/jobs/{job_id}/check-payment")
async def check_payment_link(job_id: str) -> JSONResponse:
    """Check payment: checkout_not_active_session = đã thanh toán xong."""
    from curl_cffi.requests import AsyncSession as CurlSession

    lm = get_link_manager()
    job = lm.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if not job.checkout_session_id or not job.publishable_key:
        raise HTTPException(422, "job missing checkout_session_id / publishable_key")

    url = f"https://api.stripe.com/v1/payment_pages/{job.checkout_session_id}/init"
    headers = {
        "accept": "application/json",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://pay.openai.com",
        "referer": "https://pay.openai.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    }
    data = {
        "key": job.publishable_key,
        "eid": "NA",
        "browser_locale": "vi-VN",
        "browser_timezone": "Asia/Saigon",
        "redirect_type": "url",
    }
    _proxy = lm.proxy
    _proxies = {"http": _proxy, "https": _proxy} if _proxy else None
    try:
        async with CurlSession(impersonate="chrome136", proxies=_proxies) as s:
            r = await s.post(url, headers=headers, data=data, timeout=15.0)
        body = r.json()
        err_code = (body.get("error") or {}).get("code") or ""
        if err_code == "checkout_not_active_session":
            return JSONResponse({"paid": True, "reason": "session_inactive"})
        pi_status = (body.get("payment_intent") or {}).get("status") or ""
        sub_status = (body.get("subscription") or {}).get("status") or ""
        paid = pi_status == "succeeded" or sub_status in ("active", "trialing")
        return JSONResponse({"paid": paid, "pi_status": pi_status, "sub_status": sub_status})
    except Exception as e:
        return JSONResponse({"paid": False, "error": str(e)})


class CheckAccountRequest(BaseModel):
    lines: str = Field(default="")
    max_concurrent: int = Field(default=5, ge=1, le=10)


@app.post("/api/check/run")
async def check_account_run(payload: CheckAccountRequest) -> JSONResponse:
    """Check Plus status cho nhiều account: input email|pass|2fa|Session per line."""
    lm = get_link_manager()
    lines = [ln for ln in (payload.lines or "").splitlines() if ln.strip()]
    if not lines:
        return JSONResponse({"results": []})

    results = await check_accounts(
        lines,
        proxy=lm.proxy,
        max_concurrent=payload.max_concurrent,
    )
    return JSONResponse({"results": [r.to_dict() for r in results]})


# ─────────────────────────────────────────────────────────────────────
# Check Payment
# ─────────────────────────────────────────────────────────────────────


@app.post("/api/check-payment")
async def check_payment_sessions(request: Request) -> JSONResponse:
    """Check sessions for payment status via OpenAI checkout + Stripe init APIs.

    Body: { sessions: [{token?, combo?, email?}], region: "VN" }
      - If `token` provided: use directly.
      - If `combo` provided (email|password|secret): login to obtain accessToken first,
        then run the same payment check flow.
    Flow per session:
      1. (combo only) Browser login → /api/auth/session → accessToken
      2. POST chatgpt.com checkout → checkout_session_id + publishable_key
      3. POST api.stripe.com init → full Stripe data (hosted_url + invoice)
      4. Parse amount_due / trial_period_days → is_free
    """
    import uuid as _uuid

    from ..payment_link import (
        _call_chatgpt_checkout,
        _replace_stripe_host,
        REGION_BILLING,
        DEFAULT_REGION,
        PaymentLinkError,
        SessionExpiredError,
        CloudflareBlockedError,
    )
    from ..session_phase import get_session, SessionError
    from ..config import env_insecure_tls
    from curl_cffi.requests import AsyncSession as CurlSession

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body")

    sessions_input: list[dict] = body.get("sessions") or []
    region: str = body.get("region") or DEFAULT_REGION
    if region not in REGION_BILLING:
        region = DEFAULT_REGION

    # Reuse SessionJobManager's proxy/headless config for login.
    sm = get_session_manager()
    login_proxy = sm.proxy
    login_headless = sm.headless
    login_timeout = sm.job_timeout

    results: list[dict] = []

    for item in sessions_input:
        token = (item.get("token") or "").strip()
        hint_email = (item.get("email") or "").strip()
        combo_raw = (item.get("combo") or "").strip()
        logs: list[str] = []

        # Combo mode: login first to obtain accessToken
        if not token and combo_raw:
            parts = [p.strip() for p in combo_raw.split("|")]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                results.append({
                    "email": hint_email or combo_raw[:60],
                    "error": "Combo format sai, cần: email|password|secret",
                    "logs": logs,
                })
                continue
            login_email = parts[0]
            login_pass = parts[1]
            login_secret = parts[2] if len(parts) >= 3 and parts[2] else None
            hint_email = hint_email or login_email

            def _log(msg: str, _logs=logs) -> None:
                _logs.append(msg)

            LOGIN_RETRY_MAX = 5
            session_data = None
            login_error: str | None = None
            proxy_label = _mask_proxy(login_proxy) if login_proxy else "none"
            for attempt in range(1, LOGIN_RETRY_MAX + 1):
                try:
                    _log(f"→ Login attempt {attempt}/{LOGIN_RETRY_MAX} for {login_email} (proxy={proxy_label})…")
                    session_data = await asyncio.wait_for(
                        get_session(
                            email=login_email,
                            password=login_pass,
                            secret=login_secret,
                            headless=login_headless,
                            proxy=login_proxy,
                            tls_insecure=env_insecure_tls(),
                            log=_log,
                        ),
                        timeout=login_timeout,
                    )
                    break
                except asyncio.TimeoutError:
                    login_error = f"Login timeout {login_timeout:.0f}s"
                    logs.append(f"✗ {login_error}")
                    break
                except SessionError as exc:
                    msg = str(exc)
                    retryable = ("browser launch" in msg.lower()) or ("driver" in msg.lower())
                    if attempt < LOGIN_RETRY_MAX and retryable:
                        logs.append(f"⚠ Browser/driver error (attempt {attempt}/{LOGIN_RETRY_MAX}): {exc} — retrying in 2s")
                        await asyncio.sleep(2)
                        continue
                    login_error = f"Login failed: {exc}"
                    logs.append(f"✗ {login_error}")
                    break
                except Exception as exc:
                    login_error = f"Login error: {type(exc).__name__}: {exc}"
                    logs.append(f"✗ {login_error}")
                    break

            if session_data is None:
                results.append({"email": hint_email, "error": login_error or "Login failed", "logs": logs})
                continue

            token = (session_data or {}).get("accessToken") or ""
            sd_email = ((session_data or {}).get("user") or {}).get("email") or ""
            if sd_email:
                hint_email = sd_email
            if not token:
                logs.append("✗ Login OK nhưng không lấy được accessToken")
                results.append({"email": hint_email, "error": "Login OK but no accessToken in session", "logs": logs})
                continue
            logs.append(f"✓ Got accessToken ({len(token)} chars)")

        if not token:
            results.append({"error": "Missing accessToken or combo", "email": hint_email, "logs": logs})
            continue

        _proxies = {"http": login_proxy, "https": login_proxy} if login_proxy else None
        try:
            async with CurlSession(impersonate="chrome136", proxies=_proxies) as curl_session:
                logs.append(f"→ Calling OpenAI checkout API (region={region})…")
                checkout = await _call_chatgpt_checkout(
                    curl_session, token, region=region, timeout=30.0,
                )
                logs.append(f"✓ Got checkout_session_id: {checkout.checkout_session_id[:24]}…")

                # Use checkout.url if already available (hosted mode returns it directly)
                payment_url = ""
                if checkout.url:
                    payment_url = _replace_stripe_host(checkout.url)
                    logs.append(f"✓ Payment URL from checkout response")

                # Call Stripe payment_pages init to get invoice/trial data
                logs.append("→ Calling Stripe payment_pages init…")
                stripe_js_id = str(_uuid.uuid4())
                stripe_init_url = f"https://api.stripe.com/v1/payment_pages/{checkout.checkout_session_id}/init"
                form_data = {
                    "browser_locale": "en-US",
                    "browser_timezone": "Asia/Saigon",
                    "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
                    "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
                    "elements_session_client[elements_init_source]": "custom_checkout",
                    "elements_session_client[referrer_host]": "chatgpt.com",
                    "elements_session_client[stripe_js_id]": stripe_js_id,
                    "elements_session_client[locale]": "en-US",
                    "elements_session_client[is_aggregation_expected]": "false",
                    "key": checkout.publishable_key,
                    "_stripe_version": "2025-03-31.basil; checkout_server_update_beta=v1; checkout_manual_approval_preview=v1",
                }
                stripe_resp = await curl_session.post(
                    stripe_init_url,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                        "Origin": "https://js.stripe.com",
                        "Referer": "https://js.stripe.com/",
                    },
                    data=form_data,
                    timeout=30.0,
                )

                if stripe_resp.status_code != 200:
                    err_body = stripe_resp.text[:400]
                    logs.append(f"✗ Stripe init HTTP {stripe_resp.status_code}: {err_body}")
                    # Still usable if we have payment_url from checkout
                    if payment_url:
                        logs.append("⚠ Using payment URL from checkout (Stripe data unavailable)")
                        results.append({
                            "email": hint_email,
                            "is_free": None,
                            "amount_due": -1,
                            "currency": "",
                            "trial_days": 0,
                            "product": "",
                            "payment_url": payment_url,
                            "session_id": checkout.checkout_session_id,
                            "error": f"Stripe init HTTP {stripe_resp.status_code} — trial status unknown",
                            "logs": logs,
                        })
                    else:
                        results.append({
                            "email": hint_email,
                            "error": f"Stripe init failed: HTTP {stripe_resp.status_code}",
                            "logs": logs,
                        })
                    continue

                stripe_data: dict = stripe_resp.json()
                logs.append("✓ Stripe init OK")

                # Use stripe_hosted_url if checkout.url was missing
                if not payment_url:
                    hosted = stripe_data.get("stripe_hosted_url") or ""
                    payment_url = _replace_stripe_host(hosted) if hosted else ""

                amount_due = (stripe_data.get("invoice") or {}).get("amount_due", -1)
                trial_days = (stripe_data.get("subscription_data") or {}).get("trial_period_days") or 0
                is_free = amount_due == 0 or trial_days > 0
                currency = stripe_data.get("currency", "").upper()
                email = (
                    (stripe_data.get("customer") or {}).get("email")
                    or stripe_data.get("customer_email")
                    or hint_email
                )
                lines_data = ((stripe_data.get("invoice") or {}).get("lines") or {}).get("data") or []
                product = lines_data[0].get("description", "") if lines_data else ""

                status_str = "FREE ✅" if is_free else f"PAID 💳 {amount_due / 100:.2f} {currency}"
                logs.append(f"→ Result: {status_str} | email={email} | product={product}")

                results.append({
                    "email": email,
                    "is_free": is_free,
                    "amount_due": amount_due,
                    "currency": currency,
                    "trial_days": trial_days,
                    "product": product,
                    "payment_url": payment_url,
                    "session_id": checkout.checkout_session_id,
                    "access_token": token,
                    "logs": logs,
                })

        except SessionExpiredError as e:
            logs.append(f"✗ Session expired: {e}")
            results.append({"email": hint_email, "error": f"Session expired — token invalid or revoked", "logs": logs})
        except CloudflareBlockedError as e:
            logs.append(f"✗ Cloudflare blocked: {e}")
            results.append({"email": hint_email, "error": "Cloudflare blocked the request", "logs": logs})
        except PaymentLinkError as e:
            logs.append(f"✗ {e}")
            results.append({"email": hint_email, "error": str(e), "logs": logs})
        except Exception as e:
            logs.append(f"✗ Unexpected error: {e}")
            results.append({"email": hint_email, "error": str(e), "logs": logs})

    return JSONResponse({"results": results})


# ─────────────────────────────────────────────────────────────────────
# Stripe payment polling
# ─────────────────────────────────────────────────────────────────────


@app.post("/api/stripe/poll-paid")
async def stripe_poll_paid(request: Request) -> JSONResponse:
    """Poll a Stripe checkout session for payment completion (UPI India).

    Body: { session_id, publishable_key }
    Returns: { paid: bool, status: str, subscription_status: str | null }
    """
    from ..payment_link import _call_stripe_init_full, _IMPERSONATE, StripeInitError
    from curl_cffi.requests import AsyncSession as CurlSession

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body")

    session_id: str = body.get("session_id") or ""
    publishable_key: str = body.get("publishable_key") or ""
    if not session_id or not publishable_key:
        raise HTTPException(status_code=422, detail="session_id and publishable_key required")

    _proxy = get_link_manager().proxy
    _proxies = {"http": _proxy, "https": _proxy} if _proxy else None
    try:
        async with CurlSession(impersonate=_IMPERSONATE, proxies=_proxies) as curl_session:
            data = await _call_stripe_init_full(
                curl_session, session_id, publishable_key, timeout=15.0,
            )
    except StripeInitError as e:
        return JSONResponse({"paid": False, "status": "error", "error": str(e)})
    except Exception as e:
        return JSONResponse({"paid": False, "status": "error", "error": str(e)})

    pi = data.get("payment_intent") or {}
    pi_status = pi.get("status") or ""
    sub = data.get("subscription") or {}
    sub_status = sub.get("status") or ""

    paid = pi_status == "succeeded" or sub_status == "active"
    return JSONResponse({
        "paid": paid,
        "pi_status": pi_status,
        "sub_status": sub_status,
    })


# ─────────────────────────────────────────────────────────────────────
# SMSBower API
# ─────────────────────────────────────────────────────────────────────

@app.get("/api/smsbower/balance")
async def get_smsbower_balance(api_key: str = "V7JuZljb0RQDEzawWc6IO4LPAV3x71vo") -> JSONResponse:
    """Get SMSBower account balance. Format: ACCESS_BALANCE:4.619"""
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://smsbower.page/stubs/handler_api.php",
                params={"api_key": api_key, "action": "getBalance"}
            )
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")

            # Parse plain text response: "ACCESS_BALANCE:4.619"
            text = resp.text.strip()
            if text.startswith("ACCESS_BALANCE:"):
                balance_str = text.split(":", 1)[1].strip()
                balance = float(balance_str)
                return JSONResponse({
                    "success": True,
                    "balance": balance,
                    "currency": "USD",
                })
            else:
                raise Exception(f"Unexpected response format: {text[:100]}")
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


# ─────────────────────────────────────────────────────────────────────
# UPI Watch Mode
# ─────────────────────────────────────────────────────────────────────

def _on_watch_job_action(job_id: str, action: str) -> None:
    """Callback from UpiWatchManager when Done/Fail is pressed in the browser."""
    lm = get_link_manager()
    job = lm.jobs.get(job_id)
    if job is None:
        return
    import time as _time
    if action == "done":
        # Mark the job as having succeeded — user confirmed payment in browser
        job.status = "success"
        job.finished_at = _time.time()
        if job.error:
            job.error = None
    elif action == "fail":
        job.status = "error"
        job.error = "Payment not completed (marked via Watch mode)"
        job.finished_at = _time.time()
    lm._broadcast_job(job)


_watch_manager = UpiWatchManager(on_job_action=_on_watch_job_action)


@app.post("/api/link/watch/start")
async def watch_start(request: Request) -> JSONResponse:
    """Start UPI Watch Mode for up to 3 India jobs.

    Body: { slots: [{ slot_idx: 0|1|2, job_id, email, payment_url,
                       publishable_key?, checkout_session_id? }] }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, "Invalid JSON body")
    configs: list[dict] = body.get("slots") or []
    if not configs:
        raise HTTPException(422, "slots list required")
    result = await _watch_manager.start(configs)
    return JSONResponse(result)


@app.get("/api/link/watch/status")
async def watch_status() -> JSONResponse:
    return JSONResponse(_watch_manager.get_status())


@app.post("/api/link/watch/slot/{slot_idx}/action")
async def watch_slot_action(slot_idx: int, request: Request) -> JSONResponse:
    """action: done | fail | off | reload"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, "Invalid JSON body")
    action = body.get("action") or ""
    if action not in ("done", "fail", "off", "reload"):
        raise HTTPException(422, "action must be done|fail|off|reload")
    await _watch_manager.slot_action(slot_idx, action)
    return JSONResponse({"ok": True})


@app.post("/api/link/watch/stop-all")
async def watch_stop_all() -> JSONResponse:
    await _watch_manager.stop_all()
    return JSONResponse({"ok": True})


@app.get("/api/link/watch/slot/{slot_idx}/screenshot")
async def watch_slot_screenshot(slot_idx: int):
    """Return the latest screenshot for a watch slot as image/png."""
    from fastapi.responses import FileResponse
    p = _watch_manager.get_screenshot_path(slot_idx)
    if p is None or not p.exists():
        raise HTTPException(404, "No screenshot yet")
    return FileResponse(str(p), media_type="image/png")


# ─────────────────────────────────────────────────────────────────────
# Change Password
# ─────────────────────────────────────────────────────────────────────


@app.get("/api/change-password/log/{request_id}")
async def change_password_log(request_id: str) -> JSONResponse:
    _cleanup_change_password_logs()
    entry = _change_password_logs.get(request_id) or {}
    return JSONResponse({
        "request_id": request_id,
        "logs": list(entry.get("logs") or []),
        "done": bool(entry.get("done")),
    })


@app.post("/api/change-password")
async def change_password_endpoint(request: Request) -> JSONResponse:
    """Change password for a ChatGPT account via browser automation.

    Body: { combo: "email|current_pass|2fa_secret", new_password: "...", request_id?: "..." }
    Returns: { success: bool, email: str, error?: str, logs: [...] }
    """
    from ..change_password_phase import change_password, ChangePasswordError

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body")

    combo_raw: str = (body.get("combo") or "").strip()
    new_password: str = (body.get("new_password") or "").strip()
    request_id: str = (body.get("request_id") or "").strip()[:120]
    if not combo_raw:
        raise HTTPException(status_code=422, detail="combo is required")
    if not new_password:
        raise HTTPException(status_code=422, detail="new_password is required")

    parts = [p.strip() for p in combo_raw.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise HTTPException(status_code=422, detail="combo must be email|password or email|password|2fa_secret")

    email = parts[0]
    current_password = parts[1]
    secret = parts[2] if len(parts) >= 3 and parts[2] else None

    # Use main JobManager for headless (controlled by global UI toggle) + proxy
    mgr = get_manager()
    logs: list[str] = []

    def _log(msg: str) -> None:
        logs.append(msg)
        _append_change_password_log(request_id, msg)

    try:
        new_session = await change_password(
            email=email,
            current_password=current_password,
            new_password=new_password,
            secret=secret,
            headless=mgr.headless,
            proxy=mgr.effective_proxy,
            log=_log,
        )
        _finish_change_password_log(request_id)
        return JSONResponse({
            "success": True,
            "email": email,
            "session": new_session,
            "new_session": new_session,
            "logs": logs,
        })
    except ChangePasswordError as exc:
        _finish_change_password_log(request_id)
        return JSONResponse({"success": False, "email": email, "error": str(exc), "logs": logs})
    except Exception as exc:
        _finish_change_password_log(request_id)
        return JSONResponse({"success": False, "email": email, "error": f"Unexpected: {exc}", "logs": logs})


# ─────────────────────────────────────────────────────────────────────
# Static UI
# ─────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = _STATIC_DIR / "index.html"
    html = html_path.read_text(encoding="utf-8").replace("__ASSET_VERSION__", _asset_version())
    return HTMLResponse(html)


# Mount static folder cho CSS/JS
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# Mount UPI screenshots
_UPI_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "runtime" / "upi_screenshots"
_UPI_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/upi-shots", StaticFiles(directory=_UPI_SCREENSHOT_DIR), name="upi_shots")
