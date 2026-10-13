#!/usr/bin/env bash
# infra/scripts/deploy-p2.sh
# One-command Pipeline 2 deploy with trial-mode support.
#
# Usage:
#   TRIAL_MODE=true ./infra/scripts/deploy-p2.sh testbank
#   ./infra/scripts/deploy-p2.sh firstnational production
#
# Requires: infra/setup/bank.env.<bank-id> with JIRA + GitHub credentials.
# Env knobs:
#   TRIAL_MODE=true       skip ACM + DNS + Security stack; serve via *.cloudfront.net
#   SKIP_POSTDEPLOY=true  stop after infra (no sync, no Cognito, no SPA)
#   SKIP_SECURITY=true    skip Security stack (defaults to true in trial mode)
#   ENV_FILE=<path>       override env file path
#   IMAGE_TAG=<tag>       Docker image tag (default: latest)
#   TEMP_PASSWORD=<pw>    Cognito user temp password (default: Fortress2026!)
#   NON_INTERACTIVE=true  skip all read-prompts (use env vars or fail)
#   WAF_AUTO=true         auto-run register-bank-ip.sh (same-account assumption)
#   MIGRATIONS_AUTO=true  run alembic via ECS RunTask after compute deploys

set -euo pipefail

# ── Args + env ────────────────────────────────────────────────────────────────
BANK_ID="${1:?Usage: deploy-p2.sh <bank-id> [environment]}"
ENVIRONMENT="${2:-production}"
TRIAL_MODE="${TRIAL_MODE:-false}"
SKIP_POSTDEPLOY="${SKIP_POSTDEPLOY:-false}"
SKIP_SECURITY="${SKIP_SECURITY:-$TRIAL_MODE}"
NON_INTERACTIVE="${NON_INTERACTIVE:-false}"
WAF_AUTO="${WAF_AUTO:-false}"
MIGRATIONS_AUTO="${MIGRATIONS_AUTO:-false}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
TEMP_PASSWORD="${TEMP_PASSWORD:-Fortress2026!}"

for b in TRIAL_MODE SKIP_POSTDEPLOY SKIP_SECURITY NON_INTERACTIVE WAF_AUTO MIGRATIONS_AUTO; do
  case "${!b}" in true|false) ;; *) echo "ERROR: $b must be true|false (got: ${!b})"; exit 1 ;; esac
done

ENV_FILE="${ENV_FILE:-infra/setup/bank.env.${BANK_ID}}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found."
  echo "  Copy infra/setup/bank.env.template to $ENV_FILE and fill it in."
  exit 1
fi

# Source env file; script args + ambient env override what the file sets.
_BANK_ID="$BANK_ID"
_AWS_PROFILE="${AWS_PROFILE:-}"
_AWS_REGION="${AWS_REGION:-}"
set -a; source "$ENV_FILE"; set +a
BANK_ID="$_BANK_ID"
AWS_PROFILE="${_AWS_PROFILE:-${AWS_PROFILE:-default}}"
AWS_REGION="${_AWS_REGION:-${AWS_REGION:-us-east-1}}"
REGION="$AWS_REGION"
export AWS_PROFILE AWS_REGION

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
FORTRESS_ACCOUNT_ID="${FORTRESS_ACCOUNT_ID:?Set FORTRESS_ACCOUNT_ID env var}"

echo ""
echo "======================================================"
echo "  Fortress Labs — Pipeline 2 deployment"
echo "  Bank ID     : $BANK_ID"
echo "  Environment : $ENVIRONMENT"
echo "  Region      : $REGION"
echo "  Account     : $ACCOUNT_ID"
echo "  TrialMode   : $TRIAL_MODE   (security skipped: $SKIP_SECURITY)"
echo "  Env file    : $ENV_FILE"
echo "======================================================"
echo ""

# ── Non-credential inputs ─────────────────────────────────────────────────────
if [[ "$TRIAL_MODE" == "true" ]]; then
  BANK_DOMAIN="${BANK_DOMAIN:-testbank.local}"
  ALERT_EMAIL="${ALERT_EMAIL:-noreply@${BANK_DOMAIN}}"
  VPC_CIDR="${VPC_CIDR:-10.2.0.0/16}"
  echo "  TrialMode defaults — BankDomain: $BANK_DOMAIN | AlertEmail: $ALERT_EMAIL | CIDR: $VPC_CIDR"
elif [[ "$NON_INTERACTIVE" == "true" ]]; then
  BANK_DOMAIN="${BANK_DOMAIN:?Set BANK_DOMAIN env var in NON_INTERACTIVE mode}"
  ALERT_EMAIL="${ALERT_EMAIL:?Set ALERT_EMAIL env var in NON_INTERACTIVE mode}"
  VPC_CIDR="${VPC_CIDR:-10.2.0.0/16}"
else
  read -p "  Bank domain (e.g. firstnationalbank.com): " BANK_DOMAIN
  read -p "  Alert email for CloudWatch alarms: " ALERT_EMAIL
  read -p "  VPC CIDR (default 10.2.0.0/16): " VPC_CIDR
  VPC_CIDR="${VPC_CIDR:-10.2.0.0/16}"
fi

if [[ "$SKIP_SECURITY" == "false" ]]; then
  if [[ "$NON_INTERACTIVE" == "true" ]]; then
    CLOUDTRAIL_BUCKET="${CLOUDTRAIL_BUCKET:?Set CLOUDTRAIL_BUCKET env var in NON_INTERACTIVE mode}"
  else
    read -p "  CloudTrail S3 bucket name (bank creates this): " CLOUDTRAIL_BUCKET
  fi
fi

CFN_DIR="infra/cloudformation"

deploy_stack() {
  local stack_name="$1" template="$2"
  shift 2
  echo "→ Deploying $stack_name ..."
  aws cloudformation deploy \
    --stack-name "$stack_name" \
    --template-file "$template" \
    --capabilities CAPABILITY_NAMED_IAM \
    "$@"
  echo "  ✓ $stack_name deployed"
  echo ""
}

# ── Step 1: VPC ──────────────────────────────────────────────────────────────
deploy_stack "fortress-p2-vpc-${BANK_ID}" \
  "${CFN_DIR}/pipeline2-vpc.yml" \
  --parameter-overrides \
    BankId="$BANK_ID" \
    Environment="$ENVIRONMENT" \
    VpcCidr="$VPC_CIDR"

NAT_EIP=$(aws cloudformation describe-stacks \
  --stack-name "fortress-p2-vpc-${BANK_ID}" \
  --query "Stacks[0].Outputs[?OutputKey=='NatGatewayEip'].OutputValue" \
  --output text)
echo "  NAT EIP: $NAT_EIP"

if [[ "$WAF_AUTO" == "true" ]]; then
  echo "→ WAF_AUTO=true — adding NAT EIP to P1 WAF allowlist inline ..."
  ./infra/scripts/register-bank-ip.sh "$BANK_ID" "$NAT_EIP"
elif [[ "$NON_INTERACTIVE" == "true" ]]; then
  echo "  NON_INTERACTIVE=true and WAF_AUTO=false — assuming WAF is already configured."
else
  echo "  *** Register this NAT EIP in P1 WAF allowlist ***"
  echo "  On Fortress account, run:"
  echo "    ./infra/scripts/register-bank-ip.sh $BANK_ID $NAT_EIP"
  echo ""
  read -p "  Press Enter once P1 WAF is updated..."
fi

# ── Step 2: Data stack (Aurora, Redis, KMS, secret shells) ───────────────────
deploy_stack "fortress-p2-data-${BANK_ID}" \
  "${CFN_DIR}/pipeline2-data.yml" \
  --parameter-overrides \
    BankId="$BANK_ID" \
    Environment="$ENVIRONMENT"

# ── Step 3: Load credentials (single source of truth) ────────────────────────
echo "→ Loading credentials from $ENV_FILE ..."
./scripts/load-bank-credentials.sh --env-file "$ENV_FILE"

# P1 API key (Fortress-provided, not in env file)
EXISTING_P1_KEY=$(aws secretsmanager get-secret-value \
  --secret-id "fortress/${BANK_ID}/pipeline1-api-key" \
  --query SecretString --output text 2>/dev/null || echo "")
if [[ -n "${PIPELINE1_API_KEY:-}" ]]; then
  aws secretsmanager put-secret-value \
    --secret-id "fortress/${BANK_ID}/pipeline1-api-key" \
    --secret-string "$PIPELINE1_API_KEY" >/dev/null
  echo "  ✓ P1 API key set from PIPELINE1_API_KEY env var"
elif [[ -n "$EXISTING_P1_KEY" && "$EXISTING_P1_KEY" != "REPLACE_WITH_FORTRESS_PROVIDED_API_KEY" ]]; then
  echo "  ↺ P1 API key already set (length ${#EXISTING_P1_KEY}), skipping"
elif [[ "$NON_INTERACTIVE" == "true" ]]; then
  echo "ERROR: NON_INTERACTIVE=true but PIPELINE1_API_KEY env var is unset and"
  echo "       fortress/${BANK_ID}/pipeline1-api-key still holds the placeholder."
  exit 1
else
  read -p "  Fortress P1 API key (from Fortress team, sk_…): " P1_API_KEY
  aws secretsmanager put-secret-value \
    --secret-id "fortress/${BANK_ID}/pipeline1-api-key" \
    --secret-string "$P1_API_KEY" >/dev/null
  echo "  ✓ P1 API key set"
fi
unset EXISTING_P1_KEY
echo ""

# Aurora endpoint (used by alembic step after compute deploy)
AURORA_HOST=$(aws cloudformation describe-stacks \
  --stack-name "fortress-p2-data-${BANK_ID}" \
  --query "Stacks[0].Outputs[?OutputKey=='AuroraEndpoint'].OutputValue" \
  --output text)

# ── Step 5: Build + push image ───────────────────────────────────────────────
echo "→ Building and pushing Pipeline 2 image ..."
aws ecr create-repository \
  --repository-name "fortress/pipeline2-${BANK_ID}" 2>/dev/null || true
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/fortress/pipeline2-${BANK_ID}"
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
docker build -f Dockerfile.pipeline2 -t "fortress/pipeline2:${IMAGE_TAG}" .
docker tag "fortress/pipeline2:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:${IMAGE_TAG}"
echo "  ✓ Image pushed: ${ECR_URI}:${IMAGE_TAG}"
echo ""

# ── Step 6: Compute (ECS, ALB, [CloudFront in trial]) ────────────────────────
# github-token is created out-of-band (not managed by pipeline2-data.yml). Look
# up its full ARN — incl. the random -XXXXXX suffix Secrets Manager appends —
# and pass to the compute stack. Whole-secret ECS refs require the suffix or
# they fail at task placement with ResourceNotFoundException.
GITHUB_TOKEN_ARN=$(aws secretsmanager describe-secret \
  --secret-id "fortress/${BANK_ID}/github-token" \
  --query 'ARN' --output text 2>/dev/null || true)
if [[ -z "$GITHUB_TOKEN_ARN" || "$GITHUB_TOKEN_ARN" == "None" ]]; then
  echo "ERROR: secret fortress/${BANK_ID}/github-token does not exist." >&2
  echo "  Create it first with:" >&2
  echo "    aws secretsmanager create-secret --name fortress/${BANK_ID}/github-token --secret-string <PAT>" >&2
  exit 1
fi

deploy_stack "fortress-p2-compute-${BANK_ID}" \
  "${CFN_DIR}/pipeline2-compute.yml" \
  --parameter-overrides \
    BankId="$BANK_ID" \
    Environment="$ENVIRONMENT" \
    ImageUri="${ECR_URI}:${IMAGE_TAG}" \
    BankDomain="$BANK_DOMAIN" \
    AlertEmail="$ALERT_EMAIL" \
    TrialMode="$TRIAL_MODE" \
    GithubTokenSecretArn="$GITHUB_TOKEN_ARN"

API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name "fortress-p2-compute-${BANK_ID}" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
  --output text)
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "fortress-p2-compute-${BANK_ID}" \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDns'].OutputValue" \
  --output text)

# ── Step 7: Dashboard (S3 + CloudFront + Cognito) ────────────────────────────
deploy_stack "fortress-p2-dashboard-${BANK_ID}" \
  "${CFN_DIR}/pipeline2-dashboard.yml" \
  --parameter-overrides \
    BankId="$BANK_ID" \
    Environment="$ENVIRONMENT" \
    BankDomain="$BANK_DOMAIN" \
    P2ApiUrl="$API_ENDPOINT" \
    TrialMode="$TRIAL_MODE"

DASHBOARD_CF=$(aws cloudformation describe-stacks \
  --stack-name "fortress-p2-dashboard-${BANK_ID}" \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" \
  --output text)

# ── Step 7.5: Alembic migrations (post-compute, uses compute's taskdef) ──────
if [[ "$MIGRATIONS_AUTO" == "true" ]]; then
  echo "→ MIGRATIONS_AUTO=true — running alembic via ECS RunTask ..."
  PRIVATE_SUBNET_A=$(aws cloudformation describe-stacks \
    --stack-name "fortress-p2-vpc-${BANK_ID}" \
    --query "Stacks[0].Outputs[?OutputKey=='PrivateSubnetA'].OutputValue" --output text)
  PRIVATE_SUBNET_B=$(aws cloudformation describe-stacks \
    --stack-name "fortress-p2-vpc-${BANK_ID}" \
    --query "Stacks[0].Outputs[?OutputKey=='PrivateSubnetB'].OutputValue" --output text)
  ECS_SG=$(aws cloudformation describe-stacks \
    --stack-name "fortress-p2-vpc-${BANK_ID}" \
    --query "Stacks[0].Outputs[?OutputKey=='SgEcs'].OutputValue" --output text)
  CLUSTER="fortress-pipeline2-${BANK_ID}"
  TASKDEF="fortress-pipeline2-${BANK_ID}"
  RUN_TASK_JSON=$(aws ecs run-task \
    --cluster "$CLUSTER" \
    --task-definition "$TASKDEF" \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$PRIVATE_SUBNET_A,$PRIVATE_SUBNET_B],securityGroups=[$ECS_SG],assignPublicIp=DISABLED}" \
    --overrides '{"containerOverrides":[{"name":"pipeline2","command":["python","-m","alembic","upgrade","head"]}]}' \
    --output json)
  TASK_ARN=$(echo "$RUN_TASK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['tasks'][0]['taskArn'])")
  echo "  Task: $TASK_ARN"
  echo "  Waiting for migrations to complete (up to 5 min) ..."
  aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"
  EXIT_CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
    --query "tasks[0].containers[0].exitCode" --output text)
  if [[ "$EXIT_CODE" != "0" ]]; then
    echo "  ✗ alembic task exited with code $EXIT_CODE"
    echo "  Check logs: aws logs tail /fortress/pipeline2/${BANK_ID} --since 5m"
    exit 1
  fi
  echo "  ✓ Migrations applied"
  echo ""
elif [[ "$NON_INTERACTIVE" == "true" ]]; then
  echo "→ Skipping migrations (NON_INTERACTIVE=true, MIGRATIONS_AUTO=false)"
  echo "  If tables are missing, run alembic manually via SSM/bastion."
  echo ""
else
  echo "→ Run Alembic migrations against bank Aurora (host: $AURORA_HOST):"
  echo "  DATABASE_URL='postgresql+asyncpg://fortress_${BANK_ID}:<password>@${AURORA_HOST}:5432/fortress_p2?ssl=require' \\"
  echo "  python -m alembic upgrade head"
  echo ""
  read -p "  Press Enter once all migrations have run successfully..."
fi

# ── Step 8: Security (Security Hub, GuardDuty, CloudTrail) ───────────────────
if [[ "$SKIP_SECURITY" == "false" ]]; then
  deploy_stack "fortress-p2-security-${BANK_ID}" \
    "${CFN_DIR}/pipeline2-security.yml" \
    --parameter-overrides \
      BankId="$BANK_ID" \
      Environment="$ENVIRONMENT" \
      CloudTrailBucketName="$CLOUDTRAIL_BUCKET"
else
  echo "→ Skipping Security stack (SKIP_SECURITY=$SKIP_SECURITY)"
  echo ""
fi

if [[ "$SKIP_POSTDEPLOY" == "true" ]]; then
  echo "======================================================"
  echo "  Infra deploy complete (SKIP_POSTDEPLOY=true)"
  echo "  API endpoint : $API_ENDPOINT"
  echo "  Dashboard CF : https://${DASHBOARD_CF}"
  echo "======================================================"
  exit 0
fi

# ── Step 9: Wait for API health ──────────────────────────────────────────────
echo "→ Waiting for ${API_ENDPOINT}/health (up to 5 min) ..."
for i in $(seq 1 30); do
  if curl -sf -m 5 "${API_ENDPOINT}/health" >/dev/null 2>&1; then
    echo "  ✓ API healthy"
    break
  fi
  if [[ $i == 30 ]]; then
    echo "  ✗ API never came healthy. Check: aws logs tail /fortress/pipeline2/${BANK_ID} --since 5m"
    exit 1
  fi
  sleep 10
done
echo ""

# ── Step 10: Trigger first P1 → P2 sync ──────────────────────────────────────
INTERNAL_SECRET=$(aws secretsmanager get-secret-value \
  --secret-id "fortress/${BANK_ID}/internal-sync-secret" \
  --query SecretString --output text)
echo "→ Triggering first P1 → P2 sync ..."
if curl -sf -X POST "${API_ENDPOINT}/internal/sync/trigger" \
  -H "X-Internal-Secret: ${INTERNAL_SECRET}" \
  -H "Content-Type: application/json" \
  -o /tmp/sync-trigger-${BANK_ID}.json; then
  echo "  ✓ Sync triggered. Response: $(cat /tmp/sync-trigger-${BANK_ID}.json)"
else
  echo "  ⚠ Sync trigger returned non-zero (rules can also be synced via the daily cron)"
fi
echo ""

# ── Step 11: Create Cognito dashboard users ──────────────────────────────────
POOL_ID=$(aws ssm get-parameter \
  --name "/fortress/${BANK_ID}/COGNITO_USER_POOL_ID" \
  --query Parameter.Value --output text)

create_cognito_user() {
  local email="$1" group="$2"
  if aws cognito-idp admin-get-user --user-pool-id "$POOL_ID" --username "$email" >/dev/null 2>&1; then
    echo "  ↺ $email exists, skipping"
    return
  fi
  aws cognito-idp admin-create-user \
    --user-pool-id "$POOL_ID" \
    --username "$email" \
    --user-attributes Name=email,Value="$email" Name=email_verified,Value=true \
    --temporary-password "$TEMP_PASSWORD" \
    --message-action SUPPRESS >/dev/null
  aws cognito-idp admin-add-user-to-group \
    --user-pool-id "$POOL_ID" \
    --username "$email" \
    --group-name "$group" >/dev/null
  echo "  ✓ $email → $group"
}

CCO_EMAIL="${COGNITO_CCO_EMAIL:-cco@${BANK_DOMAIN}}"
CRO_EMAIL="${COGNITO_CRO_EMAIL:-cro@${BANK_DOMAIN}}"
CIO_EMAIL="${COGNITO_CIO_EMAIL:-cio@${BANK_DOMAIN}}"
ANALYST_EMAIL="${COGNITO_ANALYST_EMAIL:-analyst@${BANK_DOMAIN}}"
ADMIN_EMAIL="${COGNITO_ADMIN_EMAIL:-admin@${BANK_DOMAIN}}"

echo "→ Creating Cognito dashboard users (temp password: $TEMP_PASSWORD) ..."
create_cognito_user "$CCO_EMAIL"     CCO
create_cognito_user "$CRO_EMAIL"     CRO
create_cognito_user "$CIO_EMAIL"     CIO
create_cognito_user "$ANALYST_EMAIL" Analyst
create_cognito_user "$ADMIN_EMAIL"   Admin
echo ""

# ── Step 12: Build + upload dashboard SPA ────────────────────────────────────
echo "→ Building and uploading dashboard SPA ..."
if ! command -v npm >/dev/null 2>&1; then
  echo "  ✗ npm not found. Install Node.js, then run manually:"
  echo "    ./dashboard/deploy.sh $BANK_ID $ENVIRONMENT"
else
  ./dashboard/deploy.sh "$BANK_ID" "$ENVIRONMENT"
fi
echo ""

# ── Final summary ────────────────────────────────────────────────────────────
echo "======================================================"
echo "  Pipeline 2 deployment complete for: $BANK_ID"
echo ""
echo "  API endpoint : $API_ENDPOINT"
echo "  ALB DNS      : $ALB_DNS"
echo "  Dashboard    : https://${DASHBOARD_CF}"
echo ""
echo "  Test the dashboard NOW:"
echo "    1. Open https://${DASHBOARD_CF}"
echo "    2. Login: $CCO_EMAIL / $TEMP_PASSWORD"
echo "    3. Cognito will force a password change on first login"
echo ""
echo "  Configure JIRA Cloud Automation webhook:"
echo "    URL    : ${API_ENDPOINT}/webhooks/jira"
echo "    Header : Authorization: Bearer \$(aws secretsmanager get-secret-value \\"
echo "               --secret-id fortress/${BANK_ID}/jira-webhook-secret \\"
echo "               --query SecretString --output text)"
if [[ "$TRIAL_MODE" == "false" ]]; then
  echo ""
  echo "  Create DNS CNAMEs (prod only):"
  echo "    fortressapi.${BANK_DOMAIN} → ${ALB_DNS}     (after ACM validation)"
  echo "    dashboard.${BANK_DOMAIN}   → ${DASHBOARD_CF}"
fi
echo "======================================================"
