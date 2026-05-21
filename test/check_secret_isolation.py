"""Smoke check: /api/jobs (list) không leak password/secret/first_code/session_path.

Inject 1 Job giả vào singleton manager, gọi list endpoint, assert secrets không
nằm trong response. Sau đó gọi /api/jobs/secrets và assert có.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

os.environ["GPT_SIGNUP_WEB_TOKEN"] = "test-secret-iso"

from fastapi.testclient import TestClient  # noqa: E402

from gpt_signup_hybrid.web import auth  # noqa: E402
from gpt_signup_hybrid.web.manager import Job, get_manager  # noqa: E402
from gpt_signup_hybrid.web.server import app  # noqa: E402


SECRET_KEYS = ("password", "secret", "first_code", "session_path")


def expect(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        sys.exit(1)


def main() -> None:
    auth.reset_token_for_tests("test-secret-iso")

    manager = get_manager()
    jid = uuid.uuid4().hex[:12]
    fake = Job(
        id=jid,
        email="leak-check@example.com",
        combo="leak-check@example.com|p|r|c",
        mail_mode="outlook",
        status="success",
        password="SECRET_PASS",
        secret="SECRET_TOTP",
        first_code="123456",
        session_path="/tmp/should-not-leak.json",
        finished_at=time.time(),
    )
    manager.jobs[jid] = fake
    manager.order.append(jid)

    client = TestClient(app)
    headers = {"X-API-Token": "test-secret-iso"}

    # 1. /api/jobs list — phải KHÔNG có secrets
    r = client.get("/api/jobs", headers=headers)
    expect(r.status_code == 200, f"/api/jobs ok ({r.status_code})")
    body = r.json()
    job_dict = next((j for j in body["jobs"] if j["id"] == jid), None)
    expect(job_dict is not None, "job có trong list")
    for k in SECRET_KEYS:
        expect(k not in job_dict, f"/api/jobs list KHÔNG chứa key {k!r}")
    # has_* flags hiện diện
    expect(job_dict.get("has_password") is True, "has_password=True")
    expect(job_dict.get("has_secret") is True, "has_secret=True")

    # 2. /api/jobs/secrets — có đầy đủ
    r = client.get("/api/jobs/secrets", headers=headers)
    expect(r.status_code == 200, f"/api/jobs/secrets ok ({r.status_code})")
    secrets_map = r.json()["secrets"]
    expect(jid in secrets_map, "secrets map chứa job_id")
    sec = secrets_map[jid]
    expect(sec["password"] == "SECRET_PASS", "password đúng")
    expect(sec["secret"] == "SECRET_TOTP", "secret đúng")
    expect(sec["first_code"] == "123456", "first_code đúng")
    expect(sec["session_path"] == "/tmp/should-not-leak.json", "session_path đúng")

    # 3. /api/jobs/{id} detail — vẫn có (vì đó là detail có authz)
    r = client.get(f"/api/jobs/{jid}", headers=headers)
    expect(r.status_code == 200, f"/api/jobs/{jid} ok ({r.status_code})")
    detail = r.json()
    for k in SECRET_KEYS:
        expect(k in detail, f"detail có key {k}")

    # 4. /api/jobs/secrets vẫn cần token
    r = client.get("/api/jobs/secrets")
    expect(r.status_code == 401, f"/api/jobs/secrets no-token → 401 ({r.status_code})")

    # Cleanup
    manager.jobs.pop(jid, None)
    if jid in manager.order:
        manager.order.remove(jid)

    print("OK — secrets isolated from list response")


if __name__ == "__main__":
    main()
