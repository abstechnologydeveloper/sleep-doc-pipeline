#!/usr/bin/env bash
set -euo pipefail

# Runs on the dedicated GitHub Actions runner installed on the VPS.
SOURCE_DIR="${GITHUB_WORKSPACE:-$(pwd)}"
DEPLOY_DIR="${STUDIO_DEPLOY_DIR:-/home/administrator/sleep-doc-pipeline}"

mkdir -p \
  "${DEPLOY_DIR}/data" \
  "${DEPLOY_DIR}/audio" \
  "${DEPLOY_DIR}/images" \
  "${DEPLOY_DIR}/sounds" \
  "${DEPLOY_DIR}/scripts" \
  "${DEPLOY_DIR}/videos" \
  "${DEPLOY_DIR}/thumbnails"

rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.git/' \
  --exclude '.DS_Store' \
  --exclude 'venv/' \
  --exclude 'data/' \
  --exclude 'audio/' \
  --exclude 'images/' \
  --exclude 'sounds/' \
  --exclude 'scripts/' \
  --exclude 'videos/' \
  --exclude 'thumbnails/' \
  "${SOURCE_DIR}/" "${DEPLOY_DIR}/"

if [[ -n "${STUDIO_ENV_FILE:-}" ]]; then
  umask 077
  printf '%s\n' "${STUDIO_ENV_FILE}" > "${DEPLOY_DIR}/.env"
elif [[ ! -s "${DEPLOY_DIR}/.env" ]]; then
  echo "Neither STUDIO_ENV_FILE nor an existing deployment .env is available." >&2
  exit 1
fi

cd "${DEPLOY_DIR}"
docker compose build
docker compose run --rm --no-deps --user root --entrypoint chown studio \
  studio:studio /app/data /app/scripts /app/audio /app/images /app/sounds /app/videos /app/thumbnails
docker compose up -d --no-build --remove-orphans --wait
curl --fail --silent --show-error http://127.0.0.1:8090/health

# Keep active containers and persistent files. Remove only unused Docker
# artifacts older than 24 hours so deployments cannot fill the VPS disk.
docker image prune -a -f --filter "until=24h"
docker builder prune -a -f --filter "until=24h"
