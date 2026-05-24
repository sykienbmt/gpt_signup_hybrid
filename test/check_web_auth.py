"""Smoke check: web auth middleware.

- /api/* không token → 401
- /api/* sai token → 401
- /api/* đúng token (header / query / cookie) → 200
- / (HTML) không cần token
- /static/* không cần token
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

# Force token cố định trước khi import server (singleton lazy init)
os.environ["GPT_SIGNUP_WEB_TOKEN"] = "test-token-fixed"

from fastapi.testclient import TestClient  # noqa: E402

from gpt_signup_hybrid.web import auth  # noqa: E402
from gpt_signup_hybrid.web.server import app  # noqa: E402


def expect(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        sys.exit(1)


def main() -> None:
    auth.reset_token_for_tests("test-token-fixed")
    client = TestClient(app)

    # 1. Không token → 401
    r = client.get("/api/jobs")
    expect(r.status_code == 401, f"/api/jobs no-token → 401 (got {r.status_code})")

    # 2. Sai token → 401
    r = client.get("/api/jobs", headers={"X-API-Token": "wrong"})
    expect(r.status_code == 401, f"/api/jobs wrong-token → 401 (got {r.status_code})")

    # 3. Đúng token via header → 200
    r = client.get("/api/jobs", headers={"X-API-Token": "test-token-fixed"})
    expect(r.status_code == 200, f"/api/jobs header-token → 200 (got {r.status_code})")

    # 4. Đúng token via query → 200
    r = client.get("/api/jobs?token=test-token-fixed")
    expect(r.status_code == 200, f"/api/jobs query-token → 200 (got {r.status_code})")

    # 5. Đúng token via cookie → 200
    client.cookies.set("gsh_token", "test-token-fixed")
    r = client.get("/api/jobs")
    expect(r.status_code == 200, f"/api/jobs cookie-token → 200 (got {r.status_code})")
    client.cookies.clear()

    # 6. HTML index không cần token
    r = client.get("/")
    expect(r.status_code == 200, f"/ no-token → 200 (got {r.status_code})")
    expect(
        "<!DOCTYPE html>" in r.text or "<html" in r.text,
        "/ trả về HTML",
    )

    # 7. /api/mail-modes (route public-ish) vẫn cần token
    r = client.get("/api/mail-modes")
    expect(
        r.status_code == 401,
        f"/api/mail-modes no-token → 401 (got {r.status_code})",
    )
    r = client.get("/api/mail-modes", headers={"X-API-Token": "test-token-fixed"})
    expect(
        r.status_code == 200,
        f"/api/mail-modes auth → 200 (got {r.status_code})",
    )

    # 8. /api/events SSE — middleware vẫn block khi sai token
    with client.stream("GET", "/api/events") as resp:
        expect(
            resp.status_code == 401,
            f"/api/events no-token → 401 (got {resp.status_code})",
        )

    # 9. Token sinh ngẫu nhiên khác token cũ khi reset(None) + env pop
    os.environ.pop("GPT_SIGNUP_WEB_TOKEN", None)
    auth.reset_token_for_tests(None)
    new_tok = auth.get_token()
    expect(new_tok != "test-token-fixed", "reset_token_for_tests(None) → token mới")
    expect(len(new_tok) >= 24, f"token length >= 24 (got {len(new_tok)})")

    print("OK — web auth middleware works")


if __name__ == "__main__":
    main()
