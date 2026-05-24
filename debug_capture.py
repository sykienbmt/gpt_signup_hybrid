"""Debug capture — mở Camoufox headed, record HAR toàn bộ network traffic.

Chạy:
    .venv/Scripts/python debug_capture.py

Kết quả lưu vào: runtime/debug_capture_<timestamp>.har  (JSON format)
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from camoufox.sync_api import Camoufox


LOG_DIR = Path("runtime")
LOG_DIR.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
HAR_FILE = LOG_DIR / f"debug_capture_{timestamp}.har"

print(f"""
╔══════════════════════════════════════════════════════╗
║         ChatGPT Signup Debug Capture                 ║
╠══════════════════════════════════════════════════════╣
║  HAR file: {str(HAR_FILE):<42}║
║  Thao tác tay trong browser, đóng browser khi xong. ║
╚══════════════════════════════════════════════════════╝
""")

with Camoufox(headless=False) as browser:
    # record_har captures toàn bộ network kể cả cross-domain navigation
    context = browser.new_context(record_har_path=str(HAR_FILE))
    page = context.new_page()
    page.goto("https://chatgpt.com/auth/login", timeout=60_000)

    print("✅ Browser đã mở tại https://chatgpt.com/auth/login")
    print("   Thao tác tay đi, nhấn ENTER ở terminal này khi xong.\n")

    try:
        input(">>> Nhấn ENTER để dừng capture và lưu HAR...")
    except (KeyboardInterrupt, EOFError):
        pass

    # Flush HAR trước khi đóng context
    try:
        context.close()
    except Exception:
        pass

print(f"\n✅ HAR đã lưu: {HAR_FILE.resolve()}")
print(f"   Size: {HAR_FILE.stat().st_size / 1024:.1f} KB")

# Parse HAR và in tóm tắt các API calls quan trọng
TRACK_DOMAINS = ("openai.com", "chatgpt.com", "auth0.com")
SKIP_EXT = (".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2", ".webp", ".map")

print("\n=== TÓM TẮT API CALLS ===\n")
try:
    har = json.loads(HAR_FILE.read_text(encoding="utf-8"))
    entries = har.get("log", {}).get("entries", [])
    count = 0
    for e in entries:
        req = e.get("request", {})
        res = e.get("response", {})
        url = req.get("url", "")
        method = req.get("method", "")
        status = res.get("status", 0)

        if not any(d in url for d in TRACK_DOMAINS):
            continue
        if any(url.lower().endswith(x) for x in SKIP_EXT):
            continue
        if "/cdn/assets/" in url or "/ces/v1/" in url or "cdn-cgi" in url:
            continue

        count += 1
        print(f"[{count:02d}] {method} {status} {url}")

        # In request body nếu có
        post_data = req.get("postData", {})
        if post_data:
            text = post_data.get("text", "")
            if text:
                print(f"     REQ: {text[:300]}")

        # In response body
        content = res.get("content", {})
        body = content.get("text", "")
        if body and len(body) < 2000:
            print(f"     RES: {body[:500]}")
        elif body:
            print(f"     RES: {body[:300]}... [{len(body)} chars]")
        print()

    print(f"\nTổng: {count} API calls quan trọng / {len(entries)} entries")
except Exception as exc:
    print(f"Parse HAR error: {exc}")
