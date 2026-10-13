#!/usr/bin/env bash
# entrypoint.sh — Run DB migrations then start the Pipeline 1 API server.
# Called by CMD in Dockerfile.pipeline1.
set -euo pipefail

echo "[entrypoint] Running Alembic migrations..."
alembic upgrade head

echo "[entrypoint] Starting Pipeline 1 API server..."
exec uvicorn src.main_pipeline1:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${UVICORN_WORKERS:-1}" \
  --log-level "${UVICORN_LOG_LEVEL:-info}" \
  --no-access-log \
  --proxy-headers \
  --forwarded-allow-ips='*'
