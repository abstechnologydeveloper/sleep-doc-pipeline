#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="administrator@69.197.164.87"
REMOTE_DIR="/home/administrator/sleep-doc-pipeline"

rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.git/' \
  --exclude '.DS_Store' \
  --exclude 'venv/' \
  --exclude 'data/' \
  --exclude 'audio/' \
  --exclude 'images/' \
  --exclude 'scripts/' \
  --exclude 'videos/' \
  ./ "${REMOTE_HOST}:${REMOTE_DIR}/"

ssh "${REMOTE_HOST}" "cd '${REMOTE_DIR}' && docker compose up -d --build --remove-orphans --wait"

# Remove only unused Docker artifacts. Persistent volumes and application data
# are deliberately excluded.
ssh "${REMOTE_HOST}" "docker image prune -a -f && docker builder prune -a -f --filter 'until=24h'"

ssh "${REMOTE_HOST}" "curl -fsS http://127.0.0.1:8090/health"
