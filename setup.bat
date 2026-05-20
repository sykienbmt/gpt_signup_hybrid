@echo off
REM gpt_signup_hybrid — 1 lệnh setup + start web UI (Windows)
REM Usage: double-click setup.bat hoặc chạy trong cmd/powershell

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ═══════════════════════════════════════════════════════════
echo   gpt_signup_hybrid — auto setup + start (Windows)
echo ═══════════════════════════════════════════════════════════
echo.

REM 1. Python venv
if not exist ".venv" (
    echo [1/5] Creating .venv...
    python -m venv .venv
) else (
    echo [1/5] .venv exists √
)

REM 2. Install deps
echo [2/5] Installing dependencies...
.venv\Scripts\pip install -q --upgrade pip 2>nul
.venv\Scripts\pip install -q pydantic typer httpx "curl_cffi>=0.7" pyotp fastapi uvicorn camoufox playwright 2>nul

REM 3. Playwright Firefox
echo [3/5] Installing Playwright Firefox...
.venv\Scripts\playwright install firefox 2>nul

REM 4. Camoufox binary
echo [4/5] Fetching Camoufox binary...
.venv\Scripts\python -m camoufox fetch 2>nul

REM 5. .env
if not exist ".env" (
    echo [5/5] Creating .env...
    (
        echo BROWSER_ENGINE=camoufox
        echo RUNTIME_DIR=runtime
        echo BROWSER_VIEWPORT_WIDTH=1440
        echo BROWSER_VIEWPORT_HEIGHT=800
        echo BROWSER_USE_PROFILE_TEMPLATE=true
        echo BROWSER_PROFILE_TEMPLATE_DIR=runtime/profiles/template
        echo BROWSER_CAMOUFOX_PROFILE_DIR=runtime/profiles/camoufox_template
    ) > .env
    echo   √ .env created
) else (
    echo [5/5] .env exists √
)

REM Create runtime dirs
if not exist "runtime\profiles\template" mkdir "runtime\profiles\template"
if not exist "runtime\profiles\camoufox_template" mkdir "runtime\profiles\camoufox_template"
if not exist "runtime\sessions" mkdir "runtime\sessions"
if not exist "runtime\outlook_state" mkdir "runtime\outlook_state"
if not exist "runtime\outlook_pool" mkdir "runtime\outlook_pool"

echo.
echo ═══════════════════════════════════════════════════════════
echo   √ Setup done. Starting web UI...
echo   → http://127.0.0.1:8089/
echo.
echo   Paste combo Outlook vao textarea + bam Chay.
echo   Format: email^|password^|refresh_token^|client_id
echo ═══════════════════════════════════════════════════════════
echo.

.venv\Scripts\python -m gpt_signup_hybrid web --host 127.0.0.1 --port 8089

pause
