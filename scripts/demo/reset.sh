#!/usr/bin/env bash
# scripts/demo/reset.sh — bring the demo stack back to baseline state.
#
# Two modes:
#   --local   (default) Truncate volatile tables on the local docker-compose
#             Postgres, then re-run `scripts.demo.seed`. Target <90s.
#   --s3      Restore from the S3 baseline snapshot
#             (s3://fortress-demo/snapshots/baseline.dump), then re-run seed.
#             Requires AWS creds + the dump to exist. Target <2 min per
#             runbook §4.
#
# Both modes are idempotent — running twice in a row leaves the same state.
# See docs/runbooks/demo-readiness.md §4 for the demo-readiness contract.

set -euo pipefail

MODE="${1:---local}"

# Resolve repo root from this script's location so it works from anywhere.
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/../.." && pwd )"
cd "${REPO_ROOT}"

PG_USER="${PG_USER:-fortress}"
PG_PASS="${PG_PASS:-fortress_dev}"
PG_DB="${PG_DB:-fortress_dev}"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
S3_DUMP="${S3_DUMP:-s3://fortress-demo/snapshots/baseline.dump}"

DATABASE_URL="postgresql+asyncpg://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${PG_DB}"
export DATABASE_URL

# REDIS_URL is required by src.utils.config Settings (no default). Default
# to the docker-compose redis; override upstream for staging.
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export REDIS_URL

# Volatile tables — wiped on every reset. Order respects FKs (children first).
VOLATILE_TABLES=(
  "checkpoint_approvals"
  "workflow_executions"
  "screening_events"
  "regulatory_change_records"
  "compliance_deadlines"
  "obligations"
  "ofac_sdn_vectors"
  "ofac_sdn_entries"
  "compliance_rules"
  "sync_metadata"
)

case "${MODE}" in
  --local)
    echo "==> demo reset (local mode) — repo: ${REPO_ROOT}"
    t0=$(date +%s)

    # Resolve psql against the docker-compose container so we don't need
    # libpq installed on the host.
    PSQL=(docker exec -i fortress-postgres
          env PGPASSWORD="${PG_PASS}"
          psql -h localhost -U "${PG_USER}" -d "${PG_DB}" -v ON_ERROR_STOP=1)

    echo "    truncating volatile tables ..."
    for tbl in "${VOLATILE_TABLES[@]}"; do
      # Tables may not exist on a partially-migrated DB; tolerate that.
      "${PSQL[@]}" -c "TRUNCATE TABLE ${tbl} CASCADE;" 2>/dev/null \
        || echo "      (skipped ${tbl})"
    done

    echo "    re-running scripts.demo.seed ..."
    python3 -m scripts.demo.seed

    t1=$(date +%s)
    echo "==> done in $((t1 - t0))s"
    ;;

  --s3)
    echo "==> demo reset (s3 mode) — dump: ${S3_DUMP}"
    t0=$(date +%s)

    : "${AWS_PROFILE:?Set AWS_PROFILE for s3 mode}"
    : "${AWS_REGION:?Set AWS_REGION for s3 mode}"

    if ! command -v aws >/dev/null; then
      echo "ERROR: aws CLI not on PATH"; exit 2
    fi
    if ! command -v pg_restore >/dev/null; then
      echo "ERROR: pg_restore not on PATH (apt install postgresql-client-15)"; exit 2
    fi

    TMPDIR="$(mktemp -d)"
    trap 'rm -rf "${TMPDIR}"' EXIT
    LOCAL_DUMP="${TMPDIR}/baseline.dump"

    echo "    aws s3 cp ${S3_DUMP} ${LOCAL_DUMP}"
    aws s3 cp "${S3_DUMP}" "${LOCAL_DUMP}" \
      --profile "${AWS_PROFILE}" --region "${AWS_REGION}"

    PGPASSWORD="${PG_PASS}" pg_restore \
      --host "${PG_HOST}" --port "${PG_PORT}" \
      --username "${PG_USER}" --dbname "${PG_DB}" \
      --clean --if-exists --no-owner --no-privileges \
      --jobs 4 \
      "${LOCAL_DUMP}"

    echo "    re-running scripts.demo.seed (idempotent top-up) ..."
    python3 -m scripts.demo.seed

    t1=$(date +%s)
    echo "==> done in $((t1 - t0))s"
    ;;

  *)
    echo "Usage: $0 [--local|--s3]"; exit 1
    ;;
esac
