# gpt_signup_hybrid

Automated ChatGPT account registration tool with hybrid approach: **Browser (Camoufox)** for anti-detect form interaction + **HTTP (curl_cffi)** for session extraction. Includes Web UI for batch processing and real-time monitoring.

---

**[English](#english)** | **[Tiếng Việt](#tiếng-việt)**

---

## English

### Overview

`gpt_signup_hybrid` automates the full ChatGPT signup flow:

1. **Phase 1 (Browser):** Opens Camoufox (anti-detect Firefox) → navigates auth.openai.com → registers account with email/password → polls OTP from mailbox → submits OTP → completes onboarding (/about-you).
2. **Phase 2 (HTTP):** Extracts session cookies from browser → fetches `access_token` via `/api/auth/session`.
3. **Phase 3 (2FA):** Enrolls TOTP via `/backend-api/accounts/mfa/enroll` → activates with first generated code.

Supports two mail providers for OTP retrieval:
- **Outlook (Microsoft Graph):** Uses refresh token combo to read OTP from Hotmail/Outlook inbox.
- **Worker (Cloudflare):** Polls OTP from a Cloudflare Worker logs endpoint (for iCloud Mail relay).

### Features

- **Web UI** — Paste combos, monitor jobs in real-time (SSE), view logs, retry failed jobs
- **CLI** — Full command set: `signup`, `web`, `totp`, `enable-2fa`, `pool-status`
- **Batch Processing** — Multi-job concurrency with stagger delay (avoid simultaneous browser launches)
- **Outlook Pool** — Auto-pick available combo from pool file, track used/failed state
- **2FA (TOTP)** — Auto-enable after signup, outputs secret + provisioning URI
- **Get Session** — Login existing account (email + password + optional 2FA) → retrieve fresh session JSON
- **Anti-Detect** — Camoufox browser with profile template, viewport spoofing, font randomization
- **Proxy Support** — HTTP/HTTPS/SOCKS5 proxy for all phases (browser + API)
- **Smart OTP Handling** — Retry on incorrect code, resend email, skip previously tried codes

### Requirements

- **Python 3.11+**
- **macOS / Linux** (Windows: use WSL)
- Dependencies: `pydantic`, `typer`, `httpx`, `curl_cffi>=0.7`, `pyotp`, `fastapi`, `uvicorn`, `camoufox`, `playwright`

### Quick Start

```bash
# Clone
git clone https://github.com/<your-username>/gpt_signup_hybrid.git
cd gpt_signup_hybrid

# One-command setup + start web UI
bash setup.sh
```

Or manually:

```bash
# 1. Create venv
python3 -m venv .venv

# 2. Install dependencies
.venv/bin/pip install pydantic typer httpx "curl_cffi>=0.7" pyotp fastapi uvicorn camoufox playwright

# 3. Install browser binaries
.venv/bin/playwright install firefox
.venv/bin/python -m camoufox fetch

# 4. Create .env (optional)
cp .env.example .env

# 5. Create runtime directories
mkdir -p runtime/profiles/template runtime/profiles/camoufox_template runtime/sessions runtime/outlook_state

# 6. Start web UI
.venv/bin/python -m gpt_signup_hybrid web --port 8089
```

Open http://127.0.0.1:8089/ in your browser.

### Configuration

Create `.env` in the project root (or set environment variables):

```env
# Max concurrent jobs in web UI (1-10, default: 2)
HYBRID_MAX_CONCURRENT=2

# Proxy for Microsoft Graph API (empty = direct)
# Format: http://user:pass@host:port or socks5://host:port
HYBRID_OUTLOOK_PROXY=

# Job timeout in seconds (must be > OTP timeout 180s, default: 240)
HYBRID_JOB_TIMEOUT=240

# Browser settings
BROWSER_ENGINE=camoufox
RUNTIME_DIR=runtime
BROWSER_VIEWPORT_WIDTH=1440
BROWSER_VIEWPORT_HEIGHT=800
BROWSER_USE_PROFILE_TEMPLATE=true
BROWSER_PROFILE_TEMPLATE_DIR=runtime/profiles/template
BROWSER_CAMOUFOX_PROFILE_DIR=runtime/profiles/camoufox_template
```

### Usage

#### Web UI

```bash
.venv/bin/python -m gpt_signup_hybrid web --port 8089
```

Options:
| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `127.0.0.1` | Bind host |
| `--port` | `8089` | Bind port |
| `--reload` | `false` | Auto-reload (dev mode) |

**Web UI workflow:**
1. Select mail mode (Outlook combo / iCloud Worker)
2. Paste combo(s) into textarea (one per line)
3. Click Run — jobs queue and execute with real-time log streaming
4. View results: password, TOTP secret, first code

**Outlook combo format:**
```
email|password|refresh_token|client_id
```

#### CLI: Signup

```bash
# Outlook combo
.venv/bin/python -m gpt_signup_hybrid signup \
  --outlook-combo "user@hotmail.com|pass123|M.C535_BAY...|8b4ba9dd-..."

# Outlook pool file
.venv/bin/python -m gpt_signup_hybrid signup \
  --outlook-pool pool.txt

# Worker (iCloud)
.venv/bin/python -m gpt_signup_hybrid signup \
  --email user@icloud.com \
  --mail-provider worker \
  --logs-url https://your-worker.workers.dev/logs \
  --api-key your_token
```

Key options:
| Flag | Description |
|------|-------------|
| `--email` | Registration email (auto-derived from combo if omitted) |
| `--name` | Display name (default: random) |
| `--birthdate` | YYYY-MM-DD, age >= 13 (default: random) |
| `--headless` | Run browser headless (not recommended, easier to detect) |
| `--proxy` | HTTP/HTTPS proxy URL |
| `--otp-timeout` | OTP poll timeout in seconds (default: 180) |
| `--output` / `-o` | Save result JSON to file |

#### CLI: Enable 2FA

```bash
.venv/bin/python -m gpt_signup_hybrid enable-2fa \
  --session-file runtime/sessions/signup-20250520-143022-user_at_hotmail.com.json
```

#### CLI: TOTP Code

```bash
.venv/bin/python -m gpt_signup_hybrid totp B2P3OQCCXINLHGPUDIS55DHQDW5MENK5
```

#### CLI: Pool Status

```bash
.venv/bin/python -m gpt_signup_hybrid pool-status pool.txt
```

### API Endpoints (Web UI)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | List all jobs |
| `POST` | `/api/jobs` | Add jobs (combos textarea) |
| `GET` | `/api/jobs/{id}` | Get job detail |
| `GET` | `/api/jobs/{id}/log` | Get job log lines |
| `POST` | `/api/jobs/{id}/retry` | Retry failed job |
| `DELETE` | `/api/jobs/{id}` | Remove job |
| `POST` | `/api/jobs/stop-all` | Cancel all running/queued |
| `POST` | `/api/jobs/clear-finished` | Clear finished jobs from memory |
| `GET/POST` | `/api/config` | Get/set runtime config |
| `POST` | `/api/proxy/test` | Test proxy connectivity |
| `GET` | `/api/mail-modes` | List available mail modes |
| `GET` | `/api/events` | SSE stream (real-time updates) |
| `GET` | `/api/session/jobs` | List session jobs |
| `POST` | `/api/session/jobs` | Add session jobs (email\|pass\|secret) |
| `GET` | `/api/session/events` | SSE stream for session jobs |

### Project Structure

```
gpt_signup_hybrid/
├── __main__.py          # Entry point: python -m gpt_signup_hybrid
├── cli.py               # Typer CLI commands (signup, web, totp, enable-2fa, pool-status)
├── config.py            # Settings loader (.env + env vars), runtime dirs
├── models.py            # Pydantic models: SignupRequest, BrowserHandoff, SignupResult
├── signup.py            # Orchestrator: Phase 1 → poll OTP → Phase 2
├── browser_phase.py     # Phase 1: Camoufox browser automation (register/login/OTP)
├── http_phase.py        # Phase 2: curl_cffi session extraction + access_token
├── mfa_phase.py         # Phase 3: Enable TOTP 2FA via /backend-api
├── session_phase.py     # Get Session: browser login → /api/auth/session
├── mail_providers.py    # OTP providers: WorkerMailProvider + OutlookMailProvider
├── outlook_pool.py      # Pool file management: parse, pick, mark used/failed
├── random_profile.py    # Random name/age/password generator
├── totp_helper.py       # TOTP code generation (pyotp wrapper)
├── web/
│   ├── server.py        # FastAPI app: REST API + SSE + static serving
│   ├── manager.py       # JobManager + SessionJobManager (worker pool pattern)
│   ├── mail_modes.py    # Mail mode registry (extensible)
│   └── static/
│       ├── index.html   # Web UI HTML
│       ├── app.js       # Frontend JS (jobs, SSE, controls)
│       ├── session.js   # Session tab JS
│       └── style.css    # Styles
├── setup.sh             # One-command setup (macOS/Linux)
├── setup.bat            # Windows setup script
├── .env.example         # Example config
└── README.md
```

### Output

Successful signup produces:
- `runtime/sessions/signup-<timestamp>-<email>.json` — Full session data (session_token, access_token, cookies, password)
- `runtime/sessions/signup-<timestamp>-<email>.2fa.json` — 2FA data (TOTP secret, provisioning URI, factor_id)

### Notes

- Camoufox headed mode is recommended (headless is more easily detected by Cloudflare/Sentinel)
- OTP poll timeout defaults to 180s — increase if mail delivery is slow
- Proxy is shared across browser + API requests
- Outlook refresh token rotates after each use — state is persisted to `runtime/outlook_state/`
- Pool file combos are automatically skipped once used or terminally failed

### License

Private / Internal use only.

---

## Tiếng Việt

### Tổng quan

`gpt_signup_hybrid` tự động hoá toàn bộ quy trình đăng ký tài khoản ChatGPT:

1. **Phase 1 (Browser):** Mở Camoufox (Firefox chống phát hiện) → điều hướng auth.openai.com → đăng ký account bằng email/password → poll OTP từ hộp thư → submit OTP → hoàn tất onboarding (/about-you).
2. **Phase 2 (HTTP):** Trích xuất session cookies từ browser → lấy `access_token` qua `/api/auth/session`.
3. **Phase 3 (2FA):** Đăng ký TOTP qua `/backend-api/accounts/mfa/enroll` → kích hoạt bằng code TOTP đầu tiên.

Hỗ trợ 2 mail provider để nhận OTP:
- **Outlook (Microsoft Graph):** Dùng refresh token combo để đọc OTP từ inbox Hotmail/Outlook.
- **Worker (Cloudflare):** Poll OTP từ endpoint Cloudflare Worker (dành cho iCloud Mail relay).

### Tính năng

- **Web UI** — Paste combo, theo dõi job real-time (SSE), xem log, retry job lỗi
- **CLI** — Đầy đủ lệnh: `signup`, `web`, `totp`, `enable-2fa`, `pool-status`
- **Xử lý hàng loạt** — Chạy nhiều job đồng thời với stagger delay (tránh mở nhiều browser cùng lúc)
- **Outlook Pool** — Tự chọn combo còn khả dụng từ file pool, theo dõi trạng thái used/failed
- **2FA (TOTP)** — Tự bật sau khi signup, output secret + provisioning URI
- **Get Session** — Login account đã có (email + password + 2FA) → lấy session JSON mới
- **Anti-Detect** — Camoufox browser với profile template, viewport spoofing, font randomization
- **Proxy** — Hỗ trợ HTTP/HTTPS/SOCKS5 proxy cho tất cả phase (browser + API)
- **OTP thông minh** — Retry khi code sai, gửi lại email, skip code đã thử

### Yêu cầu

- **Python 3.11+**
- **macOS / Linux** (Windows: dùng WSL)
- Dependencies: `pydantic`, `typer`, `httpx`, `curl_cffi>=0.7`, `pyotp`, `fastapi`, `uvicorn`, `camoufox`, `playwright`

### Bắt đầu nhanh

```bash
# Clone
git clone https://github.com/<your-username>/gpt_signup_hybrid.git
cd gpt_signup_hybrid

# Setup + chạy web UI (1 lệnh)
bash setup.sh
```

Hoặc thủ công:

```bash
# 1. Tạo venv
python3 -m venv .venv

# 2. Cài dependencies
.venv/bin/pip install pydantic typer httpx "curl_cffi>=0.7" pyotp fastapi uvicorn camoufox playwright

# 3. Cài browser binaries
.venv/bin/playwright install firefox
.venv/bin/python -m camoufox fetch

# 4. Tạo .env (tuỳ chọn)
cp .env.example .env

# 5. Tạo thư mục runtime
mkdir -p runtime/profiles/template runtime/profiles/camoufox_template runtime/sessions runtime/outlook_state

# 6. Chạy web UI
.venv/bin/python -m gpt_signup_hybrid web --port 8089
```

Mở http://127.0.0.1:8089/ trên trình duyệt.

### Cấu hình

Tạo file `.env` ở thư mục gốc project (hoặc set biến môi trường):

```env
# Số job chạy đồng thời tối đa (1-10, mặc định: 2)
HYBRID_MAX_CONCURRENT=2

# Proxy cho Microsoft Graph API (để trống = không proxy)
# Format: http://user:pass@host:port hoặc socks5://host:port
HYBRID_OUTLOOK_PROXY=

# Timeout mỗi job (giây). Phải > OTP timeout 180s. Mặc định: 240
HYBRID_JOB_TIMEOUT=240

# Cấu hình browser
BROWSER_ENGINE=camoufox
RUNTIME_DIR=runtime
BROWSER_VIEWPORT_WIDTH=1440
BROWSER_VIEWPORT_HEIGHT=800
BROWSER_USE_PROFILE_TEMPLATE=true
BROWSER_PROFILE_TEMPLATE_DIR=runtime/profiles/template
BROWSER_CAMOUFOX_PROFILE_DIR=runtime/profiles/camoufox_template
```

### Cách dùng

#### Web UI

```bash
.venv/bin/python -m gpt_signup_hybrid web --port 8089
```

Options:
| Flag | Mặc định | Mô tả |
|------|----------|-------|
| `--host` | `127.0.0.1` | Bind host |
| `--port` | `8089` | Bind port |
| `--reload` | `false` | Auto-reload (dev mode) |

**Quy trình Web UI:**
1. Chọn mail mode (Outlook combo / iCloud Worker)
2. Paste combo vào textarea (mỗi dòng 1 combo)
3. Bấm Chạy — job vào queue và thực thi, log stream real-time
4. Xem kết quả: password, TOTP secret, first code

**Format combo Outlook:**
```
email|password|refresh_token|client_id
```

#### CLI: Signup

```bash
# Outlook combo
.venv/bin/python -m gpt_signup_hybrid signup \
  --outlook-combo "user@hotmail.com|pass123|M.C535_BAY...|8b4ba9dd-..."

# Outlook pool file
.venv/bin/python -m gpt_signup_hybrid signup \
  --outlook-pool pool.txt

# Worker (iCloud)
.venv/bin/python -m gpt_signup_hybrid signup \
  --email user@icloud.com \
  --mail-provider worker \
  --logs-url https://your-worker.workers.dev/logs \
  --api-key your_token
```

Các option chính:
| Flag | Mô tả |
|------|-------|
| `--email` | Email đăng ký (tự lấy từ combo nếu không truyền) |
| `--name` | Tên hiển thị (mặc định: random) |
| `--birthdate` | YYYY-MM-DD, tuổi >= 13 (mặc định: random) |
| `--headless` | Chạy browser ẩn (không khuyến nghị, dễ bị detect) |
| `--proxy` | HTTP/HTTPS proxy URL |
| `--otp-timeout` | Timeout poll OTP (giây, mặc định: 180) |
| `--output` / `-o` | Lưu kết quả JSON ra file |

#### CLI: Bật 2FA

```bash
.venv/bin/python -m gpt_signup_hybrid enable-2fa \
  --session-file runtime/sessions/signup-20250520-143022-user_at_hotmail.com.json
```

#### CLI: Gen TOTP Code

```bash
.venv/bin/python -m gpt_signup_hybrid totp B2P3OQCCXINLHGPUDIS55DHQDW5MENK5
```

#### CLI: Xem trạng thái Pool

```bash
.venv/bin/python -m gpt_signup_hybrid pool-status pool.txt
```

### API Endpoints (Web UI)

| Method | Path | Mô tả |
|--------|------|-------|
| `GET` | `/api/jobs` | Danh sách tất cả jobs |
| `POST` | `/api/jobs` | Thêm jobs (combos từ textarea) |
| `GET` | `/api/jobs/{id}` | Chi tiết 1 job |
| `GET` | `/api/jobs/{id}/log` | Log lines của job |
| `POST` | `/api/jobs/{id}/retry` | Retry job lỗi |
| `DELETE` | `/api/jobs/{id}` | Xoá job |
| `POST` | `/api/jobs/stop-all` | Huỷ tất cả job đang chạy/queue |
| `POST` | `/api/jobs/clear-finished` | Xoá jobs đã xong khỏi bộ nhớ |
| `GET/POST` | `/api/config` | Đọc/ghi config runtime |
| `POST` | `/api/proxy/test` | Test kết nối proxy |
| `GET` | `/api/mail-modes` | Danh sách mail modes |
| `GET` | `/api/events` | SSE stream (cập nhật real-time) |
| `GET` | `/api/session/jobs` | Danh sách session jobs |
| `POST` | `/api/session/jobs` | Thêm session jobs (email\|pass\|secret) |
| `GET` | `/api/session/events` | SSE stream cho session jobs |

### Cấu trúc Project

```
gpt_signup_hybrid/
├── __main__.py          # Entry point: python -m gpt_signup_hybrid
├── cli.py               # Typer CLI (signup, web, totp, enable-2fa, pool-status)
├── config.py            # Đọc config (.env + biến môi trường), quản lý thư mục runtime
├── models.py            # Pydantic models: SignupRequest, BrowserHandoff, SignupResult
├── signup.py            # Orchestrator: Phase 1 → poll OTP → Phase 2
├── browser_phase.py     # Phase 1: Camoufox browser automation (register/login/OTP)
├── http_phase.py        # Phase 2: curl_cffi trích xuất session + access_token
├── mfa_phase.py         # Phase 3: Bật TOTP 2FA qua /backend-api
├── session_phase.py     # Get Session: login browser → /api/auth/session
├── mail_providers.py    # OTP providers: WorkerMailProvider + OutlookMailProvider
├── outlook_pool.py      # Quản lý pool file: parse, pick, đánh dấu used/failed
├── random_profile.py    # Random name/age/password generator
├── totp_helper.py       # TOTP code generation (pyotp wrapper)
├── web/
│   ├── server.py        # FastAPI app: REST API + SSE + static serving
│   ├── manager.py       # JobManager + SessionJobManager (worker pool pattern)
│   ├── mail_modes.py    # Mail mode registry (mở rộng được)
│   └── static/
│       ├── index.html   # Web UI HTML
│       ├── app.js       # Frontend JS (jobs, SSE, controls)
│       ├── session.js   # Session tab JS
│       └── style.css    # Styles
├── setup.sh             # Script setup 1 lệnh (macOS/Linux)
├── setup.bat            # Script setup Windows
├── .env.example         # Config mẫu
└── README.md
```

### Output

Signup thành công tạo ra:
- `runtime/sessions/signup-<timestamp>-<email>.json` — Dữ liệu session đầy đủ (session_token, access_token, cookies, password)
- `runtime/sessions/signup-<timestamp>-<email>.2fa.json` — Dữ liệu 2FA (TOTP secret, provisioning URI, factor_id)

### Lưu ý

- Khuyến nghị dùng Camoufox headed mode (headless dễ bị Cloudflare/Sentinel phát hiện hơn)
- OTP poll timeout mặc định 180s — tăng lên nếu mail gửi chậm
- Proxy dùng chung cho cả browser + API requests
- Outlook refresh token rotate sau mỗi lần dùng — state lưu ở `runtime/outlook_state/`
- Combo trong pool file tự động skip khi đã dùng hoặc lỗi terminal

### License

Private / Internal use only.
