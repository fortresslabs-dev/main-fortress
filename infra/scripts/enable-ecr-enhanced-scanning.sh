#!/usr/bin/env bash
# infra/scripts/enable-ecr-enhanced-scanning.sh
#
# Enables Amazon Inspector v2 for ECR and sets the ECR registry to
# ENHANCED + CONTINUOUS_SCAN for all repositories. Idempotent —
# re-running is safe.
#
# Covers FTR tracker #16 (S4/V1). Run once per AWS account (P1 +
# each P2 bank account) after the security stack is deployed.
#
# Usage:
#   ./infra/scripts/enable-ecr-enhanced-scanning.sh [--profile PROFILE]
#                                                  [--region REGION]
#
# Heads-up:
#   First scan generates a CVE backlog on every existing ECR image.
#   Expect Inspector v2 console + Security Hub to light up within
#   ~15 min. Triage flow lives in docs/runbooks/inspector-triage.md
#   (created alongside this script).

set -euo pipefail

PROFILE="${AWS_PROFILE:-}"
REGION="${AWS_REGION:-us-east-1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

aws_args=(--region "$REGION")
if [[ -n "$PROFILE" ]]; then
  aws_args=(--profile "$PROFILE" "${aws_args[@]}")
fi

ACCOUNT_ID=$(aws "${aws_args[@]}" sts get-caller-identity --query Account --output text)

echo "======================================================"
echo "  Enable ECR Enhanced Scanning + Inspector v2"
echo "  Account : $ACCOUNT_ID"
echo "  Region  : $REGION"
echo "======================================================"

# ── 1. Enable Inspector v2 for ECR resource type ──────────────────────
# This auto-creates the AWSServiceRoleForAmazonInspector2 SLR on first
# call. Returns immediately on accounts that already have it enabled.
echo ""
echo "→ Enabling Amazon Inspector v2 for ECR …"
aws "${aws_args[@]}" inspector2 enable \
  --resource-types ECR \
  --account-ids "$ACCOUNT_ID" \
  --output json | head -c 400
echo ""

# ── 2. Set ECR registry scanning to ENHANCED + CONTINUOUS_SCAN ────────
# Applies to ALL repositories via wildcard filter. Continuous scan
# re-evaluates images whenever Inspector ingests new CVEs upstream.
echo ""
echo "→ Setting ECR registry scanning configuration to ENHANCED/CONTINUOUS_SCAN …"
aws "${aws_args[@]}" ecr put-registry-scanning-configuration \
  --scan-type ENHANCED \
  --rules '[
    {
      "scanFrequency": "CONTINUOUS_SCAN",
      "repositoryFilters": [{"filter": "*", "filterType": "WILDCARD"}]
    }
  ]' \
  --output json | head -c 400
echo ""

# ── 3. Verify ─────────────────────────────────────────────────────────
echo ""
echo "→ Verifying Inspector v2 status …"
aws "${aws_args[@]}" inspector2 batch-get-account-status \
  --account-ids "$ACCOUNT_ID" \
  --query 'accounts[0].resourceState.ecr.status' \
  --output text

echo ""
echo "→ Verifying ECR registry scanning configuration …"
aws "${aws_args[@]}" ecr get-registry-scanning-configuration \
  --query 'scanningConfiguration.{scanType:scanType,rules:rules}' \
  --output json

echo ""
echo "  ✓ Done. Initial scan typically completes within ~15 min."
echo "  Findings: Inspector console + Security Hub (FSBP standard)."
