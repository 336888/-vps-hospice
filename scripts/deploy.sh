#!/usr/bin/env bash
# VPS Hospice 一键部署（Debian / Ubuntu）
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/vps-hospice}"
PORT="${PORT:-8000}"

echo "=== 1. 安装依赖 ==="
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nginx

echo "=== 2. 部署代码 ==="
mkdir -p "$APP_DIR"
if [ -d "$(dirname "$0")/../app" ]; then
    cp -r "$(dirname "$0")/.."/* "$APP_DIR"/ 2>/dev/null || true
fi

echo "=== 3. 虚拟环境 ==="
cd "$APP_DIR"
python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt

echo "=== 4. 生成演示数据 ==="
python3 -m data.seed || true

echo "=== 5. systemd ==="
cat > /etc/systemd/system/vps-hospice.service << UNIT
[Unit]
Description=VPS Hospice
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment="PORT=$PORT"
ExecStart=$APP_DIR/.venv/bin/python -m app.main
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable vps-hospice
systemctl restart vps-hospice

echo "=== 6. nginx ==="
cat > /etc/nginx/conf.d/hospice.conf << 'NGX'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGX
nginx -t && systemctl reload nginx

echo
echo "=== 完成 ==="
echo "本地: http://127.0.0.1:$PORT"
echo "服务: systemctl status vps-hospice"
