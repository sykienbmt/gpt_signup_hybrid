"""CLI cho gpt_signup_hybrid.

Usage:
    .venv/bin/python -m gpt_signup_hybrid signup --email foo@icloud.com
    .venv/bin/python -m gpt_signup_hybrid signup --email foo@icloud.com \
        --logs-url https://icloud-cf-mail.n5pskgzs9g.workers.dev/logs \
        --api-key 12345678@ \
        --name "John Doe" --birthdate 1995-03-15
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import typer

from .config import load_settings, runtime_session_dir
from .models import SignupRequest, SignupResult
from .signup import run_signup

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _emit_log(prefix: str | None = None):
    def log(msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        head = f"[{ts}]"
        if prefix:
            head += f"[{prefix}]"
        typer.echo(f"{head} {msg}")
    return log


@app.command("pool-status")
def pool_status_cmd(
    pool_file: Path = typer.Argument(..., help="Path tới pool file."),
) -> None:
    """In tóm tắt pool: bao nhiêu combo đã used / available / terminal error."""
    settings = load_settings()
    from .outlook_pool import OutlookPoolError, parse_pool_file, status_summary

    pool_path = Path(pool_file)
    if not pool_path.is_absolute():
        pool_path = settings.root_dir / pool_path

    try:
        pool = parse_pool_file(pool_path)
    except OutlookPoolError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    state_dir = settings.runtime_dir / "outlook_state"
    summary = status_summary(pool, state_dir=state_dir)
    typer.echo(json.dumps({"pool": str(pool_path), **summary}, indent=2))


@app.command("totp")
def totp_cmd(
    secret: str = typer.Argument(..., help="Base32 secret từ /mfa/enroll. VD: B2P3OQCCXINLHGPUDIS55DHQDW5MENK5"),
    account: str | None = typer.Option(None, "--account", help="Email account để in provisioning URI (tuỳ chọn)."),
) -> None:
    """Gen 6-digit TOTP code từ secret base32."""
    from .totp_helper import TotpError, generate_code, provisioning_uri, time_remaining

    try:
        code = generate_code(secret)
    except TotpError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    out: dict[str, str | int] = {
        "code": code,
        "valid_for_seconds": time_remaining(),
    }
    if account:
        out["provisioning_uri"] = provisioning_uri(secret, account=account)
    typer.echo(json.dumps(out, indent=2, ensure_ascii=False))


@app.command("enable-2fa")
def enable_2fa_cmd(
    session_file: Path = typer.Option(..., "--session-file", "-f", help="SignupResult JSON file (chứa access_token)."),
    activate: bool = typer.Option(True, "--activate/--enroll-only", help="Activate luôn (gen+verify code) hay chỉ enroll lấy secret."),
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP/HTTPS proxy."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Lưu kết quả 2FA. Default: <session-file>.2fa.json"),
) -> None:
    """Enable 2FA TOTP cho account đã đăng ký. Cần access_token từ SignupResult.

    Output gồm secret base32, provisioning_uri (cho Authenticator), first_code,
    factor_id, session_id, mfa_info.
    """
    import asyncio as _asyncio
    from .mfa_phase import MfaError, enable_2fa

    settings = load_settings()
    sf_path = Path(session_file)
    if not sf_path.is_absolute():
        sf_path = settings.root_dir / sf_path
    if not sf_path.exists():
        typer.echo(f"Error: session file not found: {sf_path}", err=True)
        raise typer.Exit(1)

    try:
        sdata = json.loads(sf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        typer.echo(f"Error: invalid JSON in {sf_path}: {exc}", err=True)
        raise typer.Exit(1)

    access_token = sdata.get("access_token")
    if not access_token:
        typer.echo(f"Error: session file missing access_token", err=True)
        raise typer.Exit(1)

    user_agent = sdata.get("user_agent") or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0"
    )

    log = _emit_log(prefix="2fa")
    try:
        result = _asyncio.run(enable_2fa(
            access_token=access_token,
            user_agent=user_agent,
            proxy=proxy,
            activate=activate,
            log=log,
        ))
    except MfaError as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)

    # Tạo output: copy session data + thêm 2fa
    out_data = {
        "email": sdata.get("email"),
        "user_id": sdata.get("user_id"),
        "account_id": sdata.get("account_id"),
        "two_factor": result,
    }

    if output is None:
        output = sf_path.with_suffix(".2fa.json")
    else:
        output = Path(output)
        if not output.is_absolute():
            output = settings.root_dir / output

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out_data, indent=2, ensure_ascii=False), encoding="utf-8")

    typer.echo(json.dumps({
        "email": out_data["email"],
        "secret": result["secret"],
        "first_code": result["first_code"],
        "activated": result["activated"],
        "provisioning_uri": result["provisioning_uri"],
        "output": str(output),
    }, indent=2, ensure_ascii=False))


# Workaround: Typer thu gọn invoke khi chỉ có 1 command. Đăng ký một no-op
# command thứ hai để giữ form `python -m gpt_signup_hybrid signup ...`.
@app.command("version", hidden=True)
def _version_cmd() -> None:
    """Print package version (hidden helper)."""
    typer.echo("gpt_signup_hybrid 0.1.0")


@app.command("web")
def web_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8089, "--port", help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload (dev mode)."),
) -> None:
    """Start web UI server tại http://<host>:<port>/.

    Web UI: textarea paste combo, list jobs, log panel, success/error output,
    mode single (1 job) / multi (max 3 song song).
    """
    import logging
    import os
    import signal
    import sys
    import uvicorn

    # Suppress ALL uvicorn/asyncio noise
    logging.getLogger("uvicorn").setLevel(logging.CRITICAL)
    logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
    logging.getLogger("uvicorn.access").setLevel(logging.CRITICAL)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    typer.echo(f"[web] starting at http://{host}:{port}/")
    typer.echo(f"[web] Ctrl+C to stop.\n")

    # Monkey-patch: khi nhận SIGINT, suppress stderr rồi exit clean
    _original_stderr = sys.stderr

    def _quiet_shutdown(signum, frame):
        sys.stderr = open(os.devnull, "w")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _quiet_shutdown)

    try:
        uvicorn.run(
            "gpt_signup_hybrid.web.server:app",
            host=host,
            port=port,
            reload=reload,
            log_level="critical",
            timeout_graceful_shutdown=1,
        )
    except SystemExit:
        pass
    except Exception:
        pass
    finally:
        sys.stderr = _original_stderr
    typer.echo("\n[web] stopped.")


@app.command("signup")
def signup_cmd(
    email: str | None = typer.Option(
        None, "--email",
        help="Email đăng ký. Auto-derive từ --outlook-combo nếu không truyền.",
    ),
    name: str = typer.Option("ChatGPT User", "--name", help="Tên hiển thị."),
    birthdate: str = typer.Option("2000-01-01", "--birthdate", help="YYYY-MM-DD, tuổi >= 13."),
    source_email: str | None = typer.Option(
        None, "--smail",
        help="Mailbox poll OTP (nếu khác email form).",
    ),
    # Provider selection
    mail_provider: str | None = typer.Option(
        None, "--mail-provider",
        help="'worker' hoặc 'outlook'. Auto-detect: outlook nếu có --outlook-combo, ngược lại worker.",
    ),
    # Worker provider opts
    logs_url: str = typer.Option(
        "https://icloud-cf-mail.n5pskgzs9g.workers.dev/logs",
        "--logs-url",
        help="[worker] Worker logs URL.",
    ),
    api_key: str = typer.Option("12345678@", "--api-key", help="[worker] Bearer cho Worker."),
    insecure_tls: bool = typer.Option(True, "--insecure-tls/--secure-tls", help="[worker] Bỏ verify TLS."),
    # Outlook provider opts
    outlook_combo: str | None = typer.Option(
        None, "--outlook-combo",
        help="[outlook] Combo `email|password|refresh_token|client_id`.",
    ),
    outlook_combo_file: Path | None = typer.Option(
        None, "--outlook-combo-file",
        help="[outlook] File chứa combo (1 dòng), tránh leak combo qua shell history.",
    ),
    outlook_pool: Path | None = typer.Option(
        None, "--outlook-pool",
        help="[outlook] File pool nhiều combo (mỗi dòng 1 combo). Tự pick combo còn khả dụng.",
    ),
    # Browser opts
    headless: bool = typer.Option(False, "--headless/--headed"),
    off_font: bool = typer.Option(False, "--off-font", help="Tắt camoufox font randomization."),
    profile_template: bool = typer.Option(True, "--profile-template/--fresh-profile"),
    proxy: str | None = typer.Option(None, "--proxy", help="HTTP/HTTPS proxy."),
    # Timing
    otp_timeout: float = typer.Option(180.0, "--otp-timeout", min=10),
    otp_interval: float = typer.Option(4.0, "--otp-interval", min=0.5),
    sentinel_timeout: float = typer.Option(30.0, "--sentinel-timeout", min=5),
    har_capture: bool = typer.Option(False, "--har/--no-har", help="Bật HAR capture Phase 1 cho debug."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Lưu SignupResult ra JSON file."),
) -> None:
    """Chạy 1 lần signup hybrid."""
    settings = load_settings()

    # Resolve combo từ file nếu cần
    if outlook_combo_file is not None:
        combo_path = Path(outlook_combo_file)
        if not combo_path.is_absolute():
            combo_path = settings.root_dir / combo_path
        if not combo_path.exists():
            typer.echo(f"Error: combo file not found: {combo_path}", err=True)
            raise typer.Exit(1)
        outlook_combo = combo_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()

    # Resolve từ pool — tự pick combo còn khả dụng
    if outlook_pool is not None:
        pool_path = Path(outlook_pool)
        if not pool_path.is_absolute():
            pool_path = settings.root_dir / pool_path
        from .outlook_pool import (
            OutlookPoolError,
            parse_pool_file,
            pick_first_available,
            status_summary,
        )

        try:
            pool = parse_pool_file(pool_path)
        except OutlookPoolError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        state_dir = settings.runtime_dir / "outlook_state"
        summary = status_summary(pool, state_dir=state_dir)
        typer.echo(
            f"[pool] {pool_path}: total={summary['total']} "
            f"used={summary['used_for_signup']} "
            f"available={summary['available']} "
            f"terminal_error={summary['terminal_error']}"
        )

        try:
            picked = pick_first_available(
                pool, state_dir=state_dir, log=lambda m: typer.echo(m),
            )
        except OutlookPoolError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

        outlook_combo = "|".join((
            picked.email, picked.password, picked.refresh_token, picked.client_id,
        ))

    # Auto-detect provider
    resolved_provider = mail_provider
    if resolved_provider is None:
        resolved_provider = "outlook" if outlook_combo else "worker"

    # Auto-derive email từ outlook combo nếu không truyền --email
    if resolved_provider == "outlook" and outlook_combo and not email:
        first_part = outlook_combo.split("|", 1)[0].strip()
        if "@" in first_part:
            email = first_part
            typer.echo(f"[cli] auto email={email} (từ outlook combo)")

    if not email:
        typer.echo(
            "Error: --email is required (hoặc --outlook-combo / --outlook-pool).",
            err=True,
        )
        raise typer.Exit(1)

    request = SignupRequest(
        email=email,
        name=name,
        birthdate=birthdate,
        source_email=source_email,
        mail_provider=resolved_provider,
        email_logs_url=logs_url,
        email_api_key=api_key,
        email_insecure_tls=insecure_tls,
        outlook_combo=outlook_combo,
        headless=headless,
        off_font=off_font,
        profile_template=profile_template,
        proxy=proxy,
        otp_timeout_seconds=otp_timeout,
        otp_poll_interval_seconds=otp_interval,
        sentinel_cookie_timeout_seconds=sentinel_timeout,
        har_capture=har_capture,
    )

    log = _emit_log()
    result: SignupResult = asyncio.run(run_signup(request, log=log))

    # Cập nhật pool state cho combo đã dùng
    if resolved_provider == "outlook" and outlook_combo:
        from .outlook_pool import mark_signup_failure, mark_signup_success
        state_dir = settings.runtime_dir / "outlook_state"
        if result.success:
            mark_signup_success(state_dir=state_dir, email=email)
            typer.echo(f"[pool] marked {email} as used_for_signup=true")
        else:
            mark_signup_failure(state_dir=state_dir, email=email, error=result.error or "unknown")
            typer.echo(f"[pool] recorded failure for {email}: {(result.error or '')[:80]}")

    if output is None:
        output = runtime_session_dir(settings) / f"signup-{datetime.now():%Y%m%d-%H%M%S}-{email.replace('@','_at_')}.json"
    else:
        output = Path(output)
        if not output.is_absolute():
            output = settings.root_dir / output
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = result.model_dump()
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {k: v for k, v in payload.items() if k not in ("cookies", "session_token", "access_token")}
    summary["session_token_len"] = len(result.session_token or "")
    summary["access_token_len"] = len(result.access_token or "")
    summary["cookies_count"] = len(result.cookies or [])
    summary["output"] = str(output)

    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
    if not result.success:
        sys.exit(1)


if __name__ == "__main__":
    app()
