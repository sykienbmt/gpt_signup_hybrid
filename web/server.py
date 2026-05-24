"""FastAPI server cho web UI gpt_signup_hybrid."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import get_token  # legacy — token auth disabled
from .manager import get_manager, get_session_manager, get_link_manager
from .mail_modes import get_registry, serialize_for_api
from .upi_automation import run_upi_automation
from .check_account import check_accounts


_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """Build a lightweight cache-busting token from static file mtimes."""
    latest_mtime = 0
    for path in _STATIC_DIR.glob("*"):
        if path.is_file():
            latest_mtime = max(latest_mtime, path.stat().st_mtime_ns)
    return str(latest_mtime or 1)


app = FastAPI(title="gpt_signup_hybrid web UI", version="0.1.0")


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
    post_reg_get_session: bool | None = None
    post_reg_get_link: bool | None = None


@app.get("/api/jobs")
async def list_jobs() -> JSONResponse:
    manager = get_manager()
    return JSONResponse({
        "max_concurrent": manager.max_concurrent,
        "headless": manager.headless,
        "debug": manager.debug,
        "job_timeout": manager.job_timeout,
        "proxy": manager.proxy,
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


@app.get("/api/config")
async def get_config() -> JSONResponse:
    manager = get_manager()
    return JSONResponse({
        "max_concurrent": manager.max_concurrent,
        "headless": manager.headless,
        "debug": manager.debug,
        "job_timeout": manager.job_timeout,
        "proxy": manager.proxy,
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
        manager.set_proxy(payload.proxy)
        # Lan proxy global sang Session + Link manager (single source of truth)
        get_session_manager().set_proxy(payload.proxy)
        get_link_manager().set_proxy(payload.proxy)
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
