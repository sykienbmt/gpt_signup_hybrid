"""Export signup results to CSV: hotmail_info|email_gpt|password|2fa_token|session_full

Chi xuất các account SUCCESS. Append vào file hiện có (không ghi đè).
Dùng email_gpt làm key để tránh duplicate.
"""
import csv
import json
from pathlib import Path

POOL_FILE = Path("pool.txt")
SESSIONS_DIR = Path("runtime/sessions")
OUTPUT = Path("accounts.csv")

HEADER = ["hotmail_info", "email_gpt", "password", "2fa_token", "session_full"]


def load_pool():
    combos = {}
    if not POOL_FILE.exists():
        return combos
    for line in POOL_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            email = parts[0].strip().lower()
            combos[email] = line
    return combos


def load_existing_emails():
    """Đọc danh sách email đã có trong CSV để tránh duplicate."""
    emails = set()
    if not OUTPUT.exists():
        return emails
    try:
        with open(OUTPUT, encoding="utf-8", newline="") as f:
            reader = csv.reader(f, delimiter="|")
            for i, row in enumerate(reader):
                if i == 0:
                    continue  # skip header
                if len(row) >= 2 and row[1]:
                    emails.add(row[1].strip().lower())
    except Exception:
        pass
    return emails


def main():
    pool = load_pool()
    existing_emails = load_existing_emails()

    # Tạo file + header nếu chưa có
    if not OUTPUT.exists():
        with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
            csv.writer(f, delimiter="|").writerow(HEADER)

    count = 0
    with open(OUTPUT, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="|")

        for session_file in sorted(SESSIONS_DIR.glob("signup-*.json")):
            if session_file.name.endswith(".2fa.json"):
                continue
            try:
                session_data = json.loads(session_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            # Chỉ export success
            if not session_data.get("success"):
                continue

            email = session_data.get("email", "").strip()
            if not email or email.lower() in existing_emails:
                continue  # skip duplicate

            password = session_data.get("password", "")
            combo = pool.get(email.lower(), "")

            twofa_file = session_file.with_suffix(".2fa.json")
            twofa_token = ""
            if twofa_file.exists():
                try:
                    twofa_data = json.loads(twofa_file.read_text(encoding="utf-8"))
                    twofa_token = twofa_data.get("two_factor", {}).get("secret", "")
                except Exception:
                    pass

            session_full = json.dumps(session_data, ensure_ascii=False, separators=(",", ":"))
            writer.writerow([combo, email, password, twofa_token, session_full])
            existing_emails.add(email.lower())
            count += 1
            print(f"  + {email} | pass={password}")

    print(f"Exported {count} new account(s) to {OUTPUT}")


if __name__ == "__main__":
    main()
