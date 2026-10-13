#!/usr/bin/env bash
# deploy.sh — Pull latest pipeline1 image on EC2 and restart services.
#
# Usage (from your local machine):
#   ./scripts/deploy.sh                         # deploy :latest
#   IMAGE_TAG=abc1234 ./scripts/deploy.sh       # deploy specific SHA
#
# Required env vars:
#   EC2_HOST          EC2 public IP or DNS (e.g. ec2-1-2-3-4.compute.amazonaws.com)
#   EC2_USER          default: ubuntu
#   EC2_KEY           path to SSH private key, default: ~/.ssh/fortress.pem
#   AWS_REGION        default: us-east-1
#   AWS_ACCOUNT_ID    auto-detected if not set
#   ECR_REPO_NAME     default: fortress/pipeline1
#   IMAGE_TAG         default: latest

set -euo pipefail

EC2_HOST="${EC2_HOST:?EC2_HOST must be set (e.g. export EC2_HOST=1.2.3.4)}"
EC2_USER="${EC2_USER:-ubuntu}"
EC2_KEY="${EC2_KEY:-$HOME/.ssh/fortress.pem}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPO_NAME="${ECR_REPO_NAME:-fortress/pipeline1}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

if [ -z "${AWS_ACCOUNT_ID:-}" ]; then
  AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
fi

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
ECR_REPO_URI="${ECR_REGISTRY}/${ECR_REPO_NAME}"

echo "[deploy] Target  : ${EC2_USER}@${EC2_HOST}"
echo "[deploy] Image   : ${ECR_REPO_URI}:${IMAGE_TAG}"

# ── 1. Copy compose file and scripts to EC2 ────────────────────────
echo "[deploy] Syncing docker-compose.pipeline1.prod.yml..."
scp -i "${EC2_KEY}" -o StrictHostKeyChecking=no \
  docker-compose.pipeline1.prod.yml \
  "${EC2_USER}@${EC2_HOST}:~/docker-compose.pipeline1.yml"

echo "[deploy] Syncing scripts/backup_postgres.sh..."
ssh -i "${EC2_KEY}" -o StrictHostKeyChecking=no "${EC2_USER}@${EC2_HOST}" \
  "mkdir -p ~/scripts"
scp -i "${EC2_KEY}" -o StrictHostKeyChecking=no \
  scripts/backup_postgres.sh \
  "${EC2_USER}@${EC2_HOST}:~/scripts/backup_postgres.sh"
ssh -i "${EC2_KEY}" -o StrictHostKeyChecking=no "${EC2_USER}@${EC2_HOST}" \
  "chmod +x ~/scripts/backup_postgres.sh"

# ── 2. Remote commands on EC2 ──────────────────────────────────────
ssh -i "${EC2_KEY}" -o StrictHostKeyChecking=no "${EC2_USER}@${EC2_HOST}" \
  AWS_REGION="${AWS_REGION}" \
  ECR_REGISTRY="${ECR_REGISTRY}" \
  ECR_REPO_URI="${ECR_REPO_URI}" \
  IMAGE_TAG="${IMAGE_TAG}" \
  'bash -s' << 'REMOTE'

set -euo pipefail

echo "[remote] Logging into ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

echo "[remote] Pulling ${ECR_REPO_URI}:${IMAGE_TAG}..."
docker pull "${ECR_REPO_URI}:${IMAGE_TAG}"

# Tag pulled image as 'pipeline1' so compose can reference it by name
docker tag "${ECR_REPO_URI}:${IMAGE_TAG}" fortress/pipeline1:current

echo "[remote] Running DB migrations..."
docker run --rm \
  --env-file /opt/fortress/.env \
  fortress/pipeline1:current \
  alembic upgrade head

echo "[remote] Restarting services (zero-downtime: compose recreates only changed)..."
docker compose -f ~/docker-compose.pipeline1.yml up -d --pull never

echo "[remote] Pruning old images (keeping last 3)..."
docker image prune -f --filter "until=72h" || true

echo "[remote] Done. Running containers:"
docker compose -f ~/docker-compose.pipeline1.yml ps

REMOTE

echo ""
echo "[deploy] Deployment complete."
