#!/bin/bash
set -euo pipefail

cd /opt/stock-agent

echo "Pulling latest code..."
git pull origin main

echo "Rebuilding and restarting containers..."
docker compose down
docker compose up -d --build

echo "Waiting for container to start..."
sleep 5

if docker compose ps --status running | grep -q stock-agent; then
  echo "Deploy successful. Container is running."
else
  echo "WARNING: Container may not be running. Check logs:"
  echo "  docker compose logs --tail 50"
  exit 1
fi
