#!/usr/bin/env bash
# deploy.sh — build dashboard and push to bank S3 + CloudFront
# Usage: AWS_PROFILE=fortress-prod ./deploy.sh testbank production
set -euo pipefail
BANK_ID="${1:?Usage: ./deploy.sh <bank-id> [environment]}"
ENV="${2:-production}"
REGION="${AWS_REGION:-us-east-1}"

POOL_ID=$(aws ssm get-parameter --region $REGION \
  --name /fortress/${BANK_ID}/COGNITO_USER_POOL_ID \
  --query Parameter.Value --output text)
CLIENT_ID=$(aws ssm get-parameter --region $REGION \
  --name /fortress/${BANK_ID}/COGNITO_CLIENT_ID \
  --query Parameter.Value --output text)
# P2_API_URL is written by pipeline2-dashboard.yml; it reflects TrialMode
# (CloudFront default domain in trial, fortressapi.<BankDomain> in prod).
API_URL=$(aws ssm get-parameter --region $REGION \
  --name /fortress/${BANK_ID}/P2_API_URL \
  --query Parameter.Value --output text)
S3=$(aws cloudformation describe-stacks --region $REGION \
  --stack-name fortress-p2-dashboard-${BANK_ID} \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output text)
CF_ID=$(aws cloudformation describe-stacks --region $REGION \
  --stack-name fortress-p2-dashboard-${BANK_ID} \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" \
  --output text 2>/dev/null || echo "")

cat > .env.production << EOF
VITE_API_URL=${API_URL}
VITE_COGNITO_USER_POOL_ID=${POOL_ID}
VITE_COGNITO_CLIENT_ID=${CLIENT_ID}
VITE_COGNITO_DOMAIN=fortress-${BANK_ID}.auth.${REGION}.amazoncognito.com
VITE_BANK_ID=${BANK_ID}
VITE_ENVIRONMENT=${ENV}
EOF

npm ci --silent && npm run build

aws s3 sync dist/ s3://${S3}/ --delete \
  --cache-control "public,max-age=31536000" --exclude "index.html"
aws s3 cp dist/index.html s3://${S3}/index.html \
  --cache-control "no-cache,no-store,must-revalidate"

[ -n "$CF_ID" ] && aws cloudfront create-invalidation \
  --distribution-id "$CF_ID" --paths "/*" --query "Invalidation.Id" --output text

rm -f .env.production
echo "✓ Dashboard deployed for ${BANK_ID}"
