#!/bin/bash
set -euo pipefail

APP_DIR="/opt/stock-agent"
cd "$APP_DIR"

git config --global --add safe.directory "$APP_DIR"

echo "==> Pulling latest code..."
git pull origin main

echo "==> Ensuring data directory exists..."
mkdir -p data

echo "==> Setting container user to match host..."
export DOCKER_UID=$(id -u)
export DOCKER_GID=$(id -g)

echo "==> Rebuilding and restarting containers..."
docker compose down
docker compose up -d --build

echo "==> Waiting for container to start..."
sleep 5

if docker compose ps --status running | grep -q stock-agent; then
  echo "==> Deploy successful. Container is running."
  docker compose logs --tail 10
else
  echo "==> FAILED: Container may not be running."
  docker compose logs --tail 30
  exit 1
fi
