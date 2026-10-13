#!/usr/bin/env bash
# infra/scripts/register-bank-ip.sh
# Add a bank's NAT Gateway EIP to the P1 sync API WAF allowlist.
# Run from Fortress account after each bank P2 VPC deployment.
# Usage: ./infra/scripts/register-bank-ip.sh <bank-id> <nat-eip>
set -euo pipefail

BANK_ID="${1:?Usage: register-bank-ip.sh <bank-id> <nat-eip>}"
NAT_EIP="${2:?Provide the bank NAT Gateway EIP}"
ENVIRONMENT="${ENVIRONMENT:-production}"
REGION="${AWS_REGION:-us-east-1}"

# Honor AWS_PROFILE when set; otherwise fall back to ambient creds
# (EC2 instance role / env vars). Passing `--profile ""` or a bogus
# profile name causes the AWS CLI to abort with "config profile could
# not be found", which silently no-ops the script.
PROFILE_ARGS=()
if [ -n "${AWS_PROFILE:-}" ]; then
  PROFILE_ARGS=(--profile "$AWS_PROFILE")
fi

echo "→ Registering $BANK_ID NAT EIP ($NAT_EIP) in P1 WAF allowlist ..."

# Store EIP in SSM for reference
aws ssm put-parameter \
  "${PROFILE_ARGS[@]}" --region "$REGION" \
  --name "/fortress/banks/${BANK_ID}/nat-eip" \
  --value "$NAT_EIP" \
  --type String \
  --overwrite

# Get WAF ACL ID
WAF_ACL_ID=$(aws wafv2 list-web-acls \
  "${PROFILE_ARGS[@]}" --region "$REGION" \
  --scope REGIONAL \
  --query "WebACLs[?Name=='fortress-p1-waf-${ENVIRONMENT}'].Id" \
  --output text)

WAF_ACL_ARN=$(aws wafv2 list-web-acls \
  "${PROFILE_ARGS[@]}" --region "$REGION" \
  --scope REGIONAL \
  --query "WebACLs[?Name=='fortress-p1-waf-${ENVIRONMENT}'].ARN" \
  --output text)

echo "  WAF ACL: $WAF_ACL_ID"

# Get IPSet — owned by pipeline1-compute.yml since H4 (the WebACL has an
# Allow rule at priority 0 referencing this set). On a fresh stack the
# IPSet exists with Addresses=[]; we update in place.
#
# CFN redeploy trap: `aws cloudformation update-stack` on pipeline1-compute
# will reset Addresses to [] from the template. After any compute-stack
# update, re-run this script for every registered bank. EIPs are kept in
# SSM under /fortress/banks/<bank>/nat-eip for that re-hydration.
IP_SET_NAME="fortress-p1-bank-ips-${ENVIRONMENT}"
EXISTING_IP_SET=$(aws wafv2 list-ip-sets \
  "${PROFILE_ARGS[@]}" --region "$REGION" \
  --scope REGIONAL \
  --query "IPSets[?Name=='${IP_SET_NAME}'].Id" \
  --output text)

if [ -z "$EXISTING_IP_SET" ]; then
  echo "  ⚠ IPSet '$IP_SET_NAME' not found — pipeline1-compute stack must be deployed first."
  echo "  Falling back to ad-hoc IPSet (pre-H4 behavior). Not visible to WebACL!"
  IP_SET_ID=$(aws wafv2 create-ip-set \
    "${PROFILE_ARGS[@]}" --region "$REGION" \
    --name "$IP_SET_NAME" \
    --scope REGIONAL \
    --ip-address-version IPV4 \
    --addresses "${NAT_EIP}/32" \
    --query "Summary.Id" \
    --output text)
else
  IP_SET_ID="$EXISTING_IP_SET"

  # Fetch lock token + current addresses in one call to avoid a TOCTOU
  # where another writer rotates the lock between calls.
  IP_SET_JSON=$(aws wafv2 get-ip-set \
    "${PROFILE_ARGS[@]}" --region "$REGION" \
    --name "$IP_SET_NAME" \
    --scope REGIONAL \
    --id "$IP_SET_ID" \
    --output json)

  LOCK_TOKEN=$(echo "$IP_SET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['LockToken'])")

  # Dedup: skip the update entirely if this EIP is already in the set.
  # WAF rejects update-ip-set with duplicate CIDRs, so a re-run after a
  # successful registration would otherwise abort the script.
  read -r ALREADY MERGED_IPS < <(echo "$IP_SET_JSON" | NAT_EIP="$NAT_EIP" python3 -c "
import json, os, sys
data = json.load(sys.stdin)
current = data['IPSet']['Addresses']
new = os.environ['NAT_EIP'] + '/32'
already = 'yes' if new in current else 'no'
merged = current if already == 'yes' else current + [new]
print(already, ' '.join(merged) if merged else '_EMPTY_')
")

  if [ "$ALREADY" = "yes" ]; then
    echo "  ✓ Bank $BANK_ID NAT EIP $NAT_EIP already in WAF allowlist — no change"
    echo "  ✓ IP set ID: $IP_SET_ID"
    exit 0
  fi

  # shellcheck disable=SC2086  # intentional word-splitting on $MERGED_IPS
  aws wafv2 update-ip-set \
    "${PROFILE_ARGS[@]}" --region "$REGION" \
    --name "$IP_SET_NAME" \
    --scope REGIONAL \
    --id "$IP_SET_ID" \
    --addresses $MERGED_IPS \
    --lock-token "$LOCK_TOKEN"
fi

echo "  ✓ Bank $BANK_ID NAT EIP $NAT_EIP added to WAF allowlist"
echo "  ✓ IP set ID: $IP_SET_ID"
