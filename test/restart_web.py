"""Restart local gpt_signup_hybrid web server on 127.0.0.1:8083."""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8083


def find_web_pids() -> list[int]:
    cmd = [
        "wmic",
        "process",
        "where",
        "CommandLine like '%gpt_signup_hybrid web%'",
        "get",
        "ProcessId",
        "/value",
    ]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        return []
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("ProcessId="):
            continue
        value = line.split("=", 1)[1].strip()
        if value.isdigit():
            pids.append(int(value))
    return pids


def stop_old_servers() -> list[int]:
    pids = find_web_pids()
    current = subprocess.run(
        ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId", "/value"],
        text=True,
        capture_output=True,
        check=False,
    )
    _ = current
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], text=True, capture_output=True, check=False)
    return pids


def start_server() -> subprocess.Popen:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "gpt_signup_hybrid",
            "web",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=str(ROOT),
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_http() -> int:
    url = f"http://127.0.0.1:{PORT}/"
    deadline = time.monotonic() + 15.0
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return int(resp.status)
        except Exception as exc:
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"server did not become ready: {last_error}")


def main() -> int:
    stopped = stop_old_servers()
    time.sleep(1.0)
    proc = start_server()
    status = wait_http()
    print(f"STOPPED={','.join(map(str, stopped)) if stopped else 'none'}")
    print(f"NEW_PID={proc.pid}")
    print(f"HTTP={status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
