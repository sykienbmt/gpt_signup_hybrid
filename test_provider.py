"""Test OutlookMailProvider directly."""
import asyncio
from pathlib import Path
from gpt_signup_hybrid.mail_providers import build_provider_outlook

STATE_DIR = Path("runtime/outlook_state")
COMBO = "otisjaydenolwen1621@outlook.com|eUgf2xHhfu|M.C519_BAY.0.U.-Cn3JPISLqH6jPyMQUSTna!WZH2UUKrSG5lFJwJBq7A*7TMteWifTEMuEPG!1xlNUj1PiEgFCA7yLVHM4FDaM7a7K5P2xyCQu0SY6W4lpIqAn2x2Qrf*iYmSPtclpgcj3wWU7WaybNSNWyGhyA9h2pUpqfYWMO!77FaNV0QNgUzEAAjZW529yMQE*OwXq16VvLdnMMgQErqtPod0QrGI4MZc9xA6C*RL2s4b96pA6W6mPtfvNdvx8EaGJqVNv2qZX!1Ps3AFFtvZSW1yoPh3pk2vqsZhJqwGFBbj0Oz12rcAznsPEUPUcRo4195KgM9*tTJcpm9As*fOakqZimeE1kDceW9GU40!WXTeAn0vltEMbRw1sGLUvU1a!8VUw!2mNYw$$|9e5f94bc-e8a4-4e73-b8be-63364c29d753"


async def main():
    provider = build_provider_outlook(combo=COMBO, state_dir=STATE_DIR)
    print(f"Combo email: {provider.combo.email}")
    print(f"Token from pool: {provider.combo.refresh_token[:50]}...")
    try:
        await provider._refresh_access(log=lambda m: print(f"[LOG] {m}"))
        print("SUCCESS! Access token obtained.")
    except Exception as exc:
        print(f"ERROR: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
