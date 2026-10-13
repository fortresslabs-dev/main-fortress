#!/usr/bin/env bash
# ecs_deploy.sh — Build, push to ECR, register new task definition revision,
#                 and update the ECS service.  Replaces deploy.sh.
#
# Usage (from your local machine):
#   ./scripts/ecs_deploy.sh                          # build + push + deploy :latest
#   IMAGE_TAG=abc1234 ./scripts/ecs_deploy.sh        # deploy specific SHA (skip build)
#   SKIP_BUILD=true ./scripts/ecs_deploy.sh          # skip build, use existing local image
#
# Required env vars:
#   AWS_REGION         default: us-east-1
#   AWS_ACCOUNT_ID     auto-detected via STS
#   ECR_REPO_NAME      default: fortress/pipeline1
#   ECS_CLUSTER        default: fortress-cluster
#   ECS_SERVICE        default: fortress-pipeline1-service
#   IMAGE_TAG          default: git SHA (built by this script)

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPO_NAME="${ECR_REPO_NAME:-fortress/pipeline1}"
ECS_CLUSTER="${ECS_CLUSTER:-fortress-cluster}"
ECS_SERVICE="${ECS_SERVICE:-fortress-pipeline1-service}"
SKIP_BUILD="${SKIP_BUILD:-false}"

if [ -z "${AWS_ACCOUNT_ID:-}" ]; then
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi
ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_REPO_URI="${ECR_REGISTRY}/${ECR_REPO_NAME}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD)}"

echo "[ecs_deploy] Cluster : ${ECS_CLUSTER}"
echo "[ecs_deploy] Service : ${ECS_SERVICE}"
echo "[ecs_deploy] Image   : ${ECR_REPO_URI}:${IMAGE_TAG}"
echo ""

# ── 1. Build and push image ─────────────────────────────────────────
if [ "${SKIP_BUILD}" != "true" ]; then
  echo "[ecs_deploy] Building and pushing image..."
  IMAGE_TAG="${IMAGE_TAG}" \
  AWS_REGION="${AWS_REGION}" \
  AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID}" \
  ECR_REPO_NAME="${ECR_REPO_NAME}" \
  bash scripts/ecr_push.sh
else
  echo "[ecs_deploy] SKIP_BUILD=true — skipping build, using existing ECR image."
fi

# ── 2. Register new task definition revision ───────────────────────
echo ""
echo "[ecs_deploy] Registering task definition revision..."
TASK_DEF_JSON=$(
  ACCOUNT_ID="${AWS_ACCOUNT_ID}" \
  REGION="${AWS_REGION}" \
  ECR_REPO_URI="${ECR_REPO_URI}" \
  IMAGE_TAG="${IMAGE_TAG}" \
  envsubst < infra/ecs/task-definition.json
)

NEW_TASK_DEF_ARN=$(aws ecs register-task-definition \
  --cli-input-json "${TASK_DEF_JSON}" \
  --region "${AWS_REGION}" \
  --query "taskDefinition.taskDefinitionArn" \
  --output text)
echo "[ecs_deploy] Task def: ${NEW_TASK_DEF_ARN}"

# ── 3. Update ECS service ───────────────────────────────────────────
echo ""
echo "[ecs_deploy] Updating ECS service..."
aws ecs update-service \
  --cluster "${ECS_CLUSTER}" \
  --service "${ECS_SERVICE}" \
  --task-definition "${NEW_TASK_DEF_ARN}" \
  --region "${AWS_REGION}" \
  --force-new-deployment \
  --output text > /dev/null
echo "[ecs_deploy] Service update triggered."

# ── 4. Wait for deployment to stabilise ────────────────────────────
echo ""
echo "[ecs_deploy] Waiting for service to stabilise (timeout: 5 min)..."
aws ecs wait services-stable \
  --cluster "${ECS_CLUSTER}" \
  --services "${ECS_SERVICE}" \
  --region "${AWS_REGION}" \
  2>/dev/null && echo "[ecs_deploy] Service is stable." || {
    echo "[ecs_deploy] WARNING: wait timed out — check ECS console for task status."
  }

# ── 5. Show running task ────────────────────────────────────────────
echo ""
echo "[ecs_deploy] Running tasks:"
aws ecs list-tasks \
  --cluster "${ECS_CLUSTER}" \
  --service-name "${ECS_SERVICE}" \
  --region "${AWS_REGION}" \
  --query "taskArns[]" \
  --output table

echo ""
echo "[ecs_deploy] Done. Image ${IMAGE_TAG} is live."
echo ""
echo "  Health check : curl http://<EC2_IP>:8000/health"
echo "  Logs         : aws logs tail /fortress/pipeline1 --follow --region ${AWS_REGION}"
