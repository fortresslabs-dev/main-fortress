# Operations — VS Code / CLI setup, P1 deploy, bank registration, MCP, connectors

## VS Code — Claude Code extension

1. Install **Claude Code** extension from VS Code marketplace (`anthropic.claude-code`)
2. Sign in with your Anthropic account
3. Open this repo: `code /path/to/textmind_banking_backend`
4. The extension reads `CLAUDE.md` automatically — all agents and commands load

**Key VS Code tasks (`.vscode/tasks.json` — create if missing):**
```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Start dev stack",
      "type": "shell",
      "command": "docker compose up -d postgres redis",
      "group": "build"
    },
    {
      "label": "Run migrations",
      "type": "shell",
      "command": "python -m alembic upgrade head",
      "group": "build"
    },
    {
      "label": "Health check all",
      "type": "shell",
      "command": "./infra/scripts/health-check.sh",
      "group": "test"
    },
    {
      "label": "Schema verify",
      "type": "shell",
      "command": "python -m pytest tests/test_schema.py -v",
      "group": "test"
    }
  ]
}
```

**Agent prompts to use in Claude Code chat:**
```
# Infra issue:
"The P1 ECS task stopped with exit code 1 — check CloudWatch and fix"

# Code ingestion:
"The OCC BSA connector is not picking up the new beneficial ownership rule — debug and fix"

# Monitor:
"Run a full system health check and report any issues"

# Sync monitor:
"Check if P2 sync is current and verify the dashboard panels have data"
```

## Claude CLI

```bash
# Install
npm install -g @anthropic-ai/claude-code

# Authenticate
claude auth login

# Start a session in this repo (reads CLAUDE.md automatically)
cd /path/to/textmind_banking_backend
claude

# Or run a one-shot command
claude -p "Check if all 17 Alembic migrations are in the chain and verify the schema"
claude -p "The P1 scraper ran last night but compliance_rules has no embeddings — diagnose"
claude -p "Run the P2 sync end-to-end test and fix any failures"
claude -p "Generate a test for WireTransferValidator with amount=$15000 and verify dual-approval triggers"
```

## Environment variables per agent session

```bash
# Agent 1 — infra (Fortress account)
export AWS_PROFILE=fortress-prod
export AWS_REGION=us-east-1

# Agent 2 — P1 code
export PIPELINE_MODE=pipeline1
export DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev"
export REDIS_URL="redis://:fortress_dev@localhost:6379"
export AWS_REGION=us-east-1

# Agent 3 — monitor (both accounts)
export AWS_PROFILE=fortress-prod   # switch to bank profile for P2

# Agent 4 — P2 sync (bank account)
export AWS_PROFILE=bank-firstnational
export PIPELINE_MODE=pipeline2
export CLIENT_NAME=firstnational
export DATABASE_URL="postgresql+asyncpg://fortress_firstnational:<pw>@<aurora-host>:5432/fortress_p2?ssl=require"
export PIPELINE1_API_URL=https://api.fortresslabs.dev
```

## Pipeline 1 automated setup

```bash
# Set env vars once
export AWS_REGION=us-east-1
export AWS_PROFILE=fortress-prod
export DOMAIN_NAME=fortresslabs.dev
export ALERT_EMAIL=jamie@fortresslabs.dev
export ENVIRONMENT=production
export IMAGE_TAG=v5.0

# Run all 8 setup steps automatically via Claude CLI
chmod +x infra/scripts/setup-p1-auto.sh
./infra/scripts/setup-p1-auto.sh

# Resume from a specific step if one fails
START_STEP=4 ./infra/scripts/setup-p1-auto.sh
```

The 8 steps (note: step 4 is deferred — it runs after step 7):
1. Verify AWS access, Bedrock models, Docker, CloudFormation templates
2. Deploy `pipeline1-vpc.yml` — VPC, subnets, SGs, 7 VPC endpoints
3. Deploy `pipeline1-data.yml` — Aurora Serverless v2 + pgvector, Redis, S3, KMS
   (pilot sizing; OpenSearch resources omitted). DB password rotation is
   intentionally **disabled** in pilot — see memory `project_p1_secret_rotation_gap`.
   After the stack deploys, run `infra/scripts/encode-app-config-secret.sh` to
   URL-encode `REDIS_URL`/`DATABASE_URL` passwords in `fortress/pipeline1/app-config`.
5. (skipped in pilot — OpenSearch index. Restore data-stack OpenSearch first.)
6. Build + push `Dockerfile.pipeline1` image to ECR (`--platform linux/amd64`)
7. Deploy `pipeline1-compute.yml` — ECS cluster, **API service starts at
   DesiredCount=0**, init + scraper task definitions, ALB, WAF, EventBridge (DISABLED).
   The scraper task def runs `scripts/run_scrape.py` one-shot; the in-process
   scheduler in the API container is gated by `SCRAPER_IN_PROCESS=true` and off
   by default so EventBridge is the single source of cron truth.
4. Bootstrap Aurora schema as a one-shot Fargate task using `EcsTaskDefInitP1`
   (`python3 -m scripts.setup.init_p1_schema` with `PYTHONPATH=/app`). The init
   script runs `Base.metadata.create_all()` from the ORM and adds the pgvector
   + tsvector + HNSW/GIN indexes. SgEcs is already in Aurora's allowlist; no
   bastion / RDS Data API needed. Then `aws ecs update-service --desired-count 1`
   to wake the API.
8. Deploy `pipeline1-security.yml` (GuardDuty + Security Hub PCI/FSBP + CloudTrail),
   enable EventBridge scheduler, trigger the first one-shot scrape via
   `aws ecs run-task --task-definition <EcsTaskDefScraperArn>`.

Prompts reference: `infra/setup/PIPELINE1_SETUP.md`
Logs written to: `infra/setup/logs/setup_YYYYMMDD_HHMMSS.log`

## Bank registration

Every bank that calls the P1 sync API must be registered. Registration:
1. Creates a row in `client_credentials` (SHA-256 hash of API key — raw key never stored)
2. Stores the raw key in the bank's Secrets Manager under `fortress/{bankId}/pipeline1-api-key`
3. Adds the bank's NAT Gateway EIP to the P1 WAF IP allowlist
4. Verifies the full auth chain works before exiting

```bash
# Register a bank (run from Fortress account)
export FORTRESS_INTERNAL_SECRET="your-internal-secret"
export AWS_PROFILE=fortress-prod

./scripts/register-bank.sh \
  --bank-id      firstnational \
  --bank-name    "First National Bank" \
  --bank-ip      1.2.3.4 \
  --bank-profile bank-firstnational \
  --tier         standard

# List registered banks
curl https://api.fortresslabs.dev/internal/admin/clients \
  -H "X-Internal-Secret: $FORTRESS_INTERNAL_SECRET" | python3 -m json.tool

# Rotate a bank's API key
curl -X POST https://api.fortresslabs.dev/internal/admin/clients/firstnational/rotate \
  -H "X-Internal-Secret: $FORTRESS_INTERNAL_SECRET"

# Suspend a bank immediately
curl -X PATCH https://api.fortresslabs.dev/internal/admin/clients/firstnational \
  -H "X-Internal-Secret: $FORTRESS_INTERNAL_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"is_active": false}'
```

Auth model: every `/api/sync/*` request from P2 must pass two checks:
1. WAF: bank NAT IP in allowlist → request reaches ECS
2. App: SHA-256(X-Api-Key) matches `client_credentials.api_key_hash` AND `is_active=true`

## MCP servers

Three MCP protocol servers under `src/mcp_servers/` provide tool-use capabilities
to the 6-agent pipeline. They are backed by `ComplianceService` (DB) with hardcoded
fallback constants when the DB is unavailable.

| Server | Purpose | Key tools |
|---|---|---|
| `compliance_knowledge` | Query US banking compliance rules | `get_compliance_rules`, `get_obligation_thresholds`, `check_cfr_citation` |
| `test_patterns` | Look up existing test templates | `get_test_patterns`, `find_similar_tests` |
| `banking_workflows` | Banking workflow definitions | `get_workflow_steps`, `get_approval_requirements` |

Debug MCP server connection:
```bash
# Test compliance knowledge server locally
PIPELINE_MODE=pipeline2 \
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
python3 -c "
from src.mcp_servers.compliance_knowledge.server import server
print('MCP server loaded:', server.name)
"
```

## Connector map — 17 federal + 5 state

```
src/connectors/regulatory/usa/
  occ_bsa.py          OCC BSA/AML (wire, CTR, SAR)
  fincen_sar.py       FinCEN SAR filing requirements
  ofac_sdn.py         OFAC SDN list scraper
  cfpb.py             CFPB — HMDA, Reg B, Section 1071
  fdic.py             FDIC risk-based regulations
  ffiec_bsa.py        FFIEC BSA examination manual
  ffiec_call_report.py FFIEC Call Report (RCRI)
  ffiec_cybersecurity.py FFIEC Cybersecurity Assessment Tool
  cra.py              Community Reinvestment Act
  peer_benchmarks.py  FFIEC peer benchmark aggregates
  irr.py              Interest Rate Risk guidelines
  liquidity_risk.py   Liquidity risk requirements
  cecl.py             CECL credit loss model
  cre_concentration.py Commercial Real Estate concentration
  operational_risk.py Operational Risk RCSA
  states/nydfs.py     NYDFS Part 500 cybersecurity
  states/ca_dfpi.py   California DFPI
  states/tx_dob.py    Texas Department of Banking
  states/fl_ofr.py    Florida OFR
  states/il_idfpr.py  Illinois IDFPR
```

Test a single connector locally:
```bash
PIPELINE_MODE=pipeline1 \
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
python3 -c "
import asyncio
from src.connectors.regulatory.usa.occ_bsa import OccBsaConnector
async def test():
    c = OccBsaConnector()
    results = await c.fetch()
    print(f'OCC BSA: {len(results)} rules fetched')
    for r in results[:3]:
        print(f'  {r.rule_id}: {r.rule_text[:80]}')
asyncio.run(test())
"
```

---

## Bank onboarding — Examination Evidence & Reports

Required once per new pilot bank, before the dashboard's Reports page can
generate any exports. Per design doc §11.4 Q20.

### 1. Provision the WORM bucket + KMS CMK

```bash
aws cloudformation deploy \
  --stack-name fortress-p2-exports-bucket-${BANK_ID}-${ENV} \
  --template-file infra/cloudformation/pipeline2-exports-bucket.yml \
  --parameter-overrides \
      BankId=${BANK_ID} Environment=${ENV} \
      CloudTrailLogBucket=${CLOUDTRAIL_BUCKET} \
  --capabilities CAPABILITY_NAMED_IAM
```

Verify Object Lock COMPLIANCE was applied:

```bash
aws s3api get-object-lock-configuration \
  --bucket fortress-p2-exports-${BANK_ID}-${ENV}
# Mode MUST report "COMPLIANCE" — GOVERNANCE is wrong
```

### 2. Verify Bedrock readiness in the bank account

Required before generating `board_report` (uses Sonnet 4.x summariser).

```bash
PIPELINE_MODE=pipeline2 python -m scripts.setup.verify_bedrock_ready
```

If it reports `use_case_form`, submit the Anthropic use-case form in the
Bedrock console (per `feedback_bedrock_anthropic_use_case_form`). If
`marketplace`, add `aws-marketplace:Subscribe/ViewSubscriptions/Unsubscribe`
to the exports-worker task role (per `feedback_bedrock_marketplace_iam`).

### 3. Deploy the exports worker task def

```bash
# ECR image (built from Dockerfile.exports-worker)
docker build -f Dockerfile.exports-worker \
  -t fortress/p2-exports-worker:${TAG} .
docker tag  fortress/p2-exports-worker:${TAG} \
  ${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/fortress-p2-exports-worker:${TAG}
docker push ${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/fortress-p2-exports-worker:${TAG}

aws cloudformation deploy \
  --stack-name fortress-p2-exports-worker-${BANK_ID}-${ENV} \
  --template-file infra/cloudformation/pipeline2-exports-worker.yml \
  --parameter-overrides \
      BankId=${BANK_ID} Environment=${ENV} \
      ImageUri=${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/fortress-p2-exports-worker:${TAG} \
      ExportsBucketArn=$(aws cloudformation list-exports --query \
        "Exports[?Name=='fortress-p2-${BANK_ID}-${ENV}-exports-bucket-arn'].Value" \
        --output text) \
      ExportsKmsKeyArn=$(aws cloudformation list-exports --query \
        "Exports[?Name=='fortress-p2-${BANK_ID}-${ENV}-exports-kms-arn'].Value" \
        --output text) \
  --capabilities CAPABILITY_NAMED_IAM
```

Note: NEVER tag this image `:latest` — per `feedback_p1_taskdef_semver_pinned`
the task def is pinned to the semver tag and `force-new-deployment` will
silently keep the old image.

### 4. Seed bank_profile + officer roster

The Reports page returns 4xx until `bank_profile` has a row for the bank.

```bash
PIPELINE_MODE=pipeline2 \
BANK_ID=${BANK_ID} \
python -c "
import asyncio, datetime as d
from sqlalchemy import text
from src.services.database import init_db, close_db, get_db
async def seed():
    await init_db()
    async with get_db() as s:
        await s.execute(text('''
            INSERT INTO bank_profile (bank_id, legal_name, primary_regulator,
                                       fiscal_year_end, created_at, updated_at)
            VALUES (:b, :n, :r, :fy, now(), now())
            ON CONFLICT (bank_id) DO NOTHING
        '''), {'b': '${BANK_ID}', 'n': 'EXAMPLE Bank, N.A.',
                'r': 'OCC', 'fy': d.date(2026, 12, 31)})
        await s.commit()
    await close_db()
asyncio.run(seed())
"
```

Then load the officer roster — at minimum CCO + Internal Audit (for
`examiner_package` dual sign-off) + BSA Officer + CRO (for
`ofac_attestation`). Officers are append-only — never DELETE, set
`ended_at` instead.

### 5. Seed at least one control + test + mapping

Without these, `examiner_package` renders an empty control catalogue.
Pull from the bank's existing GRC tool via CSV import (one-off — open a
ticket and we'll provide the import script).

### 6. Deploy CW alarms + quarterly drill

```bash
aws cloudformation deploy \
  --stack-name fortress-p2-exports-alarms-${BANK_ID}-${ENV} \
  --template-file infra/cloudformation/pipeline2-exports-alarms.yml \
  --parameter-overrides \
      BankId=${BANK_ID} Environment=${ENV} \
      AlarmSnsTopicArn=${CCO_ONCALL_SNS_TOPIC} \
      ExportsBucketName=fortress-p2-exports-${BANK_ID}-${ENV}
```

### 7. Verify end-to-end

From a CCO/CRO-authenticated dashboard session, generate one
`examiner_package` export with today's `as_of`. Verify:

  * A row appears in `export_artifacts` with `version_hash`, `sha256`
  * S3 object exists with Object Lock retention header set
  * `POST /api/v1/exports/{id}/sign` from CCO + Internal Audit unlocks
    the download (X-Fully-Signed: true)
  * `GET /api/v1/exports/{id}/download` writes an `export_access_log` row
  * Re-running the same as-of returns the existing artifact (idempotent)

If any step fails, check the exports-worker CloudWatch log group
`/fortress/p2/exports-worker/${BANK_ID}/${ENV}` — most failures are
Bedrock (board_report) or missing KMS permission on the worker role.
