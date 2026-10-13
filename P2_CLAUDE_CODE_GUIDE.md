# Pipeline 2 + Dashboard — Claude Code Step-by-Step Guide
# Bank: test_bank | Same AWS account as P1 (pilot)
# Use this file on your server with Claude Code (`claude` CLI)

---

## HOW TO USE THIS GUIDE

Open your terminal on the EC2 server where the repo is cloned.
Run `claude` to start Claude Code. Paste each prompt block when ready.
Claude Code will read your files, run commands, and fix errors automatically.

```bash
cd /path/to/fortress_server
claude   # starts Claude Code session
```

---

## PRE-FLIGHT — Confirm P1 is healthy (run these yourself first)

```bash
# P1 API health
curl -sf https://api.fortresslabs.dev/health | python3 -m json.tool

# Rules in Aurora (must be > 0)
aws ecs execute-command \
  --cluster fortress-pipeline1-production \
  --task $(aws ecs list-tasks \
    --cluster fortress-pipeline1-production \
    --query "taskArns[0]" --output text) \
  --container pipeline1 --interactive \
  --command "python3 -c \"
import asyncio, asyncpg, os
async def c():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    n = await conn.fetchval('SELECT COUNT(*) FROM compliance_rules WHERE is_active=TRUE')
    print(f'Active rules: {n}')
    await conn.close()
asyncio.run(c())
\""

# Set env vars (keep in your shell session)
export BANK_ID=test_bank
export BANK_DOMAIN=testbank.dev
export AWS_REGION=us-east-1
export AWS_PROFILE=fortress-prod
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export FORTRESS_INTERNAL_SECRET="your-secret-here"
export IMAGE_TAG=v5.3
```

---

## STEP 1 — Store external credentials

**Paste this into Claude Code:**
```
Read src/integrations/jira_client.py and src/services/github_service.py to understand
what credentials they need. Then help me store the following in AWS Secrets Manager:

1. JIRA credentials as fortress/test_bank/jira-api-token:
   {"url": "https://YOUR_ORG.atlassian.net", "username": "YOUR_EMAIL", "token": "YOUR_JIRA_TOKEN"}

2. GitHub PAT as fortress/test_bank/github-token:
   "github_pat_YOUR_TOKEN_HERE"

3. JWT secret as fortress/test_bank/jwt-secret:
   (generate with: openssl rand -hex 32)

Write and run the aws secretsmanager commands. Then verify each secret was stored.
Also store these SSM parameters:
  /fortress/test_bank/JIRA_PROJECT_KEY = COMP
  /fortress/test_bank/GITHUB_ORG = YOUR_GITHUB_ORG
  /fortress/test_bank/GITHUB_REPO = YOUR_REPO_NAME
```

**Verify manually:**
```bash
aws secretsmanager list-secrets \
  --query "SecretList[?starts_with(Name,'fortress/test_bank')].Name" \
  --output text
```

---

## STEP 2 — Deploy P2 VPC stack

**Paste into Claude Code:**
```
Deploy the P2 VPC CloudFormation stack for test_bank. Read
infra/cloudformation/pipeline2-vpc.yml first to understand the parameters.

Run:
aws cloudformation deploy \
  --stack-name fortress-p2-vpc-test-bank \
  --template-file infra/cloudformation/pipeline2-vpc.yml \
  --parameter-overrides BankId=test_bank Environment=production \
  --capabilities CAPABILITY_NAMED_IAM

Wait for CREATE_COMPLETE. Then get and show me the NAT Gateway EIP output.
Save it to a variable I can use in the next steps.
```

**Expected output:** `Stack status: CREATE_COMPLETE`, NAT EIP printed.

---

## STEP 3 — Deploy P2 Data stack

**Paste into Claude Code:**
```
Deploy the P2 Data CloudFormation stack. Read infra/cloudformation/pipeline2-data.yml.

Run:
aws cloudformation deploy \
  --stack-name fortress-p2-data-test-bank \
  --template-file infra/cloudformation/pipeline2-data.yml \
  --parameter-overrides \
    BankId=test_bank \
    Environment=production \
    P1ApiUrl=https://api.fortresslabs.dev \
  --capabilities CAPABILITY_NAMED_IAM

This takes ~10 minutes for Aurora. Monitor with:
aws cloudformation describe-stacks --stack-name fortress-p2-data-test-bank \
  --query "Stacks[0].StackStatus"

Once COMPLETE, show me all stack outputs.
Also confirm these secrets now exist in Secrets Manager:
  fortress/test_bank/db-password
  fortress/test_bank/redis-auth-token
  fortress/test_bank/jira-webhook-secret
```

---

## STEP 4 — Store JIRA/GitHub credentials (now that Secrets exist)

**Paste into Claude Code:**
```
The data stack created secret shells. Now fill them with real values.

Run these (replace placeholders with real values):
aws secretsmanager put-secret-value \
  --secret-id fortress/test_bank/jira-api-token \
  --secret-string '{"url":"https://YOUR_ORG.atlassian.net","username":"YOUR_EMAIL","token":"YOUR_TOKEN"}'

aws secretsmanager create-secret \
  --name fortress/test_bank/github-token \
  --secret-string "github_pat_YOUR_TOKEN"

JWT=$(openssl rand -hex 32)
aws secretsmanager put-secret-value \
  --secret-id fortress/test_bank/jwt-secret \
  --secret-string "$JWT"

Verify each with:
aws secretsmanager get-secret-value --secret-id fortress/test_bank/jira-api-token \
  --query "SecretString" --output text
```

---

## STEP 5 — Run P2 Alembic migrations

**Paste into Claude Code:**
```
Get the Aurora endpoint from the P2 data stack output and the DB password from
Secrets Manager. Then run Alembic migrations against the P2 Aurora database.

First get the credentials:
DB_HOST=$(aws cloudformation describe-stacks --stack-name fortress-p2-data-test-bank \
  --query "Stacks[0].Outputs[?OutputKey=='AuroraEndpoint'].OutputValue" --output text)

DB_PW=$(aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/db-password \
  --query "SecretString" --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")

Then run migrations:
DATABASE_URL="postgresql+asyncpg://fortress_test_bank:${DB_PW}@${DB_HOST}:5432/fortress_test_bank?ssl=require" \
python3 -m alembic upgrade head

Show me the current migration version. Expected: 020_change_records_tz
```

**If connection refused:** Aurora is inside VPC. Run from CloudShell or an EC2 in the same VPC.

---

## STEP 6 — Register test_bank in P1 WAF

**Paste into Claude Code:**
```
Read scripts/register-bank.sh to understand what it does. Then run it to register
test_bank in P1. I need the NAT EIP from Step 2.

NAT_EIP="<paste EIP from Step 2>"

./scripts/register-bank.sh \
  --bank-id test_bank \
  --bank-name "Test Bank" \
  --bank-ip ${NAT_EIP} \
  --bank-profile fortress-prod \
  --tier standard

Verify the output shows "License verification PASSED".
Also verify the pipeline1-api-key secret now has a 64-char hex value:
aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/pipeline1-api-key \
  --query "SecretString" --output text | wc -c
Expected: 64
```

---

## STEP 7 — Build and push P2 Docker image

**Paste into Claude Code:**
```
Read Dockerfile.pipeline2 to understand what it builds. The multi-stage build
strips P1 code (connectors, scrapers, main_pipeline1.py).

Build and push the P2 image:

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/fortress/pipeline2-test_bank"

# Create ECR repo
aws ecr create-repository --repository-name fortress/pipeline2-test_bank \
  --image-scanning-configuration scanOnPush=true 2>/dev/null || true

# Login
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  "${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com"

# Build
docker build -f Dockerfile.pipeline2 --platform linux/amd64 \
  -t fortress/pipeline2:${IMAGE_TAG} .

# Push
docker tag fortress/pipeline2:${IMAGE_TAG} ${ECR_URI}:${IMAGE_TAG}
docker push ${ECR_URI}:${IMAGE_TAG}

Show me the image digest to confirm push succeeded.
```

---

## STEP 8 — Deploy P2 Compute stack

**Paste into Claude Code:**
```
Read infra/cloudformation/pipeline2-compute.yml. Note it has a Secrets block
that injects JIRA, GitHub, and JWT secrets into the ECS task. JIRA webhooks
hit /webhooks/jira directly (HMAC-verified) and run inline via FastAPI
BackgroundTasks — no SQS queue or EventBridge Pipe in the path.

Deploy:
ECR_URI="${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/fortress/pipeline2-test_bank"

aws cloudformation deploy \
  --stack-name fortress-p2-compute-test-bank \
  --template-file infra/cloudformation/pipeline2-compute.yml \
  --parameter-overrides \
    BankId=test_bank \
    Environment=production \
    ImageUri=${ECR_URI}:${IMAGE_TAG} \
    BankDomain=testbank.dev \
    AlertEmail=ops@testbank.dev \
  --capabilities CAPABILITY_NAMED_IAM

After CREATE_COMPLETE:
1. Show me the ALB DNS output (I need to create a CNAME)
2. Verify ECS service has 2/2 tasks running:
   aws ecs describe-services --cluster fortress-pipeline2-test_bank \
     --services fortress-pipeline2-test_bank \
     --query "services[0].{Running:runningCount,Desired:desiredCount}"
3. Show last 20 lines of ECS logs:
   aws logs tail /fortress/pipeline2/test_bank --since 5m
```

**ACTION REQUIRED after this step:** Create DNS CNAME:
`fortressapi.testbank.dev → <ALB DNS from output>`

---

## STEP 9 — Deploy P2 Security stack

**Paste into Claude Code:**
```
Deploy the P2 security stack:

aws cloudformation deploy \
  --stack-name fortress-p2-security-test-bank \
  --template-file infra/cloudformation/pipeline2-security.yml \
  --parameter-overrides BankId=test_bank Environment=production \
  --capabilities CAPABILITY_NAMED_IAM

Confirm CREATE_COMPLETE and show me the stack status.
```

---

## STEP 10 — Trigger first P1 → P2 sync

**Paste into Claude Code:**
```
P2 is running but has no compliance rules yet. Trigger the first sync from P1.

curl -X POST https://fortressapi.testbank.dev/internal/sync/trigger \
  -H "X-Internal-Secret: ${FORTRESS_INTERNAL_SECRET}"

Then monitor the sync logs for 2 minutes:
aws logs tail /fortress/pipeline2/test_bank --follow --since 1m

Look for: "sync_cycle_complete" or "update_service_sync_done"

After sync, verify rules arrived:
curl https://fortressapi.testbank.dev/health/sync | python3 -m json.tool
Expected: {"rules_count": N, "status": "ok"} where N > 0

Also run the P2 health check:
curl https://fortressapi.testbank.dev/health | python3 -m json.tool
```

---

## STEP 11 — Deploy Dashboard stack (Cognito + CloudFront)

**Paste into Claude Code:**
```
Read infra/cloudformation/pipeline2-dashboard.yml. Deploy it to create:
- S3 bucket for the React app
- CloudFront distribution
- Cognito User Pool with CCO/CRO/CIO groups

aws cloudformation deploy \
  --stack-name fortress-p2-dashboard-test-bank \
  --template-file infra/cloudformation/pipeline2-dashboard.yml \
  --parameter-overrides \
    BankId=test_bank \
    BankDomain=testbank.dev \
    P2ApiUrl=https://fortressapi.testbank.dev \
  --capabilities CAPABILITY_NAMED_IAM

After CREATE_COMPLETE, show me:
1. CloudFront DNS (for CNAME: dashboard.testbank.dev)
2. Cognito User Pool ID
3. Cognito Client ID

Then create 3 users:
POOL_ID=$(aws ssm get-parameter \
  --name /fortress/test_bank/COGNITO_USER_POOL_ID \
  --query Parameter.Value --output text)

for ENTRY in "cco@testbank.dev CCO" "cro@testbank.dev CRO" "cto@testbank.dev CIO"; do
  EMAIL=$(echo $ENTRY | cut -d' ' -f1)
  GROUP=$(echo $ENTRY | cut -d' ' -f2)
  aws cognito-idp admin-create-user --user-pool-id $POOL_ID \
    --username "$EMAIL" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --temporary-password "Fortress2026!" --message-action SUPPRESS
  aws cognito-idp admin-add-user-to-group \
    --user-pool-id $POOL_ID --username "$EMAIL" --group-name $GROUP
  echo "Created: $EMAIL → $GROUP"
done
```

**ACTION REQUIRED:** Create DNS CNAME:
`dashboard.testbank.dev → <CloudFront DNS from output>`

---

## STEP 12 — Build and deploy React dashboard

**Paste into Claude Code:**
```
Read dashboard/README.md and dashboard/deploy.sh. Then build and deploy the
React dashboard for test_bank.

cd dashboard && npm install && cd ..

AWS_PROFILE=fortress-prod \
  ./dashboard/deploy.sh test_bank production

After deploy, verify the S3 bucket has content:
aws s3 ls s3://fortress-dashboard-test_bank-production/ | head -10

Then verify CloudFront serves the app (may take 5-10 min for DNS to propagate):
curl -I https://dashboard.testbank.dev | head -5
```

---

## STEP 13 — Configure JIRA webhook

**Paste into Claude Code:**
```
Read src/api/webhooks.py to understand the JIRA webhook handler. It validates
X-Hub-Signature-256 HMAC with the jira-webhook-secret.

Show me the webhook secret:
aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/jira-webhook-secret \
  --query "SecretString" --output text

Then test the webhook endpoint works with a fake payload:
WEBHOOK_SECRET=$(aws secretsmanager get-secret-value \
  --secret-id fortress/test_bank/jira-webhook-secret \
  --query "SecretString" --output text)

PAYLOAD='{"webhookEvent":"jira:issue_created","issue":{"key":"COMP-001","id":"10001","fields":{"summary":"Add CTR filing for cash deposits over $10000","description":"Implement CTR per 31 CFR 1020.315","issuetype":{"name":"Story"},"labels":["compliance"]}}}'

SIG="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | awk '{print $2}')"

curl -X POST https://fortressapi.testbank.dev/webhooks/jira \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$PAYLOAD" | python3 -m json.tool

Expected: {"workflow_id": "...", "status": "queued"}

Then watch the pipeline logs for 30 seconds:
aws logs tail /fortress/pipeline2/test_bank --follow --since 1m
```

**In JIRA UI (manual step):**
```
Project Settings → System → WebHooks → Create:
  Name:   Fortress Compliance Pipeline
  URL:    https://fortressapi.testbank.dev/webhooks/jira
  Events: ✓ Issue → Created only
  Secret: <value from command above>
```

---

## STEP 14 — GitHub CI/CD integration

**Paste into Claude Code:**
```
Create the compliance test folder and GitHub Actions job for the bank's repo.

First create the scaffold in this repo (we'll copy it to the bank's repo):
mkdir -p tests/compliance
cat > tests/compliance/README.md << 'EOF'
# Fortress Compliance Tests
Auto-generated by Fortress Labs 6-agent pipeline.
Each file corresponds to a JIRA compliance story.
Do NOT edit manually — re-generate via JIRA story update.
EOF

Then show me the GitHub Actions job to add to the bank's .github/workflows/ci.yml:

- name: Run Fortress compliance tests
  run: |
    pytest tests/compliance/ -v \
      --tb=short \
      --junit-xml=results/compliance-results.xml

- name: Upload compliance evidence
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: compliance-evidence-${{ github.sha }}
    path: results/compliance-results.xml
    retention-days: 90

- name: Block on compliance failure
  if: failure()
  run: exit 1

Also verify Agent 6 can reach GitHub by checking the GITHUB_TOKEN is set in ECS:
aws ecs describe-task-definition \
  --task-definition fortress-pipeline2-test_bank \
  --query "taskDefinition.containerDefinitions[0].secrets[?name=='GITHUB_TOKEN']"
```

---

## STEP 15 — End-to-end validation

**Paste into Claude Code:**
```
Run the full end-to-end validation:

1. P2 health:
curl https://fortressapi.testbank.dev/health | python3 -m json.tool

2. Sync status:
curl https://fortressapi.testbank.dev/health/sync | python3 -m json.tool

3. Dashboard API (using dev token):
curl -H "Authorization: Bearer changeme-token" \
  https://fortressapi.testbank.dev/api/v1/dashboard/overview | python3 -m json.tool

4. Rules available:
curl -H "Authorization: Bearer changeme-token" \
  "https://fortressapi.testbank.dev/api/v1/dashboard/rules?page_size=5" | python3 -m json.tool

5. Risk scores:
curl -H "Authorization: Bearer changeme-token" \
  https://fortressapi.testbank.dev/api/v1/dashboard/risk | python3 -m json.tool

6. Trigger a test workflow via the generate-test API:
curl -X POST \
  -H "Authorization: Bearer changeme-token" \
  -H "Content-Type: application/json" \
  -d '{"story_title": "Add CTR filing test for $10,000 cash threshold"}' \
  https://fortressapi.testbank.dev/api/v1/rules/OCC-BSA-CTR/generate-test \
  | python3 -m json.tool

Watch the 6-agent pipeline:
aws logs tail /fortress/pipeline2/test_bank --follow --since 2m

Report any errors found in each step.
```

---

## STEP 16 — Dashboard login test

**Paste into Claude Code:**
```
The dashboard should be live at https://dashboard.testbank.dev
Help me verify the Cognito setup is correct.

1. Check the Cognito user pool has 3 users:
aws cognito-idp list-users \
  --user-pool-id $(aws ssm get-parameter \
    --name /fortress/test_bank/COGNITO_USER_POOL_ID \
    --query Parameter.Value --output text) \
  --query "Users[*].{Email:Username,Status:UserStatus}" \
  --output table

2. Check CCO is in the CCO group:
aws cognito-idp list-users-in-group \
  --user-pool-id $(aws ssm get-parameter \
    --name /fortress/test_bank/COGNITO_USER_POOL_ID \
    --query Parameter.Value --output text) \
  --group-name CCO

3. Verify the CORS setting allows dashboard.testbank.dev:
curl -I -X OPTIONS \
  -H "Origin: https://dashboard.testbank.dev" \
  -H "Access-Control-Request-Method: GET" \
  https://fortressapi.testbank.dev/api/v1/dashboard/overview | grep -i "access-control"

Report any issues.
```

**Manual login test:**
```
Open: https://dashboard.testbank.dev
Login: cco@testbank.dev / Fortress2026!
Cognito forces password change on first login.

Expected after login:
✓ Overview page loads with rule count
✓ Coverage bars show regulators
✓ Rules page shows 800+ rules
✓ Generate Test button visible (CCO role)

Login as cro@testbank.dev:
✓ Generate Test button NOT visible
✓ Sign off button visible on Changes page
```

---

## COMMON ERRORS AND FIXES

**Paste these into Claude Code when you hit an error:**

### ECS task won't start
```
My P2 ECS task is not starting. Run these diagnostics:
aws ecs describe-tasks \
  --cluster fortress-pipeline2-test_bank \
  --tasks $(aws ecs list-tasks --cluster fortress-pipeline2-test_bank \
    --desired-status STOPPED --query "taskArns[0]" --output text) \
  --query "tasks[0].{StoppedReason:stoppedReason,Containers:containers[*].{Name:name,Reason:reason,ExitCode:exitCode}}"

aws logs tail /fortress/pipeline2/test_bank --since 10m

Based on the error, help me fix it.
```

### Database connection refused
```
Alembic migration fails with connection refused. The Aurora cluster
is inside the VPC. Help me check if I need to run from CloudShell
and how to set up the DATABASE_URL correctly.
Check: src/services/database.py and src/utils/config.py
```

### JIRA webhook returns 401
```
The JIRA webhook test returns 401. Read src/api/webhooks.py to understand
the HMAC validation. Help me debug why the signature check fails.
Check that JIRA_WEBHOOK_SECRET env var is set in the ECS task and matches
what I stored in Secrets Manager.
```

### Dashboard shows no rules
```
The dashboard overview shows 0 rules. Help me debug:
1. Check update_service.py to understand the P1→P2 sync
2. Verify sync ran: curl https://fortressapi.testbank.dev/health/sync
3. Check P2 Aurora has rules: connect to DB and count compliance_rules
4. Tail logs for sync errors: aws logs tail /fortress/pipeline2/test_bank
```

### Agent 6 not opening GitHub PR
```
The 6-agent pipeline completes but no GitHub PR is opened.
Read src/services/github_service.py and check:
1. GITHUB_TOKEN env var is set in ECS task definition
2. The token has repo write permissions
3. GITHUB_ORG and GITHUB_REPO are correct in SSM
Run the ECS exec to check env vars inside the container.
```

---

## DEPLOYMENT CHECKLIST

```
□ P1 rules > 0 in Aurora
□ STEP 1:  JIRA + GitHub secrets in Secrets Manager
□ STEP 2:  VPC stack CREATE_COMPLETE
□ STEP 3:  Data stack CREATE_COMPLETE (Aurora available)
□ STEP 5:  Alembic at migration 020
□ STEP 6:  register-bank.sh → License PASSED
□ STEP 7:  P2 Docker image in ECR
□ STEP 8:  Compute stack COMPLETE, 2/2 ECS tasks running
□ STEP 8:  CNAME created: fortressapi.testbank.dev → ALB
□ STEP 9:  Security stack COMPLETE
□ STEP 10: /health/sync shows rules_count > 0
□ STEP 11: Dashboard stack COMPLETE, 3 Cognito users created
□ STEP 11: CNAME created: dashboard.testbank.dev → CloudFront
□ STEP 12: React app deployed to S3
□ STEP 13: JIRA webhook configured + test returns 200
□ STEP 14: tests/compliance/ folder in bank repo
□ STEP 15: End-to-end validation all green
□ STEP 16: CCO login works, CRO cannot see Generate Test
```
