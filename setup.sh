#!/usr/bin/env bash
# gpt_signup_hybrid — 1 lệnh setup + start web UI
# Coi thư mục này là project root: tất cả file (.venv, runtime, .env)
# đều nằm trong gpt_signup_hybrid/, không leak ra parent.
#
# Usage:
#   cd gpt_signup_hybrid
#   bash setup.sh
set -e

# ROOT_DIR = chính thư mục chứa setup.sh
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# PARENT_DIR cần để Python import được package `gpt_signup_hybrid`
# khi user chạy `.venv/bin/python -m gpt_signup_hybrid` từ trong package.
PARENT_DIR="$(dirname "$ROOT_DIR")"

echo "═══════════════════════════════════════════════════════════"
echo "  gpt_signup_hybrid — auto setup + start"
echo "  root:   $ROOT_DIR"
echo "  parent: $PARENT_DIR (for python -m import)"
echo "═══════════════════════════════════════════════════════════"

# 1. Python venv (trong chính package)
if [ ! -d ".venv" ]; then
  echo "[1/6] Creating .venv in $ROOT_DIR..."
  python3 -m venv .venv
else
  echo "[1/6] .venv exists ✓"
fi

# 2. Install deps
echo "[2/6] Installing dependencies..."
.venv/bin/pip install -q --upgrade pip 2>/dev/null
.venv/bin/pip install -q \
  pydantic \
  typer \
  httpx \
  "curl_cffi>=0.7" \
  pyotp \
  fastapi \
  uvicorn \
  camoufox \
  playwright \
  2>/dev/null

# 3. Inject .pth file để Python tìm thấy package khi CWD = chính nó
SITE_PKG="$(.venv/bin/python -c "import site, sys; print(site.getsitepackages()[0])" 2>/dev/null)"
if [ -n "$SITE_PKG" ] && [ -d "$SITE_PKG" ]; then
  echo "[3/6] Registering parent dir in venv site-packages..."
  echo "$PARENT_DIR" > "$SITE_PKG/_gpt_signup_hybrid_root.pth"
  echo "  ✓ $SITE_PKG/_gpt_signup_hybrid_root.pth → $PARENT_DIR"
else
  echo "[3/6] WARN: không xác định được site-packages, skip .pth injection."
fi

# 4. Playwright Firefox (for Camoufox)
echo "[4/6] Installing Playwright Firefox..."
.venv/bin/playwright install firefox 2>/dev/null || true

# 5. Camoufox binary
echo "[5/6] Fetching Camoufox binary..."
.venv/bin/python -m camoufox fetch 2>/dev/null || true

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
