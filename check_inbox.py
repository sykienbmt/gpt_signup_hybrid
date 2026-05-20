"""Check inbox cua combo sau khi signup."""
import asyncio
import sys
from pathlib import Path
from gpt_signup_hybrid.mail_providers import build_provider_outlook

# Doc combo tu pool.txt
POOL_FILE = Path("pool.txt")
lines = [l.strip() for l in POOL_FILE.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]

EMAIL = sys.argv[1] if len(sys.argv) > 1 else None
if EMAIL:
    combo_str = next((l for l in lines if l.startswith(EMAIL)), None)
else:
    combo_str = lines[0]

if not combo_str:
    print(f"Combo not found for: {EMAIL}")
    sys.exit(1)

email = combo_str.split("|")[0]
print(f"Checking inbox: {email}")


async def main():
    provider = build_provider_outlook(combo=combo_str, state_dir=Path("runtime/outlook_state"))
    await provider._refresh_access(log=lambda m: print(f"[LOG] {m}"))
    print(f"Access token OK\n")

    msgs = await provider._list_messages(access_token=provider._access_token, folder_name=None, top=20)
    print(f"=== {len(msgs)} messages ===")
    for m in msgs:
        sender = (m.get("from") or {}).get("emailAddress", {}).get("address", "")
        subject = m.get("subject", "")
        received = m.get("receivedDateTime", "")
        preview = m.get("bodyPreview", "")[:100]
        print(f"  DATE: {received}")
        print(f"  FROM: {sender}")
        print(f"  SUBJ: {subject}")
        print(f"  PREV: {preview}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
