#!/bin/bash
set -euo pipefail

APP_DIR="/opt/stock-agent"

echo "==> Stopping existing containers..."
sudo -u stockagent docker compose -f "$APP_DIR/docker-compose.yml" down 2>/dev/null || true

echo "==> Cleaning up broken volume mounts (directories that should be files)..."
for f in agent.db stock_agent.session approval_bot.session; do
  target="$APP_DIR/$f"
  if [ -d "$target" ]; then
    echo "    Removing directory: $target"
    sudo rm -rf "$target"
  fi
done

echo "==> Pre-creating volume-mount files..."
sudo -u stockagent touch "$APP_DIR/agent.db"
sudo -u stockagent touch "$APP_DIR/stock_agent.session"
sudo -u stockagent touch "$APP_DIR/approval_bot.session"

echo "==> Fixing git safe.directory..."
sudo git config --global --add safe.directory "$APP_DIR"

echo "==> Pulling latest code..."
cd "$APP_DIR"
sudo -u stockagent git config --global --add safe.directory "$APP_DIR"
sudo -u stockagent git pull origin main

echo "==> Building and starting containers..."
sudo -u stockagent docker compose up -d --build

echo "==> Waiting for container to start..."
sleep 8

echo "==> Checking status..."
if sudo -u stockagent docker compose ps --status running 2>/dev/null | grep -q stock-agent; then
  echo "SUCCESS: Container is running."
  echo ""
  echo "==> Recent logs:"
  sudo -u stockagent docker compose logs --tail 20
else
  echo "FAILED: Container is not running."
  echo ""
  echo "==> Error logs:"
  sudo -u stockagent docker compose logs --tail 30
  exit 1
fi
