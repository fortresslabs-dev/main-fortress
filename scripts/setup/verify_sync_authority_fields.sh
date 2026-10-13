#!/usr/bin/env bash
# scripts/setup/verify_sync_authority_fields.sh
#
# Pre-window gate: confirms the running P1 image serves the migrations
# 046/047 authority columns through /api/sync/postgres and /api/sync/embeddings.
# If these keys are missing from the JSON, the P2 window will silently produce
# advisory-only rules — do NOT open the window.
#
# Usage:
#   FORTRESS_URL=https://api.fortresslabs.dev \
#   CUSTOMER_ID=firstnational \
#   API_KEY=<64-char hex> \
#     ./scripts/setup/verify_sync_authority_fields.sh
#
# Exits 0 only if every required key is present in the response payload.

set -euo pipefail

: "${FORTRESS_URL:?FORTRESS_URL is required (e.g. https://api.fortresslabs.dev)}"
: "${CUSTOMER_ID:?CUSTOMER_ID is required}"
: "${API_KEY:?API_KEY is required}"

command -v jq >/dev/null || { echo "jq is required"; exit 2; }

REQUIRED_KEYS=(
  authority_type
  authority_source
  citation_verified
  citation_verified_at
  citation_verified_source
  source_content_hash
  scraped_at
  publication_date
  comment_deadline
  federal_register_doc
  rin
  applicability_asset_min
  applicability_asset_max
  applicability_charter
  applicability_activities
  applicability_states
  is_exemption
  verify_before_display
  bank_configurable_threshold
)

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

check_keys_in_object() {
  # $1 = label, $2 = JSON object string
  local label="$1" obj="$2"
  local missing=()
  for k in "${REQUIRED_KEYS[@]}"; do
    if ! jq -e --arg k "$k" 'has($k)' <<<"$obj" >/dev/null; then
      missing+=("$k")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    fail "$label — missing keys: ${missing[*]}"
  fi
  pass "$label — all ${#REQUIRED_KEYS[@]} authority keys present"
}

# ── /api/sync/postgres ────────────────────────────────────────────────
echo "→ POST ${FORTRESS_URL}/api/sync/postgres"
POSTGRES_JSON=$(curl -sS --fail-with-body \
  -H "X-Customer-Id: ${CUSTOMER_ID}" \
  -H "X-Api-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"from_version": 0}' \
  "${FORTRESS_URL}/api/sync/postgres") \
  || fail "/api/sync/postgres request failed"

# Prefer a row from `added`, fall back to `updated`. Empty responses are a
# separate failure — verification needs a real row to inspect.
ROW=$(jq -c '(.added // []) + (.updated // []) | .[0] // empty' <<<"$POSTGRES_JSON")
[[ -z "$ROW" ]] && fail "/api/sync/postgres returned no rules — cannot verify shape (is P1 populated?)"
check_keys_in_object "/api/sync/postgres[0]" "$ROW"

# ── /api/sync/embeddings ──────────────────────────────────────────────
echo "→ POST ${FORTRESS_URL}/api/sync/embeddings"
EMBED_JSON=$(curl -sS --fail-with-body \
  -H "X-Customer-Id: ${CUSTOMER_ID}" \
  -H "X-Api-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"from_version": 0}' \
  "${FORTRESS_URL}/api/sync/embeddings") \
  || fail "/api/sync/embeddings request failed"

EMB_ROW=$(jq -c '.rules[0] // empty' <<<"$EMBED_JSON")
[[ -z "$EMB_ROW" ]] && fail "/api/sync/embeddings returned no rules — cannot verify shape"
check_keys_in_object "/api/sync/embeddings[0]" "$EMB_ROW"

# ── Content sanity — at least one row where authority_type is non-null ─
NON_NULL=$(jq '[(.added // []) + (.updated // []) | .[] | select(.authority_type != null)] | length' <<<"$POSTGRES_JSON")
if (( NON_NULL == 0 )); then
  fail "Every /api/sync/postgres row has authority_type=null. Scrape did not populate the column — do NOT open the window."
fi
pass "authority_type is non-null on ${NON_NULL} row(s)"

echo
echo "OK — P1 sync payload carries authority fields. Safe to proceed to P2."
