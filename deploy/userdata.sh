#!/bin/bash
exec > /var/log/userdata.log 2>&1
set -e

# ---- Only two things you edit ----
S3_BUCKET="taskmatch-deploy-k3dmx"
REGION="us-east-1"

echo "[1/8] Installing packages..."
dnf update -y
dnf install -y python3-pip python3 nginx awscli mariadb105

echo "[2/8] Fetching secrets from SSM Parameter Store..."
get_param() {
    aws ssm get-parameter \
        --name "$1" \
        --with-decryption \
        --query "Parameter.Value" \
        --output text \
        --region "$REGION"
}

DB_HOST=$(get_param "/taskmatch/DB_HOST")
DB_USER=$(get_param "/taskmatch/DB_USER")
DB_PASS=$(get_param "/taskmatch/DB_PASS")
DB_NAME=$(get_param "/taskmatch/DB_NAME")
SECRET_KEY=$(get_param "/taskmatch/SECRET_KEY")

echo "[3/8] Writing env file..."
cat > /etc/taskmatch.env <<EOF
DB_HOST=$DB_HOST
DB_USER=$DB_USER
DB_PASS=$DB_PASS
DB_NAME=$DB_NAME
SECRET_KEY=$SECRET_KEY
EOF
chmod 600 /etc/taskmatch.env

echo "[4/8] Pulling app bundle from S3..."
mkdir -p /opt/taskmatch
aws s3 cp "s3://$S3_BUCKET/taskmatch-deploy-k3dmx.tar.gz" /tmp/ --region "$REGION"
tar -xzf /tmp/taskmatch-deploy-k3dmx.tar.gz -C /opt/taskmatch

echo "[5/8] Setting up Python venv and installing dependencies..."
cd /opt/taskmatch
python3 -m venv venv
source venv/bin/activate
pip install cryptography
pip install -r requirements.txt

echo "[6/8] Configuring Nginx..."
cp /opt/taskmatch/nginx.conf /etc/nginx/conf.d/taskmatch.conf
sed -i '/^    server {/,/^    }/d' /etc/nginx/nginx.conf
nginx -t && systemctl restart nginx

echo "[7/8] Creating and starting Gunicorn systemd service..."
cat > /etc/systemd/system/taskmatch.service <<UNIT
[Unit]
Description=TaskMatch Flask App
After=network.target

[Service]
WorkingDirectory=/opt/taskmatch
EnvironmentFile=/etc/taskmatch.env
ExecStart=/opt/taskmatch/venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable taskmatch
systemctl start taskmatch

echo "[8/8] === Boot complete ==="
