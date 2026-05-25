"""FastAPI server cho web UI gpt_signup_hybrid — Get Link only."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .manager import get_link_manager
from .upi_automation import run_upi_automation


_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _asset_version() -> str:
    """Build a lightweight cache-busting token from static file mtimes."""
    latest_mtime = 0
    for path in _STATIC_DIR.glob("*"):
        if path.is_file():
            latest_mtime = max(latest_mtime, path.stat().st_mtime_ns)
    return str(latest_mtime or 1)


app = FastAPI(title="GSH · Get Link", version="1.0.0")


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
    """Verify proxy connectivity."""
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
    except Exception as exc:  # noqa: BLE001
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
    headless: bool | None = Field(default=None)


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
        "headless": lm.headless,
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
    if payload.headless is not None:
        lm.set_headless(payload.headless)
    return JSONResponse({
        "max_concurrent": lm.max_concurrent,
        "job_timeout": lm.job_timeout,
        "proxy": lm.proxy,
        "region": lm.region,
        "headless": lm.headless,
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
        headless=lm.headless,
        log=lambda msg: lm._job_log(job, msg),
        job_id=job_id,
        email=job.email,
    )

    shots = result.get("screenshots") or []
    urls = [f"/upi-shots/{Path(p).name}" for p in shots]
    if urls:
        job.screenshot_urls = list(dict.fromkeys((job.screenshot_urls or []) + urls))
        lm._broadcast_job(job)
    result["screenshot_urls"] = urls
    return JSONResponse(result)


@app.on_event("shutdown")
async def on_shutdown():
    """Force close tất cả SSE subscriber queues khi server shutdown."""
    lm = get_link_manager()
    for q in list(lm._subscribers):
        try:
            q.put_nowait(None)
        except Exception:
            pass
    lm._subscribers.clear()


# ─────────────────────────────────────────────────────────────────────
# Static UI
# ─────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    html_path = _STATIC_DIR / "index.html"
    html = html_path.read_text(encoding="utf-8").replace("__ASSET_VERSION__", _asset_version())
    return HTMLResponse(html)


if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_UPI_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "runtime" / "upi_screenshots"
_UPI_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/upi-shots", StaticFiles(directory=_UPI_SCREENSHOT_DIR), name="upi_shots")
