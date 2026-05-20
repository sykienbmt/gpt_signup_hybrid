"""Export accounts_gpt_only.csv: email_gpt|password|2fa_token|session_full
session_full = format giong nhu /api/auth/session tra ve.
Doc tu 2 nguon:
  1. runtime/sessions/*.json (session files moi nhat)
  2. accounts.csv column session_full (fallback cho session cu khong con file)
"""
import base64
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_DIR = Path("runtime/sessions")
ACCOUNTS_CSV = Path("accounts.csv")
OUTPUT = Path("accounts_gpt_only.csv")
HEADER = ["email_gpt", "password", "2fa_token", "session_full"]

WARNING = (
    "!!!!!!!!!!!!!!!!!!!! DO NOT SHARE ANY PART OF THE INFORMATION YOU SEE HERE. "
    "THIS INFORMATION IS SENSITIVE AND CAN GRANT ACCESS TO YOUR ACCOUNT. "
    "SHARING THIS INFORMATION IS LIKE SHARING YOUR PASSWORD. !!!!!!!!!!!!!!!!!!!!"
)


def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def build_session_format(data: dict, twofa_secret: str) -> dict:
    access_token = data.get("access_token", "")
    session_token = data.get("session_token", "")

    jwt = _decode_jwt_payload(access_token)
    auth_claims = jwt.get("https://api.openai.com/auth", {})
    profile = jwt.get("https://api.openai.com/profile", {})

    iat = jwt.get("iat", 0)
    exp = jwt.get("exp", 0)
    amr = auth_claims.get("amr", ["pwd"])
    user_id = auth_claims.get("chatgpt_user_id") or data.get("user_id", "")
    account_id = auth_claims.get("chatgpt_account_id") or data.get("account_id", "")
    plan_type = auth_claims.get("chatgpt_plan_type", "free")
    compute_residency = auth_claims.get("chatgpt_compute_residency", "no_constraint")
    email = profile.get("email") or data.get("email", "")
    name = data.get("name", "")

    expires = (
        datetime.fromtimestamp(exp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if exp else ""
    )

    return {
        "WARNING_BANNER": WARNING,
        "user": {
            "id": user_id,
            "name": name,
            "email": email,
            "idp": "auth0",
            "iat": iat,
            "amr": amr,
            "mfa": bool(twofa_secret),
        },
        "expires": expires,
        "account": {
            "id": account_id,
            "planType": plan_type,
            "structure": "personal",
            "isConversationClassifierEnabledForWorkspace": True,
            "isFinservEnabledWorkspace": False,
            "isFedrampCompliantWorkspace": False,
            "isDelinquent": False,
            "residencyRegion": "no_constraint",
            "computeResidency": compute_residency,
        },
        "accessToken": access_token,
        "authProvider": "openai",
        "sessionToken": session_token,
    }


def iter_session_files() -> list[dict]:
    """Yield (email, password, twofa_secret, raw_data) from runtime/sessions/*.json"""
    records = []
    for session_file in sorted(SESSIONS_DIR.glob("signup-*.json")):
        if ".2fa." in session_file.name:
            continue
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not data.get("success") or not data.get("access_token"):
            continue
        email = data.get("email", "").strip()
        if not email:
            continue
        twofa_secret = ""
        twofa_file = session_file.with_suffix(".2fa.json")
        if twofa_file.exists():
            try:
                twofa_data = json.loads(twofa_file.read_text(encoding="utf-8"))
                twofa_secret = twofa_data.get("two_factor", {}).get("secret", "")
            except Exception:
                pass
        records.append((email, data.get("password", ""), twofa_secret, data))
    return records


def iter_accounts_csv() -> list[dict]:
    """Yield (email, password, twofa_secret, raw_data) from accounts.csv fallback."""
    records = []
    if not ACCOUNTS_CSV.exists():
        return records
    try:
        with open(ACCOUNTS_CSV, encoding="utf-8", newline="") as f:
            for i, row in enumerate(csv.reader(f, delimiter="|")):
                if i == 0:
                    continue
                if len(row) < 5:
                    continue
                # format: hotmail_info|email_gpt|password|2fa_token|session_full
                email = row[1].strip()
                password = row[2].strip()
                twofa_secret = row[3].strip()
                try:
                    data = json.loads(row[4])
                except Exception:
                    continue
                if not data.get("access_token") or not data.get("success"):
                    continue
                records.append((email, password, twofa_secret, data))
    except Exception as e:
        print(f"  [WARN] could not read {ACCOUNTS_CSV}: {e}")
    return records


def main():
    # Gom tat ca records, uu tien session files (moi nhat), fallback tu accounts.csv
    all_records: dict[str, tuple] = {}  # email -> (email, password, twofa, data)

    # 1. Load tu accounts.csv truoc (priority thap)
    for rec in iter_accounts_csv():
        email = rec[0].lower()
        if email not in all_records:
            all_records[email] = rec

    # 2. Override bang session files moi hon (priority cao)
    for rec in iter_session_files():
        all_records[rec[0].lower()] = rec

    if OUTPUT.exists():
        OUTPUT.unlink()

    count = 0
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("|".join(HEADER) + "\n")
        for _, (email, password, twofa_secret, data) in sorted(all_records.items()):
            # Uu tien auth_session (format /api/auth/session chuan), fallback build tu JWT
            auth_session = data.get("auth_session")
            out = auth_session if (auth_session and auth_session.get("accessToken")) else build_session_format(data, twofa_secret)
            session_full = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
            f.write("|".join([email, password, twofa_secret, session_full]) + "\n")
            count += 1
            print(f"  + {email} | 2fa={'YES' if twofa_secret else 'NO'}")

    print(f"\nExported {count} account(s) to {OUTPUT}")


if __name__ == "__main__":
    main()
