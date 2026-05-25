"""CLI cho gpt_signup_hybrid — Get Link web UI."""
from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("web")
def web_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8083, "--port", help="Bind port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload (dev mode)."),
    unsafe_expose_network: bool = typer.Option(
        False,
        "--unsafe-expose-network",
        help="Cho phép bind non-loopback host (LAN/0.0.0.0).",
    ),
) -> None:
    """Start web UI server tại http://<host>:<port>/."""
    import logging
    import os
    import signal
    import sys
    import uvicorn

    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not is_loopback and not unsafe_expose_network:
        typer.echo(
            f"[web] refuse bind to non-loopback host {host!r}.\n"
            f"      Re-run với --unsafe-expose-network nếu bạn thật sự muốn.",
            err=True,
        )
        raise typer.Exit(2)

    logging.getLogger("uvicorn").setLevel(logging.CRITICAL)
    logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
    logging.getLogger("uvicorn.access").setLevel(logging.CRITICAL)
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)

    typer.echo(f"[web] starting at http://{host}:{port}/")
    typer.echo(f"[web] Ctrl+C to stop.\n")

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


@app.command("version", hidden=True)
def _version_cmd() -> None:
    """Print version."""
    typer.echo("gpt_signup_hybrid 1.0.0")


if __name__ == "__main__":
    app()
