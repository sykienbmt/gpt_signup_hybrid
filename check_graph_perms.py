"""Debug Graph API permissions va folders."""
import asyncio
import httpx
from pathlib import Path
from gpt_signup_hybrid.mail_providers import build_provider_outlook, _TOKEN_URL

POOL_FILE = Path("pool.txt")
lines = [l.strip() for l in POOL_FILE.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


async def check_combo(combo_str: str):
    email = combo_str.split("|")[0]
    parts = combo_str.split("|")
    client_id = parts[3]
    refresh_token = parts[2]
    print(f"\n=== {email} ===")

    # Try with explicit Mail.Read scope
    scopes_to_try = [
        "https://graph.microsoft.com/.default offline_access",
        "https://graph.microsoft.com/Mail.Read offline_access",
        "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadBasic offline_access",
    ]

    for scope in scopes_to_try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                _TOKEN_URL,
                data={
                    "client_id": client_id,
                    "scope": scope,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        if resp.status_code != 200:
            print(f"  Scope [{scope[:50]}]: FAILED HTTP {resp.status_code} - {resp.text[:100]}")
            continue

        data = resp.json()
        access = data.get("access_token")
        granted_scope = data.get("scope", "")
        print(f"  Scope [{scope[:60]}]:")
        print(f"    Granted: {granted_scope[:120]}")

        # Check /me endpoint
        async with httpx.AsyncClient(timeout=15.0) as client:
            me = await client.get(f"{GRAPH_BASE}/me", headers={"Authorization": f"Bearer {access}"})
            print(f"    /me: HTTP {me.status_code} | {me.json().get('userPrincipalName', me.text[:80])}")

            # List folders
            folders_resp = await client.get(
                f"{GRAPH_BASE}/me/mailFolders",
                headers={"Authorization": f"Bearer {access}"},
            )
            if folders_resp.status_code == 200:
                folders = folders_resp.json().get("value", [])
                print(f"    Folders ({len(folders)}):")
                for f in folders:
                    print(f"      - {f.get('displayName')}: {f.get('totalItemCount')} items, {f.get('unreadItemCount')} unread")
            else:
                print(f"    /me/mailFolders: HTTP {folders_resp.status_code} - {folders_resp.text[:100]}")

            # List messages /me/messages
            msgs_resp = await client.get(
                f"{GRAPH_BASE}/me/messages",
                params={"$top": 5, "$orderby": "receivedDateTime desc"},
                headers={"Authorization": f"Bearer {access}"},
            )
            if msgs_resp.status_code == 200:
                msgs = msgs_resp.json().get("value", [])
                print(f"    /me/messages: {len(msgs)} messages")
            else:
                print(f"    /me/messages: HTTP {msgs_resp.status_code} - {msgs_resp.text[:150]}")
        break  # Only test first working scope


async def main():
    for combo_str in lines[:2]:  # check 2 combos
        await check_combo(combo_str)


if __name__ == "__main__":
    asyncio.run(main())
