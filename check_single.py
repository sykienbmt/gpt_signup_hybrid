# -*- coding: utf-8 -*-
"""Test Graph API mail listing + poll_otp flow."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from gpt_signup_hybrid.mail_providers import build_provider_outlook

STATE_DIR = Path("runtime/outlook_state")
COMBO = open("pool.txt").read().strip()


async def main():
    provider = build_provider_outlook(combo=COMBO, state_dir=STATE_DIR)
    log = lambda m: print(m)

    # Refresh access token
    print("=== Refresh access token ===")
    await provider._refresh_access(log=log)
    access = provider._access_token
    print(f"Access token: {access[:40]}...\n")

    # List messages
    print("=== List 10 latest messages ===")
    msgs = await provider._list_messages(access_token=access, folder_name=None, top=10)
    print(f"Found {len(msgs)} messages")
    for m in msgs:
        sender = (m.get("from") or {}).get("emailAddress", {}).get("address", "")
        subject = m.get("subject", "")
        received = m.get("receivedDateTime", "")
        body_preview = m.get("bodyPreview", "")[:80]
        print(f"  FROM: {sender}")
        print(f"  SUBJ: {subject}")
        print(f"  DATE: {received}")
        print(f"  PREV: {body_preview}")
        print()

    # Test poll_otp with short timeout
    print("=== Test poll_otp (20s timeout, expect TimeoutError) ===")
    started_at = datetime.now(timezone.utc)
    try:
        code = await provider.poll_otp(
            recipient=provider.combo.email,
            started_at=started_at,
            timeout_seconds=20.0,
            poll_interval_seconds=5.0,
            log=log,
        )
        print(f"OTP found: {code}")
    except TimeoutError as e:
        print(f"[OK] TimeoutError (expected if no OTP): {e}")
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
