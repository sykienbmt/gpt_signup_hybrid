#!/usr/bin/env python3
"""Test SmsBowerDirectProvider integration."""

import asyncio
from mail_providers import build_provider_smsbower_direct, SmsBowerDirectProvider

async def test_smsbower_direct():
    """Test provider initialization and pre_check."""
    api_key = "V7JuZljb0RQDEzawWc6IO4LPAV3x71vo"

    def log(msg):
        print(f"  {msg}")

    # Test 1: Initialize provider
    print("Test 1: Initialize SmsBowerDirectProvider")
    provider = build_provider_smsbower_direct(
        api_key=api_key,
        service="dr",
        domain="gmail.com",
        max_price=0.05
    )
    print(f"✅ Provider created: service={provider.service}, domain={provider.domain}")

    # Test 2: Pre-check (verify price & stock)
    print("\nTest 2: Pre-check (check price & availability)")
    try:
        await provider.pre_check(log=log)
        print("✅ Pre-check passed")
    except Exception as e:
        print(f"❌ Pre-check failed: {e}")
        return

    # Test 3: Acquire email
    print("\nTest 3: Acquire email via getActivation")
    try:
        await provider.acquire_email(log=log)
        print(f"✅ Email acquired: {provider.email} (ID: {provider.mail_id})")
    except Exception as e:
        print(f"❌ Acquire failed: {e}")
        return

    # Test 4: Poll OTP (will timeout since not actually signup)
    print("\nTest 4: Poll OTP (will timeout after 15s)")
    try:
        from datetime import datetime, timezone
        otp = await asyncio.wait_for(
            provider.poll_otp(
                recipient="test@chatgpt.com",
                started_at=datetime.now(timezone.utc),
                timeout_seconds=15.0,
                poll_interval_seconds=5.0,
                log=log,
            ),
            timeout=20.0
        )
        print(f"✅ OTP received: {otp}")
    except asyncio.TimeoutError:
        print("✅ Timeout as expected (no actual signup)")
    except Exception as e:
        print(f"ℹ️  Poll exception (expected): {type(e).__name__}: {e}")

    print("\n=== Integration Test Complete ===")


if __name__ == "__main__":
    asyncio.run(test_smsbower_direct())
