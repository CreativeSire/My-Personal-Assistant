#!/bin/bash
# Victor OS - GCE Spot Instance Startup Script
# This script automatically provisions the environment and starts Victor on boot.

# 1. System Updates & Dependencies
apt-get update
apt-get install -y python3-venv python3-pip git ffmpeg portaudio19-dev

# 2. Clone/Update Repo (You will replace REPO_URL in the deployment guide)
# For the first run, we assume the code is uploaded or cloned to /opt/victor
mkdir -p /opt/victor
cd /opt/victor

# 3. Virtual Environment
python3 -m venv venv
source venv/bin/activate

# 4. Install Requirements
# (We create a temporary requirements.txt if one doesn't exist to prevent crash)
if [ -f "victor_os/requirements.txt" ]; then
    pip install -r victor_os/requirements.txt
else
    pip install google-generativeai python-dotenv rich flask requests
fi

# 5. Setup Systemd Service (Self-Healing)
cat <<EOT > /etc/systemd/system/victor.service
[Unit]
Description=Victor OS Cloud Node
After=network.target

[Service]
User=root
WorkingDirectory=/opt/victor
ExecStart=/opt/victor/venv/bin/python3 manage.py run
Restart=always
RestartSec=5
EnvironmentFile=/opt/victor/.env

[Install]
WantedBy=multi-user.target
EOT

# 6. Start Victor
systemctl daemon-reload
systemctl enable victor
systemctl start victor

echo "Victor OS Cloud Node is alive."
