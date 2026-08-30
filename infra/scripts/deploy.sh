#!/bin/bash
set -euo pipefail

APP_DIR="/opt/stock-agent"
cd "$APP_DIR"

git config --global --add safe.directory "$APP_DIR"

echo "==> Pulling latest code..."
git fetch origin main
git reset --hard origin/main

echo "==> Ensuring data directory exists..."
mkdir -p data

echo "==> Setting container user to match host..."
DOCKER_UID=$(id -u)
DOCKER_GID=$(id -g)
cat > "$APP_DIR/.env.docker" <<EOF
DOCKER_UID=$DOCKER_UID
DOCKER_GID=$DOCKER_GID
EOF
export DOCKER_UID DOCKER_GID

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
