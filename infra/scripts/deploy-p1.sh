#!/usr/bin/env bash
# infra/scripts/deploy-p1.sh
# Deploy all Pipeline 1 stacks to the Fortress AWS account.
# Run from the repo root: ./infra/scripts/deploy-p1.sh [staging|production]
set -euo pipefail

ENVIRONMENT="${1:-production}"
REGION="${AWS_REGION:-us-east-1}"
PROFILE="${AWS_PROFILE:-fortress-prod}"
ACCOUNT_ID=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
IMAGE_TAG="${IMAGE_TAG:-latest}"
ALERT_EMAIL="${ALERT_EMAIL:-ops@fortresslabs.dev}"
DOMAIN_NAME="${DOMAIN_NAME:-fortresslabs.dev}"
API_DESIRED_COUNT="${API_DESIRED_COUNT:-2}"

echo ""
echo "======================================================"
echo "  Fortress Labs — Pipeline 1 deployment"
echo "  Environment : $ENVIRONMENT"
echo "  Region      : $REGION"
echo "  Account     : $ACCOUNT_ID"
echo "======================================================"
echo ""

CFN_DIR="infra/cloudformation"

deploy_stack() {
  local stack_name="$1"
  local template="$2"
  shift 2
  echo "→ Deploying $stack_name ..."
  aws cloudformation deploy \
    --profile "$PROFILE" \
    --region "$REGION" \
    --stack-name "$stack_name" \
    --template-file "$template" \
    --capabilities CAPABILITY_NAMED_IAM \
    "$@"
  echo "  ✓ $stack_name deployed"
  echo ""
}

# ── Step 1: VPC ──────────────────────────────────────────────────────
deploy_stack "fortress-p1-vpc-${ENVIRONMENT}" \
  "${CFN_DIR}/pipeline1-vpc.yml" \
  --parameter-overrides Environment="$ENVIRONMENT"

# ── Step 2: Data (Aurora, Redis, S3, KMS, Secrets) ───────────────────
deploy_stack "fortress-p1-data-${ENVIRONMENT}" \
  "${CFN_DIR}/pipeline1-data.yml" \
  --parameter-overrides Environment="$ENVIRONMENT"

# Extract S3 bucket name for security stack
S3_BUCKET=$(aws cloudformation describe-stacks \
  --profile "$PROFILE" --region "$REGION" \
  --stack-name "fortress-p1-data-${ENVIRONMENT}" \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output text)

echo "  Data stack outputs:"
echo "    S3 bucket: $S3_BUCKET"

# ── Step 2b: URL-encode passwords in fortress/pipeline1/app-config ───
# CFN renders REDIS_URL/DATABASE_URL with raw {{resolve:secretsmanager:...}}
# substitution; reserved chars in the auto-generated passwords would break URL
# parsing in the app. Encode them idempotently before the API container boots.
AWS_PROFILE="$PROFILE" AWS_REGION="$REGION" \
  "${CFN_DIR%/cloudformation}/scripts/encode-app-config-secret.sh"

# ── Step 3: Push Docker image (schema bootstrap deferred to one-shot
#            ECS init task after Step 5 so SgEcs is in Aurora's allowlist) ─────
echo "→ Building and pushing Pipeline 1 image ..."
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/fortress/pipeline1"

aws ecr get-login-password --region "$REGION" --profile "$PROFILE" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# linux/amd64 — ECS Fargate runs on x86_64 even when built on Apple Silicon
docker buildx build --platform linux/amd64 \
  -f Dockerfile.pipeline1 \
  -t "${ECR_URI}:${IMAGE_TAG}" \
  --push .
echo "  ✓ Image pushed: ${ECR_URI}:${IMAGE_TAG}"
echo ""

# ── Step 4: Compute (ECS, ALB, WAF, EventBridge) ─────────────────────
# API service is created at DesiredCount=0; we bump to 1 after the schema
# bootstrap one-shot task succeeds in Step 5.
read -p "  Enter Route53 Hosted Zone ID for ${DOMAIN_NAME}: " HOSTED_ZONE_ID
aws ssm put-parameter --profile "$PROFILE" --region "$REGION" \
  --name /fortress/pipeline1/HOSTED_ZONE_ID \
  --value "$HOSTED_ZONE_ID" \
  --type String \
  --overwrite

deploy_stack "fortress-p1-compute-${ENVIRONMENT}" \
  "${CFN_DIR}/pipeline1-compute.yml" \
  --parameter-overrides \
    Environment="$ENVIRONMENT" \
    ImageTag="$IMAGE_TAG" \
    DomainName="$DOMAIN_NAME" \
    AlertEmail="$ALERT_EMAIL" \
    ApiDesiredCount="$API_DESIRED_COUNT"

# ── Step 5: Bootstrap Aurora schema (one-shot Fargate task) ──────────
# Aurora is only reachable from SgEcs, so we run scripts/setup/init_p1_schema.py
# via EcsTaskDefInitP1. The init task stamps alembic_version='019' so the
# main API container's `alembic upgrade head` is a no-op.
INIT_TASK_DEF=$(aws cloudformation describe-stacks \
  --profile "$PROFILE" --region "$REGION" \
  --stack-name "fortress-p1-compute-${ENVIRONMENT}" \
  --query "Stacks[0].Outputs[?OutputKey=='EcsTaskDefInitArn'].OutputValue" \
  --output text)
SUBNET=$(aws cloudformation list-exports --profile "$PROFILE" --region "$REGION" \
  --query "Exports[?Name=='FortressP1-${ENVIRONMENT}-PrivateSubnetA'].Value" --output text)
SG_ECS=$(aws cloudformation list-exports --profile "$PROFILE" --region "$REGION" \
  --query "Exports[?Name=='FortressP1-${ENVIRONMENT}-SgEcs'].Value" --output text)

echo "→ Bootstrapping Aurora schema via init task ..."
INIT_TASK_ARN=$(aws ecs run-task --profile "$PROFILE" --region "$REGION" \
  --cluster "fortress-pipeline1-${ENVIRONMENT}" \
  --task-definition "$INIT_TASK_DEF" \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG_ECS],assignPublicIp=DISABLED}" \
  --query "tasks[0].taskArn" --output text)

aws ecs wait tasks-stopped --profile "$PROFILE" --region "$REGION" \
  --cluster "fortress-pipeline1-${ENVIRONMENT}" --tasks "$INIT_TASK_ARN"

INIT_EXIT=$(aws ecs describe-tasks --profile "$PROFILE" --region "$REGION" \
  --cluster "fortress-pipeline1-${ENVIRONMENT}" --tasks "$INIT_TASK_ARN" \
  --query "tasks[0].containers[0].exitCode" --output text)
if [[ "$INIT_EXIT" != "0" ]]; then
  echo "  ✗ Schema init task exited $INIT_EXIT — check /fortress/pipeline1 logs"
  exit 1
fi
echo "  ✓ Schema bootstrap complete"

# Bump API service to 1 so the long-running container picks up the seeded schema
aws ecs update-service --profile "$PROFILE" --region "$REGION" \
  --cluster "fortress-pipeline1-${ENVIRONMENT}" \
  --service "fortress-pipeline1-api-${ENVIRONMENT}" \
  --desired-count 1 >/dev/null
echo "  ✓ API service bumped to desired-count=1"

# ── Step 6: Security (Security Hub, GuardDuty, CloudTrail) ───────────
deploy_stack "fortress-p1-security-${ENVIRONMENT}" \
  "${CFN_DIR}/pipeline1-security.yml" \
  --parameter-overrides \
    Environment="$ENVIRONMENT" \
    S3BucketName="$S3_BUCKET"

# ── Step 7: Enable EventBridge + first scrape ─────────────────────────
SCHEDULE_NAME="fortress-p1-daily-scrape-${ENVIRONMENT}"
echo "→ Enabling EventBridge scheduler: $SCHEDULE_NAME"
aws scheduler update-schedule \
  --profile "$PROFILE" \
  --region "$REGION" \
  --name "$SCHEDULE_NAME" \
  --state ENABLED \
  --schedule-expression "cron(0 2 * * ? *)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "$(aws scheduler get-schedule --profile "$PROFILE" --region "$REGION" --name "$SCHEDULE_NAME" --query Target --output json)"

echo "  ✓ Scheduler enabled"
echo ""

# ── Verify health ─────────────────────────────────────────────────────
ALB_DNS=$(aws cloudformation describe-stacks \
  --profile "$PROFILE" --region "$REGION" \
  --stack-name "fortress-p1-compute-${ENVIRONMENT}" \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDns'].OutputValue" \
  --output text)

echo "======================================================"
echo "  Pipeline 1 deployment complete!"
echo ""
echo "  Sync API endpoint: https://api.${DOMAIN_NAME}"
echo "  ALB DNS: $ALB_DNS"
echo ""
echo "  Next steps:"
echo "  1. Create CNAME: api.${DOMAIN_NAME} → ${ALB_DNS}"
echo "  2. Verify (trailing slash required — router is mounted at /health):"
echo "       curl -k https://${ALB_DNS}/health/"
echo "  3. Issue P2 api_keys per bank: ./scripts/register-bank.sh --bank-id <id> ..."
echo "======================================================"
