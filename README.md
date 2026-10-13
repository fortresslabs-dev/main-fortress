# Fortress Labs — AI-Powered Banking Compliance Platform

[![License](https://img.shields.io/badge/license-Proprietary-red)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![Patent](https://img.shields.io/badge/patent-pending%20USPTO%2064%2F056%2C542-green)]()

**Patent Pending — USPTO Application No. 64/056,542, filed May 3, 2026.**  
SS-MoE architecture · Geography-Aware Expert Routing · Hybrid Two-Pipeline Data Architecture.

---

## What this is

Fortress Labs automates regulatory compliance testing for US regional banks across 18 federal
regulators (OCC, FDIC, CFPB, FinCEN, OFAC, FFIEC, FRB). Updated daily, zero manual effort.

- JIRA story created → test code with CFR citations posted back in minutes
- Real-time validators: wire transfer BSA/AML, OFAC SDN screening, CFPB 1071 loan checks
- CCO dashboard: health score, deadlines, regulatory change inbox, enforcement heatmap
- Exam-ready evidence: regulation → obligation → test → result traceability chain

---

## Two pipelines, two AWS accounts

| | Pipeline 1 — Fortress Cloud | Pipeline 2 — Bank Account |
|---|---|---|
| **Location** | Fortress AWS account | Bank's own AWS account |
| **Purpose** | Scrape 18 regulators, serve sync API | 6-agent test pipeline, validators, CCO dashboard |
| **Compute** | ECS Fargate (EventBridge-triggered) | ECS Fargate auto-scale 2–10 tasks |
| **Database** | Aurora PostgreSQL r6g.large (Provisioned, Graviton, I/O-Opt, Multi-AZ) | Aurora Serverless v2 (0.5–4 ACU) |
| **Search** | OpenSearch Serverless (BM25 + k-NN) | pgvector HNSW on Aurora |
| **Cache** | ElastiCache Redis r7g.medium | ElastiCache Redis t4g.micro |
| **Entry point** | `src/main_pipeline1.py` | `src/main_pipeline2.py` |
| **Dockerfile** | `Dockerfile.pipeline1` | `Dockerfile.pipeline2` (strips P1 code) |
| **EC2** | None in production | None in production |

---

## Repository structure

```
.
├── src/
│   ├── agents/           # 6-agent pipeline (Discovery → Execution)
│   ├── api/              # FastAPI routers (sync_api, validation_api, webhooks, auth)
│   ├── connectors/       # 18 regulatory scrapers (P1 only — stripped from P2 image)
│   ├── models/database.py  # SQLAlchemy models for all tables
│   ├── services/         # Business logic (validators, compliance_rule_resolver, etc.)
│   └── utils/config.py   # Unified config — detects PIPELINE_MODE, injects secrets
├── alembic/versions/     # 17 migrations (001–017)
├── infra/
│   ├── cloudformation/   # 8 CFN templates (P1+P2 × vpc/data/compute/security)
│   ├── scripts/          # deploy-p1.sh, deploy-p2.sh, register-bank-ip.sh
│   └── params/           # p1-prod.json, p2-template.json
├── Dockerfile.pipeline1  # Full P1 image
├── Dockerfile.pipeline2  # Multi-stage — strips P1 proprietary code
├── docker-compose.yml    # Dev: both pipelines + Postgres + Redis
└── scripts/entrypoint.sh # P1: Alembic then uvicorn
```

---

## Local development

```bash
cp .env.example .env        # fill in DATABASE_URL, REDIS_URL, AWS_REGION
docker compose up -d        # starts postgres (pgvector:pg15), redis, pipeline1+2
docker compose exec pipeline1 python -m alembic upgrade head
curl http://localhost:8000/health
```

`PIPELINE_MODE=pipeline1` runs the scraper + sync API.  
`PIPELINE_MODE=pipeline2` runs the bank agent pipeline + validators + dashboard.

---

## Deploying to AWS

### Pipeline 1 — Fortress account (one-time)

```bash
export AWS_PROFILE=fortress-prod
export ALERT_EMAIL=ops@fortresslabs.dev
export DOMAIN_NAME=fortresslabs.dev
chmod +x infra/scripts/*.sh
./infra/scripts/deploy-p1.sh production
```

Deploys in order: VPC → data (Aurora + OpenSearch + Redis) → compute (ECS + ALB + WAF) → security.
Script pauses for Alembic migrations and Docker image push.

### Pipeline 2 — Bank account (per bank, ~20 min)

```bash
export AWS_PROFILE=bank-firstnational
export FORTRESS_ACCOUNT_ID=<fortress-aws-account-id>
./infra/scripts/deploy-p2.sh firstnational production
```

Collects bank inputs interactively, deploys all 4 stacks, runs 17 Alembic migrations,
pushes P2 Docker image, and guides you through JIRA webhook configuration.

### Update deployed image

```bash
# P1 rolling deploy (zero downtime)
aws ecs update-service --cluster fortress-pipeline1-production \
  --service fortress-pipeline1-api-production --force-new-deployment

# P2 rolling deploy (per bank)
aws ecs update-service --cluster fortress-pipeline2-firstnational \
  --service fortress-pipeline2-firstnational --force-new-deployment
```

---

## Database migrations

```bash
python -m alembic upgrade head    # run all pending
python -m alembic current         # check state
python -m alembic downgrade -1    # roll back one
```

Migrations 016 and 017 add P2-specific tables (workflow_executions, bank_policies, etc.)
and must run against each bank's Aurora instance after P2 data stack deployment.

---

## Key API endpoints

**Pipeline 1 — api.fortresslabs.dev**

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | None | Health check |
| `POST /api/sync/postgres` | HMAC api_key | Incremental rule diffs |
| `POST /api/sync/initial` | HMAC api_key | Full first-time seed |
| `POST /api/sync/obligations` | HMAC api_key | Obligation sync |
| `POST /api/sync/deadlines` | HMAC api_key | Deadline sync |
| `POST /api/license/verify` | HMAC api_key | Verify bank key |

**Pipeline 2 — fortressapi.{bank-domain}**

| Endpoint | Auth | Description |
|---|---|---|
| `POST /validate/wire-transfer` | Bearer JWT | Wire BSA/AML check |
| `POST /validate/ofac-screen` | Bearer JWT | OFAC SDN screening |
| `POST /webhooks/jira` | HMAC | JIRA → 6-agent pipeline |
| `GET /api/v1/dashboard/compliance-overview` | Bearer JWT | 7-panel CCO dashboard |

---

## Security

- No EC2 in production — ECS Fargate removes OS patching from PCI scope
- Bank data never leaves the bank account — sync transfers public regulatory data only
- Bank manages all KMS keys — Fortress has zero access to bank encryption
- Security Hub (PCI DSS v3.2.1 + FSBP) enabled independently in both accounts
- Multi-stage Docker build strips P1 proprietary code from the bank-deployed image

---

## Team

| Role | Status |
|---|---|
| Founder & AI/ML Architect | Jamie Ashikur Rahman |
| AI Developer (bank premises) | **Hiring — urgent** |
| Head of Compliance | **Hiring — urgent** |
| Senior ML Engineer | Hiring |
| Bank Sales Representative (×2) | Hiring |

---

**Fortress Labs, Inc.** (US) · **Text Mind Private Limited** (India)  
Patent Pending — USPTO #64/056,542 · Proprietary and Confidential
