#!/bin/bash
# Victor-OS EC2 Deployment Script
# Run this on your EC2 instance after cloning the repo.
# Usage: bash deploy/deploy_ec2.sh

set -e

VICTOR_DIR="/opt/victor_os"
SERVICE_USER="victor"
PYTHON_VERSION="python3.11"

echo ""
echo "=================================================="
echo "  Victor-OS EC2 Deployment"
echo "=================================================="

# ------------------------------------------------------------------ #
# System dependencies                                                 #
# ------------------------------------------------------------------ #
echo "[1/8] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3.11 python3.11-venv python3-pip \
    nginx certbot python3-certbot-nginx \
    supervisor git curl unzip awscli \
    build-essential libffi-dev libssl-dev

# ------------------------------------------------------------------ #
# Create dedicated system user                                        #
# ------------------------------------------------------------------ #
echo "[2/8] Creating system user..."
if ! id "$SERVICE_USER" &>/dev/null; then
    sudo useradd --system --shell /bin/bash --home $VICTOR_DIR $SERVICE_USER
fi

# ------------------------------------------------------------------ #
# Set up application directory                                        #
# ------------------------------------------------------------------ #
echo "[3/8] Setting up application directory..."
sudo mkdir -p $VICTOR_DIR
sudo cp -r . $VICTOR_DIR/
sudo chown -R $SERVICE_USER:$SERVICE_USER $VICTOR_DIR

# Create required directories
sudo -u $SERVICE_USER mkdir -p $VICTOR_DIR/memory_store
sudo -u $SERVICE_USER mkdir -p $VICTOR_DIR/workspace/proposed_changes
sudo -u $SERVICE_USER mkdir -p $VICTOR_DIR/data
sudo -u $SERVICE_USER mkdir -p $VICTOR_DIR/logs

# ------------------------------------------------------------------ #
# Python virtual environment                                          #
# ------------------------------------------------------------------ #
echo "[4/8] Creating Python virtual environment..."
sudo -u $SERVICE_USER $PYTHON_VERSION -m venv $VICTOR_DIR/venv
sudo -u $SERVICE_USER $VICTOR_DIR/venv/bin/pip install --upgrade pip -q
sudo -u $SERVICE_USER $VICTOR_DIR/venv/bin/pip install -r $VICTOR_DIR/requirements.txt -q

# ------------------------------------------------------------------ #
# Environment configuration                                           #
# ------------------------------------------------------------------ #
echo "[5/8] Configuring environment..."
if [ ! -f "$VICTOR_DIR/.env" ]; then
    echo ""
    echo "  No .env file found. Run the setup wizard first:"
    echo "  python victor_setup.py"
    echo ""
    echo "  Then copy .env to $VICTOR_DIR/.env"
    echo ""
fi

# ------------------------------------------------------------------ #
# Systemd service                                                     #
# ------------------------------------------------------------------ #
echo "[6/8] Installing systemd service..."
sudo tee /etc/systemd/system/victor.service > /dev/null <<EOF
[Unit]
Description=Victor-OS AI Assistant
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$VICTOR_DIR
ExecStart=$VICTOR_DIR/venv/bin/python telegram_server.py
Restart=always
RestartSec=5
StandardOutput=append:$VICTOR_DIR/logs/victor.log
StandardError=append:$VICTOR_DIR/logs/victor_error.log
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$VICTOR_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

# Desktop server (web UI) as separate service
sudo tee /etc/systemd/system/victor-web.service > /dev/null <<EOF
[Unit]
Description=Victor-OS Web Interface
After=network.target victor.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$VICTOR_DIR
ExecStart=$VICTOR_DIR/venv/bin/python desktop_server.py
Restart=always
RestartSec=10
StandardOutput=append:$VICTOR_DIR/logs/victor_web.log
StandardError=append:$VICTOR_DIR/logs/victor_web_error.log
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$VICTOR_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable victor victor-web
sudo systemctl start victor victor-web

# ------------------------------------------------------------------ #
# Nginx reverse proxy                                                 #
# ------------------------------------------------------------------ #
echo "[7/8] Configuring Nginx..."
read -p "  Enter your domain (e.g. victor.yourdomain.com): " DOMAIN

sudo tee /etc/nginx/sites-available/victor > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    # Telegram bot webhook
    location /telegram {
        proxy_pass http://127.0.0.1:8443;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Victor REST API
    location /api {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_http_version 1.1;
        proxy_read_timeout 300s;
    }

    # Victor Web UI (primary)
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }

    # Webhook receiver
    location /webhook {
        proxy_pass http://127.0.0.1:7878;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    client_max_body_size 50M;
}
EOF

sudo ln -sf /etc/nginx/sites-available/victor /etc/nginx/sites-enabled/victor
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx

# SSL via Certbot
read -p "  Set up SSL with Let's Encrypt? (yes/no): " SETUP_SSL
if [ "$SETUP_SSL" = "yes" ]; then
    read -p "  Your email for Let's Encrypt: " LETSENCRYPT_EMAIL
    sudo certbot --nginx -d $DOMAIN --email $LETSENCRYPT_EMAIL \
        --agree-tos --non-interactive --redirect
    echo "  SSL configured. Victor is now at https://$DOMAIN"
fi

# ------------------------------------------------------------------ #
# Automated S3 backup                                                 #
# ------------------------------------------------------------------ #
echo "[8/8] Setting up automated backups..."
read -p "  S3 bucket for backups (e.g. my-victor-backups, or press Enter to skip): " S3_BUCKET

if [ -n "$S3_BUCKET" ]; then
    sudo tee /opt/victor_backup.sh > /dev/null <<EOF2
#!/bin/bash
# Victor-OS daily backup to S3
TIMESTAMP=\$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="/tmp/victor_backup_\$TIMESTAMP.tar.gz"
tar -czf \$BACKUP_FILE -C $VICTOR_DIR memory_store data
aws s3 cp \$BACKUP_FILE s3://$S3_BUCKET/backups/
rm -f \$BACKUP_FILE
echo "Victor backup completed: \$TIMESTAMP"
EOF2
    sudo chmod +x /opt/victor_backup.sh

    # Daily cron at 03:00
    (sudo crontab -l 2>/dev/null; echo "0 3 * * * /opt/victor_backup.sh >> $VICTOR_DIR/logs/backup.log 2>&1") | sudo crontab -
    echo "  Automated backup configured to s3://$S3_BUCKET"
fi

# ------------------------------------------------------------------ #
# Done                                                                #
# ------------------------------------------------------------------ #
echo ""
echo "=================================================="
echo "  Deployment Complete!"
echo "=================================================="
echo ""
echo "  Victor services:"
echo "  sudo systemctl status victor"
echo "  sudo systemctl status victor-web"
echo ""
echo "  View logs:"
echo "  tail -f $VICTOR_DIR/logs/victor.log"
echo ""
if [ -n "$DOMAIN" ]; then
    echo "  Victor is accessible at:"
    echo "  https://$DOMAIN"
fi
echo ""
echo "  Next: open Telegram, find your bot, send /start"
echo ""
