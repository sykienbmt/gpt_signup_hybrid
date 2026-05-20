"""Quick check Outlook pool refresh tokens via Microsoft OAuth."""
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
                "line": line,
                "email": parts[0].strip(),
                "password": parts[1].strip(),
                "refresh_token": parts[2].strip(),
                "client_id": parts[3].strip(),
            })
    return combos


async def check_combo(combo, client):
    try:
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
        if resp.status_code == 200:
            return combo["email"], "LIVE", resp.status_code, ""
        body = resp.text[:200]
        if "invalid_grant" in body or "AADSTS" in body:
            return combo["email"], "DEAD", resp.status_code, body[:120]
        return combo["email"], "ERROR", resp.status_code, body[:120]
    except Exception as exc:
        return combo["email"], "TIMEOUT", 0, str(exc)[:120]


async def main():
    combos = load_combos()
    print(f"Checking {len(combos)} combos...\n")

    async with httpx.AsyncClient() as client:
        tasks = [check_combo(c, client) for c in combos]
        results = await asyncio.gather(*tasks)

    live = dead = err = 0
    for email, status, code, detail in results:
        icon = "[LIVE]" if status == "LIVE" else "[DEAD]" if status == "DEAD" else "[ERR] "
        print(f"{icon} {email:<45} | {status:<7} | HTTP {code} | {detail}")
        if status == "LIVE":
            live += 1
        elif status == "DEAD":
            dead += 1
        else:
            err += 1

    print(f"\n--- Summary ---")
    print(f"LIVE:   {live}")
    print(f"DEAD:   {dead}")
    print(f"ERROR:  {err}")


if __name__ == "__main__":
    asyncio.run(main())
