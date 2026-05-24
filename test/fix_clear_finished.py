"""Fix clear_finished: giữ lại cancelled jobs để user có thể retry.

SessionJobManager + LinkJobManager vẫn xoá cancelled — sửa thành chỉ xoá success/error.
"""
from __future__ import annotations
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "web" / "manager.py"

OLD = '''    def clear_finished(self) -> int:
        removed = 0
        for jid in list(self.order):
            job = self.jobs.get(jid)
            if job and job.status in ("success", "error", "cancelled"):
                self.jobs.pop(jid, None)
                self.order.remove(jid)
                self._tasks.pop(jid, None)
                removed += 1
        if removed:
            self._broadcast({"type": "clear_finished", "removed": removed})
        return removed'''

NEW = '''    def clear_finished(self) -> int:
        """Xoá jobs đã xong (success/error). Giữ cancelled để user retry."""
        removed = 0
        for jid in list(self.order):
            job = self.jobs.get(jid)
            if job and job.status in ("success", "error"):
                self.jobs.pop(jid, None)
                self.order.remove(jid)
                self._tasks.pop(jid, None)
                removed += 1
        if removed:
            self._broadcast({"type": "clear_finished", "removed": removed})
        return removed'''


def main() -> int:
    src = PATH.read_text(encoding="utf-8")
    count = src.count(OLD)
    if count == 0:
        print("[skip] không tìm thấy block cũ")
        return 0
    new_src = src.replace(OLD, NEW)
    PATH.write_text(new_src, encoding="utf-8")
    print(f"[ok] thay thế {count} clear_finished block")
    return count


if __name__ == "__main__":
    main()
