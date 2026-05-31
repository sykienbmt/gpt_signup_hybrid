"""Verify exported email|pass|2fa|session lines are accepted by parsers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from gpt_signup_hybrid.web.check_account import parse_line  # noqa: E402
from gpt_signup_hybrid.web.manager import LinkJobManager  # noqa: E402


def main() -> int:
    session = {
        "user": {"email": "user@example.com", "id": "user-1"},
        "accessToken": "eyJ.header.payload.signature",
        "sessionToken": "session-token",
    }
    line = f"user@example.com|new-pass|TOTPSECRET|{json.dumps(session, separators=(',', ':'))}"

    email, token = parse_line(line)
    assert email == "user@example.com"
    assert token == "eyJ.header.payload.signature"

    manager = LinkJobManager(max_concurrent=1)
    session_jobs = manager._parse_session_json([line], set(), "VN")
    assert len(session_jobs) == 1
    assert session_jobs[0].email == "user@example.com"
    assert session_jobs[0]._access_token == "eyJ.header.payload.signature"

    token_jobs = manager._parse_access_token([line], set(), "VN")
    assert len(token_jobs) == 1
    assert token_jobs[0].email == "user@example.com"
    assert token_jobs[0]._access_token == "eyJ.header.payload.signature"

    print("OK exported session parse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
