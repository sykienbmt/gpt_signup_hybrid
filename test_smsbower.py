#!/usr/bin/env python3
"""Test SMSBower mail flow: acquire email → poll OTP"""

import requests
import time
import sys

API_KEY = "V7JuZljb0RQDEzawWc6IO4LPAV3x71vo"
BASE_URL = "https://smsbower.page/api/mail"

def get_activation():
    """Acquire 1 email for ChatGPT signup"""
    resp = requests.get(f"{BASE_URL}/getActivation", params={
        "api_key": API_KEY,
        "service": "dr",  # ChatGPT
        "domain": "gmail.com"
    }).json()

    if resp["status"] != 1:
        print(f"❌ Failed to acquire email: {resp}")
        return None

    mail = resp["mail"]
    mail_id = resp["mailId"]
    print(f"✅ Acquired email: {mail} (ID: {mail_id})")
    return mail, mail_id

def poll_otp(mail_id, max_wait=120, poll_interval=5):
    """Poll for OTP code, wait up to max_wait seconds"""
    print(f"\n⏳ Polling OTP for mailId={mail_id} (max wait: {max_wait}s, interval: {poll_interval}s)...")

    start = time.time()
    attempt = 0

    while time.time() - start < max_wait:
        attempt += 1
        resp = requests.get(f"{BASE_URL}/getCode", params={
            "api_key": API_KEY,
            "mailId": mail_id
        }).json()

        elapsed = int(time.time() - start)

        if resp["status"] == 1:
            otp = resp.get("code", "")
            print(f"🎉 OTP RECEIVED at {elapsed}s (attempt #{attempt}): {otp}")
            return otp

        error = resp.get("error", "Unknown error")
        print(f"  [{elapsed}s] Attempt #{attempt}: {error}")

        time.sleep(poll_interval)

    print(f"❌ Timeout after {max_wait}s — no OTP received")
    return None

def cancel_activation(mail_id):
    """Cancel activation to refund money"""
    resp = requests.get(f"{BASE_URL}/setStatus", params={
        "api_key": API_KEY,
        "id": mail_id,
        "status": 2  # Cancel
    }).json()

    if resp["status"] == 1:
        print(f"✅ Activation #{mail_id} cancelled (refunded)")
        return True
    else:
        print(f"❌ Failed to cancel: {resp}")
        return False

if __name__ == "__main__":
    print("=== SMSBower Mail Test ===\n")

    # Step 1: Acquire email
    result = get_activation()
    if not result:
        sys.exit(1)

    mail, mail_id = result

    # Step 2: Poll OTP (without actually signing up — will timeout)
    # In real flow, you'd signup here with Camoufox/browser
    print(f"\n📧 Email ready for signup: {mail}")
    print("   (Note: Not doing real signup — just testing OTP polling)")

    otp = poll_otp(mail_id, max_wait=120, poll_interval=5)

    # Step 3: If timeout, cancel to refund
    if not otp:
        print("\n🔄 Cancelling activation due to timeout...")
        cancel_activation(mail_id)

    print("\n=== Test Complete ===")
