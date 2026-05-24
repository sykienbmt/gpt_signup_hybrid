"""Check: web/static/{app,session,link}.js render job-index, style.css có .job-index.

Parse-only check, không spawn browser.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"


def expect(cond: bool, msg: str) -> None:
    print(("OK  " if cond else "FAIL") + " " + msg)
    if not cond:
        sys.exit(1)


def main() -> int:
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    session_js = (STATIC / "session.js").read_text(encoding="utf-8")
    link_js = (STATIC / "link.js").read_text(encoding="utf-8")
    style_css = (STATIC / "style.css").read_text(encoding="utf-8")

    # 1. Mỗi file js phải có state.order.map((id, idx) ...)
    pat = re.compile(r"state\.order\.map\(\(\s*(?:id|job)\s*,\s*idx\s*\)")
    expect(bool(pat.search(app_js)), "app.js: state.order.map((id, idx)")
    expect(bool(pat.search(session_js)), "session.js: state.order.map((id, idx)")
    expect(bool(pat.search(link_js)), "link.js: state.order.map((id, idx)")

    # 2. Mỗi file js phải render <div class="job-index">${idx + 1}</div>
    idx_pat = re.compile(r'<div class="job-index">\$\{idx \+ 1\}</div>')
    expect(bool(idx_pat.search(app_js)), "app.js: render job-index")
    expect(bool(idx_pat.search(session_js)), "session.js: render job-index")
    expect(bool(idx_pat.search(link_js)), "link.js: render job-index")

    # 3. style.css có .job-index rule
    expect(".job-index {" in style_css, "style.css: .job-index rule tồn tại")

    # 4. .job grid-template-columns đã update để chứa thêm 1 cột
    grid_pat = re.compile(
        r"\.job\s*\{[^}]*grid-template-columns:\s*auto\s+auto\s+1fr\s+auto\s+auto\s*;",
        re.DOTALL,
    )
    expect(bool(grid_pat.search(style_css)), "style.css: .job grid 5 cột")

    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
