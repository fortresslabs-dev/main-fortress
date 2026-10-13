#!/usr/bin/env bash
# infra/scripts/audit-cw-log-retention.sh
#
# Lists CloudWatch log groups in the current account and flags any with
# retention < 365 days (FFIEC IT Hdbk minimum 1y). Covers the long tail of
# AWS-auto-created log groups that CFN does not own (e.g. /aws/rds/*,
# /aws/events/*, /aws/codebuild/*). Optionally fixes them in-place.
#
# Covers drift side of FTR tracker #17. The CFN side (LogGroupP1 /
# LogGroupP2) is owned by the compute stacks.
#
# Usage:
#   ./infra/scripts/audit-cw-log-retention.sh                # report only
#   ./infra/scripts/audit-cw-log-retention.sh --fix          # set 365d on each non-compliant group
#   ./infra/scripts/audit-cw-log-retention.sh --profile PROFILE --region REGION
#
# Excludes:
#   /aws/lambda/*  — function-specific, typically owned elsewhere
#   /aws/codebuild/* — CI scope
# Override by re-running with --no-skip-defaults.

set -euo pipefail

PROFILE="${AWS_PROFILE:-}"
REGION="${AWS_REGION:-us-east-1}"
TARGET_DAYS=365
FIX=false
SKIP_DEFAULTS=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --target-days) TARGET_DAYS="$2"; shift 2 ;;
    --fix) FIX=true; shift ;;
    --no-skip-defaults) SKIP_DEFAULTS=false; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

aws_args=(--region "$REGION")
[[ -n "$PROFILE" ]] && aws_args=(--profile "$PROFILE" "${aws_args[@]}")

echo "======================================================"
echo "  CW Log Retention Audit  (target: ${TARGET_DAYS}d)"
echo "  Region : $REGION"
echo "  Mode   : $([[ $FIX == true ]] && echo "FIX" || echo "REPORT-ONLY")"
echo "======================================================"

# Collect log groups: name + retentionInDays (may be missing = "never expire")
mapfile -t rows < <(
  aws "${aws_args[@]}" logs describe-log-groups \
    --query 'logGroups[].[logGroupName, retentionInDays]' \
    --output text
)

non_compliant=0
for row in "${rows[@]}"; do
  name=$(awk '{print $1}' <<<"$row")
  retention=$(awk '{print $2}' <<<"$row")

  if [[ "$SKIP_DEFAULTS" == true ]]; then
    case "$name" in
      /aws/lambda/*|/aws/codebuild/*) continue ;;
    esac
  fi

  if [[ "$retention" == "None" ]]; then
    status="NEVER-EXPIRE"
  elif (( retention < TARGET_DAYS )); then
    status="${retention}d"
  else
    continue  # compliant — skip
  fi

  non_compliant=$((non_compliant + 1))
  printf "  %-60s  %s\n" "$name" "$status"

  if [[ "$FIX" == true ]]; then
    aws "${aws_args[@]}" logs put-retention-policy \
      --log-group-name "$name" \
      --retention-in-days "$TARGET_DAYS" \
      && echo "    → set to ${TARGET_DAYS}d"
  fi
done

echo ""
if (( non_compliant == 0 )); then
  echo "  ✓ All log groups already ≥ ${TARGET_DAYS}d (or excluded)."
else
  echo "  Non-compliant: $non_compliant"
  if [[ "$FIX" == false ]]; then
    echo "  Re-run with --fix to set them to ${TARGET_DAYS}d."
  fi
fi
