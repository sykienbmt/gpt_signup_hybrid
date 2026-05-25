# Hướng Dẫn Chạy gpt_signup_hybrid

> Tool tự động đăng ký ChatGPT account theo kiến trúc hybrid: Phase 1 dùng Camoufox (Firefox anti-detect), Phase 2 dùng `curl_cffi` để extract token nhanh.

---

## Yêu Cầu Hệ Thống

| Yêu cầu | Chi tiết |
|---|---|
| Python | **3.11+** |
| OS | Windows / macOS / Linux |
| RAM | ≥ 4 GB (browser headed mặc định) |
| Network | Cần truy cập `auth.openai.com`, Microsoft Graph API |

---

## 1. Cài Đặt Lần Đầu

### Windows

```bat
setup.bat
```

### macOS / Linux

```bash
bash setup.sh
```

Script này sẽ tự động:
1. Tạo virtual environment `.venv/`
2. Cài toàn bộ dependencies từ `requirements.txt`
3. Tải Camoufox Firefox binary
4. Tạo file `.env` từ `.env.example`

---

## 2. Cấu Hình `.env`

Sau khi setup, mở file `.env` và chỉnh các biến cần thiết:

```env
# Max concurrent jobs (1-10, mặc định 2)
HYBRID_MAX_CONCURRENT=2

# Proxy cho Microsoft Graph API (để trống = direct)
# Format: http://user:pass@host:port  hoặc  socks5://host:port
HYBRID_OUTLOOK_PROXY=

# Job timeout tính bằng giây (phải > 180s — thời gian chờ OTP)
HYBRID_JOB_TIMEOUT=240
```

> **Lưu ý:** File `.env` không commit lên git. Không điền API key / credential vào `.env.example`.

---

## 3. Khởi Tạo Database

Chạy migration trước lần đầu hoặc sau khi pull code mới:

```bash
# Windows
.venv\Scripts\python.exe -m gpt_signup_hybrid migrate

# macOS / Linux
.venv/bin/python -m gpt_signup_hybrid migrate
```

> Database SQLite lưu tại `runtime/data.db` — tạo tự động nếu chưa có.

---

## 4. Chạy Web UI (Khuyến Nghị)

```bash
# Windows
.venv\Scripts\python.exe -m gpt_signup_hybrid web --host 127.0.0.1 --port 8083

# macOS / Linux
.venv/bin/python -m gpt_signup_hybrid web --host 127.0.0.1 --port 8083
```

Truy cập: **http://127.0.0.1:8083**

Web UI gồm 3 tab:
- **Reg** — Đăng ký tài khoản ChatGPT mới
- **Get Session** — Lấy session token từ tài khoản có sẵn
- **Get Link** — Tạo payment link

> **Bind ra ngoài loopback** (VD: server từ xa): dùng `--host 0.0.0.0`. Khi đó Auth Token sẽ được yêu cầu tự động.

---

## 5. Chạy CLI (Nâng Cao)

### 5.1 Đăng Ký Tài Khoản

**Dùng Outlook combo (cascade: DongVanFB → Microsoft fallback):**

```bash
.venv/bin/python -m gpt_signup_hybrid signup \
  --outlook-combo "user@hotmail.com|password|refresh_token|client_id"
```

**Dùng DongVanFB trực tiếp:**

```bash
.venv/bin/python -m gpt_signup_hybrid signup \
  --outlook-combo "user@hotmail.com|password|refresh_token|client_id" \
  --mail-provider dongvanfb
```

**Dùng iCloud Worker relay:**

```bash
.venv/bin/python -m gpt_signup_hybrid signup \
  --email user@icloud.com \
  --mail-provider worker \
  --logs-url https://your-worker.workers.dev/logs \
  --api-key YOUR_API_TOKEN
```

### 5.2 TOTP / 2FA

**Tạo TOTP code từ secret:**

```bash
.venv/bin/python -m gpt_signup_hybrid totp SECRET_BASE32
```

**Bật 2FA cho session đã có:**

```bash
.venv/bin/python -m gpt_signup_hybrid enable-2fa \
  --session-file runtime/sessions/signup-<timestamp>-<email>.json
```

### 5.3 Pool Management

**Xem trạng thái pool:**

```bash
.venv/bin/python -m gpt_signup_hybrid pool-status pool.txt
```

**Import combo pool vào SQLite:**

```bash
.venv/bin/python -m gpt_signup_hybrid import-pool pool.txt
```

---

## 6. Mail Provider — Định Dạng Combo

| Provider | Combo Format | Ghi chú |
|---|---|---|
| `outlook` (cascade) | `email\|password\|refresh_token\|client_id` | Default |
| `dongvanfb` | `email\|password\|refresh_token\|client_id` | Dùng API tools.dongvanfb.net |
| `worker` | Không cần combo — truyền `--logs-url` + `--api-key` | iCloud relay qua Cloudflare Worker |
| `gmail_advanced` | Không cần combo — truyền `--gmail-api-url` | API checkotpgmail.live |

---

## 7. Output Files

Kết quả lưu tại `runtime/sessions/`:

| File | Nội dung |
|---|---|
| `signup-<ts>-<email>.json` | Full SignupResult + token |
| `<file>.2fa.json` | Email + user_id + TOTP secret |
| `accounts.txt` | `email\|password\|2fa_secret` (một dòng / tài khoản) |
| `links.txt` | Payment links (một URL / dòng) |

---

## 8. Troubleshooting

### Lỗi: `ModuleNotFoundError`
→ Chưa kích hoạt venv hoặc chưa cài deps. Chạy lại `setup.bat` / `setup.sh`.

### Lỗi: DB migration fail
→ Chạy `python -m gpt_signup_hybrid migrate` trước khi start web.

### Browser bị detect / Cloudflare block
→ Đảm bảo chạy **headed** (không dùng `--headless`). Đây là mặc định, đừng thay đổi `BROWSER_ENGINE`.

### OTP không nhận được
→ Kiểm tra `refresh_token` còn hạn. Outlook refresh token rotate sau mỗi lần dùng — DB tự cập nhật.

### Web UI không mở được
→ Kiểm tra port `8083` chưa bị chiếm: `netstat -ano | findstr 8083` (Windows).

---

## 9. Cấu Trúc Thư Mục Quan Trọng

```
gpt_signup_hybrid/
├── .env                  # Config (không commit!)
├── runtime/
│   ├── data.db           # SQLite database
│   └── sessions/         # Output: JSON + accounts.txt + links.txt
├── web/
│   ├── server.py         # FastAPI app
│   ├── manager.py        # Job manager
│   └── static/           # Frontend (app.js, session.js, link.js)
├── db/                   # Database layer
├── browser_phase.py      # Phase 1: Camoufox browser automation
├── http_phase.py         # Phase 2: curl_cffi token extraction
├── mail_providers.py     # OTP mail backends
└── signup.py             # Orchestrator
```

---

## 10. Chạy Nhanh (TL;DR)

```bash
# 1. Cài đặt
setup.bat                                     # Windows
bash setup.sh                                 # macOS/Linux

# 2. DB
.venv/bin/python -m gpt_signup_hybrid migrate

# 3. Chạy web
.venv/bin/python -m gpt_signup_hybrid web --host 127.0.0.1 --port 8083

# 4. Mở trình duyệt → http://127.0.0.1:8083
```
