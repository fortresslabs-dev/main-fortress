#!/usr/bin/env bash
# ecr_push.sh — Build pipeline1 image, tag with git SHA, push to ECR.
#
# Usage:
#   ./scripts/ecr_push.sh                      # build + push
#   ./scripts/ecr_push.sh --build-only          # build, skip push
#   AWS_REGION=eu-west-1 ./scripts/ecr_push.sh  # override region
#
# Required env vars (or set in ~/.bashrc):
#   AWS_REGION        default: us-east-1
#   AWS_ACCOUNT_ID    auto-detected via STS if not set
#   ECR_REPO_NAME     default: fortress/pipeline1

set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPO_NAME="${ECR_REPO_NAME:-fortress/pipeline1}"
BUILD_ONLY="${1:-}"

# ── Resolve AWS account ID ──────────────────────────────────────────
if [ -z "${AWS_ACCOUNT_ID:-}" ]; then
  echo "[ecr_push] Detecting AWS account ID..."
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_REPO_URI="${ECR_REGISTRY}/${ECR_REPO_NAME}"

# ── Tags: git SHA (immutable) + latest ─────────────────────────────
GIT_SHA=$(git rev-parse --short HEAD)
IMAGE_TAG="${GIT_SHA}"

echo "[ecr_push] Registry : ${ECR_REGISTRY}"
echo "[ecr_push] Repo     : ${ECR_REPO_NAME}"
echo "[ecr_push] Tag      : ${IMAGE_TAG}"

# ── Build ──────────────────────────────────────────────────────────
echo "[ecr_push] Building image..."
docker build \
  -f Dockerfile.pipeline1 \
  -t "${ECR_REPO_URI}:${IMAGE_TAG}" \
  -t "${ECR_REPO_URI}:latest" \
  --label "git.sha=${GIT_SHA}" \
  --label "built.at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  .

if [ "${BUILD_ONLY}" = "--build-only" ]; then
  echo "[ecr_push] --build-only set, skipping push."
  exit 0
fi

# ── Authenticate to ECR ────────────────────────────────────────────
echo "[ecr_push] Authenticating to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

# ── Create repo if it doesn't exist ───────────────────────────────
aws ecr describe-repositories \
  --repository-names "${ECR_REPO_NAME}" \
  --region "${AWS_REGION}" \
  --output text > /dev/null 2>&1 || \
aws ecr create-repository \
  --repository-name "${ECR_REPO_NAME}" \
  --region "${AWS_REGION}" \
  --image-scanning-configuration scanOnPush=true \
  --output text > /dev/null

# ── Push ──────────────────────────────────────────────────────────
echo "[ecr_push] Pushing ${IMAGE_TAG}..."
docker push "${ECR_REPO_URI}:${IMAGE_TAG}"
docker push "${ECR_REPO_URI}:latest"

echo ""
echo "[ecr_push] Done."
echo "  SHA tag : ${ECR_REPO_URI}:${IMAGE_TAG}"
echo "  Latest  : ${ECR_REPO_URI}:latest"
echo ""
echo "Deploy with:"
echo "  IMAGE_TAG=${IMAGE_TAG} ./scripts/ecs_deploy.sh"
