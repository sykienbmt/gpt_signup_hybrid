# gpt_signup_hybrid

Tool tự động đăng ký ChatGPT + bật 2FA + lấy session/payment link, dùng **Camoufox + curl_cffi**.

- **Camoufox (Firefox stealth)** đi qua flow signup thật (set password, type OTP, fill form name+age) — Sentinel SDK collect đủ data behavior để bypass anti-abuse.
- **curl_cffi** (impersonate Firefox 135) chỉ làm việc nhẹ Phase 2: extract `__Secure-next-auth.session-token` từ cookies + gọi `/api/auth/session` lấy `accessToken`.
- **Auto enable 2FA** (TOTP) ngay sau signup → output `email|password|secret_2fa`.
- **3 mail providers**: Outlook combo (Microsoft Graph), iCloud Worker, Gmail Advanced (checkotpgmail.live API).
- **3 chế độ làm việc**: Reg (signup batch), Get Session (login lấy session JSON), Get Link (lấy payment URL pay.openai.com).

Avg ~28-35s/account, success rate cao trên combo mới.

---

## 1. Setup — 1 lệnh

Mọi thứ (`.venv`, `runtime/`, `.env`) đều nằm trong chính `gpt_signup_hybrid/`. Tất cả lệnh dưới đây chạy với CWD = `gpt_signup_hybrid/`.

### macOS / Linux

```bash
cd gpt_signup_hybrid
bash setup.sh
```

### Windows

Double-click `setup.bat` (mở trực tiếp từ thư mục `gpt_signup_hybrid/`) hoặc:

```cmd
cd gpt_signup_hybrid
setup.bat
```

Script tự động:
1. Tạo Python venv `.venv/` ngay trong `gpt_signup_hybrid/`.
2. Install deps: `pydantic typer httpx curl_cffi>=0.7 pyotp fastapi uvicorn camoufox playwright`.
3. Inject 1 file `.pth` vào `.venv/lib/.../site-packages/_gpt_signup_hybrid_root.pth` trỏ tới thư mục cha — để Python `-m gpt_signup_hybrid` import được package dù CWD chính là package.
4. Cài Playwright Firefox.
5. Fetch Camoufox binary (`python -m camoufox fetch`).
6. Tạo `.env` mặc định trong `gpt_signup_hybrid/`.
7. Tạo các thư mục `runtime/profiles/template`, `runtime/profiles/camoufox_template`, `runtime/sessions`, `runtime/outlook_state`, `runtime/outlook_pool`, `runtime/har_hybrid`.
8. Start web UI ngay tại `http://127.0.0.1:8089/`.

### Yêu cầu

- Python 3.11+ (recommend 3.12).
- macOS hoặc Windows (Linux cần Xvfb cho headless Camoufox).
- Internet connection.

### Chạy lại sau khi đã setup

```bash
cd gpt_signup_hybrid

# macOS/Linux
.venv/bin/python -m gpt_signup_hybrid web

# Windows
.venv\Scripts\python -m gpt_signup_hybrid web
```

---

## 2. Quick start

### Web UI (recommend)

```bash
cd gpt_signup_hybrid
bash setup.sh
# → http://127.0.0.1:8089/
```

UI có 3 tab độc lập: **Reg**, **Get Session**, **Get Link**.

#### Tab Reg

1. Chọn **Mail Mode**: Hotmail (combo) / iCloud Mail (Worker) / Gmail Advanced (API).
2. Paste input vào textarea (mỗi dòng 1 entry — format thay đổi theo mode, xem help bên dưới textarea).
3. (Optional) Set **Default password** — để trống thì runner gen random 12 ký tự cho mỗi job.
4. (Optional) Set **Timeout (s/job)** — mặc định 240, range [30, 600].
5. Toggle **Fetch Session** / **Fetch Link** nếu muốn lấy thêm session JSON / payment URL ngay sau signup + 2FA.
6. Chọn **Mode** ở topbar: Single (1 job) hoặc Multi (2 song song mặc định, có thể tăng tới 10 qua API hoặc env).
7. Toggle **Headless** (ẩn browser) / **Debug** (giữ browser mở sau khi job xong, chỉ work khi headed).
8. (Optional) Set **Proxy global** ở thanh proxy đầu trang — bấm **Test** để verify reachability tới Microsoft/Graph/IP-check, **Save** để áp.
9. Click **Run** → xem progress realtime ở panel Jobs + Log (click 1 job để focus log).
10. Output:
    - **Success pane**: format `email|password|secret_2fa` mỗi dòng, copy all.
    - **Error pane**: list email lỗi + reason.
11. **Stop All** dừng tất cả job đang queue/run, **Clear Done** giải phóng RAM (xoá job đã xong khỏi memory).

#### Tab Get Session

Login lại account đã có → trả về full `/api/auth/session` JSON (chứa `accessToken`, `user`, `expires`).

- Input: 1 dòng = `email|password|2fa_secret` (secret tuỳ chọn — bỏ qua nếu account chưa bật 2FA).
- Tool dùng Camoufox login đến chatgpt.com (vì auth.openai.com có Cloudflare JS challenge), điền password + TOTP nếu cần.
- Output session JSON hiển thị inline cho từng job (có thể copy).

#### Tab Get Link

Lấy URL `pay.openai.com/c/pay/...` (ChatGPT Plus checkout). 3 mode input:

| Mode | Input format | Hành vi |
|---|---|---|
| **Combo** | `email|password|2fa_secret` mỗi dòng | Login full bằng Camoufox để lấy `accessToken`, rồi POST `/payments/checkout` + Stripe init |
| **Session JSON** | Paste 1 JSON object (output từ Get Session/Reg) | Đọc `accessToken` trực tiếp, không login lại |
| **Access Token** | Mỗi dòng 1 raw JWT bearer | Dùng token thẳng, decode JWT để hiện email |

Output là URL với host đã rewrite từ `checkout.stripe.com` → `pay.openai.com` (giữ nguyên path `/c/pay/cs_...`).

### CLI

CLI subcommands chỉ phục vụ 1 lần signup hoặc helper TOTP/2FA — workflow batch khuyên dùng web UI.

#### Mode A — iCloud mail qua Worker `icloud-cf-mail`

Email `*@icloud.com` được forward về Cloudflare Worker:

```bash
.venv/bin/python -m gpt_signup_hybrid signup --email foo@icloud.com
```

Default Worker URL = `https://icloud-cf-mail.n5pskgzs9g.workers.dev/logs`, key = `12345678@`.

#### Mode B — Outlook/Hotmail combo (Microsoft Graph)

Mỗi combo: `email|password|refresh_token|client_id`:

```text
benjaminreiddavis8195@hotmail.com|benjamin@669178|M.C524_BL2.0...|9e5f94bc-e8a4-4e73-b8be-63364c29d753
```

Truyền inline:

```bash
.venv/bin/python -m gpt_signup_hybrid signup \
  --outlook-combo 'mail|pass|M.C...|9e5f94bc-...'
```

Hoặc qua file (tránh leak shell history):

```bash
echo 'mail|pass|M.C...|9e5f94bc-...' > runtime/outlook_pool/single.txt
.venv/bin/python -m gpt_signup_hybrid signup --outlook-combo-file runtime/outlook_pool/single.txt
```

#### Mode C — Pool nhiều combo Outlook (recommend cho batch CLI)

```bash
# Pool format: 1 combo / dòng. Comment bằng #.
cat > runtime/outlook_pool/batch.txt <<EOF
mail1@hotmail.com|pwd1|M.C548_BAY...|9e5f94bc-e8a4-4e73-b8be-63364c29d753
mail2@outlook.com|pwd2|M.C525_BAY...|9e5f94bc-e8a4-4e73-b8be-63364c29d753
mail3@hotmail.com|pwd3|M.C530_BL2...|9e5f94bc-e8a4-4e73-b8be-63364c29d753
EOF

# Pool tự pick combo còn khả dụng, mark used sau khi success.
.venv/bin/python -m gpt_signup_hybrid signup --outlook-pool runtime/outlook_pool/batch.txt
```

Xem trạng thái pool:

```bash
.venv/bin/python -m gpt_signup_hybrid pool-status runtime/outlook_pool/batch.txt
# {"pool": "...", "total": 3, "used_for_signup": 1, "available": 2, "terminal_error": 0}
```

> **Lưu ý**: CLI không hỗ trợ Gmail Advanced (chỉ web UI dùng được), vì input chỉ là 1 URL API, không có schema CLI option.

---

## 3. CLI reference

### 3.1 `signup` — chạy 1 lần signup

```text
.venv/bin/python -m gpt_signup_hybrid signup [OPTIONS]
```

| Option | Default | Mô tả |
|---|---|---|
| `--email` | (auto từ combo) | Email đăng ký. Tự derive từ phần đầu Outlook combo nếu không truyền. |
| `--name` | `ChatGPT User` (sau đó random) | Tên hiển thị (form `/about-you`). Nếu vẫn là default sẽ bị runner override bằng tên random. |
| `--birthdate` | `2000-01-01` (sau đó random) | YYYY-MM-DD, tuổi >= 13. Nếu vẫn default sẽ random sang age 19-30. |
| `--smail` | `--email` | Mailbox poll OTP (nếu khác email form). |
| `--mail-provider` | auto | `worker` / `outlook`. Auto-detect: `outlook` nếu có combo, ngược lại `worker`. |
| `--logs-url` | `https://icloud-cf-mail.n5pskgzs9g.workers.dev/logs` | [worker] Worker logs API. |
| `--api-key` | `12345678@` | [worker] Bearer token cho Worker. |
| `--insecure-tls/--secure-tls` | bật | [worker] Bỏ verify TLS (host có `_`). |
| `--outlook-combo` | `null` | [outlook] Combo inline (không recommend, lộ shell history). |
| `--outlook-combo-file` | `null` | [outlook] File chứa 1 combo (chỉ đọc dòng đầu). |
| `--outlook-pool` | `null` | [outlook] File pool nhiều combo, auto pick combo còn khả dụng. |
| `--headless/--headed` | `--headed` | Camoufox visible / hidden. |
| `--off-font` | tắt | Tắt camoufox font randomization (fix hex glyph box). |
| `--profile-template/--fresh-profile` | `--profile-template` | Clone `runtime/profiles/camoufox_template`. |
| `--proxy` | `null` | HTTP/HTTPS proxy cho Phase 1 + Phase 2. |
| `--otp-timeout` | `180.0` | Hard deadline đợi OTP (giây), min 10. |
| `--otp-interval` | `4.0` | Gap giữa 2 lần poll, min 0.5. |
| `--sentinel-timeout` | `30.0` | Timeout đợi OTP form ready, min 5. |
| `--har/--no-har` | tắt | Bật HAR capture Phase 1 cho debug. Output `runtime/har_hybrid/<ts>.har`. |
| `--output, -o` | `runtime/sessions/signup-<ts>-<email>.json` | Output JSON path. |

### 3.2 `pool-status` — in trạng thái pool

```bash
.venv/bin/python -m gpt_signup_hybrid pool-status <pool_file>
```

Output JSON:
```json
{
  "pool": "/path/to/pool.txt",
  "total": 10,
  "used_for_signup": 4,
  "available": 5,
  "terminal_error": 1
}
```

### 3.3 `totp` — gen TOTP code

Gen 6-digit code từ secret base32 (lấy từ `enable-2fa` hoặc nhập tay từ Authenticator app):

```bash
.venv/bin/python -m gpt_signup_hybrid totp <SECRET>
.venv/bin/python -m gpt_signup_hybrid totp DKDCLDHEHC7PNSSYK3CVF6JPWA6HTDNK
# {
#   "code": "863534",
#   "valid_for_seconds": 11
# }
```

Có thể truyền `--account` để gen `otpauth://` URI cho QR code:

```bash
.venv/bin/python -m gpt_signup_hybrid totp <SECRET> --account foo@hotmail.com
```

### 3.4 `enable-2fa` — bật 2FA cho 1 account

Cần `access_token` từ SignupResult JSON. Flow: `POST /backend-api/accounts/mfa/enroll` (lấy secret) → `POST /backend-api/accounts/mfa/user/activate_enrollment` (verify với code TOTP).

```bash
.venv/bin/python -m gpt_signup_hybrid enable-2fa \
  -f runtime/sessions/signup-20260519-115540-foo_at_hotmail.com.json
```

Output `<session-file>.2fa.json`:
```json
{
  "email": "foo@hotmail.com",
  "user_id": "user-...",
  "account_id": "...",
  "two_factor": {
    "secret": "DKDCLDHEHC7PNSSYK3CVF6JPWA6HTDNK",
    "factor_id": "6a0bedbc94f48191be17",
    "session_id": "...",
    "first_code": "863534",
    "activated": true,
    "provisioning_uri": "otpauth://totp/ChatGPT?secret=...&issuer=ChatGPT",
    "mfa_info": { "mfa_enabled": true }
  }
}
```

Options:
- `--enroll-only` — chỉ lấy secret, không activate (để tự confirm sau qua UI).
- `--proxy` — HTTP/HTTPS proxy.
- `--output, -o` — custom path output.

Module `mfa_phase` có retry/backoff cho 502/503/504 (4 attempts, backoff 3s/6s/10s) và auto refresh `access_token` qua cookies nếu gặp 401 `token_revoked`.

### 3.5 `web` — start web UI

```bash
.venv/bin/python -m gpt_signup_hybrid web [OPTIONS]
```

Options:
| Option | Default | Mô tả |
|---|---|---|
| `--host` | `127.0.0.1` | Bind host. |
| `--port` | `8089` | Bind port. |
| `--reload` | tắt | Auto-reload (dev mode). |

Server suppress hầu hết uvicorn log để chừa stdout cho job log.

### 3.6 `version` (hidden)

```bash
.venv/bin/python -m gpt_signup_hybrid version
# gpt_signup_hybrid 0.1.0
```

---

## 4. Web UI Backend API

### 4.1 Common (config + jobs Reg)

| Method | Path | Mô tả |
|---|---|---|
| GET | `/` | UI HTML. |
| GET | `/static/*` | CSS/JS/HTML assets. |
| GET | `/api/jobs` | List jobs Reg + config (max_concurrent, headless, debug, job_timeout, proxy). |
| POST | `/api/jobs` | Add jobs từ textarea. Body: `{combos, default_password?, mail_mode, email_logs_url?, email_api_key?}`. |
| GET | `/api/jobs/<id>` | Detail job. |
| GET | `/api/jobs/<id>/log` | Toàn bộ log lines. |
| POST | `/api/jobs/<id>/retry` | Reset state + chạy lại (smart: nếu signup OK nhưng 2FA fail thì chỉ retry Phase 2). |
| DELETE | `/api/jobs/<id>` | Cancel + remove. |
| POST | `/api/jobs/stop-all` | Cancel tất cả jobs đang running/queued. |
| POST | `/api/jobs/clear-finished` | Xoá tất cả jobs đã xong khỏi memory. |
| GET | `/api/config` | Trả config hiện tại. |
| POST | `/api/config` | Update config. Body: `{max_concurrent?, headless?, debug?, job_timeout?, proxy?, post_reg_get_session?, post_reg_get_link?}`. |
| GET | `/api/mail-modes` | List mail modes available cho UI render selector + config panels. |
| POST | `/api/proxy/test` | Test proxy reachability. Body: `{proxy?}`. Test 3 endpoint: `login.microsoftonline.com`, `graph.microsoft.com`, `api.ipify.org`. |
| GET | `/api/events` | SSE stream — snapshot + realtime job/log events. |

### 4.2 Get Session

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/session/jobs` | List session jobs. |
| POST | `/api/session/jobs` | Add jobs. Body: `{combos}` (mỗi dòng `email|password|secret`). |
| GET | `/api/session/jobs/<id>` | Detail (kèm `session_data`). |
| GET | `/api/session/jobs/<id>/log` | Log lines. |
| POST | `/api/session/jobs/<id>/retry` | Retry. |
| DELETE | `/api/session/jobs/<id>` | Cancel + remove. |
| POST | `/api/session/jobs/stop-all` | Stop all. |
| POST | `/api/session/jobs/clear-finished` | Clear done. |
| GET | `/api/session/config` | Config (max_concurrent, job_timeout). |
| POST | `/api/session/config` | Update config. |
| GET | `/api/session/events` | SSE stream. |

### 4.3 Get Link

| Method | Path | Mô tả |
|---|---|---|
| GET | `/api/link/jobs` | List link jobs. |
| POST | `/api/link/jobs` | Add jobs. Body: `{combos, mode}` với `mode ∈ {combo, session_json, access_token}`. |
| GET | `/api/link/jobs/<id>` | Detail (kèm `payment_link`). |
| POST | `/api/link/jobs/<id>/retry` | Retry (chỉ allowed khi status=error). |
| DELETE | `/api/link/jobs/<id>` | Remove. |
| POST | `/api/link/jobs/stop-all` | Stop all. |
| POST | `/api/link/jobs/clear-finished` | Clear done. |
| GET | `/api/link/events` | SSE stream. |

---

## 5. Mail provider chi tiết

### 5.1 `WorkerMailProvider` — iCloud relay

- GET `<logs_url>?mail=<email>` với header `Authorization: Bearer <api_key>`.
- Parse JSON response: list trực tiếp hoặc `{messages|items|logs|emails|data: [...]}`.
- Filter messages: chỉ giữ message mới hơn `started_at`, regex tìm 6 chữ số trong subject/body.
- Insecure-TLS bật mặc định (host Worker thường có ký tự `_` không hợp chuẩn).

### 5.2 `OutlookMailProvider` — Microsoft Graph

- POST `login.microsoftonline.com/consumers/oauth2/v2.0/token` với `grant_type=refresh_token` → `access_token` + `refresh_token` mới (rotate).
- Persist token mới ra `runtime/outlook_state/<email>.json` ngay sau mỗi lần refresh — **bắt buộc** vì lần sau dùng combo gốc sẽ bị `invalid_grant`.
- GET `graph.microsoft.com/v1.0/me/messages?$top=5&$orderby=receivedDateTime desc&$select=...` (toàn mailbox, không filter folder — bắt được cả Inbox lẫn Junk).
- Filter: chỉ accept message từ sender chứa `openai.com` (hoặc subject chứa `openai`) để tránh nhặt nhầm OTP của Microsoft.
- Phân biệt fatal vs transient error:
  - Fatal (`invalid_grant`, `AADSTS50173`, `AADSTS70008`, `AADSTS50034`, `AADSTS50057`, `AADSTS700016`, `unauthorized_client`) → raise `OutlookComboError`, combo coi như chết.
  - Transient (5xx, network blip) → raise `OutlookProviderUnavailable`, retry 3 lần liên tiếp; nếu vẫn fail thì bail nhanh (không chờ hết OTP timeout 180s).
- Hỗ trợ proxy (HTTP/HTTPS/SOCKS5). Proxy URL có credential sẽ ẩn `user:pass` trong log.
- Timeout chuẩn: connect 6s, read 12s.

### 5.3 `GmailAdvancedProvider` — checkotpgmail.live API

- Input format (web UI): `email|api_url` hoặc chỉ `api_url` (URL-only).
- API response mẫu:
  ```json
  {
    "ok": true,
    "order_id": "...",
    "service": "chatgpt",
    "email": "brandonspencer7424@gmail.com",
    "status": "success",
    "mail_status": "live",
    "otp": "123456",
    "otp_history": [...]
  }
  ```
- **Pre-check** trước khi mở browser: GET API 1 lần để verify `ok=true` + `mail_status=live`. Nếu `mail_status != live` → fail job ngay.
- URL-only mode: tool tự đọc `email` từ response pre-check (placeholder `pending@gmail-advanced.local`).
- Poll: GET liên tục, lấy `otp` 6-digit; fallback đọc `otp_history` (last item) nếu API ghi vào history thay vì field hiện hành.
- Status terminal: `expired`, `cancelled`, `not_found` → raise `TimeoutError`.

---

## 6. Output JSON

`runtime/sessions/signup-<ts>-<email>.json` (model `SignupResult`):

```json
{
  "success": true,
  "email": "calumbrooksjebp@outlook.com",
  "password": "Abc12345xyz@",
  "name": "Charles White",
  "age": 24,
  "user_id": "user-yfNPJmidFXvIHbKYELyqHyDh",
  "account_id": "957d5570-ddd2-42b1-9faf-6eddc0beef3a",
  "session_token": "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..xxx",
  "access_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6IjE5MzQ0..yyy",
  "cookies": [
    {"name": "_account", "value": "...", "domain": "chatgpt.com", "path": "/", "secure": false},
    {"name": "__Secure-next-auth.session-token.0", "value": "...", "domain": ".chatgpt.com", "path": "/", "secure": true},
    {"name": "__Secure-next-auth.session-token.1", "value": "...", "domain": ".chatgpt.com", "path": "/", "secure": true}
  ],
  "phase1_seconds": 28.73,
  "phase2_seconds": 0.22,
  "otp_seconds": 6.78,
  "error": null
}
```

Field quan trọng:
- `session_token` — `__Secure-next-auth.session-token` JWT (NextAuth). Nếu cookie chunked thành `.0`/`.1`, runner tự ghép.
- `access_token` — Bearer JWT cho `/backend-api/`.
- `cookies` — list cookies chatgpt.com dạng Playwright dict, inject vào browser khác để dùng tiếp.
- `password` / `name` / `age` — credential đã set khi signup (random nếu không truyền).

Thêm file `<session>.2fa.json` sau khi enable 2FA:

```json
{
  "email": "...",
  "user_id": "...",
  "two_factor": {
    "secret": "...",
    "factor_id": "...",
    "session_id": "...",
    "first_code": "863534",
    "activated": true,
    "provisioning_uri": "otpauth://...",
    "mfa_info": { "mfa_enabled": true }
  }
}
```

---

## 7. Architecture

```
gpt_signup_hybrid/
├── __init__.py          # exports public API: SignupRequest, SignupResult, BrowserHandoff, run_signup
├── __main__.py          # python -m gpt_signup_hybrid → CLI
├── cli.py               # typer: signup, pool-status, totp, enable-2fa, web, version
├── config.py            # Settings + load_settings + prepare_profile_dir + runtime dirs
├── models.py            # Pydantic: SignupRequest, BrowserHandoff, SignupResult
├── random_profile.py    # Random name + age + password (12 ký tự kết thúc @|#) + birthdate
├── totp_helper.py       # pyotp wrapper: generate_code, time_remaining, provisioning_uri, verify
├── mail_providers.py    # WorkerMailProvider, OutlookMailProvider, GmailAdvancedProvider + builders
├── outlook_pool.py      # parse pool, pick available combo, mark success/failure state
├── browser_phase.py     # Phase 1 — Camoufox state machine (chính)
├── http_phase.py        # Phase 2 — extract session-token + fetch access_token
├── mfa_phase.py         # Enable 2FA TOTP qua /mfa/enroll + /mfa/user/activate_enrollment
├── session_phase.py     # Get Session: login browser HOẶC HTTP-only via cookies
├── payment_link.py      # Get pay.openai.com URL: checkout API + Stripe init fallback
├── signup.py            # Orchestrator chính: provider → Phase 1 → Phase 2
├── setup.sh / setup.bat # 1-lệnh setup + start web
├── .env.example         # HYBRID_MAX_CONCURRENT, HYBRID_OUTLOOK_PROXY, HYBRID_JOB_TIMEOUT
└── web/
    ├── __init__.py
    ├── server.py        # FastAPI: /api/jobs, /api/session/*, /api/link/*, /api/proxy/test, SSE /api/events
    ├── manager.py       # 3 job managers: JobManager (Reg), SessionJobManager, LinkJobManager — worker pool + stagger
    ├── mail_modes.py    # Registry pattern: OUTLOOK_MODE, WORKER_MODE, GMAIL_ADVANCED_MODE
    └── static/
        ├── index.html   # 3 tab: Reg, Get Session, Get Link
        ├── style.css
        ├── app.js       # Reg tab logic
        ├── session.js   # Get Session tab
        └── link.js      # Get Link tab
```

### 7.1 Phase 1 — Camoufox (state machine `_drive_signup_flow`)

Detect màn hình hiện tại từ URL + DOM, dispatch handler. Lặp đến khi đến `chatgpt.com` (login OK) hoặc fail.

Các screen:
- `chatgpt` — đã login xong, page ở `chatgpt.com` → đợi session cookies → return.
- `continue` — `/email-verification` có button "Continue with password" → click.
- `password_create` — `/create-account/password` → POST `/api/accounts/user/register` `{username, password}` → server trả `continue_url` → goto trigger OTP send.
- `password_login` — `/log-in/password` (account đã tồn tại) → fill password + submit.
- `otp` — input `[name=code]` visible → poll OTP qua mail provider → fill + submit. Có retry: detect "incorrect/expired" → click Resend → poll lại; stuck >30s sau submit → re-poll code mới.
- `about_you` — fill name + age (force click vì label che) → đợi cookie `oai-sc` (Sentinel) → POST `/api/accounts/create_account` qua page evaluate.
- `auth_error` — raise `BrowserPhaseError`.
- `unknown` — đợi page settle.

Sau `/about-you` (signup) hoặc login thẳng → đợi cookie `__Secure-next-auth.session-token` (single hoặc chunked `.0`/`.1`) trên `chatgpt.com` → exfil cookies → đóng browser.

Bootstrap đầu tiên:
1. `goto chatgpt.com/`.
2. JS evaluate: `GET /api/auth/csrf` → `csrfToken` → `POST /api/auth/signin/openai?login_hint=<email>&...` → trả URL `auth.openai.com/api/accounts/authorize?...&state=...`.
3. `goto authorize URL` → server set ~10 cookies session → redirect `/email-verification` → state machine bắt đầu.

Profile management:
- Mỗi job clone profile từ `runtime/profiles/camoufox_template` sang `runtime/profiles/<job_id>` (bỏ qua `BrowserMetrics`, `Crashpad`, `LOCK`, `Singleton*`).
- Cleanup profile sau khi job xong.
- Viewport mặc định 1440x800 (config qua env). Camoufox nhận `screen.width/height/avail*` qua extra config.

### 7.2 Phase 2 — curl_cffi (extract + verify, ~0.2s)

1. Đọc cookies `chatgpt.com` từ handoff.
2. Lấy `session_token` từ `__Secure-next-auth.session-token` (single) hoặc ghép `.0` + `.1` (chunked).
3. Lấy `account_id` từ cookie `_account`.
4. Build curl_cffi `Session(impersonate=firefox135)`, inject cookies.
5. GET `chatgpt.com/api/auth/session` → `accessToken` JWT + `user.id`.

Phase 2 không gọi lại `email-otp/validate` hoặc `create_account` (browser đã làm), chỉ extract + warmup.

### 7.3 Mail Mode Registry (`web/mail_modes.py`)

Mỗi `MailModeSpec` định nghĩa:
- `id`, `label`, `input_placeholder`, `input_help` — cho UI.
- `parse_line(line) -> ParsedLine` — validate từng dòng input, raise `MailModeParseError` nếu sai.
- `build_request(parsed, **kwargs) -> SignupRequest` — build request signup từ parsed + config (worker_config, password, headless, proxy, ...).
- `config_schema` — list field config cho UI render selector + persist localStorage. Hiện chỉ `worker` có schema (logs_url + api_key).

Thêm provider mới = thêm 1 spec vào `_REGISTRY` + add factory trong `mail_providers.py`.

### 7.4 Job Manager (worker pool pattern)

3 manager singleton: `JobManager` (Reg), `SessionJobManager`, `LinkJobManager` — cùng pattern.

- Job queue (`asyncio.Queue[str]`) chứa job_id. N worker coroutine (`_worker_loop`) lấy job từ queue, chạy tuần tự từng cái.
- Scale concurrency runtime: thêm task khi `set_max_concurrent` tăng, cancel task thừa khi giảm — không restart.
- **Stagger**: khi `max_concurrent > 1`, mỗi worker đợi ít nhất 5s + jitter random 5-10s sau lần start gần nhất → tránh nhiều browser khởi tạo cùng tick.
- Broadcast event qua SSE: `{type: "snapshot|job|log|remove|clear_finished"}` cho subscriber bằng `asyncio.Queue` (drop event nếu queue đầy).
- Smart retry (`JobManager.retry_job`): nếu signup đã thành công (có `session_path`) nhưng 2FA fail → retry chỉ Phase 2, không signup lại.
- Job timeout: wrap `_run_job` trong `asyncio.wait_for(timeout=job_timeout)`. Debug + headed → no timeout (chờ user cancel).

---

## 8. Pool management chi tiết

### 8.1 Format pool file

```text
# Comment dòng đầu (#)
email1@hotmail.com|password1|refresh_token_1|client_id_1
email2@outlook.com|password2|refresh_token_2|client_id_2
```

- 1 combo / dòng. Phải có đủ 4 phần phân cách `|`.
- `client_id` Outlook desktop: `8b4ba9dd-3ea5-4e5f-86f1-ddba2230dcf2`.
- `client_id` Outlook mobile: `9e5f94bc-e8a4-4e73-b8be-63364c29d753`.
- `refresh_token` phải bắt đầu bằng `M.C` (Microsoft refresh token format).
- Email trùng trong cùng pool sẽ bị reject.

### 8.2 State tracker

Mỗi combo có 1 state file `runtime/outlook_state/<email>.json`:

```json
{
  "email": "...",
  "client_id": "9e5f94bc-...",
  "refresh_token": "M.C...",
  "last_refresh_at": "2026-05-19T03:30:00+00:00",
  "expires_in": 3599,
  "scope": "https://graph.microsoft.com/.default ...",
  "used_for_signup": true,
  "used_at": "2026-05-19T03:35:00+00:00"
}
```

Field quan trọng:
- `used_for_signup: true` → pool skip lần sau.
- `last_error` chứa terminal pattern → pool skip:
  - `registration_disallowed` — OpenAI block.
  - `invalid_grant` — refresh_token chết.
  - `AADSTS50173` / `AADSTS70008` — token revoked/expired.

Token rotate sau mỗi lần refresh — runner persist atomically (`*.tmp` rồi rename) vào state file. **Không xoá state file** vì lần sau dùng combo gốc sẽ bị `invalid_grant`.

### 8.3 Selection logic

```
for combo in pool:
    state = read_state(combo.email)
    if state["used_for_signup"]: skip
    if state["last_error"] in TERMINAL_ERRORS: skip
    # Hydrate refresh_token mới nhất từ state vào combo
    yield combo
```

`pick_first_available` trả combo đầu tiên còn dùng được. Nếu hết → exit 1 với message rõ.

### 8.4 Sau khi run

- Success → `mark_signup_success` → `used_for_signup: true` + clear `last_error`.
- Fail terminal (`registration_disallowed`, `invalid_grant`, `AADSTS5*`) → `mark_signup_failure` ghi `last_error` → skip lần sau.
- Fail transient (timeout, network) → cũng ghi `last_error` nhưng không match terminal pattern → vẫn được retry lần sau.

---

## 9. Configuration

Toàn bộ config nằm trong `gpt_signup_hybrid/.env` (file này được `setup.sh` tạo sẵn). Override bất kỳ key nào qua `os.environ` lúc chạy.

```dotenv
# ── Browser / runtime (đọc bởi config.load_settings) ─────────────────
BROWSER_ENGINE=camoufox
RUNTIME_DIR=runtime
BROWSER_VIEWPORT_WIDTH=1440
BROWSER_VIEWPORT_HEIGHT=800
BROWSER_USE_PROFILE_TEMPLATE=true
BROWSER_PROFILE_TEMPLATE_DIR=runtime/profiles/template
BROWSER_CAMOUFOX_PROFILE_DIR=runtime/profiles/camoufox_template

# ── Web UI (đọc bởi web.manager) ─────────────────────────────────────
# Max concurrent jobs (web UI multi mode). Range [1, 10], mặc định 2.
HYBRID_MAX_CONCURRENT=2

# Proxy chung cho web UI (set thẳng vào JobManager).
# Format: http://user:pass@host:port hoặc socks5://host:port. Để trống = direct.
HYBRID_OUTLOOK_PROXY=

# Job timeout (giây). Range [30, 600]. Phải > OTP timeout (180s). Mặc định 240.
HYBRID_JOB_TIMEOUT=240
```

Override runtime: `HYBRID_MAX_CONCURRENT=4 .venv/bin/python -m gpt_signup_hybrid web`.

Project root được resolve theo thứ tự: arg `root_dir` → env `GPT_REG_ROOT` → `Path.cwd()`. Khi chạy CLI/web từ chính `gpt_signup_hybrid/`, CWD chính là root → mọi path tương đối (`runtime/...`) đều resolve trong package.

---

## 10. Failure modes & debug

### 10.1 Phase 1 không đến được `/email-verification`

Nguyên nhân:
- IP bị Cloudflare bounce → đổi proxy / network.
- Profile template bị poison cookies cũ → CLI: `--fresh-profile`.
- Camoufox font glyph hex → CLI: `--off-font`.

### 10.2 OTP timeout

#### Worker:
- Email không có forwarder vào Worker. Test thủ công:
  ```bash
  curl -H 'Authorization: Bearer 12345678@' \
    'https://icloud-cf-mail.n5pskgzs9g.workers.dev/logs?mail=foo@icloud.com'
  ```

#### Outlook:
- Combo expire (refresh_token chết). Verify:
  ```bash
  .venv/bin/python -c "import httpx; r=httpx.post('https://login.microsoftonline.com/consumers/oauth2/v2.0/token', data={'client_id':'9e5f94bc-e8a4-4e73-b8be-63364c29d753','scope':'https://graph.microsoft.com/.default offline_access','refresh_token':'M.C...','grant_type':'refresh_token'}); print(r.status_code, r.text[:200])"
  ```
- Network không tới được Microsoft (proxy fail). Web UI: dùng nút **Test** ở proxy strip.

#### Gmail Advanced:
- Pre-check trả `mail_status != live` → job fail ngay với message rõ.
- API trả `status ∈ {expired, cancelled, not_found}` → raise terminal.

### 10.3 Sentinel block: `registration_disallowed`

- Server detect bot behavior. Tool đã fix bằng cách type tên + tuổi vào form thật + đợi `oai-sc` cookie.
- Vẫn gặp = IP đã trigger anti-abuse → đổi proxy / đổi combo.

### 10.4 Outlook combo: `invalid_grant`

`refresh_token` cũ đã rotate. State file `runtime/outlook_state/<email>.json` lưu token mới nhất — nếu file mất hoặc đã reset, phải xin combo mới.

### 10.5 Bật HAR debug

```bash
.venv/bin/python -m gpt_signup_hybrid signup --outlook-pool runtime/outlook_pool/batch.txt --har
```

HAR lưu vào `runtime/har_hybrid/hybrid-<ts>.har`.

### 10.6 Get Link errors

- **`SessionExpiredError` (HTTP 401)** — `accessToken` đã hết hạn / revoke. Mode `combo` thì auto login lại; mode `session_json`/`access_token` cần refresh.
- **`CloudflareBlockedError` (HTTP 403 + CF markers)** — CF challenge đang block. Đổi proxy / IP.
- **`StripeInitError`** — Stripe trả response thiếu `stripe_hosted_url`. Throttle / blacklist.

---

## 11. Performance

### 11.1 Timing breakdown trung bình (combo Outlook)

| Stage | Duration | Note |
|---|---|---|
| Camoufox cold start | 2-3s | Profile clone + browser launch |
| chatgpt.com load + bootstrap NextAuth | 3-4s | csrf + signin/openai |
| Goto authorize → `/email-verification` | 2-3s | CF challenge + Sentinel |
| Click "Continue with password" → set password → register | 3-5s | POST /user/register + trigger OTP send |
| Poll OTP | 2-15s | Outlook Graph API ~6s, Worker iCloud <1s, Gmail Advanced 5-15s |
| Type OTP + submit | 1-2s | |
| Sentinel SDK fire `oai-sc` cookie | <1s | |
| Fill name + age + click submit `/about-you` | 4-5s | Form render delay |
| Capture callback + đợi session-token | 3-5s | NextAuth process |
| Phase 2 (curl_cffi) | 0.2s | Extract + GET `/api/auth/session` |
| Enable 2FA | 1-3s | enroll + activate (có retry/backoff) |
| **Total** | **~28-35s** | |

### 11.2 Stagger trong multi mode

Khi `max_concurrent > 1`, worker thứ N đợi `last_start_ts + 5s + jitter(5-10s)` trước khi launch browser mới — tránh OOM/Cloudflare 429 khi N browser khởi tạo cùng tick.

---

## 12. Limitations đã biết

1. **`accessToken` không tự refresh** — JWT expire sau ~10 ngày. Cần dùng tab Get Session login lại khi cần token mới.
2. **Cookie `cf_clearance` valid ngắn (~30 phút)** — không reuse handoff cho lần khác.
3. **Phụ thuộc Worker icloud-cf-mail** (mode A) — Worker down thì OTP poll fail.
4. **OpenAI rate-limit per IP** — không nên >5 signup/giờ trên 1 IP. Dùng proxy rotate.
5. **Outlook không support sub-address** (`+`) — mỗi combo gắn 1 email duy nhất, không alias.
6. **Refresh_token rotate sau mỗi call** — phải persist state, không reuse combo gốc.
7. **`oai-sc` cookie chỉ set sau khi browser load `/about-you`** — Phase 2 không thể replay create_account thuần HTTP.
8. **Random profile** — name/age/birthdate gen ngẫu nhiên (age 19-30) trừ khi truyền explicit qua `--name` / `--birthdate`. Pattern password: 1 chữ HOA + giữa lower+digit + kết thúc `@` hoặc `#`.
9. **CLI không hỗ trợ Gmail Advanced** — chỉ dùng được qua web UI.

---

## 13. Workflow điển hình

### 13.1 Batch signup CLI với pool Outlook

```bash
cd gpt_signup_hybrid

# 1. Tạo pool file 10 combo
cat > runtime/outlook_pool/oct2026.txt <<EOF
mail1@hotmail.com|pwd1|M.C548...|9e5f94bc-...
mail2@outlook.com|pwd2|M.C525...|9e5f94bc-...
...
mail10@hotmail.com|pwd10|M.C530...|9e5f94bc-...
EOF

# 2. Check status pool
.venv/bin/python -m gpt_signup_hybrid pool-status runtime/outlook_pool/oct2026.txt

# 3. Chạy 10 lần, mỗi lần pool tự pick combo kế tiếp
for i in {1..10}; do
  .venv/bin/python -m gpt_signup_hybrid signup --outlook-pool runtime/outlook_pool/oct2026.txt
  sleep 3   # đợi 3s giữa các signup
done

# 4. Output ở runtime/sessions/signup-*.json + signup-*.2fa.json
ls runtime/sessions/

# 5. Pool status: 10 used + 0 available
.venv/bin/python -m gpt_signup_hybrid pool-status runtime/outlook_pool/oct2026.txt
```

### 13.2 Web UI flow đầy đủ

1. Mở UI tab **Reg** → chọn Mail Mode → paste combo → bật toggle **Fetch Session** + **Fetch Link** → Run.
2. Tool tự động: signup → enable 2FA → fetch `/api/auth/session` JSON → fetch `pay.openai.com` URL.
3. Click 1 job để xem detail; copy `email|password|secret_2fa` từ Success pane.
4. Tab **Get Link** với mode `session_json` paste lại JSON đã có để regen URL khác (ví dụ promo khác).
5. Tab **Get Session** dùng để login lại sau ~10 ngày khi `accessToken` expire — input `email|password|secret`.

### 13.3 Dùng output cho automation khác

Inject session vào Playwright:
```python
import json
from playwright.async_api import async_playwright

data = json.load(open("runtime/sessions/signup-xxx.json"))
async with async_playwright() as pw:
    browser = await pw.firefox.launch(headless=False)
    ctx = await browser.new_context()
    await ctx.add_cookies(data["cookies"])
    page = await ctx.new_page()
    await page.goto("https://chatgpt.com/")  # đã login
```

Gọi `/backend-api/` bằng `access_token`:
```python
import json, httpx
data = json.load(open("runtime/sessions/signup-xxx.json"))
headers = {"Authorization": f"Bearer {data['access_token']}"}
r = httpx.get("https://chatgpt.com/backend-api/me", headers=headers)
print(r.json())
```

Gen TOTP code từ secret bất kỳ lúc nào:
```bash
.venv/bin/python -m gpt_signup_hybrid totp <SECRET>
```

---

## 14. Public API Python

```python
from gpt_signup_hybrid import SignupRequest, SignupResult, run_signup

async def main():
    req = SignupRequest(
        email="foo@hotmail.com",
        mail_provider="outlook",
        outlook_combo="foo@hotmail.com|pwd|M.C...|9e5f94bc-...",
        headless=True,
    )
    result: SignupResult = await run_signup(req)
    print(result.success, result.session_token, result.access_token)
```

Module utility độc lập:
```python
from gpt_signup_hybrid.totp_helper import generate_code, time_remaining
from gpt_signup_hybrid.random_profile import random_profile
from gpt_signup_hybrid.session_phase import get_session, fetch_session_via_http
from gpt_signup_hybrid.payment_link import get_checkout_url
from gpt_signup_hybrid.mfa_phase import enable_2fa
```
