"""Debug check: log response details from Microsoft OAuth."""
import asyncio
import json
from pathlib import Path

import httpx

TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
SCOPE = "https://graph.microsoft.com/.default offline_access"
POOL_FILE = Path("pool.txt")


def load_combos():
    combos = []
    for line in POOL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) == 4:
            combos.append({
                "email": parts[0].strip(),
                "refresh_token": parts[2].strip(),
                "client_id": parts[3].strip(),
            })
    return combos


async def main():
    combos = load_combos()
    async with httpx.AsyncClient() as client:
        for combo in combos[:3]:  # Test first 3 only
            print(f"\n=== {combo['email']} ===")
            print(f"Request token: {combo['refresh_token'][:40]}...")
            resp = await client.post(
                TOKEN_URL,
                data={
                    "client_id": combo["client_id"],
                    "scope": SCOPE,
                    "refresh_token": combo["refresh_token"],
                    "grant_type": "refresh_token",
                },
                timeout=15.0,
            )
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                new_rt = data.get("refresh_token", "")
                print(f"New token: {new_rt[:60]}..." if new_rt else "No new refresh_token")
            else:
                print(f"Body: {resp.text[:300]}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
