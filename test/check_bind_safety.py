"""Smoke check: web_cmd phải refuse bind non-loopback nếu thiếu --unsafe-expose-network.

Test invoke web_cmd với typer.testing.CliRunner và assert exit code + stderr.
Patch uvicorn.run để không thật sự boot server.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from typer.testing import CliRunner  # noqa: E402

from gpt_signup_hybrid.cli import app  # noqa: E402


def expect(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        sys.exit(1)


def main() -> None:
    runner = CliRunner()

    # 1. Default 127.0.0.1 → uvicorn.run được gọi (allowed)
    with patch("uvicorn.run") as urun:
        result = runner.invoke(app, ["web", "--host", "127.0.0.1"])
        expect(
            urun.called,
            f"127.0.0.1 → uvicorn.run() invoked (called={urun.called}, exit={result.exit_code})",
        )

    # 2. 0.0.0.0 không có flag → exit 2, không boot uvicorn
    with patch("uvicorn.run") as urun:
        result = runner.invoke(app, ["web", "--host", "0.0.0.0"])
        expect(
            result.exit_code == 2 and not urun.called,
            f"0.0.0.0 no-flag → exit 2 + skip uvicorn (exit={result.exit_code}, called={urun.called})",
        )
        # CliRunner.output gộp stdout+stderr — match cả 2
        combined = (result.output or "") + (result.stderr or "" if hasattr(result, "stderr") else "")
        expect(
            "refuse" in combined.lower(),
            "output mentions refuse",
        )

    # 3. LAN IP không có flag → exit 2
    with patch("uvicorn.run") as urun:
        result = runner.invoke(app, ["web", "--host", "192.168.1.10"])
        expect(
            result.exit_code == 2 and not urun.called,
            f"LAN IP no-flag → exit 2 (exit={result.exit_code}, called={urun.called})",
        )

    # 4. 0.0.0.0 + --unsafe-expose-network → uvicorn được gọi
    with patch("uvicorn.run") as urun:
        result = runner.invoke(
            app, ["web", "--host", "0.0.0.0", "--unsafe-expose-network"],
        )
        expect(
            urun.called,
            f"0.0.0.0 + --unsafe → uvicorn invoked (called={urun.called}, exit={result.exit_code})",
        )

    print("OK — bind safety enforces loopback default")


if __name__ == "__main__":
    main()
