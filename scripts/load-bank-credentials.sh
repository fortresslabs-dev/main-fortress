#!/usr/bin/env bash
# scripts/load-bank-credentials.sh
# Pushes human-provided credentials into Secrets Manager + SSM for a P2 bank.
# Idempotent: re-runs overwrite existing values.
#
# Usage:
#   ./scripts/load-bank-credentials.sh --env-file infra/setup/bank.env.testbank
#
# Prerequisites:
#   - pipeline2-data.yml deployed (secret shells exist)
#   - AWS profile in the env file has SecretsManager + SSM write perms

set -euo pipefail

ENV_FILE=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

[[ -z "$ENV_FILE" ]] && { echo "ERROR: --env-file required"; exit 1; }
[[ ! -f "$ENV_FILE" ]] && { echo "ERROR: $ENV_FILE not found"; exit 1; }

# shellcheck source=/dev/null
set -a; source "$ENV_FILE"; set +a

# ── Validate required vars ────────────────────────────────────────────────
require() {
  local name="$1"
  local val="${!name:-}"
  if [[ -z "$val" || "$val" == *REPLACE_ME* || "$val" == "YOUR_ORG"* ]]; then
    echo "ERROR: $name is unset or still a placeholder in $ENV_FILE"
    exit 1
  fi
}

require BANK_ID
require AWS_REGION
require AWS_PROFILE
require JIRA_URL
require JIRA_USERNAME
require JIRA_API_TOKEN
require JIRA_PROJECT_KEY
require GITHUB_TOKEN
require GITHUB_ORG
require GITHUB_REPO

# JWT_SECRET auto-generated if blank
if [[ -z "${JWT_SECRET:-}" ]]; then
  JWT_SECRET="$(openssl rand -hex 32)"
  echo "→ JWT_SECRET auto-generated"
fi

export AWS_REGION AWS_PROFILE

echo "═════════════════════════════════════════════"
echo "  Loading credentials for bank: $BANK_ID"
echo "═════════════════════════════════════════════"

# ── Secrets Manager helpers ───────────────────────────────────────────────
put_secret() {
  local name="$1" value="$2"
  if aws secretsmanager describe-secret --secret-id "$name" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value --secret-id "$name" --secret-string "$value" >/dev/null
    echo "  ✓ updated  $name"
  else
    aws secretsmanager create-secret --name "$name" --secret-string "$value" >/dev/null
    echo "  ✓ created  $name"
  fi
}

put_ssm() {
  local name="$1" value="$2"
  aws ssm put-parameter --name "$name" --value "$value" --type String --overwrite >/dev/null
  echo "  ✓ ssm      $name"
}

# ── 1. JIRA secret (JSON; CFN extracts :url/:username/:token keys) ────────
echo ""
echo "[1/3] Secrets Manager"
JIRA_JSON="$(printf '{"url":"%s","username":"%s","token":"%s"}' \
  "$JIRA_URL" "$JIRA_USERNAME" "$JIRA_API_TOKEN")"
put_secret "fortress/${BANK_ID}/jira-api-token" "$JIRA_JSON"
put_secret "fortress/${BANK_ID}/github-token"   "$GITHUB_TOKEN"
put_secret "fortress/${BANK_ID}/jwt-secret"     "$JWT_SECRET"

# ── 2. SSM parameters ─────────────────────────────────────────────────────
echo ""
echo "[2/3] SSM Parameter Store"
put_ssm "/fortress/${BANK_ID}/JIRA_PROJECT_KEY" "$JIRA_PROJECT_KEY"
put_ssm "/fortress/${BANK_ID}/GITHUB_ORG"       "$GITHUB_ORG"
put_ssm "/fortress/${BANK_ID}/GITHUB_REPO"      "$GITHUB_REPO"

# ── 3. Sanity check — read back ───────────────────────────────────────────
echo ""
echo "[3/3] Verification"
aws secretsmanager get-secret-value \
  --secret-id "fortress/${BANK_ID}/jira-api-token" \
  --query "SecretString" --output text | python3 -c \
  'import sys,json; d=json.loads(sys.stdin.read()); assert d["url"] and d["username"] and d["token"]; print("  ✓ jira-api-token JSON parses with url/username/token")'

TOKEN_HEAD="$(aws secretsmanager get-secret-value \
  --secret-id "fortress/${BANK_ID}/github-token" \
  --query "SecretString" --output text | cut -c1-12)"
echo "  ✓ github-token  starts with: ${TOKEN_HEAD}…"

echo ""
echo "═════════════════════════════════════════════"
echo "  Done. Proceed to pipeline2-compute.yml deploy."
echo "═════════════════════════════════════════════"
