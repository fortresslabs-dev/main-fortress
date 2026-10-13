Pipeline 2 — Step-by-Step Deployment Guide
Bank: testbank | Same AWS account as P1 | Updated: 2026-05-25

---

## TL;DR — one-command deploy (recommended)

The 12 manual steps below are now bundled in `infra/scripts/deploy-p2.sh`,
which also runs the post-deploy work the dashboard needs (first sync, Cognito
users, SPA build + upload). The trial path skips ACM + DNS + the Security
stack so there is nothing to wait on.

**Trial deploy (no real domain, dashboard served via *.cloudfront.net):**

```bash
# 1. One-time: fill in JIRA + GitHub credentials
cp infra/setup/bank.env.template infra/setup/bank.env.testbank
# edit infra/setup/bank.env.testbank — replace REPLACE_ME values

# 2. Deploy
export FORTRESS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
TRIAL_MODE=true ./infra/scripts/deploy-p2.sh testbank production
```

What the script does, in order:
1. VPC (Step 1)
2. Data stack — Aurora, Redis, secret shells (Step 2)
3. `scripts/load-bank-credentials.sh` — JIRA JSON shape, GitHub, JWT, SSM params (Step 3)
4. *Pauses* — operator runs `alembic upgrade head` against the Aurora endpoint (Step 4)
5. Image build + push to ECR (Step 6)
6. Compute stack — ECS + ALB + (CloudFront in trial) (Step 7)
7. Dashboard stack — S3 + CloudFront + Cognito + SSM `P2_API_URL` (Step 10a)
8. Security stack (skipped in trial; set `SKIP_SECURITY=false` to include)
9. Wait for `/health` to come up
10. Trigger first P1 → P2 sync via `/internal/sync/trigger` (Step 9)
11. Create 3 Cognito users — CCO/CRO/CIO (Step 10b)
12. Build + upload dashboard SPA via `dashboard/deploy.sh` (Step 10c)

**Final test — open the dashboard:**
The summary prints a `https://<CloudFront>.cloudfront.net` URL. Log in as
`cco@trial.invalid` / `Fortress2026!`. Cognito forces a password change on
first login.

**Knobs:**
- `TRIAL_MODE=true` — placeholder domain, skip ACM/DNS/Security
- `SKIP_POSTDEPLOY=true` — stop after Security stack (infra only)
- `SKIP_SECURITY=true` — skip Security stack only (auto when `TRIAL_MODE=true`)
- `PIPELINE1_API_KEY=sk_…` — provide P1 API key non-interactively
- `IMAGE_TAG=<tag>` — Docker image tag (default `latest`)
- `TEMP_PASSWORD=<pw>` — Cognito temp password (default `Fortress2026!`)
- `COGNITO_CCO_EMAIL`, `COGNITO_CRO_EMAIL`, `COGNITO_CIO_EMAIL` — user emails
  (default `cco@${BANK_DOMAIN}` etc; can be set in the bank env file)

**JIRA webhook auth (post-deploy, one-time):**
`webhooks.py` defaults to Bearer-token auth (JIRA Cloud Automation rule):
```bash
aws secretsmanager get-secret-value \
  --secret-id fortress/testbank/jira-webhook-secret \
  --query SecretString --output text
```
In your JIRA Cloud Automation rule (`Send web request`), add header
`Authorization: Bearer <value above>` pointing at
`${API_ENDPOINT}/webhooks/jira`. For JIRA Server, set
`JIRA_AUTH_MODE=hmac` on the ECS task instead.

---

## Manual step-by-step (for debugging / partial re-runs)

Pre-flight — Confirm P1 is healthy
bash# Confirm P1 API is up
curl -sf https://api.fortresslabs.dev/health | python3 -m json.tool

# Confirm rules exist in P1 Aurora
aws logs filter-log-events \
  --log-group-name /fortress/pipeline1 \
  --filter-pattern "sync_version" \
  --start-time $(date -d '24 hours ago' +%s000) \
  --query "events[-1].message" --output text

# Set these in your shell — keep them for the whole session
export BANK_ID=test_bank
export AWS_REGION=us-east-1
export AWS_PROFILE=default              # simulation: use whatever profile gives 520385696621
export ENVIRONMENT=production
export BANK_DOMAIN=testbank.dev          # SIMULATION ONLY — not a real domain. Steps 7/10 will hit ALB/CloudFront DNS directly.
                                         # For a real customer: own a domain, set BANK_DOMAIN=<theirs>, add CNAMEs in steps 7+10a.
export BANK_ALERT_EMAIL=jamierhmn@gmail.com
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account: $ACCOUNT_ID"

STEP 1 — Deploy P2 VPC
Separate VPC from P1 (P1 = 10.1.0.0/16, P2 = 10.2.0.0/16).
bashaws cloudformation deploy \
  --stack-name fortress-p2-vpc-test-bank \
  --template-file infra/cloudformation/pipeline2-vpc.yml \
  --parameter-overrides \
    BankId=test_bank \
    Environment=production \
  --capabilities CAPABILITY_NAMED_IAM

# Get NAT EIP — needed for Step 5
export NAT_EIP=$(aws cloudformation describe-stacks \
  --stack-name fortress-p2-vpc-test-bank \
  --query "Stacks[0].Outputs[?OutputKey=='NatGatewayEip'].OutputValue" \
  --output text)

echo "NAT EIP: $NAT_EIP"   # SAVE THIS
Expected: CREATE_COMPLETE

STEP 2 — Deploy P2 Data stack
Creates Aurora Serverless v2, Redis, Secrets Manager shells, KMS keys.
bashaws cloudformation deploy \
  --stack-name fortress-p2-data-test-bank \
  --template-file infra/cloudformation/pipeline2-data.yml \
  --parameter-overrides \
    BankId=test_bank \
    Environment=production \
    P1ApiUrl=https://api.fortresslabs.dev \
  --capabilities CAPABILITY_NAMED_IAM
Takes ~10 minutes for Aurora. Check status:
bashaws cloudformation describe-stacks \
  --stack-name fortress-p2-data-test-bank \
  --query "Stacks[0].StackStatus" --output text
# Expected: CREATE_COMPLETE

# Confirm auto-created secrets
aws secretsmanager list-secrets \
  --query "SecretList[?starts_with(Name,'fortress/test_bank')].Name" \
  --output text
# Expected:
#   fortress/test_bank/db-password
#   fortress/test_bank/redis-auth-token
#   fortress/test_bank/jira-webhook-secret

STEP 3 — Store credentials in Secrets Manager and SSM
Run while Step 2 is deploying.
3a — JIRA credentials
Get API token at: https://id.atlassian.com/manage-profile/security/api-tokens
bashaws secretsmanager create-secret \
  --name "fortress/test_bank/jira-api-token" \
  --secret-string '{
    "url":      "https://YOUR_ORG.atlassian.net",
    "username": "YOUR_EMAIL@domain.com",
    "token":    "ATATT3xFfGF0YOUR_JIRA_TOKEN"
  }' 2>/dev/null || \
aws secretsmanager put-secret-value \
  --secret-id "fortress/test_bank/jira-api-token" \
  --secret-string '{
    "url":      "https://YOUR_ORG.atlassian.net",
    "username": "YOUR_EMAIL@domain.com",
    "token":    "ATATT3xFfGF0YOUR_JIRA_TOKEN"
  }'

# Verify
aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/jira-api-token \
  --query "SecretString" --output text
3b — GitHub token (Agent 6 opens PRs)
Create at: github.com → Settings → Developer settings → Fine-grained tokens
Permissions needed on bank repo only:

Contents: read + write
Pull requests: read + write

bashaws secretsmanager create-secret \
  --name "fortress/test_bank/github-token" \
  --secret-string "github_pat_YOUR_TOKEN_HERE"

# Verify
aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/github-token \
  --query "SecretString" --output text | cut -c1-20
# Expected: github_pat_...
3c — JWT secret
bashJWT_SECRET=$(openssl rand -hex 32)

aws secretsmanager create-secret \
  --name "fortress/test_bank/jwt-secret" \
  --secret-string "$JWT_SECRET" 2>/dev/null || \
aws secretsmanager put-secret-value \
  --secret-id "fortress/test_bank/jwt-secret" \
  --secret-string "$JWT_SECRET"

echo "JWT secret stored: ${JWT_SECRET:0:8}..."
3d — SSM parameters
bashaws ssm put-parameter --name /fortress/test_bank/JIRA_PROJECT_KEY \
  --value "COMP" --type String --overwrite

aws ssm put-parameter --name /fortress/test_bank/JIRA_URL \
  --value "https://YOUR_ORG.atlassian.net" --type String --overwrite

aws ssm put-parameter --name /fortress/test_bank/JIRA_USERNAME \
  --value "YOUR_EMAIL@domain.com" --type String --overwrite

aws ssm put-parameter --name /fortress/test_bank/GITHUB_ORG \
  --value "YOUR_GITHUB_ORG" --type String --overwrite

aws ssm put-parameter --name /fortress/test_bank/GITHUB_REPO \
  --value "YOUR_BANK_REPO_NAME" --type String --overwrite

aws ssm put-parameter --name /fortress/test_bank/PIPELINE1_API_URL \
  --value "https://api.fortresslabs.dev" --type String --overwrite

aws ssm put-parameter --name /fortress/test_bank/CLIENT_NAME \
  --value "test_bank" --type String --overwrite

aws ssm put-parameter --name /fortress/test_bank/PIPELINE_MODE \
  --value "pipeline2" --type String --overwrite

# Verify all 8 parameters
aws ssm get-parameters-by-path \
  --path /fortress/test_bank \
  --query "Parameters[*].{Name:Name,Value:Value}" \
  --output table

STEP 4 — Run Alembic migrations
Aurora is inside VPC — run from AWS CloudShell (same account, no VPN needed).
Open CloudShell in AWS Console, then:
bashpip install asyncpg --quiet

DB_PW=$(aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/db-password \
  --query "SecretString" --output text | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")

AURORA_HOST=$(aws ssm get-parameter \
  --name /fortress/test_bank/AURORA_HOST \
  --query Parameter.Value --output text)

echo "Aurora: $AURORA_HOST"

# Clone and migrate
git clone https://github.com/YOUR_ORG/fortress_server.git
cd fortress_server/fortress_server

DATABASE_URL="postgresql+asyncpg://fortress_test_bank:${DB_PW}@${AURORA_HOST}:5432/fortress_test_bank?ssl=require" \
python3 -m alembic upgrade head

# Confirm
DATABASE_URL="postgresql+asyncpg://fortress_test_bank:${DB_PW}@${AURORA_HOST}:5432/fortress_test_bank?ssl=require" \
python3 -m alembic current
# Expected: 020_change_records_tz (head)

STEP 5 — Register test_bank in P1 WAF
Back on your EC2 (not CloudShell). NAT_EIP must be set from Step 1.
bashecho "Registering: $NAT_EIP"

chmod +x scripts/register-bank.sh
./scripts/register-bank.sh \
  --bank-id      test_bank \
  --bank-name    "Test Bank" \
  --bank-ip      ${NAT_EIP} \
  --bank-profile fortress-prod \
  --tier         standard

# Verify P1 API key was stored
aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/pipeline1-api-key \
  --query "SecretString" --output text | wc -c
# Expected: 64
Expected output:
Step 1: ✓ Bank registered in client_credentials
Step 2: ✓ Key stored in fortress/test_bank/pipeline1-api-key
Step 4: ✓ NAT IP added to WAF allowlist
Step 6: ✓ License verification PASSED

STEP 6 — Build and push P2 Docker image
bashECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/fortress/pipeline2-test_bank"

# Create ECR repo
aws ecr create-repository \
  --repository-name fortress/pipeline2-test_bank \
  --image-scanning-configuration scanOnPush=true 2>/dev/null || true

# Login
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin \
  "${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Build — strips P1 scraper code
docker build -f Dockerfile.pipeline2 \
  --platform linux/amd64 \
  -t fortress/pipeline2:latest \
  -t ${ECR_URI}:latest .

docker push ${ECR_URI}:latest
echo "Pushed: ${ECR_URI}:latest"

STEP 7 — Deploy P2 Compute stack
Creates ECS Fargate, ALB, WAFv2. JIRA webhooks reach /webhooks/jira directly and run inline via FastAPI BackgroundTasks (no queue).
bashaws cloudformation deploy \
  --stack-name fortress-p2-compute-test-bank \
  --template-file infra/cloudformation/pipeline2-compute.yml \
  --parameter-overrides \
    BankId=test_bank \
    Environment=production \
    ImageUri=${ECR_URI}:latest \
    BankDomain=${BANK_DOMAIN} \
    AlertEmail=${BANK_ALERT_EMAIL} \
  --capabilities CAPABILITY_NAMED_IAM

# Get ALB DNS
export ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name fortress-p2-compute-test-bank \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDns'].OutputValue" \
  --output text)

echo "ALB DNS: $ALB_DNS"
echo "ACTION: Create CNAME → fortressapi.${BANK_DOMAIN} → ${ALB_DNS}"

# Confirm 2/2 tasks running
aws ecs describe-services \
  --cluster fortress-pipeline2-test_bank \
  --services fortress-pipeline2-test_bank \
  --query "services[0].{Running:runningCount,Desired:desiredCount}" \
  --output table

ACTION REQUIRED: Add DNS CNAME before Step 9.
fortressapi.testbank.dev → <ALB DNS>


STEP 8 — Deploy Security stack
bashaws cloudformation deploy \
  --stack-name fortress-p2-security-test-bank \
  --template-file infra/cloudformation/pipeline2-security.yml \
  --parameter-overrides \
    BankId=test_bank \
    Environment=production \
  --capabilities CAPABILITY_NAMED_IAM
Expected: CREATE_COMPLETE

STEP 9 — Trigger first P1 → P2 sync
bashexport FORTRESS_INTERNAL_SECRET="your-internal-secret-here"

# Trigger sync (pulls all 847 rules from P1 into P2 Aurora)
curl -X POST https://fortressapi.${BANK_DOMAIN}/internal/sync/trigger \
  -H "X-Internal-Secret: ${FORTRESS_INTERNAL_SECRET}"

# Watch logs
aws logs tail /fortress/pipeline2/test_bank --follow

# Verify rules arrived
curl https://fortressapi.${BANK_DOMAIN}/health/sync | python3 -m json.tool
# Expected: {"rules_count": 847, "status": "ok"}

STEP 10 — Deploy Dashboard
10a — Cognito + CloudFront + S3
bashaws cloudformation deploy \
  --stack-name fortress-p2-dashboard-test-bank \
  --template-file infra/cloudformation/pipeline2-dashboard.yml \
  --parameter-overrides \
    BankId=test_bank \
    BankDomain=${BANK_DOMAIN} \
    P2ApiUrl=https://fortressapi.${BANK_DOMAIN} \
  --capabilities CAPABILITY_NAMED_IAM

# Get CloudFront DNS
export CF_DNS=$(aws cloudformation describe-stacks \
  --stack-name fortress-p2-dashboard-test-bank \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" \
  --output text)

echo "ACTION: Create CNAME → dashboard.${BANK_DOMAIN} → ${CF_DNS}"

ACTION REQUIRED: Add DNS CNAME before Step 10c.
dashboard.testbank.dev → <CloudFront DNS>

10b — Create dashboard users
bashPOOL_ID=$(aws ssm get-parameter \
  --name /fortress/test_bank/COGNITO_USER_POOL_ID \
  --query Parameter.Value --output text)

for ENTRY in "cco@testbank.dev CCO" "cro@testbank.dev CRO" "cto@testbank.dev CIO"; do
  EMAIL=$(echo $ENTRY | cut -d' ' -f1)
  GROUP=$(echo $ENTRY | cut -d' ' -f2)
  aws cognito-idp admin-create-user \
    --user-pool-id $POOL_ID \
    --username "$EMAIL" \
    --user-attributes \
      Name=email,Value="$EMAIL" \
      Name=email_verified,Value=true \
    --temporary-password "Fortress2026!" \
    --message-action SUPPRESS
  aws cognito-idp admin-add-user-to-group \
    --user-pool-id $POOL_ID \
    --username "$EMAIL" --group-name $GROUP
  echo "✓ $EMAIL → $GROUP"
done
10c — Build and deploy React app
bashcd dashboard && npm install && cd ..
chmod +x dashboard/deploy.sh
./dashboard/deploy.sh test_bank production

# Verify S3 has content
aws s3 ls s3://fortress-dashboard-test_bank-production/ | head -5
Test login: Open https://dashboard.testbank.dev
Login: cco@testbank.dev / Fortress2026!
Cognito forces password change on first login.

STEP 11 — Configure JIRA webhook
In JIRA UI:
Project: COMP
→ Project Settings → System → WebHooks → Create

Name:    Fortress Compliance Pipeline
URL:     https://fortressapi.testbank.dev/webhooks/jira
Events:  ✓ Issue → Created   (only this one)
Secret:  <paste from command below>
bash# Get webhook secret to paste into JIRA
aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/jira-webhook-secret \
  --query "SecretString" --output text
Test the webhook:
bashWEBHOOK_SECRET=$(aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/jira-webhook-secret \
  --query "SecretString" --output text)

PAYLOAD='{"webhookEvent":"jira:issue_created","issue":{"key":"COMP-001","id":"10001","fields":{"summary":"Add CTR filing check for cash deposits over $10000","description":"Implement CTR per 31 CFR 1020.315","issuetype":{"name":"Story"},"labels":["compliance"]}}}'

SIG="sha256=$(echo -n "$PAYLOAD" | \
  openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')"

curl -X POST https://fortressapi.${BANK_DOMAIN}/webhooks/jira \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$PAYLOAD" | python3 -m json.tool

# Expected: {"workflow_id": "...", "status": "queued"}

# Watch 6-agent pipeline run
aws logs tail /fortress/pipeline2/test_bank --follow

STEP 12 — End-to-end smoke tests
bash# P2 health
curl https://fortressapi.${BANK_DOMAIN}/health | python3 -m json.tool

# Sync status
curl https://fortressapi.${BANK_DOMAIN}/health/sync | python3 -m json.tool

# Dashboard API (dev token)
curl -H "Authorization: Bearer changeme-token" \
  https://fortressapi.${BANK_DOMAIN}/api/v1/dashboard/overview \
  | python3 -m json.tool

# Rules loaded
curl -H "Authorization: Bearer changeme-token" \
  "https://fortressapi.${BANK_DOMAIN}/api/v1/dashboard/rules?page_size=5" \
  | python3 -m json.tool

# Risk scores
curl -H "Authorization: Bearer changeme-token" \
  https://fortressapi.${BANK_DOMAIN}/api/v1/dashboard/risk \
  | python3 -m json.tool

Secrets manifest — complete reference
SecretContentSet byfortress/test_bank/db-password{"username":"...","password":"..."}CFN auto (Step 2)fortress/test_bank/redis-auth-tokenrandom tokenCFN auto (Step 2)fortress/test_bank/jira-webhook-secretHMAC secretCFN auto (Step 2)fortress/test_bank/jwt-secret64-char hexStep 3cfortress/test_bank/jira-api-token{"url","username","token"}Step 3afortress/test_bank/github-tokengithub_pat_xxxStep 3bfortress/test_bank/pipeline1-api-key64-char hexStep 5 (register-bank.sh)

SSM parameters — complete reference
ParameterValueSet by/fortress/test_bank/PIPELINE_MODEpipeline2Step 3d/fortress/test_bank/CLIENT_NAMEtest_bankStep 3d/fortress/test_bank/AURORA_HOSTAurora endpointCFN auto (Step 2)/fortress/test_bank/PIPELINE1_API_URLhttps://api.fortresslabs.devStep 3d/fortress/test_bank/JIRA_PROJECT_KEYCOMPStep 3d/fortress/test_bank/JIRA_URLAtlassian URLStep 3d/fortress/test_bank/JIRA_USERNAMELogin emailStep 3d/fortress/test_bank/GITHUB_ORGGitHub orgStep 3d/fortress/test_bank/GITHUB_REPOBank repo nameStep 3d/fortress/test_bank/COGNITO_USER_POOL_IDPool IDCFN auto (Step 10a)/fortress/test_bank/COGNITO_CLIENT_IDClient IDCFN auto (Step 10a)

DNS records required
CNAMEPoints toWhenfortressapi.testbank.devALB DNS from Step 7 outputAfter Step 7dashboard.testbank.devCloudFront DNS from Step 10a outputAfter Step 10a

Deployment checklist
□ Pre-flight:  P1 /health returns ok
□ Step 1:      fortress-p2-vpc-test-bank       CREATE_COMPLETE
□ Step 1:      NAT_EIP exported
□ Step 2:      fortress-p2-data-test-bank      CREATE_COMPLETE
□ Step 2:      3 auto-created secrets confirmed
□ Step 3a:     jira-api-token stored
□ Step 3b:     github-token stored
□ Step 3c:     jwt-secret stored
□ Step 3d:     8 SSM parameters set
□ Step 4:      Alembic at 020_change_records_tz
□ Step 5:      register-bank.sh → License PASSED
□ Step 5:      pipeline1-api-key = 64 chars
□ Step 6:      Docker image in ECR
□ Step 7:      fortress-p2-compute-test-bank   CREATE_COMPLETE
□ Step 7:      2/2 ECS tasks running
□ Step 7:      DNS CNAME: fortressapi.testbank.dev → ALB
□ Step 8:      fortress-p2-security-test-bank  CREATE_COMPLETE
□ Step 9:      /health/sync → rules_count: 847
□ Step 10a:    fortress-p2-dashboard-test-bank CREATE_COMPLETE
□ Step 10a:    DNS CNAME: dashboard.testbank.dev → CloudFront
□ Step 10b:    3 Cognito users created (CCO/CRO/CTO)
□ Step 10c:    React app deployed, login works
□ Step 11:     JIRA webhook configured + test returns 200
□ Step 11:     Logs show pipeline agents running
□ Step 12:     All smoke tests green