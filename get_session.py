"""Test Get Session for existing ChatGPT account."""
import json
from gpt_signup_hybrid.session_phase import get_session_sync

# Test combo 4: rosaleentheodorafarrah7310@hotmail.com
EMAIL = "rosaleentheodorafarrah7310@hotmail.com"
PASSWORD = "nEz7jFdsMQ"
SECRET = None


def main():
    try:
        result = get_session_sync(
            email=EMAIL,
            password=PASSWORD,
            secret=SECRET,
            headless=True,
            log=lambda m: print(f"[get-session] {m}"),
        )
        print("\n=== SUCCESS ===")
        print(json.dumps(result, indent=2, ensure_ascii=False)[:2000])
    except Exception as exc:
        print(f"\n=== FAILED ===\n{exc}")


if __name__ == "__main__":
    main()
