#!/usr/bin/env bash
# gpt_signup_hybrid — 1 lệnh setup + start web UI.
#
# Coi thư mục này là project root: tất cả file (.venv, runtime, .env)
# đều nằm trong gpt_signup_hybrid/, không leak ra parent.
#
# Pinned stack (xem requirements.txt):
#   - Python 3.13 (Camoufox 0.4.11 + Firefox 135 chưa hỗ trợ Python 3.14)
#   - playwright==1.49.1 (Firefox 132 driver — match Camoufox FF135)
#   - camoufox==0.4.11 (binary FF 135.0.1-beta.24)
#
# Lý do: playwright 1.60 + Camoufox FF135 gây lỗi
#   "Connection closed while reading from the driver" khi page.goto sang
#   auth.openai.com (CDP protocol mismatch).
#
# Usage:
#   cd gpt_signup_hybrid
#   bash setup.sh
set -euo pipefail

# ROOT_DIR = chính thư mục chứa setup.sh
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# PARENT_DIR cần để Python import được package `gpt_signup_hybrid`
# khi user chạy `.venv/bin/python -m gpt_signup_hybrid` từ trong package.
PARENT_DIR="$(dirname "$ROOT_DIR")"

# Python 3.13 bắt buộc — fail-fast nếu không có.
PY_BIN="${PYTHON:-}"
if [ -z "${PY_BIN}" ]; then
  if command -v python3.13 >/dev/null 2>&1; then
    PY_BIN="$(command -v python3.13)"
  else
    echo "ERROR: cần Python 3.13 (Camoufox 0.4.11 chưa hỗ trợ 3.14)." >&2
    echo "  Cài qua Homebrew:  brew install python@3.13" >&2
    echo "  Hoặc set PYTHON=/path/to/python3.13 rồi chạy lại." >&2
    exit 1
  fi
fi
PY_VERSION="$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$PY_VERSION" != "3.13" ]; then
  echo "ERROR: $PY_BIN báo Python $PY_VERSION, cần 3.13." >&2
  exit 1
fi

REQ_FILE="$ROOT_DIR/requirements.txt"
if [ ! -f "$REQ_FILE" ]; then
  echo "ERROR: $REQ_FILE không tồn tại." >&2
  exit 1
fi

echo "═══════════════════════════════════════════════════════════"
echo "  gpt_signup_hybrid — auto setup + start"
echo "  python: $PY_BIN ($PY_VERSION)"
echo "  root:   $ROOT_DIR"
echo "  parent: $PARENT_DIR (for python -m import)"
echo "═══════════════════════════════════════════════════════════"

# 1. Python venv (trong chính package)
if [ ! -d ".venv" ]; then
  echo "[1/6] Creating .venv (python $PY_VERSION)..."
  "$PY_BIN" -m venv .venv
else
  EXISTING="$(.venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "?")"
  if [ "$EXISTING" != "3.13" ]; then
    echo "[1/6] .venv đang dùng Python $EXISTING — recreate cho 3.13..."
    rm -rf .venv
    "$PY_BIN" -m venv .venv
  else
    echo "[1/6] .venv exists (python 3.13) ✓"
  fi
fi

# 2. Install pinned deps
echo "[2/6] Installing dependencies (pinned)..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r "$REQ_FILE"

# 3. Inject .pth file để Python tìm thấy package khi CWD = chính nó
SITE_PKG="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
if [ -n "$SITE_PKG" ] && [ -d "$SITE_PKG" ]; then
  echo "[3/6] Registering parent dir in venv site-packages..."
  echo "$PARENT_DIR" > "$SITE_PKG/_gpt_signup_hybrid_root.pth"
  echo "  ✓ $SITE_PKG/_gpt_signup_hybrid_root.pth → $PARENT_DIR"
else
  echo "ERROR: không xác định được site-packages." >&2
  exit 1
fi

# 4. Playwright Firefox (driver browser) — chỉ install nếu chưa có.
echo "[4/6] Installing Playwright Firefox (driver)..."
.venv/bin/playwright install firefox

# 5. Camoufox binary (Firefox stealth build) — fetch idempotent.
echo "[5/6] Fetching Camoufox binary..."
.venv/bin/python -m camoufox fetch

# 6. .env trong chính package
if [ ! -f ".env" ]; then
  echo "[6/6] Creating .env..."
  cat > .env << 'EOF'
# Browser / runtime config (đọc bởi config.load_settings)
BROWSER_ENGINE=camoufox
RUNTIME_DIR=runtime
BROWSER_VIEWPORT_WIDTH=1440
BROWSER_VIEWPORT_HEIGHT=800
BROWSER_USE_PROFILE_TEMPLATE=true
BROWSER_PROFILE_TEMPLATE_DIR=runtime/profiles/template
BROWSER_CAMOUFOX_PROFILE_DIR=runtime/profiles/camoufox_template

# Web UI config (đọc bởi web.manager)
HYBRID_MAX_CONCURRENT=2
HYBRID_OUTLOOK_PROXY=
HYBRID_JOB_TIMEOUT=240
EOF
  echo "  ✓ .env created"
else
  echo "[6/6] .env exists ✓"
fi

# Tạo runtime dirs trong package
mkdir -p \
  runtime/profiles/template \
  runtime/profiles/camoufox_template \
  runtime/sessions \
  runtime/outlook_state \
  runtime/outlook_pool \
  runtime/har_hybrid

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✓ Setup done. Starting web UI..."
echo "  → http://127.0.0.1:8089/"
echo ""
echo "  Paste combo Outlook vào textarea + bấm Run."
echo "  Format: email|password|refresh_token|client_id"
echo "═══════════════════════════════════════════════════════════"
echo ""

# CWD vẫn là $ROOT_DIR (= gpt_signup_hybrid/). Python sẽ load .pth
# để tìm thấy package từ $PARENT_DIR.
.venv/bin/python -m gpt_signup_hybrid web --host 127.0.0.1 --port 8089
