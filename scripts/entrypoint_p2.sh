#!/usr/bin/env bash
# entrypoint_p2.sh — Compose DATABASE_URL from ECS-injected pieces, run
# Alembic migrations, then start the Pipeline 2 API server.
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" && -n "${DB_USER:-}" && -n "${DB_PASSWORD:-}" \
        && -n "${AURORA_HOST:-}" && -n "${DB_NAME:-}" ]]; then
  export DATABASE_URL=$(python3 -c "
import os, urllib.parse
pw = urllib.parse.quote(os.environ['DB_PASSWORD'], safe='')
print(f\"postgresql+asyncpg://{os.environ['DB_USER']}:{pw}@{os.environ['AURORA_HOST']}:{os.environ.get('AURORA_PORT','5432')}/{os.environ['DB_NAME']}?ssl=require\")
")
fi

# UpdateService (src/services/update_service.py) reads FORTRESS_API_KEY +
# CUSTOMER_ID, but the bank task def injects them under the names
# PIPELINE1_API_KEY (secret) + CLIENT_NAME (plain). Alias here so the 24h
# scheduled _schedule_loop sees populated values; the on-startup sync was
# fine only because overrides were set by hand. Existing values win.
: "${FORTRESS_API_KEY:=${PIPELINE1_API_KEY:-}}"
: "${CUSTOMER_ID:=${CLIENT_NAME:-}}"
export FORTRESS_API_KEY CUSTOMER_ID

echo "[entrypoint] Running Alembic migrations..."
alembic upgrade head

echo "[entrypoint] Starting Pipeline 2 API server..."
exec uvicorn src.main_pipeline2:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${UVICORN_WORKERS:-1}" \
  --log-level "${UVICORN_LOG_LEVEL:-info}" \
  --proxy-headers \
  --forwarded-allow-ips='*'
