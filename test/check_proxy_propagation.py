"""Smoke check: khi user set proxy ở /api/config thì proxy phải lan tới:

- enable_2fa() trong _run_job
- enable_2fa() trong _run_2fa_only_inner
- get_session() trong post-reg
- get_checkout_url() trong post-reg
- get_session() trong LinkJobManager._run_job (combo mode)
- get_checkout_url() trong LinkJobManager._run_job
- get_session() trong SessionJobManager._run_job
- fetch_session_via_http() trong post-reg

Cách test: grep manager.py source xem có chỗ nào gọi 1 trong các fn này MÀ KHÔNG
truyền `proxy=` argument.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "web" / "manager.py"

NETWORK_FNS = (
    "enable_2fa",
    "get_session",
    "get_checkout_url",
    "fetch_session_via_http",
)


def expect(cond: bool, label: str) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"{status} {label}")
    if not cond:
        sys.exit(1)


def main() -> None:
    src = TARGET.read_text(encoding="utf-8")

    for fn in NETWORK_FNS:
        # Tìm pattern await? <fn>(  ... ) nhiều dòng. Match tới đóng ngoặc tương ứng.
        # Đơn giản: tìm mọi `<fn>(` rồi extract block tới ngoặc đóng cùng cấp.
        idx = 0
        call_count = 0
        missing_proxy: list[str] = []
        while True:
            m = re.search(rf"\b{fn}\s*\(", src[idx:])
            if not m:
                break
            start = idx + m.start()
            open_idx = idx + m.end() - 1  # vị trí của '('
            # Skip nếu đây là def function (def fn(...))
            ctx_before = src[max(0, start - 40):start]
            if "def " in ctx_before.split("\n")[-1]:
                idx = open_idx + 1
                continue
            # Skip nếu trong import (from ... import fn)
            line_start = src.rfind("\n", 0, start) + 1
            line_end = src.find("\n", start)
            line = src[line_start:line_end]
            if "import" in line:
                idx = open_idx + 1
                continue
            # Match parentheses
            depth = 0
            i = open_idx
            while i < len(src):
                c = src[i]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            block = src[open_idx:i + 1]
            call_count += 1
            if "proxy=" not in block:
                # Lấy số dòng để dễ debug
                line_no = src[:start].count("\n") + 1
                missing_proxy.append(f"line {line_no}: {block[:120]}")
            idx = i + 1

        if call_count == 0:
            print(f"SKIP {fn} — không có call site")
            continue

        expect(
            not missing_proxy,
            f"{fn}: tất cả {call_count} call site đều truyền proxy="
            + (f" (missing {len(missing_proxy)}: {missing_proxy})" if missing_proxy else ""),
        )

    print("OK — proxy lan đầy đủ trong manager.py")


if __name__ == "__main__":
    main()
