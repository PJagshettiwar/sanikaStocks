#!/bin/bash
set -euo pipefail

# Cloud-init first-boot provisioning for stock-agent VM
# NOTE: This file is rendered via Terraform templatefile() — do not run directly.

export DEBIAN_FRONTEND=noninteractive

# System updates
apt-get update -y
apt-get upgrade -y

# Install Docker
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Install git
apt-get install -y git

# Create system user with pinned UID/GID for reproducibility
groupadd --system --gid 997 stockagent || true
useradd --system --uid 997 --gid 997 --shell /usr/sbin/nologin --create-home stockagent || true
usermod -aG docker stockagent

# Clone repo
git clone ${repo_url} /opt/stock-agent
chown -R stockagent:stockagent /opt/stock-agent
git config --global --add safe.directory /opt/stock-agent
sudo -u stockagent git config --global --add safe.directory /opt/stock-agent

# Create data directory for persistent files (db, sessions)
sudo -u stockagent mkdir -p /opt/stock-agent/data

# Write DOCKER_UID/GID env file for systemd and watchdog
STOCK_UID=$(id -u stockagent)
STOCK_GID=$(id -g stockagent)
cat > /opt/stock-agent/.env.docker <<ENVDOCKER
DOCKER_UID=$STOCK_UID
DOCKER_GID=$STOCK_GID
ENVDOCKER
chown stockagent:stockagent /opt/stock-agent/.env.docker

# Create systemd service for stock-agent container
cat > /etc/systemd/system/stock-agent.service <<'UNIT'
[Unit]
Description=Stock Trading Agent (Docker Compose)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=stockagent
WorkingDirectory=/opt/stock-agent
EnvironmentFile=/opt/stock-agent/.env.docker
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
UNIT

# Install health watchdog script
mkdir -p /opt/stock-agent/scripts
cat > /opt/stock-agent/scripts/health-watchdog.sh <<'WATCHDOG'
#!/bin/bash
set -euo pipefail

APP_DIR="/opt/stock-agent"
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  exit 0
fi

# Source DOCKER_UID/GID for docker compose
if [ -f "$APP_DIR/.env.docker" ]; then
  export $(cat "$APP_DIR/.env.docker" | xargs)
fi

BOT_TOKEN="${bot_token}"
CHAT_ID="${alert_chat_id}"
HOSTNAME=$(hostname)

send_alert() {
  local message="$1"
  curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
    -d chat_id="$CHAT_ID" \
    -d text="$message" \
    -d parse_mode="HTML" > /dev/null 2>&1
}

# Check container
cd "$APP_DIR"
if ! docker compose ps --status running 2>/dev/null | grep -q stock-agent; then
  send_alert "⚠️ <b>[$HOSTNAME] Container down</b>%0AAttempting restart..."
  if docker compose up -d 2>/dev/null; then
    sleep 10
    if docker compose ps --status running 2>/dev/null | grep -q stock-agent; then
      send_alert "✅ <b>[$HOSTNAME] Container restarted successfully</b>"
    else
      send_alert "❌ <b>[$HOSTNAME] Container restart FAILED</b>%0AManual intervention required."
    fi
  else
    send_alert "❌ <b>[$HOSTNAME] Container restart FAILED</b>%0AManual intervention required."
  fi
fi

# Check CPU (>80%)
CPU_USAGE=$(top -bn1 | grep "Cpu(s)" | awk '{print int($2 + $4)}' || echo 0)
if [ "$CPU_USAGE" -gt 80 ]; then
  send_alert "⚠️ <b>[$HOSTNAME] High CPU</b>: $${CPU_USAGE}%"
fi

# Check memory (>85%)
MEM_USAGE=$(free | awk '/Mem:/ {printf "%d", $3/$2 * 100}' || echo 0)
if [ "$MEM_USAGE" -gt 85 ]; then
  send_alert "⚠️ <b>[$HOSTNAME] High Memory</b>: $${MEM_USAGE}%"
fi

# Check disk (>80%)
DISK_USAGE=$(df / | awk 'NR==2 {print int($5)}' || echo 0)
if [ "$DISK_USAGE" -gt 80 ]; then
  send_alert "⚠️ <b>[$HOSTNAME] High Disk</b>: $${DISK_USAGE}%"
fi
WATCHDOG
chmod 700 /opt/stock-agent/scripts/health-watchdog.sh
chown stockagent:stockagent /opt/stock-agent/scripts/health-watchdog.sh

# Create systemd timer for watchdog (every 5 minutes)
cat > /etc/systemd/system/stock-agent-watchdog.service <<'WDUNIT'
[Unit]
Description=Stock Agent Health Watchdog

[Service]
Type=oneshot
User=stockagent
WorkingDirectory=/opt/stock-agent
EnvironmentFile=/opt/stock-agent/.env.docker
ExecStart=/opt/stock-agent/scripts/health-watchdog.sh
WDUNIT

cat > /etc/systemd/system/stock-agent-watchdog.timer <<'TIMER'
[Unit]
Description=Run Stock Agent Health Watchdog every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
TIMER

# Enable timer (starts on next boot), but do NOT start the main service
systemctl daemon-reload
systemctl enable stock-agent-watchdog.timer
systemctl start stock-agent-watchdog.timer

echo "Cloud-init provisioning complete. Run 'sudo systemctl start stock-agent' after setting up .env and session files."
