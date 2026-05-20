#!/usr/bin/env bash
# gpt_signup_hybrid — 1 lệnh setup + start web UI
# Usage:
#   cd gpt_signup_hybrid
#   bash setup.sh
#
# Hoặc từ root repo:
#   bash gpt_signup_hybrid/setup.sh
set -e

# Detect root dir (thư mục chứa setup.sh)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

echo "═══════════════════════════════════════════════════════════"
echo "  gpt_signup_hybrid — auto setup + start"
echo "═══════════════════════════════════════════════════════════"

# 1. Python venv
if [ ! -d ".venv" ]; then
  echo "[1/5] Creating .venv..."
  python3 -m venv .venv
else
  echo "[1/5] .venv exists ✓"
fi

# 2. Install deps
echo "[2/5] Installing dependencies..."
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

# 3. Playwright Firefox (for Camoufox)
echo "[3/5] Installing Playwright Firefox..."
.venv/bin/playwright install firefox 2>/dev/null || true

# 4. Camoufox binary
echo "[4/5] Fetching Camoufox binary..."
.venv/bin/python -m camoufox fetch 2>/dev/null || true

# 5. .env
if [ ! -f ".env" ]; then
  echo "[5/5] Creating .env..."
  cat > .env << 'EOF'
BROWSER_ENGINE=camoufox
RUNTIME_DIR=runtime
BROWSER_VIEWPORT_WIDTH=1440
BROWSER_VIEWPORT_HEIGHT=800
BROWSER_USE_PROFILE_TEMPLATE=true
BROWSER_PROFILE_TEMPLATE_DIR=runtime/profiles/template
BROWSER_CAMOUFOX_PROFILE_DIR=runtime/profiles/camoufox_template
EOF
  echo "  ✓ .env created"
else
  echo "[5/5] .env exists ✓"
fi

# Ensure BROWSER_ENGINE=camoufox
if ! grep -q "BROWSER_ENGINE=camoufox" .env 2>/dev/null; then
  echo "BROWSER_ENGINE=camoufox" >> .env
fi

# Create runtime dirs
mkdir -p runtime/profiles/template runtime/profiles/camoufox_template runtime/sessions runtime/outlook_state runtime/outlook_pool

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✓ Setup done. Starting web UI..."
echo "  → http://127.0.0.1:8089/"
echo ""
echo "  Paste combo Outlook vào textarea + bấm Chạy."
echo "  Format: email|password|refresh_token|client_id"
echo "═══════════════════════════════════════════════════════════"
echo ""

.venv/bin/python -m gpt_signup_hybrid web --host 127.0.0.1 --port 8089
