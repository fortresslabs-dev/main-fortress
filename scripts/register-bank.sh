#!/usr/bin/env bash
# scripts/register-bank.sh
# One-command bank registration: creates credentials, updates WAF, stores key in bank Secrets Manager.
#
# Usage:
#   ./scripts/register-bank.sh \
#     --bank-id      firstnational \
#     --bank-name    "First National Bank" \
#     --bank-ip      1.2.3.4 \
#     --bank-profile bank-firstnational \
#     --tier         standard
#
# Requirements:
#   - AWS_PROFILE=fortress-prod (P1 account access)
#   - P1 API running and accessible
#   - FORTRESS_INTERNAL_SECRET env var set
#   - Bank AWS profile configured (--bank-profile)

set -euo pipefail

# ── Parse args ────────────────────────────────────────────────────────
BANK_ID=""
BANK_NAME=""
BANK_NAT_IP=""
BANK_PROFILE=""
TIER="standard"
P1_REGION="${AWS_REGION:-us-east-1}"
FORTRESS_PROFILE="${AWS_PROFILE:-fortress-prod}"
P1_API_URL="${P1_API_URL:-https://api.fortresslabs.dev}"
INTERNAL_SECRET="${FORTRESS_INTERNAL_SECRET:?Set FORTRESS_INTERNAL_SECRET env var}"

while [[ $# -gt 0 ]]; do
  case $1 in
    --bank-id)      BANK_ID="$2";      shift 2 ;;
    --bank-name)    BANK_NAME="$2";    shift 2 ;;
    --bank-ip)      BANK_NAT_IP="$2";  shift 2 ;;
    --bank-profile) BANK_PROFILE="$2"; shift 2 ;;
    --tier)         TIER="$2";         shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

[[ -z "$BANK_ID" ]]      && { echo "ERROR: --bank-id required";      exit 1; }
[[ -z "$BANK_NAME" ]]    && { echo "ERROR: --bank-name required";     exit 1; }
[[ -z "$BANK_NAT_IP" ]]  && { echo "ERROR: --bank-ip required";       exit 1; }
[[ -z "$BANK_PROFILE" ]] && { echo "ERROR: --bank-profile required";  exit 1; }

echo ""
echo "══════════════════════════════════════════════"
echo "  Fortress Labs — Bank registration"
echo "  Bank ID   : $BANK_ID"
echo "  Bank name : $BANK_NAME"
echo "  NAT IP    : $BANK_NAT_IP"
echo "  Tier      : $TIER"
echo "══════════════════════════════════════════════"
echo ""

# ── Step 1: Register bank in P1 — get API key ─────────────────────────
echo "Step 1: Registering bank in P1 client_credentials..."

REGISTER_RESPONSE=$(curl -sf \
  -X POST "${P1_API_URL}/internal/admin/clients/" \
  -H "Content-Type: application/json" \
  -H "X-Internal-Secret: ${INTERNAL_SECRET}" \
  -d "{
    \"customer_id\": \"${BANK_ID}\",
    \"bank_name\": \"${BANK_NAME}\",
    \"tier\": \"${TIER}\"
  }")

# Extract the raw API key from response
RAW_API_KEY=$(echo "$REGISTER_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])")

if [[ -z "$RAW_API_KEY" ]]; then
  echo "ERROR: Registration failed. Response:"
  echo "$REGISTER_RESPONSE"
  exit 1
fi

echo "  ✓ Bank registered in client_credentials"
echo "  ✓ API key generated (${#RAW_API_KEY} chars)"

# ── Step 2: Store API key in bank's Secrets Manager ───────────────────
echo ""
echo "Step 2: Storing API key in bank Secrets Manager..."

aws secretsmanager put-secret-value \
  --profile "$BANK_PROFILE" \
  --region "$P1_REGION" \
  --secret-id "fortress/${BANK_ID}/pipeline1-api-key" \
  --secret-string "$RAW_API_KEY" \
  --query "VersionId" \
  --output text

echo "  ✓ Key stored in fortress/${BANK_ID}/pipeline1-api-key"

# Immediately overwrite the variable in memory
unset RAW_API_KEY

# ── Step 3: Store bank NAT IP in P1 SSM for audit trail ───────────────
echo ""
echo "Step 3: Recording bank NAT IP in P1 SSM..."

aws ssm put-parameter \
  --profile "$FORTRESS_PROFILE" \
  --region "$P1_REGION" \
  --name "/fortress/banks/${BANK_ID}/nat-eip" \
  --value "$BANK_NAT_IP" \
  --type String \
  --overwrite

echo "  ✓ NAT IP recorded: /fortress/banks/${BANK_ID}/nat-eip"

# ── Step 4: Add bank NAT IP to P1 WAF allowlist ───────────────────────
echo ""
echo "Step 4: Adding ${BANK_NAT_IP} to P1 WAF allowlist..."

ENVIRONMENT="${ENVIRONMENT:-production}"
IP_SET_NAME="fortress-p1-bank-ips-${ENVIRONMENT}"

# Get or create the IP set
IP_SET_JSON=$(aws wafv2 list-ip-sets \
  --profile "$FORTRESS_PROFILE" \
  --region "$P1_REGION" \
  --scope REGIONAL \
  --query "IPSets[?Name=='${IP_SET_NAME}']" \
  --output json)

IP_SET_COUNT=$(echo "$IP_SET_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")

if [[ "$IP_SET_COUNT" == "0" ]]; then
  echo "  Creating new IP set: $IP_SET_NAME"
  CREATE_RESULT=$(aws wafv2 create-ip-set \
    --profile "$FORTRESS_PROFILE" \
    --region "$P1_REGION" \
    --name "$IP_SET_NAME" \
    --scope REGIONAL \
    --ip-address-version IPV4 \
    --addresses "${BANK_NAT_IP}/32")
  IP_SET_ID=$(echo "$CREATE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['Summary']['Id'])")
else
  IP_SET_ID=$(echo "$IP_SET_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['Id'])")

  # Get lock token and current IPs
  GET_RESULT=$(aws wafv2 get-ip-set \
    --profile "$FORTRESS_PROFILE" \
    --region "$P1_REGION" \
    --name "$IP_SET_NAME" \
    --scope REGIONAL \
    --id "$IP_SET_ID")

  LOCK_TOKEN=$(echo "$GET_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['LockToken'])")

  # Add new IP to existing list
  UPDATED_IPS=$(echo "$GET_RESULT" | python3 -c "
import sys,json
data = json.load(sys.stdin)
ips = data['IPSet']['Addresses']
new_ip = '${BANK_NAT_IP}/32'
if new_ip not in ips:
    ips.append(new_ip)
print(' '.join(ips))
")

  aws wafv2 update-ip-set \
    --profile "$FORTRESS_PROFILE" \
    --region "$P1_REGION" \
    --name "$IP_SET_NAME" \
    --scope REGIONAL \
    --id "$IP_SET_ID" \
    --addresses $UPDATED_IPS \
    --lock-token "$LOCK_TOKEN" \
    --query "NextLockToken" \
    --output text
fi

echo "  ✓ ${BANK_NAT_IP}/32 added to WAF allowlist"
echo "  ✓ IP set ID: $IP_SET_ID"

# ── Step 5: Set P1 API URL in bank SSM ────────────────────────────────
echo ""
echo "Step 5: Setting P1 API URL in bank SSM..."

aws ssm put-parameter \
  --profile "$BANK_PROFILE" \
  --region "$P1_REGION" \
  --name "/fortress/${BANK_ID}/PIPELINE1_API_URL" \
  --value "$P1_API_URL" \
  --type String \
  --overwrite

aws ssm put-parameter \
  --profile "$BANK_PROFILE" \
  --region "$P1_REGION" \
  --name "/fortress/${BANK_ID}/PIPELINE1_CUSTOMER_ID" \
  --value "$BANK_ID" \
  --type String \
  --overwrite

echo "  ✓ P1 API URL and customer ID stored in bank SSM"

# ── Step 6: Verify registration ───────────────────────────────────────
echo ""
echo "Step 6: Verifying registration..."

VERIFY_API_KEY=$(aws secretsmanager get-secret-value \
    --profile "$BANK_PROFILE" \
    --region "$P1_REGION" \
    --secret-id "fortress/${BANK_ID}/pipeline1-api-key" \
    --query SecretString --output text)

VERIFY_RESPONSE=$(curl -sf \
  -X POST "${P1_API_URL}/api/license/verify?customer_id=${BANK_ID}&api_key=${VERIFY_API_KEY}")

unset VERIFY_API_KEY

echo "$VERIFY_RESPONSE" | python3 -c "
import sys,json
data = json.load(sys.stdin)
if data.get('valid'):
    print('  ✓ License verification PASSED')
    print(f'  ✓ Bank: {data.get(\"bank_name\")}')
    print(f'  ✓ Tier: {data.get(\"tier\")}')
else:
    print('  ✗ License verification FAILED')
    sys.exit(1)
"

# ── Summary ───────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════"
echo "  Registration complete: $BANK_NAME"
echo ""
echo "  Next step — trigger first sync from P2:"
echo "  curl -X POST https://fortressapi.BANK_DOMAIN/internal/sync/trigger \\"
echo "    -H 'X-Internal-Secret: \$FORTRESS_INTERNAL_SECRET'"
echo "══════════════════════════════════════════════"
