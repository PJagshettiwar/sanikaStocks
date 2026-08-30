#!/bin/bash
set -euo pipefail

APP_DIR="/opt/stock-agent"

echo "==> Stopping existing containers..."
sudo -u stockagent docker compose -f "$APP_DIR/docker-compose.yml" down 2>/dev/null || true

echo "==> Cleaning up old volume mounts (directories/files from previous layout)..."
for f in agent.db stock_agent.session approval_bot.session; do
  target="$APP_DIR/$f"
  if [ -e "$target" ]; then
    echo "    Removing: $target"
    sudo rm -rf "$target"
  fi
done

echo "==> Fixing git safe.directory..."
sudo git config --global --add safe.directory "$APP_DIR"
sudo -u stockagent git config --global --add safe.directory "$APP_DIR"

echo "==> Pulling latest code..."
cd "$APP_DIR"
sudo -u stockagent git fetch origin main
sudo -u stockagent git reset --hard origin/main

echo "==> Creating data directory..."
sudo -u stockagent mkdir -p "$APP_DIR/data"

DOCKER_UID=$(id -u stockagent)
DOCKER_GID=$(id -g stockagent)

echo "==> Writing .env.docker (uid=$DOCKER_UID, gid=$DOCKER_GID)..."
sudo tee "$APP_DIR/.env.docker" > /dev/null <<EOF
DOCKER_UID=$DOCKER_UID
DOCKER_GID=$DOCKER_GID
EOF
sudo chown stockagent:stockagent "$APP_DIR/.env.docker"

echo "==> Building and starting containers..."
sudo -u stockagent bash -c "cd $APP_DIR && export DOCKER_UID=$DOCKER_UID DOCKER_GID=$DOCKER_GID && docker compose up -d --build"

echo "==> Waiting for container to start..."
sleep 10

echo "==> Checking status..."
if sudo -u stockagent bash -c "cd $APP_DIR && export DOCKER_UID=$DOCKER_UID DOCKER_GID=$DOCKER_GID && docker compose ps --status running" 2>/dev/null | grep -q stock-agent; then
  echo "SUCCESS: Container is running."
  echo ""
  echo "==> Recent logs:"
  sudo -u stockagent bash -c "cd $APP_DIR && export DOCKER_UID=$DOCKER_UID DOCKER_GID=$DOCKER_GID && docker compose logs --tail 20"
else
  echo "FAILED: Container is not running."
  echo ""
  echo "==> Error logs:"
  sudo -u stockagent bash -c "cd $APP_DIR && export DOCKER_UID=$DOCKER_UID DOCKER_GID=$DOCKER_GID && docker compose logs --tail 30"
  exit 1
fi
