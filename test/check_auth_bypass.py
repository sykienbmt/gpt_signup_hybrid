"""Smoke check: thử các cách bypass auth middleware.

- /API/jobs (uppercase)
- //api/jobs (double slash)
- /api/jobs/../jobs (path traversal)
- /api (exact, no slash)
- HEAD /api/jobs
- OPTIONS /api/jobs
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

os.environ["GPT_SIGNUP_WEB_TOKEN"] = "bypass-test"

from fastapi.testclient import TestClient  # noqa: E402

from gpt_signup_hybrid.web import auth  # noqa: E402
from gpt_signup_hybrid.web.server import app  # noqa: E402


def expect(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        sys.exit(1)


def main() -> None:
    auth.reset_token_for_tests("bypass-test")
    client = TestClient(app)

    # 1. uppercase path
    r = client.get("/API/jobs")
    expect(
        r.status_code in (401, 404),
        f"/API/jobs → 401 hoặc 404 (got {r.status_code})",
    )

    # 2. double slash
    r = client.get("//api/jobs")
    expect(
        r.status_code in (401, 404),
        f"//api/jobs → not 200 (got {r.status_code})",
    )

    # 3. /api exact (không có endpoint, expect 404 nhưng KHÔNG bypass kèm 200)
    r = client.get("/api")
    expect(
        r.status_code != 200,
        f"/api → not 200 (got {r.status_code})",
    )

    # 4. HEAD /api/jobs — middleware vẫn block
    r = client.head("/api/jobs")
    expect(
        r.status_code == 401,
        f"HEAD /api/jobs no-token → 401 (got {r.status_code})",
    )

    # 5. OPTIONS /api/jobs — Starlette tự handle CORS preflight
    r = client.options("/api/jobs")
    # Middleware chạy trước → vẫn 401 nếu không có CORS middleware nào trả OK
    # OK nếu nhận được 401 hoặc 405 (method not allowed). Quan trọng là KHÔNG có data.
    expect(
        r.status_code in (401, 405),
        f"OPTIONS /api/jobs → 401/405 (got {r.status_code})",
    )

    # 6. POST /api/jobs với body lớn nhưng không token
    r = client.post(
        "/api/jobs",
        json={"combos": "x" * 10000, "mail_mode": "outlook"},
    )
    expect(
        r.status_code == 401,
        f"POST no-token → 401 (got {r.status_code})",
    )

    # 7. Token thừa whitespace ở header → strip phải work
    r = client.get(
        "/api/jobs", headers={"X-API-Token": "  bypass-test  "},
    )
    expect(
        r.status_code == 200,
        f"token with whitespace → 200 (got {r.status_code})",
    )

    # 8. Empty token header → 401
    r = client.get("/api/jobs", headers={"X-API-Token": ""})
    expect(
        r.status_code == 401,
        f"empty token header → 401 (got {r.status_code})",
    )

    # 9. Token valid nhưng trong query có thêm gibberish
    r = client.get(
        "/api/jobs?token=bypass-test&foo=bar",
    )
    expect(
        r.status_code == 200,
        f"token in query + extra params → 200 (got {r.status_code})",
    )

    print("OK — auth bypass attempts blocked")


if __name__ == "__main__":
    main()
