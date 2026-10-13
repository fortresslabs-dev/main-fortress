#!/usr/bin/env bash
# scripts/setup/invalidate_rule_cache.sh
#
# Post-sync gate for the rule-correction maintenance window.
#
# compliance_rule_resolver.py caches resolved ComplianceRule objects in Redis
# under the prefix `compliance:resolved:` with a 1h TTL. After a schema-changing
# sync (e.g. migrations 046/047 land authority_type + citation_verified on
# every row), any cached entry is a stale JSON blob missing the new keys —
# may_assert_as_law() reads them as None and every rule silently degrades to
# advisory-only until the TTL burns off.
#
# Run this once against the P1 Redis after the P1 window closes, and once
# against the P2 (bank) Redis after /admin/sync/full completes on P2.
#
# Usage:
#   REDIS_URL=rediss://:password@host:6379/0 \
#     ./scripts/setup/invalidate_rule_cache.sh
#
# Optional:
#   REDIS_URL_FILE=/path/to/url  # read URL from file instead (safer for CI logs)
#   DRY_RUN=1                    # count matching keys, do not delete
#
# Exits 0 on success. Non-zero on connection failure or partial deletion.

set -euo pipefail

PREFIX="compliance:resolved:*"

if [[ -z "${REDIS_URL:-}" && -n "${REDIS_URL_FILE:-}" ]]; then
  REDIS_URL="$(< "${REDIS_URL_FILE}")"
fi
: "${REDIS_URL:?REDIS_URL is required (or REDIS_URL_FILE pointing at a file)}"

command -v redis-cli >/dev/null || { echo "redis-cli is required"; exit 2; }

# ── 1. Connectivity check ────────────────────────────────────────────
if ! redis-cli -u "${REDIS_URL}" PING >/dev/null 2>&1; then
  echo "FAIL: cannot reach Redis (PING failed)" >&2
  exit 1
fi

# ── 2. Count keys under the resolver prefix ──────────────────────────
# SCAN is non-blocking; KEYS would stall a large prod Redis.
KEY_COUNT=$(redis-cli -u "${REDIS_URL}" --scan --pattern "${PREFIX}" | wc -l | tr -d ' ')
echo "Found ${KEY_COUNT} keys matching ${PREFIX}"

if (( KEY_COUNT == 0 )); then
  echo "OK — nothing to invalidate."
  exit 0
fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "DRY_RUN=1 — would delete ${KEY_COUNT} keys. Exiting without changes."
  exit 0
fi

# ── 3. Delete in batches of 500 to keep pipelines predictable ────────
# redis-cli --scan streams matching keys; xargs batches them into UNLINK
# (non-blocking DEL). UNLINK returns 0 for missing keys, so partial races
# with a concurrent writer are safe.
DELETED=$(
  redis-cli -u "${REDIS_URL}" --scan --pattern "${PREFIX}" \
    | xargs -n 500 -r redis-cli -u "${REDIS_URL}" UNLINK \
    | awk '{s+=$1} END {print s+0}'
)
echo "Invalidated ${DELETED} keys."

# ── 4. Verify nothing survived ───────────────────────────────────────
REMAINING=$(redis-cli -u "${REDIS_URL}" --scan --pattern "${PREFIX}" | wc -l | tr -d ' ')
if (( REMAINING > 0 )); then
  echo "WARN: ${REMAINING} keys still present after UNLINK — check for a writer racing the invalidation." >&2
  exit 3
fi

echo "OK — resolver cache is clean."
