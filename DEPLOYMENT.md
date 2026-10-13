# Fortress Labs — Deployment Guide (Fresh Install)

## What changed since last version

| File | Change |
|---|---|
| `infra/cloudformation/pipeline1-compute.yml` | S3_BUCKET + S3_CHUNK_PREFIX added to ECS task — scraper chunks now persist to S3 |
| `src/utils/chunk_exporter.py` | S3 primary write + local fallback |
| `src/services/scraper_scheduler.py` | _embed_and_store_rules, _export_all_chunks_to_s3 replace Weaviate methods (OpenSearch sync deferred for pilot — pgvector handles search) |
| `src/services/opensearch_client.py` | DEFERRED — file kept in repo but not wired up in pilot to reduce cost; pgvector HNSW + BM25 GIN on Aurora handles vector + keyword search |
| `src/services/update_service.py` | Weaviate → _refresh_vector_index (pgvector ANALYZE) |
| `src/api/sync_api.py` | /sync/weaviate → /sync/embeddings with pre-computed vectors |
| `src/api/sync_auth.py` | New — header-based HMAC auth FastAPI dependency |
| `src/services/client_credential_service.py` | .verify() alias added for sync_api.py compat |
| `src/mcp_servers/scrape_monitor/server.py` | New — log_scrape_run, get_scrape_health, get_failed_connectors tools |
| `scripts/reconcile_rules.py` | New — detects + fixes 5 categories of invalid/stale rules |
| `scripts/register-bank.sh` | New — full bank onboarding with WAF + Secrets Manager |
| `alembic/versions/018_add_scrape_runs.py` | New — scrape_runs table for MCP monitor |
| `alembic/versions/019_add_embed_tracking.py` | New — embed_updated_at column for stale detection |
| `docker-compose.yml` | pgvector:pg15, Weaviate removed |
| `CLAUDE.md` | 1,360 lines — all agents, S3 flow, rule validity, bank registration |
| `scripts/setup/initialize_weaviate.py` | DEPRECATED — raises error on import |

## Architecture

```
Pipeline 1 — Fortress AWS account
  ECS Fargate (EventBridge 02:00 UTC)
  Aurora PostgreSQL r6g.large + pgvector HNSW + BM25 GIN  ← single search backend in pilot
  ElastiCache Redis r7g.medium (Graviton)
  S3 (scraper-chunks/ + scrape-logs/ + cloudtrail/)
  No EC2. No Weaviate. OpenSearch Serverless deferred post-pilot (cost reduction).

Pipeline 2 — Bank AWS account (per bank)
  ECS Fargate auto-scale 2-10 tasks
  Aurora Serverless v2 0.5-4 ACU + pgvector HNSW
  ElastiCache Redis t4g.micro (Graviton)
  JIRA → /webhooks/jira (HMAC-verified) → FastAPI BackgroundTasks → 6-agent pipeline
  All bank data stays in bank VPC — Fortress has zero visibility
```

## Scraper data flow (what goes where)

```
18 connectors fetch content
  → Aurora compliance_rules (structured, hashed, versioned)   ALWAYS
  → Bedrock Titan Embed v2 → embedding column (delta only)    NEW — pgvector HNSW handles k-NN
  → tsvector column auto-generated → GIN index                       handles BM25-style keyword search
  → S3 scraper-chunks/{type}/{date}/ (raw JSON audit trail)   NEW (needs S3_BUCKET env var — now in CFN)
  → S3 scrape-logs/{date}/ (MCP monitor result per connector) NEW

  (OpenSearch Serverless step deferred — restore when ready to scale beyond pilot)
```

## Five categories of invalid rules

```
Category 1  Rule removed from source    → is_active=FALSE in Aurora (OpenSearch tombstone N/A in pilot — pgvector only)
Category 2  Missing embedding           → embedding IS NULL, GIN keyword only, no k-NN vector search
Category 3  (Reserved for OpenSearch orphans — not applicable in pilot, pgvector is single source of truth)
Category 4  Stale embedding             → rule text changed, embed_updated_at < updated_at
Category 5  P2 sync drift               → bank version behind P1, has tombstoned rules
```

Fix: `python3 scripts/reconcile_rules.py --fix-all`

---

## P1 Deployment — Fresh Install (old infra deleted)

### Current pilot state (as of 2026-05-13)

| Stack | Status |
|---|---|
| `fortress-p1-vpc-production` | UPDATE_COMPLETE |
| `fortress-p1-data-production` | UPDATE_COMPLETE — Aurora + Redis + S3 + KMS (no OpenSearch) |
| `fortress-p1-compute-production` | UPDATE_COMPLETE — ECS API running 1/1 |
| `fortress-p1-security-production` | CREATE_COMPLETE |
| Schema | Stamped to alembic 019 |
| OpenSearch Serverless | **Deferred for pilot** — pgvector HNSW + GIN tsvector is the only search backend |
| Scraper | In progress — wiring `EcsTaskDefScraper` + EventBridge first run |

### Set env vars

```bash
export AWS_PROFILE=fortress-prod
export AWS_REGION=us-east-1
export ENVIRONMENT=production
export DOMAIN_NAME=fortresslabs.dev
export ALERT_EMAIL=jamie@fortresslabs.dev
export IMAGE_TAG=v5.0
```

### Option A — Claude CLI auto (recommended)

```bash
chmod +x infra/scripts/setup-p1-auto.sh
./infra/scripts/setup-p1-auto.sh
# Resume from step N: START_STEP=4 ./infra/scripts/setup-p1-auto.sh
```

### Option B — Manual (7 steps — OpenSearch step removed for pilot)

**Step 1 — VPC**  *(✅ done)*
```bash
aws cloudformation deploy \
  --stack-name fortress-p1-vpc-production \
  --template-file infra/cloudformation/pipeline1-vpc.yml \
  --parameter-overrides Environment=production \
  --capabilities CAPABILITY_NAMED_IAM
```

**Step 2 — Data (15-20 min)**  *(✅ done — no OpenSearch resources provisioned)*
```bash
aws cloudformation deploy \
  --stack-name fortress-p1-data-production \
  --template-file infra/cloudformation/pipeline1-data.yml \
  --parameter-overrides Environment=production \
  --capabilities CAPABILITY_NAMED_IAM
```

**Step 3 — Schema (Python, no Alembic)**  *(✅ done)*
See `infra/setup/PIPELINE1_SETUP.md` Prompt 4 — creates all 12 tables + pgvector + HNSW + GIN indexes fresh.
Stamps alembic_version=019.
Also creates `embed_updated_at` column (migration 019).

> **Pilot note:** the original Step 4 ("OpenSearch index") is deferred. Aurora pgvector (HNSW on `embedding`) handles k-NN and the auto-generated `tsv` column + GIN index handles keyword search. To restore OpenSearch later, re-add the resources to `pipeline1-data.yml`, run `infra/setup/PIPELINE1_SETUP.md` Prompt 5, and re-enable `_sync_rules_to_opensearch` in `scraper_scheduler.py`.

**Step 4 — Build + push image**  *(⏳ in progress — required before first scrape run)*
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/fortress/pipeline1"
aws ecr create-repository --repository-name fortress/pipeline1 \
  --image-scanning-configuration scanOnPush=true 2>/dev/null || true
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com"
docker build -f Dockerfile.pipeline1 --platform linux/amd64 -t fortress/pipeline1:${IMAGE_TAG} .
docker tag fortress/pipeline1:${IMAGE_TAG} ${ECR_URI}:${IMAGE_TAG}
docker push ${ECR_URI}:${IMAGE_TAG}
```

**Step 5 — Compute (ECS + ALB + WAF)**  *(✅ done — API service running 1/1)*
```bash
HOSTED_ZONE_ID=$(aws route53 list-hosted-zones \
  --query "HostedZones[?Name=='${DOMAIN_NAME}.'].Id" \
  --output text | cut -d'/' -f3)
aws ssm put-parameter --name /fortress/pipeline1/HOSTED_ZONE_ID \
  --value "$HOSTED_ZONE_ID" --type String --overwrite

aws cloudformation deploy \
  --stack-name fortress-p1-compute-production \
  --template-file infra/cloudformation/pipeline1-compute.yml \
  --parameter-overrides \
    Environment=production ImageTag=${IMAGE_TAG} \
    DomainName=${DOMAIN_NAME} AlertEmail=${ALERT_EMAIL} \
  --capabilities CAPABILITY_NAMED_IAM

# Get ALB DNS then create CNAME: api.fortresslabs.dev → <ALB DNS>
aws cloudformation describe-stacks --stack-name fortress-p1-compute-production \
  --query "Stacks[0].Outputs[?OutputKey=='AlbDns'].OutputValue" --output text
```

**Step 6 — Security**  *(✅ done)*
```bash
S3_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-data-production \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output text)
aws cloudformation deploy \
  --stack-name fortress-p1-security-production \
  --template-file infra/cloudformation/pipeline1-security.yml \
  --parameter-overrides Environment=production S3BucketName=${S3_BUCKET} \
  --capabilities CAPABILITY_NAMED_IAM
```

**Step 7 — Enable scraper + first run**  *(⏳ next — current focus)*
```bash
aws scheduler update-schedule \
  --name fortress-p1-daily-scrape-production --state ENABLED \
  --schedule-expression "cron(0 2 * * ? *)" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target "$(aws scheduler get-schedule \
    --name fortress-p1-daily-scrape-production --query Target --output json)"

TASK_DEF=$(aws cloudformation describe-stacks \
  --stack-name fortress-p1-compute-production \
  --query "Stacks[0].Outputs[?OutputKey=='EcsTaskDefArn'].OutputValue" --output text)
SUBNET=$(aws cloudformation list-exports \
  --query "Exports[?Name=='FortressP1-production-PrivateSubnetA'].Value" --output text)
SG=$(aws cloudformation list-exports \
  --query "Exports[?Name=='FortressP1-production-SgEcs'].Value" --output text)

aws ecs run-task --cluster fortress-pipeline1-production \
  --task-definition "$TASK_DEF" --launch-type FARGATE \
  --network-configuration \
    "awsvpcConfiguration={subnets=[${SUBNET}],securityGroups=[${SG}],assignPublicIp=DISABLED}"

aws logs tail /fortress/pipeline1 --follow
```

**Verify P1 complete**
```bash
curl https://api.fortresslabs.dev/health
python3 scripts/verify_schema.py
aws s3 ls s3://fortress-p1-data-${ACCOUNT_ID}-production/scraper-chunks/ --recursive | head -10

# Confirm pgvector is the search backend and embeddings are landing
psql $DATABASE_URL -c "SELECT extname FROM pg_extension WHERE extname='vector';"
psql $DATABASE_URL -c "SELECT COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
                             COUNT(*) AS total FROM compliance_rules WHERE is_active=TRUE;"
```

---

## P2 Deployment — Per bank

```bash
export AWS_PROFILE=bank-firstnational
export FORTRESS_ACCOUNT_ID=<fortress-account-id>
export IMAGE_TAG=v5.0

chmod +x infra/scripts/deploy-p2.sh
./infra/scripts/deploy-p2.sh firstnational production
# Prompts for domain, JIRA URL, VPC CIDR, alert email

# Register bank in P1 WAF + Secrets Manager
export FORTRESS_INTERNAL_SECRET="..."
export AWS_PROFILE=fortress-prod
NAT_EIP=$(aws cloudformation describe-stacks \
  --profile bank-firstnational \
  --stack-name fortress-p2-vpc-firstnational \
  --query "Stacks[0].Outputs[?OutputKey=='NatGatewayEip'].OutputValue" \
  --output text)

./scripts/register-bank.sh \
  --bank-id firstnational \
  --bank-name "SMB_Demo" \
  --bank-ip ${NAT_EIP} \
  --bank-profile bank-demo \
  --tier standard
```

---

## Maintenance commands

```bash
make schema-check                            # verify schema health
python3 scripts/reconcile_rules.py --check  # check rule validity
python3 scripts/reconcile_rules.py --fix-all  # fix all rule issues
make logs-p1                                 # tail P1 CloudWatch
make logs-p2 BANK_ID=firstnational          # tail P2 CloudWatch
make deploy-p1 IMAGE_TAG=v5.1               # rolling P1 deploy
make deploy-p2-bank BANK_ID=firstnational IMAGE_TAG=v5.1  # rolling P2
```
