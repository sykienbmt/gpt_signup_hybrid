# Deploy & Run Guide

## Cloud Server

- **IP**: `132.145.119.18`
- **User**: `opc`
- **Provider**: Oracle Cloud (OCI) — Oracle Linux 9.7
- **SSH Key**: `ssh-key-2026-05-26.key` (trong thư mục này)
- **Project path trên server**: `~/gpt_signup_hybrid`
- **Port**: `8083`
- **URL**: http://132.145.119.18:8083

---

## SSH vào server

```bash
ssh -i ssh-key-2026-05-26.key opc@132.145.119.18
```

---

## Khởi động server (sau reboot hoặc bị tắt)

```bash
ssh -i ssh-key-2026-05-26.key opc@132.145.119.18 "bash ~/start.sh"
```

`start.sh` tự động:
1. Kill process cũ (nếu có)
2. Khởi động Xvfb `:99` (virtual display cho browser)
3. Start web server với `DISPLAY=:99`

---

## Upload code mới lên cloud

### Pack các file đã thay đổi

```bash
# Windows (PowerShell) — chạy từ thư mục gpt_signup_hybrid
tar -czf C:\Temp\update.tar.gz web/static/index.html web/static/app.js web/static/style.css web/static/session.js web/static/link.js web/manager.py web/server.py
```

### Upload + extract

```bash
scp -i ssh-key-2026-05-26.key C:\Temp\update.tar.gz opc@132.145.119.18:~/
ssh -i ssh-key-2026-05-26.key opc@132.145.119.18 "cd ~/gpt_signup_hybrid && tar -xzf ~/update.tar.gz"
```

### Restart server

```bash
ssh -i ssh-key-2026-05-26.key opc@132.145.119.18 "bash ~/start.sh"
```

---

## Upload toàn bộ project (fresh deploy)

```bash
# Từ thư mục D:\my-projects\gpt_signup_hybrid
tar --exclude=".venv" --exclude="runtime" --exclude=".git" --exclude="__pycache__" --exclude="plans" -czf C:\Temp\gpt_signup_hybrid.tar.gz .

scp -i ssh-key-2026-05-26.key C:\Temp\gpt_signup_hybrid.tar.gz opc@132.145.119.18:~/
ssh -i ssh-key-2026-05-26.key opc@132.145.119.18 "mkdir -p ~/gpt_signup_hybrid && cd ~/gpt_signup_hybrid && tar -xzf ~/gpt_signup_hybrid.tar.gz"
```

---

## Setup lần đầu trên server mới (OCI Oracle Linux 9)

```bash
# 1. Cài Python 3.12 + git
sudo dnf install -y python3.12 python3.12-pip git

# 2. Cài system libs cho Playwright/browser
sudo dnf install -y gtk3 dbus-glib libXt nss alsa-lib atk at-spi2-atk \
    libdrm mesa-libgbm libxkbcommon-x11 xorg-x11-server-Xvfb

# 3. Tạo venv Python 3.12
python3.12 -m venv ~/gpt_signup_hybrid/.venv

# 4. Cài Python deps
cd ~/gpt_signup_hybrid
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -r requirements-linux.txt

# 5. Đăng ký package path
SITE_PKG=$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')
echo '/home/opc' > "$SITE_PKG/_gpt_signup_hybrid_root.pth"

# 6. Cài Playwright Firefox + Camoufox binary
.venv/bin/playwright install firefox
.venv/bin/python -m camoufox fetch

# 7. Tạo runtime dirs
mkdir -p runtime/profiles/template runtime/profiles/camoufox_template \
         runtime/sessions runtime/upi_screenshots

# 8. Mở firewall port 8083
sudo firewall-cmd --permanent --add-port=8083/tcp
sudo firewall-cmd --reload

# 9. Tạo start.sh (nếu chưa có — xem bên dưới)
# 10. Khởi động
bash ~/start.sh
```

### Nội dung `~/start.sh` trên server

```bash
#!/bin/bash
pkill Xvfb 2>/dev/null || true
pkill -f "gpt_signup_hybrid web" 2>/dev/null || true
sleep 1
Xvfb :99 -screen 0 1280x800x24 &>/tmp/xvfb.log &
sleep 1
export DISPLAY=:99
cd ~/gpt_signup_hybrid
nohup .venv/bin/python -m gpt_signup_hybrid web --host 0.0.0.0 --port 8083 --unsafe-expose-network \
    > ~/gpt_signup_hybrid/runtime/web.log 2>&1 &
sleep 2
echo "[start.sh] Server running at http://$(curl -s ifconfig.me):8083"
```

---

## OCI Security List (mở port lần đầu)

1. Vào https://cloud.oracle.com
2. **Networking → Virtual Cloud Networks → vcn-20260526-2350 → Security → Default Security List**
3. **Add Ingress Rules**:
   - Source CIDR: `0.0.0.0/0`
   - Protocol: TCP
   - Destination Port: `8083`

---

## Xem log server

```bash
ssh -i ssh-key-2026-05-26.key opc@132.145.119.18 "tail -f ~/gpt_signup_hybrid/runtime/web.log"
```

## Kiểm tra server còn chạy không

```bash
ssh -i ssh-key-2026-05-26.key opc@132.145.119.18 "ps aux | grep gpt_signup | grep -v grep"
```
