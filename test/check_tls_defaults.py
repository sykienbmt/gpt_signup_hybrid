"""Smoke check: TLS-related defaults phải secure-by-default sau Lô A."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add parent (package root) to sys.path để import gpt_signup_hybrid
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from gpt_signup_hybrid.config import env_insecure_tls
from gpt_signup_hybrid.mail_providers import WorkerMailProvider
from gpt_signup_hybrid.models import SignupRequest


def expect(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        sys.exit(1)


def main() -> None:
    # 1. SignupRequest defaults — TLS phải secure
    req = SignupRequest(email="x@x.com")
    expect(req.tls_insecure is False, "SignupRequest.tls_insecure default False")
    expect(
        req.email_insecure_tls is False,
        "SignupRequest.email_insecure_tls default False",
    )

    # 2. WorkerMailProvider default secure
    p = WorkerMailProvider(logs_url="https://x.example/logs", api_key=None)
    expect(p.insecure_tls is False, "WorkerMailProvider default insecure_tls=False")

    # 3. env_insecure_tls — sạch khi env unset
    os.environ.pop("GPT_SIGNUP_INSECURE_TLS", None)
    expect(env_insecure_tls() is False, "env_insecure_tls() default False")

    # 4. env opt-in bật được
    os.environ["GPT_SIGNUP_INSECURE_TLS"] = "1"
    try:
        expect(env_insecure_tls() is True, "env_insecure_tls() True khi env=1")
    finally:
        os.environ.pop("GPT_SIGNUP_INSECURE_TLS", None)

    # 5. Các giá trị truthy chấp nhận được
    for raw in ("true", "yes", "on", "TRUE"):
        os.environ["GPT_SIGNUP_INSECURE_TLS"] = raw
        try:
            expect(env_insecure_tls() is True, f"env_insecure_tls() truthy='{raw}'")
        finally:
            os.environ.pop("GPT_SIGNUP_INSECURE_TLS", None)

    # 6. Các giá trị falsy phải = False
    for raw in ("0", "false", "no", "off", ""):
        os.environ["GPT_SIGNUP_INSECURE_TLS"] = raw
        try:
            expect(
                env_insecure_tls() is False, f"env_insecure_tls() falsy='{raw}'",
            )
        finally:
            os.environ.pop("GPT_SIGNUP_INSECURE_TLS", None)

    print("OK — TLS defaults are secure")


if __name__ == "__main__":
    main()
