"""Smoke test ComboRepository với in-memory SQLite."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.engine import DatabaseEngine
from db.repositories import ComboRepository


def main() -> None:
    # Dùng temp file thay vì :memory: vì engine tạo dirs
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = DatabaseEngine(db_path=db_path)
        repo = ComboRepository(engine)

        # 1) list_all — empty
        assert repo.list_all() == [], "Should be empty initially"
        print("✓ list_all() returns empty list")

        # 2) upsert
        combo = {
            "email": "test@hotmail.com",
            "password": "pass123",
            "refresh_token": "M.C_token1",
            "client_id": "client1",
        }
        repo.upsert(combo)
        result = repo.get_by_email("test@hotmail.com")
        assert result is not None
        assert result["email"] == "test@hotmail.com"
        assert result["password"] == "pass123"
        assert result["refresh_token"] == "M.C_token1"
        assert result["used_for_signup"] == 0
        print("✓ upsert() + get_by_email() works")

        # 3) upsert existing — preserves state
        repo.mark_failure("test@hotmail.com", "some_error")
        repo.upsert({
            "email": "test@hotmail.com",
            "password": "newpass",
            "refresh_token": "M.C_token2",
            "client_id": "client2",
        })
        updated = repo.get_by_email("test@hotmail.com")
        assert updated["password"] == "newpass"
        assert updated["refresh_token"] == "M.C_token2"
        assert updated["client_id"] == "client2"
        # upsert preserves these via ON CONFLICT DO UPDATE (only updates password/refresh_token/client_id)
        # last_error is preserved because it's not in the UPDATE SET clause
        assert updated["last_error"] == "some_error"
        print("✓ upsert() preserves tracking state on conflict")

        # 4) mark_success
        repo.mark_success("test@hotmail.com")
        success = repo.get_by_email("test@hotmail.com")
        assert success["used_for_signup"] == 1
        assert success["used_at"] is not None
        assert success["last_error"] is None
        print("✓ mark_success() sets correct fields")

        # 5) mark_failure — doesn't touch used_for_signup
        repo.mark_failure("test@hotmail.com", "new_error")
        failed = repo.get_by_email("test@hotmail.com")
        assert failed["used_for_signup"] == 1  # unchanged
        assert failed["last_error"] == "new_error"
        assert failed["last_failed_at"] is not None
        print("✓ mark_failure() preserves used_for_signup")

        # 6) pick_available — no available (used_for_signup=1)
        assert repo.pick_available() is None
        print("✓ pick_available() returns None when pool exhausted")

        # 7) Add available combo + pick
        repo.upsert({
            "email": "avail@hotmail.com",
            "password": "p",
            "refresh_token": "M.C_t",
            "client_id": "c",
        })
        picked = repo.pick_available()
        assert picked is not None
        assert picked["email"] == "avail@hotmail.com"
        print("✓ pick_available() returns available combo")

        # 8) Terminal error filtering
        repo.mark_failure("avail@hotmail.com", "registration_disallowed by server")
        assert repo.pick_available() is None
        print("✓ pick_available() filters terminal errors")

        # 9) Non-terminal error — still pickable
        repo.upsert({
            "email": "retry@hotmail.com",
            "password": "p",
            "refresh_token": "M.C_t",
            "client_id": "c",
        })
        repo.mark_failure("retry@hotmail.com", "network_timeout")
        picked2 = repo.pick_available()
        assert picked2 is not None
        assert picked2["email"] == "retry@hotmail.com"
        print("✓ pick_available() allows non-terminal errors")

        # 10) update_refresh_token
        repo.update_refresh_token("retry@hotmail.com", "M.C_new_token")
        refreshed = repo.get_by_email("retry@hotmail.com")
        assert refreshed["refresh_token"] == "M.C_new_token"
        assert refreshed["last_refresh_at"] is not None
        print("✓ update_refresh_token() works")

        # 11) get_by_email — non-existent returns None
        assert repo.get_by_email("nonexistent@hotmail.com") is None
        print("✓ get_by_email() returns None for non-existent")

        # 12) list_all — returns all
        all_combos = repo.list_all()
        assert len(all_combos) == 3
        print("✓ list_all() returns all combos")

    print("\n✅ All smoke tests passed!")


if __name__ == "__main__":
    main()
