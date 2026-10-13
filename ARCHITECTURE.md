# Fortress Labs — Platform Architecture

**Banking Compliance Automation Platform**
Version: P1 v5.3 (production-pilot)
Last updated: 2026-05-14

---

## 1. Executive summary

Fortress Labs operates a **two-pipeline regulatory-compliance platform** that ingests, structures, and serves US banking regulations to partner banks. The platform is split across **two isolated AWS accounts** so that proprietary scraping/AI logic lives in the Fortress account while every bank gets its own bank-controlled production deployment.

| | Pipeline 1 — Fortress Cloud | Pipeline 2 — Bank Account |
|---|---|---|
| **Owner account** | Fortress Labs | The bank (e.g. First National) |
| **Role** | Master: scrape, embed, distribute | Replica: validate, run agent workflows |
| **Public name** | `api.fortresslabs.dev` | `fortressapi.<bank>.com` |
| **Trigger** | EventBridge cron (daily 02:00 UTC) | JIRA webhooks + UI requests |
| **Compute model** | API service (1 task) + one-shot scraper | Auto-scaling API (2–10 tasks) |
| **Source of regulatory truth** | Direct scrape of 20 regulator sites (15 federal + 5 state) | Sync from P1 over HTTPS |

The two pipelines never share a database. The only link is an **HTTPS sync API** with header-based authentication and a WAF IP allowlist.

---

## 2. The two-pipeline model — why split?

Banks regulate themselves with **bank-managed infrastructure** — Fortress can't run inside their compliance perimeter without losing scale. Scraping and AI workloads, on the other hand, are expensive and best amortized across all customers. The split solves both:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  FORTRESS ACCOUNT (P1)                                                  │
│  ─────────────────────                                                  │
│  • Scrape 20 regulator sites daily (15 federal + 5 state)               │
│  • Extract obligations + risk scores via Bedrock                        │
│  • Compute 1024-dim embeddings via Titan v2                             │
│  • Serve /api/sync/* — 11 endpoints                                     │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              │  HTTPS, header-auth, WAF IP allowlist
                              │  P2 NAT EIP → P1 ALB
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  BANK ACCOUNT (P2 — one per bank)                                       │
│  ──────────────────────────                                             │
│  • Daily UpdateService pulls deltas from P1                             │
│  • Local Aurora is bank-encrypted, bank-keyed                           │
│  • 6-agent JIRA-to-test pipeline (validation, generation, exec)         │
│  • Bank's CCO dashboard, validators, policy gap detection               │
└─────────────────────────────────────────────────────────────────────────┘
```

**Account-level isolation gives:**
- Bank-controlled KMS keys — Fortress can't read bank data even with full root
- Independent WAF, GuardDuty, Security Hub, CloudTrail per bank
- Per-bank failure blast radius

---

## 3. AWS service inventory

| Layer | Service | Purpose |
|---|---|---|
| Network | VPC, Subnets, NAT, IGW, VPC Endpoints | Network isolation, private egress |
| Identity | IAM Roles, Secrets Manager, SSM Parameter Store, KMS | Per-task credentials, secret rotation, encryption |
| Compute | ECS Fargate, ECS Service, ECS Task Definition | Container orchestration (no EC2) |
| Edge | ALB, ACM, Route 53, WAFv2 | TLS termination, DNS, rate-limit + IP allowlist |
| Database | Aurora Serverless v2 (PostgreSQL 15) + pgvector | Structured rules + 1024-dim vector search |
| Cache | ElastiCache Redis 7.1 | Compliance rule cache (P2 Tier-2 lookup) |
| Storage | S3 (versioned + KMS) | Raw scrape chunks + CloudTrail logs |
| Async | EventBridge Scheduler | Daily cron (P1 scraper trigger) |
| AI | Amazon Bedrock — `anthropic.claude-sonnet-4`, `amazon.titan-embed-text-v2:0` | Obligation extraction + embeddings |
| Observability | CloudWatch (logs, metrics, alarms), SNS | Operations + email alerts |
| Security | GuardDuty, Security Hub (PCI/FSBP), CloudTrail | Continuous compliance monitoring |
| CI/CD | ECR, GitHub Actions | Image registry + automated builds |

**Notably absent (intentional):**
- **No EC2** — Fargate only
- **No Weaviate in production** — pgvector + tsvector replaced it
- **No OpenSearch in P1** — decision 2026-05-14, Aurora pgvector is the sole vector + BM25 store (`src/services/opensearch_client.py` retained as a deprecated stub for import compatibility, not called)
- **No DynamoDB** — Aurora is the single relational source

---

## 4. Pipeline 1 — Fortress Cloud

### 4.1 Network (`pipeline1-vpc.yml`)

- **VPC CIDR:** `10.1.0.0/16`, 2 AZs (us-east-1a + 1b)
- **Public subnets:** `10.1.0.0/24`, `10.1.1.0/24` (ALB + NAT)
- **Private subnets:** `10.1.10.0/24`, `10.1.11.0/24` (ECS, Aurora, Redis)
- **NAT Gateway:** 1 (pilot-cost; will be 2 for HA at GA)
- **VPC Endpoints (private):** Interface — Bedrock-Runtime, SSM, Secrets Manager, CloudWatch Logs, ECR API + DKR, STS. Gateway — S3.

**Security-group chain:** every flow is one-direction and SG-to-SG, never CIDR-based.

```
0.0.0.0/0 ─[443]─▶ SgAlb ─[8000]─▶ SgEcs ─[5432]─▶ SgAurora
                                        ─[6379]─▶ SgRedis
                                        ─[443] ─▶ SgVpcEndpoints (Bedrock/SSM/Secrets)
                                        ─[443] ─▶ NAT  ─▶ regulator sites
```

### 4.2 Data layer (`pipeline1-data.yml`)

| Resource | Sizing | Notes |
|---|---|---|
| Aurora Serverless v2, PG 15.10 | min 0.5 ACU / max 2 ACU | 7-day backup, deletion-protected, pgvector enabled |
| ElastiCache Redis 7.1 | `cache.t4g.micro`, 1 node | TLS + AUTH; pilot single-node (HA at GA) |
| KMS CMKs (3) | RDS, Redis, S3 | Key rotation enabled |
| S3 `fortress-p1-data-{acct}-{env}` | versioned, KMS-encrypted | 365-day GLACIER_IR lifecycle |
| Secrets Manager | `fortress/p1/db-password`, `fortress/p1/redis-token`, `fortress/pipeline1/app-config` | Rotation **disabled** since 2026-05-13 — AWS-managed `PostgreSQLSingleUser` rotation broke Aurora; CFN block commented out. Manual rotation only until re-enabled. |
| SSM Parameter Store | 5 static params | Aurora host/port, Redis host, DB name, S3 bucket |

### 4.3 Compute layer (`pipeline1-compute.yml`)

| Task definition | Sizing | Purpose |
|---|---|---|
| `EcsTaskDefP1` (API) | 2 vCPU / 4 GB | Long-running uvicorn — `/api/sync/*` + `/health` |
| `EcsTaskDefScraperP1` | 2 vCPU / 8 GB | One-shot `python3 -m scripts.run_scrape` |
| `EcsTaskDefInitP1` | 0.5 vCPU / 1 GB | One-shot Alembic + bootstrap schema |

**Edge stack:** ALB (`api.fortresslabs.dev`) → HTTPS:443 → Target Group :8000 → ECS API.
HTTP:80 → 301 → HTTPS.

**WAFv2 Web ACL — 2 rules:**
1. **RateLimit** — 100 req / 300s, scoped to `/api/sync*` (per-IP)
2. **AWS Managed Common Rule Set** — OWASP-style baseline

**EventBridge Scheduler:**
- Name: `fortress-p1-daily-scrape-production`
- Cron: `cron(0 2 * * ? *)` — daily 02:00 UTC
- Target: scraper task definition
- Retry: up to 2

**IAM — `EcsTaskRoleP1` highlights:**
- `bedrock:InvokeModel` on `claude-sonnet-4-20250514` + `titan-embed-text-v2:0`
- `s3:PutObject/GetObject/ListBucket` on the P1 data bucket only
- `secretsmanager:GetSecretValue` on `fortress/p1/*` + `fortress/pipeline1/*`
- `cloudwatch:PutMetricData` scoped to namespace `Fortress/Scraper`

### 4.4 Security layer (`pipeline1-security.yml`)

- **GuardDuty** — Fargate runtime monitoring + RDS login events
- **Security Hub** — PCI DSS v3.2.1 + AWS Foundational Security Best Practices
- **CloudTrail** — multi-region trail, log file validation, S3-archived to KMS-encrypted bucket

---

## 5. Pipeline 2 — Bank Account

### 5.1 Network (`pipeline2-vpc.yml`)

- **VPC CIDR:** `10.2.0.0/16` (configurable to avoid bank overlap)
- **NAT Gateway EIP** — exported as a stack output and **must be added to the P1 WAF allowlist** before sync will work
- **Subnets + endpoints:** mirror P1 (2 AZs, 7 interface + 1 gateway endpoint)

### 5.2 Data layer (`pipeline2-data.yml`)

| Resource | Sizing | Notes |
|---|---|---|
| Aurora Serverless v2, PG 15.4 | min 0.5 / max 4 ACU | pgvector enabled — local hybrid search |
| ElastiCache Redis 7.1 | `cache.t4g.micro`, **2 nodes** | MultiAZ + auto-failover (banks demand HA) |
| KMS CMKs (2) | RDS, Redis | **Bank-owned** — Fortress has no key access |
| Secrets Manager (6 secrets) | DB password, Redis token, JWT secret, JIRA token + webhook secret, P1 API key | 90-day rotation enabled (DB password) |
| SSM Parameter Store | 7 params | Includes `PIPELINE1_API_URL` for sync target |

### 5.3 Compute layer (`pipeline2-compute.yml`)

| Task definition | Sizing | Purpose |
|---|---|---|
| `EcsTaskDef` | 1 vCPU / 2 GB | API service: validators + 6-agent pipeline |

- **ECS service** — DesiredCount 2, auto-scales 2 → 10 based on 70 % CPU
- **WAF** — 1000 req / 300s aggregate + Common Rule Set
- **CloudWatch alarms** — ALB p99 latency > 3 s
- **JIRA webhook flow** — JIRA → `POST /webhooks/jira` (HMAC-verified) → FastAPI `BackgroundTasks` → 6-agent pipeline. No queue between webhook and orchestrator; if the task dies mid-flight the workflow row is left in `RUNNING` and JIRA's webhook-retry logic re-delivers on next attempt.

**IAM — `EcsTaskRoleBank` highlights:**
- `bedrock:InvokeModel` on Claude Sonnet 4
- No `cloudwatch:PutMetricData` (Fortress-side observability stays in P1)

### 5.4 Security layer (`pipeline2-security.yml`)

Mirrors P1: GuardDuty, Security Hub (PCI + FSBP), CloudTrail — but **owned and audited by the bank's security org**.

---

## 6. Cross-pipeline sync — how P2 talks to P1

```
┌─────────────────────┐         ┌──────────────────────┐
│ BANK P2 (UpdateSvc) │         │ FORTRESS P1 (sync API)│
└─────────────────────┘         └──────────────────────┘
        │                                  ▲
        │  POST /api/sync/postgres         │
        │  X-Customer-Id: firstnational    │
        │  X-Api-Key: <64-hex>             │
        │  body: {from_version: 41}        │
        │                                  │
        ▼                                  │
   NAT Gateway (bank)         ────────▶ ALB :443 (WAF allowlist)
        │                                  │
        └──────────► Public Internet ──────┘
                    (TLS 1.2+, ALB ACM cert)
```

**Auth chain (every request):**
1. WAF — source IP must be in `fortress-p1-waf-allowlist-production`
2. ALB → ECS — request reaches FastAPI
3. `ClientCredentialService.validate()` — `SHA-256(X-Api-Key)` matches `client_credentials.api_key_hash` AND `is_active=true`
4. On any failure → `401 Unauthorized` (no leak about which check failed)

**11 sync endpoints:**

```
POST /api/sync/postgres        rule diffs since version N
POST /api/sync/embeddings      rules + 1024-dim vectors
POST /api/sync/initial         first-time paginated dump
POST /api/sync/obligations     extracted obligation sentences
POST /api/sync/deadlines       upcoming filing deadlines
POST /api/sync/change-records  detected rule mutations
POST /api/sync/risk-scores     enforcement risk per rule
POST /api/sync/benchmarks      FFIEC peer benchmarks
POST /api/sync/ofac-sdn        SDN list snapshot
POST /api/sync/counts          row-count summary (cheap health check)
POST /api/license/verify       API key validation
```

---

## 7. Application stack

| | P1 | P2 |
|---|---|---|
| **Language / runtime** | Python 3.11-slim | Python 3.11-slim |
| **Web framework** | FastAPI + uvicorn | FastAPI + uvicorn |
| **ORM / DB driver** | SQLAlchemy 2 async + asyncpg | SQLAlchemy 2 async + asyncpg |
| **Migrations** | Alembic — 19 versions (002 → 020). Latest: `020_change_records_tz` (RegulatoryChangeRecord timestamps → `TIMESTAMPTZ`). | Same chain — P2 applies subset |
| **Container image base** | `python:3.11-slim` | `python:3.11-slim`, **multi-stage strip** |
| **Vector store** | Aurora pgvector + tsvector hybrid | Same |
| **AI client** | `boto3 bedrock-runtime` | Same |
| **Auth (sync)** | `client_credentials` table + SHA-256 | `X-Api-Key` header outbound |
| **Auth (UI / agents)** | n/a | JWT (HS256) signed with SecretJwtSecret |

**Multi-stage Dockerfile.pipeline2** strips proprietary code before publishing to the bank's ECR — removes `main_pipeline1.py`, `connectors/`, `scrapers/`, `langgraph/`, etc. The bank only ever runs a redacted subset.

### 7.0 ComplianceRuleResolver — 4-tier lookup (P2)

Every rule resolution in the agent pipeline walks four tiers in order; the first match wins and is cached upward:

| Tier | Source | Latency | When used |
|---|---|---|---|
| 1 | Postgres `compliance_rules` (structured threshold) | <10 ms | Authoritative — if `threshold_amount` is set, flagged immediately |
| 2 | Redis cache (1 h TTL) | <2 ms | Repeat queries within the hour |
| 3 | **pgvector hybrid search** — `0.7 × cosine(embedding) + 0.3 × ts_rank(tsv)` over `compliance_rules`, top-3 | ~30 ms | New / emerging rules or rules without structured threshold; query embedded with Bedrock Titan v2 at request time, falls back to pure ts_rank if Bedrock is unreachable |
| 4 | Hardcoded `_FALLBACK_THRESHOLDS` | <1 µs | Last resort (BSA/CTR/SAR constants from `CLAUDE.md`) |

Tier-3 hits whose generated tests pass validation are **promoted**: the proven test code is written back to `compliance_rules.test_template` so the next JIRA story benefits from the template. (Previously this tier called `weaviate_client.search_compliance_rules`; Weaviate is fully removed from the lookup path as of 2026-05-14.)

### 7.1 Regulatory connectors (20)

| Federal (15) |
|---|
| `occ_bsa` · `fdic` · `cfpb` · `fincen_sar` · `ofac_sdn` · `cra` · `ffiec_call_report` · `ffiec_bsa` · `ffiec_cybersecurity` · `irr` · `cre_concentration` · `liquidity_risk` · `cecl` · `peer_benchmarks` · `operational_risk` |

| State (5) |
|---|
| `nydfs` · `ca_dfpi` · `tx_dob` · `fl_ofr` · `il_idfpr` |

### 7.2 P2 six-agent pipeline (JIRA story → tested code)

```
JIRA Story → POST /webhooks/jira ──┬→ 1. Discovery       (parse story, classify risk)
  (HMAC-verified, BackgroundTasks) │   ▼
                                   │ 2. Understanding    (pgvector + BM25 hybrid search)
                                   │   ▼
                                   │ 3. Framework        (select test framework)
                                   │   ▼
                                   │ 4. Generation       (Bedrock Claude Sonnet 4)
                                   │   ▼
                                   │ 5. Validation       (compliance rule check)
                                   │   ▼
                                   │ 6. Execution        (sandbox pytest + JIRA post)
```

---

## 8. Data flow — daily scrape cycle

```
02:00 UTC  EventBridge Scheduler
   │
   ▼
ECS RunTask → EcsTaskDefScraperP1 (one-shot)
   │
   ▼
ScraperScheduler._run_scrape_cycle()
   │
   ├─▶ ConnectorManager.run_all()    22 connectors, federal + state
   │         │
   │         ├─ ObligationExtractor (Bedrock Claude Sonnet 4)
   │         └─ EnforcementRiskScorer
   │
   ├─▶ Upsert to Aurora              hash-compare → changed rules only
   ├─▶ Bedrock Titan v2 embed        only new/changed rule_ids (delta)
   ├─▶ pgvector HNSW index updated
   ├─▶ S3 raw-chunk dump             scraper-chunks/{type}/{date}/
   ├─▶ DeadlineTracker.refresh()
   ├─▶ ChangeManager.detect_and_record()
   └─▶ SyncVersion.increment()       bank pulls next morning
```

**Per-connector observability (v5.3):**
- success → CloudWatch metric `Fortress/Scraper/ConnectorSuccess`
- failure → CloudWatch metric `Fortress/Scraper/ConnectorFailure` + S3 audit log + structured ERROR log

---

## 9. Observability & alerting

| Signal | Source | Alarm | Action |
|---|---|---|---|
| Per-connector failure | `Fortress/Scraper/ConnectorFailure` | `fortress-scraper-connector-failure-production` — Sum ≥ 1 / 24h | Email → jamie@fortresslabs.dev |
| Scraper didn't run | `Fortress/Scraper/ConnectorSuccess` | `fortress-scraper-no-run-production` — Sum < 1 / 25h, TreatMissingData=breaching | Email → jamie@fortresslabs.dev |
| ECS API CPU | `AWS/ECS CPUUtilization` | `fortress-p1-ecs-cpu-high-production` — > 80 % / 5 min | Email |
| ALB p99 latency (P2) | `AWS/ApplicationELB TargetResponseTime` | p99 > 3s | Email |

All alarms publish to `fortress-p1-alarms-production` SNS → confirmed email subscription.

**Metric emission detail — dual-publish pattern.** CloudWatch treats `MetricName + Dimensions=[Connector]` and `MetricName + Dimensions=[]` as **independent series**. `scraper_scheduler._log_connector_success` / `_log_connector_failure` therefore emit each success/failure twice: once with the `Connector` dimension (per-connector dashboards) and once dimensionless (the heartbeat the no-run alarm actually watches). Removing the dimensionless data point causes the no-run alarm to stay in ALARM forever even when scrapes are succeeding.

---

## 10. CI/CD & deployment

- **Source of truth:** GitHub — branch `fortress_v1` (current dev), merges to `main`
- **CI:** `.github/workflows/ci-cd.yml` — ruff + black + py_compile + alembic migrate + schema-check on every PR
- **CD:** `make push-p1` → ECR; `make deploy-p1` → ECS rolling update
- **Image tags:** semantic — `v5.x` (latest `v5.3`)
- **Stack deploy:** `infra/scripts/setup-p1-auto.sh` — orchestrates 8 CloudFormation steps via Claude CLI
- **Bank onboarding:** `scripts/register-bank.sh` — creates client_credentials row, writes raw key to bank's Secrets Manager, registers bank NAT EIP in P1 WAF

---

## 11. Cost & scaling posture (pilot)

| | Pilot (today) | GA target |
|---|---|---|
| P1 NAT Gateways | 1 | 2 (HA) |
| P1 Aurora capacity | 0.5–2 ACU | 0.5–8 ACU |
| P1 Redis nodes | 1 | 2 + multi-AZ |
| P1 API tasks | 0 desired → 1 on schema bootstrap | 2 + auto-scale |
| Secret rotation | disabled — manual (PostgreSQLSingleUser rotation broke Aurora 2026-05-13) | enabled (90-day) once rotation Lambda fixed |
| Bedrock spend driver | ~200K rules embedded daily | S3-diff approach: embed only changed (~5 % daily) |

The single biggest near-term cost optimization is the **S3-diff approach** (see `CLAUDE.md`): stage 1 dumps raw scrapes to S3 with a SHA-256 manifest, stage 2 embeds only files whose hash changed. Projected 85–90 % reduction in Bedrock daily spend.

---

## 12. Source of truth for everything else

- **Codebase quick map:** `CLAUDE.md` § "Architecture quick map"
- **CloudFormation:** `infra/cloudformation/pipeline{1,2}-{vpc,data,compute,security}.yml`
- **Migrations:** `alembic/versions/`
- **Setup runbook (P1):** `infra/setup/PIPELINE1_SETUP.md` + `infra/scripts/setup-p1-auto.sh`
- **Bank onboarding:** `scripts/register-bank.sh`
- **Local dev:** `Makefile` (run `make help`)
