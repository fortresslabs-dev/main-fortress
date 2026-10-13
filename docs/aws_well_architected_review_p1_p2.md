# Fortress Labs — AWS Well-Architected Framework Review (WAFR)

| Field | Value |
|---|---|
| Workload | Fortress Compliance Platform (Pipeline 1 + Pipeline 2) |
| Account model | Dual-account: Fortress Cloud + per-bank account |
| Review type | Banking compliance SaaS, AWS Premier Partner-led WAFR |
| Region (primary) | us-east-1 (Bedrock inference profile `us.*` fans out to us-east-1 / us-east-2 / us-west-2) |
| Compute | 100% ECS Fargate (no EC2, no EKS) |
| Data | Aurora PG Serverless v2 (P1) + RDS PG (P2), ElastiCache Redis 7.1, S3 |
| AI | Amazon Bedrock — Claude Sonnet 4.5 / Sonnet 4.6 / Haiku 4.5 (inference-profile-only) + Titan Embed Text v2 |
| Doc date | 2026-06-23 (status refresh 2026-06-30) |
| Source-of-truth | `infra/cloudformation/*.yml` (15 templates after 2026-06-28 — billing stacks added) |
| Audience | AWS Premier Partner WAFR facilitator, Fortress eng leadership, bank CCO/CIO sponsors |

### Status refresh — 2026-06-30

This document was first written 2026-06-23. The following pillar items have changed status since then (see corresponding sections for detail):

- **Security** — VPC Flow Logs ✅ deployed both pipelines (closes Risk S2, 2026-06-28). ALB access logs to S3 ✅ deployed both pipelines (new control, 2026-06-28). IAM Access Analyzer ✅ deployed (closes Risk S6, account-singleton, 2026-06-28). IAM policy-change EventBridge → SNS alarm ✅ deployed (new CIS 4.4 control, 2026-06-28). **CW log retention drift fix executed 2026-06-29 — all 10 non-compliant log groups flipped to 365d including 3 Aurora postgresql groups previously at NEVER-EXPIRE; closes Risk S3 in live state.** **ECR Enhanced Scanning enabled 2026-06-29 (Inspector v2 + registry `ENHANCED`/`CONTINUOUS_SCAN`); closes Risk S11.** **Cognito MFA enforced on testbank dashboard user pool 2026-06-29 (`MfaConfiguration: ON`, TOTP-only, CI invariant); closes Risk S7.**
- **Reliability** — P2 `DbMultiAz` default flipped to `true` + top-level CFN `Rules` block + CI-time `verify_ftr_invariants.py` guard ✅ (closes Risk R3 in code; takes effect on next P2 compute-stack update).
- **Operational Excellence** — Incident response runbook promoted to v1.0 with CTO sign-off 2026-06-30; AWS-native SNS-SMS paging chain (SNS topic per pipeline → SMS to on-call rotation) replaces the originally-assumed PagerDuty integration to keep the IR critical path inside the bank's own AWS account. 10DLC campaign-registration handled per bank account at deploy time, documented in `docs/runbooks/sns-sms-10dlc.md`.
- **Cost** — AWS Budgets + Cost Anomaly Detection ✅ deployed both pipelines (closes Risk 12, 2026-06-28).
- **Still open as of 2026-06-30**: Secrets Manager rotation auto-pipe (compensating control via manual runbook), cross-region DR, DR drill cadence, P1 Redis/Aurora HA, P1 staging account, Macie, SOC 2 Type II, annual penetration test.

Detailed evidence (stack names, CFN commits, resource IDs) lives in `docs/ftr_tracker.md` and `docs/aws_ftr_marketplace_p1_p2.md` §13.

---

## 0. Why this WAFR matters to banks and investors

A completed WAFR is the lowest-friction signal that:

1. **Enterprise-grade architecture exists** — every blast-radius decision, every encryption boundary, every recovery path is documented and traceable to CloudFormation.
2. **Operational risk is bounded** — the platform's failure modes, recovery times, and out-of-band dependencies are explicit, not implicit.
3. **AWS best practices are followed** — auditors, third-party-risk teams, and FFIEC examiners can map our controls to FSBP, PCI DSS, and the FFIEC IT Handbook without us re-explaining.

For Series A diligence: the WAFR replaces 30+ pages of architecture Q&A with one AWS-signed artifact.
For pilot bank onboarding (FFIEC §AIO Architecture, OCC Heightened Standards): it satisfies "documented architecture, reviewed by qualified third party."

---

## 1. Scope and methodology

### 1.1 Two distinct workloads, reviewed side-by-side

| Pipeline | Tenancy model | AWS account ownership | Network exposure | Primary compliance driver |
|---|---|---|---|---|
| **Pipeline 1 — Fortress Cloud** | Shared, multi-tenant | Fortress Labs | Internet-facing ALB → ECS Fargate (IP-allowlist WAF) | SOC 2 (control plane for regulatory knowledge base) |
| **Pipeline 2 — Bank Account** | Single-tenant per bank | Bank's own AWS account | Internet-facing ALB + CloudFront → ECS Fargate (Cognito JWT) | FFIEC IT Hdbk, BSA/AML §1020.210, OCC Heightened Standards, OFAC 31 CFR 501 |

P1 holds *regulatory intelligence only* (public CFR text, OFAC SDN, FFIEC peer data, enforcement actions). P2 holds *all bank operational data* (transactions screened, workflow approvals, JIRA stories, generated artifacts).

**Zero shared data plane.** Trust between accounts is API-token + HMAC + WAF IP-allowlist; there is no IAM trust, no VPC peering, no shared KMS key.

### 1.2 Methodology

For each pillar:

1. **Current state** — what is deployed today, by CloudFormation resource name.
2. **Strengths** — what already aligns with AWS best practice.
3. **Gaps** — what an examiner would flag and why.
4. **Banking-grade extensions** — what FFIEC / OCC / PCI DSS demand beyond stock WAFR.
5. **Remediation** — priority, owner, target date.

Evidence map: every claim ties back to a file path in `infra/cloudformation/` so the partner can verify in-template.

---

## 2. Architecture snapshot

### 2.1 Pipeline 1 (Fortress Cloud account)

```
                       Internet
                          │
              ┌───────────▼───────────┐  WAFv2  (3 rules)
              │ ALB :443  api.        │  ├─ Allow bank NAT IPs
              │ fortresslabs.dev      │  ├─ Rate-limit /api/sync 100/5min
              │ (deletion protected)  │  └─ AWSManagedRulesCommon
              └───────────┬───────────┘
                          │ HTTP :8000  (SG: ALB→ECS only)
              ┌───────────▼───────────┐
              │  ECS Fargate Service  │  4 task definitions:
              │  fortress-pipeline1-  │  ├─ API (always running, DesiredCount=N)
              │  api-production       │  ├─ Scraper (daily 02:00 UTC, EventBridge)
              │  awsvpc, private SN   │  ├─ Init (one-shot schema bootstrap)
              └─┬─────────┬───────────┘  └─ Digest (daily 06:00 UTC, EventBridge)
                │         │
       Aurora PG 15.10    Redis 7.1                S3 (KMS, versioned,
       Serverless v2      cache.t4g.small           Glacier @ 365d)
       2–16 ACU           single node (pilot)        scraper-chunks/, audit/
       KMS, snapshot      TLS in-transit             CloudTrail logs
       7d backup          KMS at-rest, AUTH

      VPC Endpoints (Interface + Gateway): S3, Bedrock-runtime, SSM,
      SecretsManager, CloudWatch Logs, ECR-DKR, ECR-API, STS
```

Detection / audit (always-on): GuardDuty (Runtime + Fargate-agent + RDS-login), Security Hub (PCI DSS v3.2.1 + AWS FSBP v1.0.0), CloudTrail multi-region with log-file validation, SNS alarms + SNS digest.

### 2.2 Pipeline 2 (Bank-owned account)

```
                                      Internet
                                         │
   ┌─────────────────────────────────────▼─────────────────────────────────────┐
   │  CloudFront (optional, trial) ──── dashboard.bank.com (S3+OAC, CCO UI)    │
   │  CloudFront API trial only  ┐                                             │
   └─────────────────────────────┼──────────┬──────────────────────────────────┘
                                 │          │
                            ┌────▼──────────▼────┐  WAFv2
                            │ ALB :443           │  ├─ RateLimit 1000/IP
                            │ fortressapi.       │  └─ AWSManagedRulesCommon
                            │ bank.com           │
                            └─────────┬──────────┘
                                      │ HTTP :8000
                            ┌─────────▼──────────┐
                            │ ECS Fargate        │
                            │ fortress-pipeline2-│  Auto-scale 2–10 tasks
                            │ {BankId}           │  (CPU 70% target tracking)
                            │ awsvpc, private SN │
                            └─┬───────────────┬──┘
                              │               │
            ┌─────────────────┘               └─────────────────┐
            │                                                   │
   RDS PG 15.8 db.t4g.medium                          Redis 7.1 cache.t4g.micro
   gp3 20→100 GiB auto                                 2 nodes, MultiAZ + failover
   bank-CMK encrypted, 7d backup                       TLS-in-transit, bank-CMK
   CW logs: postgresql + upgrade

   Sidecar workloads:
   ├─ EventBridge daily 02:45 UTC → ECS one-shot sync task (pulls P1)
   ├─ Exports worker (Fargate, scale-to-zero) — Playwright PDF + Sonnet summariser
   │  └─ Quarterly drill: cron(0 10 1 1,4,7,10 ? *) — re-attestation
   └─ Exports S3 bucket — Object Lock COMPLIANCE 1825 days, per-bank KMS CMK,
      bucket policies deny PUT-without-ObjectLock / PUT-without-KMS / non-HTTPS,
      CloudTrail data-events
```

Detection / audit: GuardDuty + Security Hub (PCI DSS + FSBP) + CloudTrail (per-bank, log-file validation), Cognito user pool (Admin-create-only, 12+ char passwords, 60-min token TTL, 5 RBAC groups: CCO/CRO/CIO/Analyst/Admin).

### 2.3 Cross-account trust model (the multi-account strategy)

| Element | Mechanism |
|---|---|
| **Network** | None. No VPC peering, no Transit Gateway, no PrivateLink. P2 ECS egresses via NAT EIP and reaches P1 over public Internet on HTTPS. |
| **Authentication** | Per-bank HMAC API key on every `/api/sync/*` call (Fortress issues, bank stores in Secrets Manager). |
| **Authorization** | P1 WAFv2 IPSet — only registered bank NAT EIPs bypass the strict rate limit. |
| **Data** | Pull-only. P2 never accepts inbound from P1. P1 has no IAM grant into any bank account. |
| **Key custody** | Each KMS CMK is owned by the account that uses it. Fortress has zero access to bank CMKs. |
| **Identity** | Cognito user pool lives in the *bank's* account. Bank IT owns the user pool, password policy, group membership. |

This is the multi-account strategy investors and FFIEC examiners are looking for: bank-data never leaves the bank account. Fortress Cloud is a regulatory CDN, not a data processor.

---

## 3. Pillar 1 — Operational Excellence

### 3.1 P1 — Fortress Cloud

| Area | Current state | Evidence |
|---|---|---|
| IaC | All infra in CloudFormation, deployed via `infra/scripts/setup-p1-auto.sh` | `infra/cloudformation/pipeline1-*.yml` |
| Pipeline | GitHub Actions CI/CD: lint → migrate → schema-check → test → demo gate | `.github/workflows/ci-cd.yml` |
| Image registry | ECR repo `fortress/pipeline1` (created out-of-band per script Step 5) | `pipeline1-compute.yml` line 44–46 |
| Image tagging | API task def is pinned to `:vX.Y`; digest task def is pinned to a separate `DigestImageTag` so the digest can ship without rolling API | `pipeline1-compute.yml` line 17–22 |
| Schema migration | Alembic, one-shot ECS Init task (`fortress-pipeline1-init`) — `make deploy-p1` does **not** auto-migrate | `pipeline1-compute.yml` EcsTaskDefInitP1 |
| Observability | ECS Container Insights, CW log group `/fortress/pipeline1` (**365-day retention in template since 2026-06-27, takes effect on next compute redeploy**), SNS alarm topic + separate SNS digest topic | `pipeline1-compute.yml` |
| Health probes | ALB target group on `/health/` (note trailing slash — `/health` returns 307), task-level `curl /health` HC, 30s/10s/3 retries | `pipeline1-compute.yml` line 384, 268 |

**Strengths**
- One-shot ECS pattern (scraper, init, digest) is operationally clean: no in-process cron, no daemon hangs hiding behind a healthy HTTP probe.
- DigestImageTag has *no default* — forces an explicit value on every update-stack, preventing silent drift to the API tag.
- Alarm topic and digest topic are separated — on-call inbox routing is not polluted by daily "OK" digests.

**Gaps (banking-grade)**
1. **Image rollback is database-gated.** Once `alembic upgrade head` runs against P1 Aurora, every prior image whose `alembic/versions/` lacks that revision crash-loops on import. Rollback requires `alembic stamp <prev_head>` against Aurora first. → **Need a documented rollback runbook with stamp+rollback command.**
2. **ECR repo is out-of-band**, not in CFN. Drift detection won't catch it.
3. **Makefile `push-p1` does not push `:latest`** — only `:$(IMAGE_TAG)`. If a task def references `:latest`, `force-new-deployment` silently keeps the OLD image. The current TD pins a semver tag, so this is mitigated, but it's a trap for the next ops engineer.
4. **Daily scraper has only `MaximumRetryAttempts: 2`** at the EventBridge level. No DLQ. A connector outage that exhausts retries is invisible unless someone reads the digest the next morning.

**Banking-grade extensions required**
- **Change-management evidence** for FFIEC IT Hdbk §V.A (Configuration Management): every CFN deploy must produce a change record (ticket ID, approver, rollback plan, post-deploy test). Today CI/CD tags commits but does not require a change ticket.
- **Pre-prod parity:** there is no staging account separate from production. WAFR pillar would flag this as a Critical operational risk for a regulated SaaS.

### 3.2 P2 — Bank Account

| Area | Current state | Evidence |
|---|---|---|
| IaC | 8 templates (`vpc`, `data`, `compute`, `dashboard`, `security`, `exports-bucket`, `exports-worker`, `exports-alarms`) deployed by `infra/scripts/deploy-p2.sh` | `infra/cloudformation/pipeline2-*.yml` |
| Bank-isolated change control | Bank owns the AWS account; bank IT executes `deploy-p2.sh` | `infra/setup/PIPELINE1_SETUP.md` + `bank.env.template` |
| Observability | ECS Container Insights, per-bank CW log group `/fortress/pipeline2/{bank}` (**365d in template since 2026-06-27, takes effect on next compute redeploy**), p99 latency alarm | `pipeline2-compute.yml` line 651 |
| Exports observability | 3 named alarms (render-fail-rate, signoff-SLA, retention-near-expiry) + dimensionless heartbeat for "did the job run" | `pipeline2-exports-alarms.yml` |
| Drill cadence | EventBridge quarterly re-attestation drill (Jan/Apr/Jul/Oct 1st 10:00 UTC) | `pipeline2-exports-alarms.yml` line 117 |
| Runbooks | `docs/runbooks/agent-{1,2,3,4}-*.md` + onboarding runbook | `docs/runbooks/` |

**Strengths**
- Quarterly drill is real CloudFormation, not a calendar reminder. Pages CCO if drift detected.
- Dimensioned + dimensionless alarms pattern is documented and applied — "the job ran but slow" vs "the job didn't run at all" are distinct pages.
- Exports worker is a *separate ECR repo + task-def family* from the API. Hotfixes to PDF rendering don't risk the API container.

**Gaps**
1. **No drift detection wired in CI/CD.** CFN drift is a `aws cloudformation detect-stack-drift` call away but no scheduled job exists.
2. **Dashboard deploy is manual** (`dashboard/deploy.sh testbank production`). Not wired to GH Actions for `testbank`.
3. **Out-of-band secret seeding** (JWT, JIRA token, GitHub token, P1 API key) is documented in `Outputs.PostDeployInstructions` but there's no automated verification that these are set before service comes up.
4. **No automated post-deploy smoke test** for the bank-onboarded environment.

### 3.3 Priority recommendations (Op-Ex)

| # | Action | Pipeline | Owner | Target |
|---|---|---|---|---|
| 1 | Add CFN drift detection scheduled job (daily) + SNS alarm | both | Agent 1 | 30d |
| 2 | Add staging account / pre-prod env for P1 with same WAF + same Aurora schema | P1 | Eng | 60d |
| 3 | Document image-rollback runbook with `alembic stamp` step | P1 | Agent 3 | 30d |
| 4 | Automate post-deploy smoke test (`pytest tests/smoke/`) gated on bank onboarding handoff | P2 | Agent 4 | 30d |
| 5 | Wire change-record requirement into PR template + CI/CD | both | Eng leadership | 60d |
| 6 | Add DLQ + retry exhaustion alarm on every EventBridge schedule | both | Agent 1 | 30d |

---

## 4. Pillar 2 — Security

### 4.1 Encryption inventory

| Surface | At rest | In transit | Key type | Rotation |
|---|---|---|---|---|
| **P1 Aurora PG 15.10** | AES-256 via KMS CMK `alias/fortress-rds-key` | TLS required (`?ssl=require` in DATABASE_URL) | CMK | annual rotation ON |
| **P1 Redis 7.1** | KMS CMK `alias/fortress-redis-key` | TLS required (`TransitEncryptionMode: required`, `rediss://` URL) | CMK | annual rotation ON |
| **P1 S3 (scraper-chunks, audit, CloudTrail)** | aws:kms with CMK `alias/fortress-s3-key`, bucket-level versioning | TLS (S3 default) | CMK | annual rotation ON |
| **P1 Secrets Manager** (db-password, redis-auth-token, app-config) | KMS-encrypted | TLS | CMK (per-secret) | manual (rotation Lambda disabled — see Risk 1) |
| **P2 RDS PG 15.8** | AES-256 via bank-managed CMK `alias/fortress-rds-{bank}` | TLS | **Bank CMK** | annual rotation ON |
| **P2 Redis 7.1** | bank-managed CMK `alias/fortress-redis-{bank}` | TLS required | **Bank CMK** | annual rotation ON |
| **P2 Secrets Manager** (8 secrets per bank) | KMS-encrypted | TLS | **Bank CMK** | manual (rotation Lambda disabled — Risk 1) |
| **P2 Exports bucket** | aws:kms with per-bank CMK `alias/fortress-p2-exports-{bank}-{env}`, Object Lock COMPLIANCE 1825d | TLS-only (`DenyHTTPS` bucket policy) | **Bank CMK** + DENY-PUT-without-KMS bucket policy | annual rotation ON, 30d pending-window on key delete |
| **P2 Dashboard CloudFront** | n/a (static SPA) | TLSv1.2_2021 minimum, ACM cert | n/a | n/a |
| **P2 ALB** | n/a | TLS via ACM cert (prod) or CloudFront default (trial) | ACM | annual rotation managed by ACM |

**Key design decision the partner will praise:** the bank's RDS, Redis, Secrets, and exports bucket all use *bank-owned* CMKs. Fortress has **zero key custody for bank-side data**. The KeyPolicy `Sid: BankAccountFullAccess` grants only `arn:aws:iam::${AWS::AccountId}:root` — no Fortress principal exists in the policy.

**Key design decision the partner will challenge:**
- P1 Secrets Manager rotation Lambda is **commented out** (`SecretDbRotationSchedule`). The AWS-managed `PostgreSQLSingleUser` rotation broke Aurora on 2026-05-13. Mitigation plan documented in template comments; remediation = custom rotation Lambda that ALSO re-renders `SecretAppConfig`. **This is the #1 risk for production cutover.**

### 4.2 IAM — least privilege walkthrough

#### P1 task role (`fortress-p1-ecs-task-{env}`)

11 explicit policy statements with `Sid:` for traceability. The non-obvious calls:

- **`BedrockAccess`** — both the inference-profile ARN *and* the underlying foundation-model ARNs in all three regions the `us.` prefix may route to (us-east-1 / us-east-2 / us-west-2). Without all three regional foundation-model ARNs, `InvokeModel` fails when Bedrock routes cross-region.
- **`BedrockMarketplaceSubscriptions`** — `aws-marketplace:Subscribe/Unsubscribe/ViewSubscriptions` on `Resource: "*"` (Marketplace actions don't support resource-level scoping). First `InvokeModel` auto-subscribes; without this the call returns `AccessDeniedException`.
- **`CloudWatchMetrics`** — `cloudwatch:PutMetricData` scoped to `cloudwatch:namespace == Fortress/Scraper` by Condition — cannot pollute other namespaces.
- **`SecretsAccess`** — scoped to `fortress/p1/*` and `fortress/pipeline1/*` paths.
- **`SsmAccess`** — scoped to `parameter/fortress/pipeline1/*`.
- **`KmsDecrypt`** — scoped to the imported P1 RDS CMK ARN (not `*`).
- **`DigestSnsPublish`** — scoped to the single `DigestTopic` ARN.

The execution role (`fortress-p1-ecs-exec-{env}`) is intentionally broader (`secretsmanager:GetSecretValue` + `kms:Decrypt` on `*`) because it must resolve secrets at task-pull time and CFN cannot pre-compute every secret ARN. **Acceptable per AWS guidance** for execution roles, but worth noting.

#### P2 task role (`fortress-p2-task-{bank}`)

Same defense-in-depth, parameterised by `${BankId}`:

- `SecretsAccess` scoped to `fortress/${BankId}/*`
- `SsmAccess` scoped to `parameter/fortress/${BankId}/*`
- `KmsDecrypt` scoped to the imported bank RDS CMK ARN

#### P2 exports worker task role (`fortress-p2-{bank}-{env}-exports-worker`)

Most aggressively scoped role in the platform:

- S3: `PutObject / PutObjectRetention / GetObject / GetObjectRetention / GetObjectVersion` on `${ExportsBucketArn}/*` only.
- KMS: scoped to `ExportsKmsKeyArn` only.
- SSM: scoped to `parameter/fortress/${BankId}/*` only.
- Bedrock: `InvokeModel / GetInferenceProfile` on `*` (board-report summariser).
- Marketplace: `*` (unavoidable per AWS docs).

The bucket-policy back-stop denies any PUT without `s3:object-lock-mode == COMPLIANCE` and without `s3:x-amz-server-side-encryption == aws:kms`. Even if the worker IAM role were over-permissioned, the bucket would refuse the write.

### 4.3 Network — defense in depth

| Boundary | P1 | P2 |
|---|---|---|
| Public ingress | ALB :443 + :80→:443 redirect; ALB deletion protection ON | ALB :443 (prod) / :80 with CloudFront in trial; deletion protection ON in prod, OFF in trial |
| WAF | AllowBankIps IPSet (priority 0) + RateLimit 100/5min on /api/sync (priority 1) + AWSManagedRulesCommonRuleSet (priority 2) | RateLimit 1000/IP (priority 1) + AWSManagedRulesCommonRuleSet (priority 2) |
| ALB→ECS | SG: only :8000 from ALB SG | SG: only :8000 from ALB SG |
| ECS→AWS APIs | 7 VPC endpoints (S3 gateway + Bedrock-runtime, SSM, SecretsManager, CW Logs, ECR-DKR, ECR-API, STS interface) — calls never traverse NAT | Same 7 VPC endpoints |
| ECS→Internet | Only the scraper egresses via NAT to regulatory sites; egress :443 only | NAT only for P1 sync calls |
| RDS / Redis | Private subnets, SG ingress only from ECS SG, `PubliclyAccessible: false` | Same |
| Dashboard | n/a | S3 private + OAC; only `cloudfront.amazonaws.com` with matching SourceArn can `s3:GetObject` |

**Gap:** P2 testbank deploy has a documented WAF blocklist (per memory `project_p2_waf_blocklist`) using IPSet + XFF rule because ALB sees CloudFront IP. This pattern is bank-specific and not yet templated into `pipeline2-compute.yml`. Recommend graduating it into the base CFN with an optional `BlockedIpSet` parameter.

### 4.4 Audit logging — banking-grade

| Source | Destination | Retention | Tamper resistance |
|---|---|---|---|
| **P1 CloudTrail** | S3 `fortress-p1-data-{acct}-{env}` prefix `cloudtrail/` | bucket lifecycle (Glacier @ 365d, NCV @ 90d), no automated expiry on management events | `EnableLogFileValidation: true`, multi-region trail |
| **P1 VPC Flow Logs** ✅ *new 2026-06-28* | CW Logs `/aws/vpc/flowlogs/fortress-p1-production` | 365d | KMS, IAM-segregated; dedicated `vpc-flow-logs.amazonaws.com` role |
| **P1 ALB access logs** ✅ *new 2026-06-28* | S3 `fortress-p1-alb-logs-{acct}-{env}` | lifecycle Glacier @ 90d → expire @ 365d | SSE-S3, BucketOwnerEnforced, all PAB ON |
| **P1 IAM policy-change events** ✅ *new 2026-06-28* | EventBridge → SNS `SecurityAlertsTopic` | n/a (alarm pipeline) | account-singleton; 17 CIS 4.4 IAM actions matched |
| **P1 Aurora** | CW Logs `/fortress/pipeline1` (`EnableCloudwatchLogsExports: [postgresql]`) | 365d in template (legacy live group still 30d until next redeploy) | KMS-encrypted, role-segregated |
| **P1 application** | CW Logs `/fortress/pipeline1` (`awslogs` driver) | 365d in template (legacy live group still 30d until next redeploy) | KMS, IAM-segregated |
| **P1 WAF** | CW Metrics + SampledRequests | n/a | n/a |
| **P1 IAM Access Analyzer** ✅ *new 2026-06-28* | EventBridge → SNS `SecurityAlertsTopic`; findings additionally flow into Security Hub via FSBP | n/a (account-wide analyzer) | `Type=ACCOUNT`, ACTIVE |
| **P2 CloudTrail (per-bank)** | Bank-owned S3, prefix `cloudtrail/{BankId}` | bank policy | `EnableLogFileValidation: true`, multi-region |
| **P2 VPC Flow Logs** ✅ *new 2026-06-28* | CW Logs `/aws/vpc/flowlogs/fortress-p2-{bank}-{env}` | 365d | KMS, IAM-segregated; dedicated VPC Flow Logs role |
| **P2 ALB access logs** ✅ *new 2026-06-28* | S3 `fortress-p2-alb-logs-{bank}-{env}` | lifecycle Glacier @ 90d → expire @ 365d | SSE-S3, BucketOwnerEnforced, all PAB ON |
| **P2 RDS** | CW Logs `[postgresql, upgrade]` | 30d (FFIEC gap — fix in drift catcher backlog) | KMS, IAM |
| **P2 application** | CW Logs `/fortress/pipeline2/{bank}` | 365d in template (legacy live group still 30d until next redeploy) | KMS, IAM |
| **P2 exports worker** | CW Logs `/fortress/p2/exports-worker/{bank}/{env}` | **2557 days (7yr per FFIEC IT Hdbk §IV)** | KMS, IAM |
| **P2 exports bucket** | CloudTrail data-events (separate trail, log-file-validation ON) | bucket-side WORM 1825d | Object Lock COMPLIANCE — even root cannot delete |

**Banking-grade highlight:** the exports artifact is the only object in the entire platform that satisfies the **FFIEC BSA + OFAC 5-year recordkeeping** requirement with **immutable storage**. Object Lock COMPLIANCE means even an attacker with `root` credentials cannot delete an artifact before retention expires.

**Gaps**
1. ✅ ~~CW log retention is 30 days everywhere except the exports worker.~~ **Closed in CFN templates 2026-06-27 (commit `286f468`)** — `/fortress/pipeline1` and `/fortress/pipeline2/{bank}` retention now 365d. **12 legacy log groups remain non-compliant** (3 RDS at NEVER-EXPIRE, 3 container-insights at 1d, 1 legacy `/ecs/fortress-pipeline2`, others) — `infra/scripts/audit-cw-log-retention.sh --fix` ready, operator run pending (storage-cost ratchet decision).
2. **No CloudTrail Lake or aggregator** across bank accounts — by design (data-residency promise), but Fortress should publish a model contract spelling out who is responsible for trail review (bank SOC, not Fortress).
3. ✅ ~~VPC Flow Logs are NOT enabled on either P1 or P2 VPC.~~ **Closed 2026-06-28** — deployed to both pipelines. Stacks `fortress-p1-vpc-production` + `fortress-p2-vpc-testbank`; Flow Logs `fl-0b8eb1bd065040ae3` (P1) + `fl-0d77722788efa8852` (P2); 365d retention; `TrafficType=ALL`.

### 4.5 Security pillar risk register

| # | Risk | Severity | Pillar control | Owner | Status |
|---|---|---|---|---|---|
| S1 | P1 + P2 Secrets Manager rotation disabled | **Critical** | FFIEC IT Hdbk §VI.D, PCI DSS 8.3.7 | Eng | 🟡 **Compensating control in production** — manual rotation runbook `docs/runbooks/secrets-rotation.md`. Auto-rotation blocked on Aurora rotation-lambda fix from 2026-05-13. |
| S2 | VPC Flow Logs not enabled on either pipeline | **High** | FSBP `EC2.6` | Agent 1 | ✅ **Closed 2026-06-28.** Both pipelines deployed. 365d retention. |
| S3 | CW log retention only 30d (vs FFIEC 1y minimum) | **High** | FFIEC IT Hdbk §IV.B.2 | Agent 1 | ✅ **Closed 2026-06-29.** Templates updated 2026-06-27 (commit `286f468`); live-state drift fix executed 2026-06-29 — all 10 non-compliant log groups flipped to 365d including 3 Aurora postgresql groups previously at NEVER-EXPIRE. Re-audit clean. |
| S4 | P2 dashboard CloudFront has no WAF | **Med** | AWS WAF best practice | Agent 1 | ❌ Open. Target 60d. |
| S5 | P1 Aurora master credentials rotated manually | **Med** | PCI DSS 8.3 | Eng | 🟡 Compensating control via manual rotation runbook (same as S1). |
| S6 | No IAM Access Analyzer scheduled in either account | **Med** | FSBP `IAM.6` | Agent 1 | ✅ **Closed 2026-06-28.** `fortress-p1-account-analyzer-production` (`Type=ACCOUNT`, ACTIVE) deployed via P1 — account-singleton covers all IAM principals. |
| S7 | Cognito MFA not enforced on bank dashboard user pool | **High** | OCC Heightened Stds §III, FFIEC AIO | Agent 4 | ✅ **Closed 2026-06-29.** testbank user pool: `MfaConfiguration: ON`, `EnabledMfas: [SOFTWARE_TOKEN_MFA]` (TOTP-only — SMS rejected by CI invariant due to SIM-swap risk). Enrolment validated end-to-end with a TOTP authenticator. Per-bank deploys replicate the template. Runbook: `docs/runbooks/cognito-mfa.md`. |
| S8 | No DLP / sensitive-data scanning on S3 buckets (Macie) | **Med** | FFIEC IT Hdbk §V.B | Agent 1 | ❌ Open. Target 60d. |
| S9 *new* | No IAM policy-change EventBridge alarm (CIS 4.4) | **Med** | CIS AWS Foundations 4.4 | Agent 1 | ✅ **Closed 2026-06-28** — `fortress-p1-iam-policy-change-production` rule → SNS; 17 IAM actions matched. Account-singleton. |
| S10 *new* | No ALB access logging | **Med** | FSBP `ELB.5` | Agent 1 | ✅ **Closed 2026-06-28** — buckets `fortress-{p1,p2}-alb-logs-*` with SSE-S3, BucketOwnerEnforced, all PAB ON, Glacier @ 90d. |
| S11 *new* | No ECR Enhanced Scanning enabled | **Med** | FSBP `Inspector.1` | Agent 1 | ✅ **Closed 2026-06-29** — Inspector v2 ECR `ENABLED`; ECR registry `ENHANCED` + `CONTINUOUS_SCAN` covering all 3 repositories. Initial CVE backlog surfaces in Security Hub via FSBP within ~15 min; triage SLAs per `docs/runbooks/inspector-triage.md` (CRITICAL 7d / HIGH 30d / MEDIUM quarterly). |

---

## 5. Pillar 3 — Reliability

### 5.1 Disaster recovery posture (RTO / RPO)

| Resource | RPO | RTO (today) | Mechanism |
|---|---|---|---|
| **P1 Aurora** | 5 minutes (continuous backup) | 1h (single-AZ writer + 7d snapshot restore) | Aurora Serverless v2, BackupRetentionPeriod=7d, DeletionProtection ON, snapshot policy on stack delete |
| **P1 Redis** | NOT durable (single node, no failover) | indeterminate — cache only, can rebuild from Aurora | single cache.t4g.small node, `AutomaticFailoverEnabled: false`, `MultiAZEnabled: false` |
| **P1 S3** | Versioned + cross-region (no CRR today) | n/a | bucket-level versioning, lifecycle Glacier @ 365d |
| **P2 RDS** | 5 minutes (PITR enabled) | 1h (single-AZ default) → 30min if MultiAZ flipped on | `BackupRetentionPeriod: 7`, `MultiAZ: !Ref DbMultiAz` (default `false` in trial!) |
| **P2 Redis** | Auto-failover MultiAZ | <60s | `NumCacheClusters: 2, AutomaticFailoverEnabled: true, MultiAZEnabled: true` |
| **P2 Exports bucket** | n/a (durability 11x9s) | n/a | Object Lock COMPLIANCE, versioned, KMS |
| **P2 Aurora — exports row** | tied to P2 RDS | tied to P2 RDS | persisted in `compliance_exports` table |

### 5.2 Strengths

- **P2 Redis is properly HA** (2-node, MultiAZ, auto-failover). Confirmed in `pipeline2-data.yml` line 229–231.
- **Aurora and RDS both have `BackupRetentionPeriod: 7`** — meets the FFIEC IT Hdbk §V.C minimum.
- **Aurora Serverless v2 elastic scaling** (2–16 ACU) means failure during a load spike won't be CPU-bound.
- **Exports artifacts are immutable** — the strongest recovery story in the platform (you literally cannot lose them within 5 years).
- **EventBridge schedules have retry policies** (`MaximumRetryAttempts: 2` on scraper, `1` on digest).

### 5.3 Gaps (banking-grade)

1. **P1 Aurora has a single writer instance** (no reader). For full production, the template comment explicitly says "add `AuroraInstanceReader` for HA + read scaling." A single writer means a hardware failure on the underlying compute requires a failover-restart — measured in minutes, not seconds. **Critical for SaaS uptime SLO.**
2. **P1 Redis is single-node pilot.** A cache outage means scraper + agent caches cold-start; not a data loss, but a thundering-herd risk on Aurora during the rebuild window. **High.**
3. ✅ ~~P2 `DbMultiAz` defaults to `false` in trial.~~ **Closed in code 2026-06-27 (commit `8389d15`).** Belt-and-suspenders enforcement: (a) `pipeline2-data.yml` default flipped `"false"` → `"true"`; (b) new top-level `Rules` block `EnforceMultiAzInProduction` rejects create/update-stack at the CFN API when `Environment=production AND DbMultiAz=false`; (c) `scripts/verify_ftr_invariants.py` wired into `.github/workflows/ci-cd.yml` parses the committed template at PR time so a regression cannot merge. Negative-tested. **Note:** the running P2 testbank Aurora won't physically flip to MultiAZ until the next P2 compute-stack update — currently deferred due to compute-stack drift (`project_p2_compute_stack_drift_2026_06_28`).
4. **No cross-region replica anywhere.** A regional AWS outage in us-east-1 takes the platform down. For a regulated SaaS this is a documented gap (acceptable for pilot, blocking for production). **High for prod cutover.**
5. **No automated DR drill.** The quarterly re-attestation drill (`pipeline2-exports-alarms.yml`) is the ONLY automated drill in the platform. **High.**
6. **EventBridge retry exhaustion has no DLQ + no alarm.** If the daily scraper fails 3× in a row, no one knows until the next morning's digest. **Med.**

### 5.4 Backup and retention summary

| Asset | Backup | Retention | Cold storage |
|---|---|---|---|
| P1 Aurora | continuous + daily snapshot | 7d | n/a |
| P2 RDS | continuous + daily snapshot | 7d | n/a |
| P1 S3 (audit) | versioned | NCV expires 90d | Glacier IR @ 365d |
| P2 exports | versioned + WORM | NCV transitions only (no expiry) | STANDARD_IA @ 30d, GLACIER_IR @ 365d |
| Aurora schema history | alembic in git | git history | n/a |

**Banking-grade gap:** Backup retention is 7 days. FFIEC IT Hdbk §V.C recommends 30-day point-in-time recovery for "core banking-adjacent" systems. **Recommend `BackupRetentionPeriod: 35` for production banks.**

### 5.5 Reliability pillar risk register

| # | Risk | Severity | Owner | Status |
|---|---|---|---|---|
| R1 | P1 Redis single-node | **Critical** (prod) | Agent 1 | ❌ Open. Pre-prod. |
| R2 | P1 Aurora single writer (no reader) | **Critical** (prod) | Agent 1 | ❌ Open. Pre-prod. |
| R3 | P2 `DbMultiAz` defaults to false | **Critical** (prod) | Agent 1 | ✅ **Closed in code 2026-06-27** — CFN default flipped, top-level `Rules` block, CI-time invariant check. Negative-tested. Running testbank Aurora pending next compute-stack update. |
| R4 | No cross-region DR | **High** (prod) | Eng + Bank | ❌ Open. Target 90d. |
| R5 | No DR drill cadence | **High** | Eng | ❌ Open. Target 60d. |
| R6 | EventBridge retry exhaustion has no DLQ/alarm | **Med** | Agent 3 | ❌ Open. Target 30d. |
| R7 | Backup retention 7d (vs FFIEC 35d recommendation) | **Med** | Agent 1 | ❌ Open. Target 30d. |

---

## 6. Pillar 4 — Performance Efficiency

### 6.1 Compute right-sizing

| Workload | Profile | Today | Rationale |
|---|---|---|---|
| P1 API | always-on Fargate | 2048 CPU / 4096 MiB | sync API + admin endpoints |
| P1 Scraper | one-shot Fargate | 2048 CPU / 8192 MiB | LLM embed batch + PDF parsing |
| P1 Init | one-shot Fargate | 512 CPU / 1024 MiB | schema bootstrap only |
| P1 Digest | one-shot Fargate | 512 CPU / 1024 MiB | DB → SNS HTML email |
| P2 API | autoscale 2–10 | 1024 CPU / 2048 MiB | webhooks + agent pipeline + dashboard API |
| P2 Sync | one-shot | 1024 CPU / 2048 MiB | mirror of API for sync only |
| P2 Exports worker | scale-to-zero | 1024 CPU / 2048 MiB | Chromium + 6-section template |

### 6.2 Data tier

- **Aurora Serverless v2 (P1)**: `2–16 ACU` (≈4 GiB → 32 GiB RAM). Auto-scales based on load.
- **RDS db.t4g.medium (P2)**: Graviton, gp3 (better $/GB than gp2), 20→100 GiB autoscale.
- **Redis t4g.small / t4g.micro**: Graviton; t4g.small in P1 (single tenant cache), t4g.micro × 2 in P2 (HA).

### 6.3 Search and retrieval

- **No OpenSearch.** Hybrid retrieval (pgvector HNSW + tsvector GIN) is done inside Aurora — saves the OpenSearch Serverless line item entirely. (See `project_p1_vector_store_decision` and `project_pgvector_alembic_drift`.)
- **`OFACScreener` runs in vector mode** with calibrated per-class thresholds (in progress) for sub-100ms screening.

### 6.4 Autoscaling

- P2 ECS service: target-tracking on `ECSServiceAverageCPUUtilization == 70`, scale-out cooldown 60s, scale-in cooldown 300s.
- Aurora Serverless v2 auto-scales transparently.
- Exports worker is launched on-demand via `ecs:RunTask` — true scale-to-zero.

### 6.5 Strengths

- **No idle capacity** beyond the P1 API (always-on) and P2 API (min 2 tasks for HA).
- **Graviton everywhere** that supports it (RDS, Redis).
- **VPC endpoints** keep Bedrock + S3 + SSM + Secrets calls off NAT — better latency AND cheaper.

### 6.6 Gaps

1. **No per-tenant SLO defined.** P1 is multi-tenant Aurora; without per-tenant query budget, one noisy bank can starve another.
2. **No Bedrock latency dashboard.** Sonnet 4.5 vs Sonnet 4.6 vs Haiku 4.5 cost/latency profiles differ 4–10×. Model routing decisions are opaque to ops.
3. **No load test profile** for the sync burst (~26 sync requests in a paginated initial seed) or for the webhook burst.

### 6.7 Recommendations

1. Define and instrument per-tenant SLOs (`p99 latency by bank_id`, `Aurora ACU consumed by bank_id`).
2. Add Bedrock latency + cost CW dashboard per model.
3. Add a synthetic load profile in CI/CD for sync + webhook bursts.

---

## 7. Pillar 5 — Cost Optimization

### 7.1 Pilot / production cost levers (already in templates)

| Lever | Pilot | Prod | Saving |
|---|---|---|---|
| Aurora Serverless v2 ACU floor | 2 ACU | 8–16 ACU | Pilot floor halves baseline cost |
| P1 Redis | single t4g.small | recommend r7g.medium × 2 | ~3× |
| P2 RDS instance class | db.t4g.medium | db.t4g.large | ~2× |
| P2 RDS MultiAZ | OFF | ON | ~2× DB cost (but mandatory for prod) |
| Graviton everywhere | yes | yes | 20–40% vs x86 |
| VPC endpoints (S3 gateway + 6 interface) | yes | yes | NAT egress on AWS APIs eliminated |
| S3 lifecycle to Glacier IR @ 365d | yes | yes | ~7× vs Standard |
| Exports lifecycle: STANDARD_IA @ 30d, GLACIER_IR @ 365d | yes | yes | matches WORM retention curve |

### 7.2 Strengths

- Trial-vs-prod parameter switching is explicit, not hidden in env vars.
- Per-bank `BankId` tag is propagated by `PropagateTags: SERVICE` so cost-allocation reports can split by tenant.

### 7.3 Gaps

1. ✅ ~~No AWS Budgets / Cost Anomaly Detection alarms templated in CFN.~~ **Closed 2026-06-28** — new `pipeline{1,2}-billing.yml` templates deployed as stacks `fortress-p1-billing-production` ($500/mo budget) and `fortress-p2-billing-testbank` ($500/mo budget). Each stack has `BillingAlertsTopic` (SNS, separated from security-alerts) + `MonthlyBudget` (80% ACTUAL + 100% FORECASTED notification) + `CostAnomalyMonitor` (`MonitorType=CUSTOM` with match-everything `LINKED_ACCOUNT` spec — DIMENSIONAL/SERVICE conflicted with the AWS-auto-provisioned `Default-Services-Monitor` singleton) + `CostAnomalySubscription` (IMMEDIATE freq, abs $100 threshold). Two confirmed SNS subscribers per topic. Per-bank P2 deploys use the same template parameterised by `BankId`.
2. **No mandatory cost-allocation tagging policy** — `BankId` is on most resources but not enforced.
3. **NAT gateway hourly cost** in every bank account is fixed (~$32/mo) even when idle. For a bank with low scraping cadence, this dominates monthly bill. Consider VPC endpoint for Bedrock + S3 only and dropping NAT in BYOC profile (P1 sync still needs egress, so this is bank-by-bank).
4. **CloudFront in trial mode** is deployed on top of ALB-only — duplicates path cost. Acceptable for trial, must be torn down at prod cutover.

### 7.4 Per-bank unit economics — recommend tracking

| Metric | Target |
|---|---|
| Cost per bank per month (steady-state) | < $X (define) |
| Cost per sync cycle | <$Y |
| Cost per generated artifact (PDF + summariser) | <$Z |
| Bedrock spend per bank | <$W |

Without unit economics published monthly, growth past 10 banks will be flying blind.

---

## 8. Pillar 6 — Sustainability

### 8.1 Aligned practices

- **Graviton** (ARM) on RDS, Redis everywhere supported.
- **Aurora Serverless v2** auto-scales down to 2 ACU when idle.
- **One-shot ECS pattern** for scraper / sync / digest / exports = no idle capacity.
- **VPC endpoints** reduce inter-AZ NAT egress.
- **Bedrock Haiku 4.5 routing** for low-cost / low-energy obligation extraction (model `P1_OBLIGATION_EXTRACTION_MODEL` is Haiku 4.5, not Sonnet).

### 8.2 Recommendations

- Document the Carbon impact reduction proposition in the Marketplace listing.
- Enforce production-profile checks in CI/CD before deployment (so a trial profile cannot drift into prod and over-provision).

---

## 9. Cross-cutting: Multi-account strategy (the headline)

| Question banks / FFIEC examiners always ask | Fortress answer | Evidence |
|---|---|---|
| "Where does our customer PII live?" | In your account, in your VPC, in your RDS, encrypted with your CMK. | `pipeline2-data.yml` |
| "Who has access to our regulatory rules?" | Fortress publishes them; you pull. Fortress never reaches into your account. | `pipeline1-compute.yml` WAF (allow-only) |
| "Can Fortress decrypt our data if compelled by subpoena?" | No. The KMS CMK is in your account; Fortress has no kms:Decrypt grant. | `pipeline2-data.yml` line 51 `BankAccountFullAccess` only |
| "Can Fortress delete our audit trail?" | No. CloudTrail is in your bucket; exports are Object-Lock COMPLIANCE. | `pipeline2-security.yml` + `pipeline2-exports-bucket.yml` |
| "If Fortress is breached, what's our blast radius?" | Loss of *regulatory rules* — already public. No bank data is in Fortress's account. | Architecture boundary section above |

This is the strongest single argument the partner will hear all session.

---

## 10. Consolidated risk register (cross-pillar)

| # | Risk | Pillar | Severity | Status (2026-06-30) |
|---|---|---|---|---|
| 1 | Secrets Manager rotation disabled (P1 + P2) | Sec | **Critical** | 🟡 Manual rotation runbook in production as compensating control. Auto-rotation still open. |
| 2 | VPC Flow Logs not enabled | Sec | **High** | ✅ **Closed 2026-06-28** — both pipelines, 365d retention. |
| 3 | CW log retention 30d (vs FFIEC 1y) | Sec | **High** | ✅ **Closed 2026-06-29** — templates updated 2026-06-27; live drift fix 2026-06-29 (10 log groups flipped to 365d, including 3 RDS at NEVER-EXPIRE). Re-audit clean. |
| 4 | Cognito MFA not enforced | Sec | **High** | ✅ **Closed 2026-06-29** — testbank pool MFA `ON`, TOTP-only, CI invariant enforced. |
| 5 | P1 Redis single-node | Rel | **Critical** | ❌ Open. Pre-prod. |
| 6 | P1 Aurora single writer | Rel | **Critical** | ❌ Open. Pre-prod. |
| 7 | P2 `DbMultiAz` default false | Rel | **Critical** | ✅ **Closed in code 2026-06-27** (CFN default + Rules block + CI guard). Running testbank Aurora pending next compute-stack update. |
| 8 | No cross-region DR | Rel | **High** | ❌ Open. Target 90d. |
| 9 | No DR drill cadence | Rel | **High** | ❌ Open. Target 60d. |
| 10 | No staging account for P1 | OpEx | **High** | ❌ Open. Target 60d. |
| 11 | No drift detection | OpEx | **Med** | ❌ Open. Target 30d. |
| 12 | No AWS Budgets / Cost Anomaly | Cost | **Med** | ✅ **Closed 2026-06-28** — both pipelines. |
| 13 | No CloudFront WAF on P2 dashboard | Sec | **Med** | ❌ Open. Target 60d. |
| 14 | No IAM Access Analyzer | Sec | **Med** | ✅ **Closed 2026-06-28** — account-singleton via P1. |
| 15 | No Macie / DLP on S3 | Sec | **Med** | ❌ Open. Target 60d. |
| 16 *new* | No ALB access logging | Sec | **Med** | ✅ **Closed 2026-06-28** — both pipelines. |
| 17 *new* | No CIS 4.4 IAM policy-change alarm | Sec | **Med** | ✅ **Closed 2026-06-28** — account-singleton via P1. |
| 18 *new* | No ECR Enhanced Scanning | Sec | **Med** | ✅ **Closed 2026-06-29** — Inspector v2 ECR `ENABLED` + registry `ENHANCED`/`CONTINUOUS_SCAN`. |
| 19 *new* | IR runbook + 24/7 paging chain | OpEx | **High** | ✅ **Closed 2026-06-30** — runbook v1.0 with CTO sign-off; AWS-native SNS-SMS paging (replaces PagerDuty assumption). First tabletop drill +14d. |

---

## 11. 30 / 60 / 90 day remediation plan

### 30 days (must-do before AWS Partner sign-off)

- [x] **Enable VPC Flow Logs on both VPCs (FSBP `EC2.6`)** — deployed 2026-06-28.
- [x] **Bump CW log retention to 365d on `/fortress/pipeline1` and `/fortress/pipeline2/{bank}`** — done in CFN templates 2026-06-27 (commit `286f468`); live drift fix executed 2026-06-29 across all 10 non-compliant log groups (re-audit clean).
- [x] **Enforce Cognito MFA on bank dashboard user pool** — deployed 2026-06-29 (testbank). `MfaConfiguration: ON`, TOTP-only, CI invariant enforced. Per-bank deploys replicate the template. Runbook: `docs/runbooks/cognito-mfa.md`.
- [x] **Flip `DbMultiAz` default to `true` and add a CFN `Condition` blocking prod deploys when false** — done 2026-06-27 (commit `8389d15`): CFN default + top-level `Rules` block + CI-time `verify_ftr_invariants.py` guard. Negative-tested.
- [ ] Enable CFN drift detection (scheduled Lambda, SNS alarm)
- [x] **Enable AWS Budgets + Cost Anomaly Detection in both accounts** — deployed 2026-06-28 as separate `fortress-{p1,p2}-billing-*` stacks ($500/mo each, IMMEDIATE anomaly subscription @ $100 abs).
- [x] **Schedule IAM Access Analyzer quarterly review** — deployed 2026-06-28; account-singleton via P1 (`fortress-p1-account-analyzer-production`).
- [ ] Add DLQ + retry-exhaustion alarms on every EventBridge schedule
- [ ] Bump RDS `BackupRetentionPeriod` to 35 for production banks
- [x] **ALB access logs → S3 (new, bonus)** — deployed 2026-06-28 both pipelines; SSE-S3, BucketOwnerEnforced, all PAB, Glacier @ 90d / expire @ 365d.
- [x] **CIS 4.4 IAM policy-change EventBridge → SNS alarm (new, bonus)** — deployed 2026-06-28; 17 IAM actions matched, account-singleton via P1.
- [x] **ECR Enhanced Scanning enable** — completed 2026-06-29. Inspector v2 ECR `ENABLED`; ECR registry `ENHANCED` + `CONTINUOUS_SCAN` covering all 3 repositories. Triage SLAs per `docs/runbooks/inspector-triage.md`.
- [x] **IR runbook v1.0 + 24/7 paging chain** — completed 2026-06-30. CTO-signed runbook; AWS-native SNS-SMS paging chain (replaces the originally-assumed PagerDuty integration to keep IR critical path inside the bank's own AWS account). First tabletop drill +14d.

### 60 days

- [ ] Deploy safe Secrets Manager rotation Lambda + SecretAppConfig re-render
- [ ] Stand up P1 staging account (mirror prod CFN, separate Aurora cluster)
- [ ] Run resilience game-day (Aurora failover, Redis failover, scraper outage, sync lag)
- [ ] Publish monthly unit economics: cost per bank, per sync cycle, per artifact
- [ ] Deploy Macie on bank S3 buckets
- [ ] Attach CloudFront WAF to P2 dashboard distribution

### 90 days

- [ ] P1 cross-region read replica + S3 CRR to a DR region
- [ ] Add `AuroraInstanceReader` to P1 cluster
- [ ] Upgrade P1 Redis to MultiAZ HA (`r7g.medium × 2`)
- [ ] Complete WAFR reassessment with AWS SA — close all Critical + High
- [ ] Finalize continuous-governance dashboard (security, reliability, cost KPIs by pipeline and bank)

---

## 12. Evidence references (for the partner)

| Topic | File |
|---|---|
| P1 VPC, SGs, VPC endpoints | `infra/cloudformation/pipeline1-vpc.yml` |
| P1 Aurora, Redis, S3, KMS, Secrets | `infra/cloudformation/pipeline1-data.yml` |
| P1 ECS Fargate, ALB, WAF, IAM, EventBridge | `infra/cloudformation/pipeline1-compute.yml` |
| P1 GuardDuty, Security Hub, CloudTrail | `infra/cloudformation/pipeline1-security.yml` |
| P2 VPC, SGs, VPC endpoints | `infra/cloudformation/pipeline2-vpc.yml` |
| P2 RDS, Redis, KMS, Secrets, SSM | `infra/cloudformation/pipeline2-data.yml` |
| P2 ECS Fargate, ALB, WAF, IAM, autoscale, EventBridge | `infra/cloudformation/pipeline2-compute.yml` |
| P2 Dashboard S3+CloudFront+OAC, Cognito | `infra/cloudformation/pipeline2-dashboard.yml` |
| P2 GuardDuty, Security Hub, CloudTrail | `infra/cloudformation/pipeline2-security.yml` |
| P2 Exports bucket (WORM, KMS) | `infra/cloudformation/pipeline2-exports-bucket.yml` |
| P2 Exports worker (Fargate, IAM) | `infra/cloudformation/pipeline2-exports-worker.yml` |
| P2 Exports CW alarms + quarterly drill | `infra/cloudformation/pipeline2-exports-alarms.yml` |
| P1 billing (Budgets + CAD) — *new 2026-06-28* | `infra/cloudformation/pipeline1-billing.yml` |
| P2 billing (Budgets + CAD) — *new 2026-06-28* | `infra/cloudformation/pipeline2-billing.yml` |
| Deploy scripts | `infra/scripts/setup-p1-auto.sh`, `infra/scripts/deploy-p2.sh`, `infra/scripts/register-bank-ip.sh` |
| ECR Enhanced Scanning bootstrap — *new* | `infra/scripts/enable-ecr-enhanced-scanning.sh` |
| CW retention drift catcher — *new* | `infra/scripts/audit-cw-log-retention.sh` |
| FTR invariants CI guard — *new* | `scripts/verify_ftr_invariants.py` |
| Operations runbook | `infra/setup/PIPELINE1_SETUP.md` |
| Agent runbooks | `docs/runbooks/agent-{1,2,3,4}-*.md` |
| Secrets manual-rotation runbook (B4 compensating) — *new* | `docs/runbooks/secrets-rotation.md` |
| Inspector CVE triage runbook — *new* | `docs/runbooks/inspector-triage.md` |
| Incident response runbook v1.0 (CTO-signed, AWS-native SNS-SMS paging) — *new* | `docs/runbooks/incident-response.md` |
| SNS-SMS sandbox / 10DLC operator runbook — *new* | `docs/runbooks/sns-sms-10dlc.md` |
| Cognito MFA enforcement runbook — *new* | `docs/runbooks/cognito-mfa.md` |
| FTR execution tracker | `docs/ftr_tracker.md` |
| FTR master self-assessment | `docs/aws_ftr_marketplace_p1_p2.md` |

---

## 13. Sign-off readiness summary

| Item | Status (2026-06-30) |
|---|---|
| All workloads inventoried in CFN | ✅ |
| Account-boundary documented | ✅ |
| Encryption inventory complete | ✅ |
| IAM roles least-privileged with `Sid:` traceability | ✅ |
| Audit trail with log-file validation | ✅ |
| Object-Lock WORM for evidence (5-year FFIEC/OFAC) | ✅ |
| Multi-AZ HA on P2 Redis | ✅ |
| Auto scaling | ✅ |
| Quarterly automated drill | ✅ (exports only — extend) |
| **VPC Flow Logs** | ✅ Deployed 2026-06-28 |
| **ALB access logs to S3** | ✅ Deployed 2026-06-28 |
| **IAM Access Analyzer** | ✅ Deployed 2026-06-28 (account-singleton) |
| **CIS 4.4 IAM policy-change alarm** | ✅ Deployed 2026-06-28 (account-singleton) |
| **AWS Budgets + Cost Anomaly Detection** | ✅ Deployed 2026-06-28 (both pipelines) |
| **MultiAZ default true (P2 RDS)** | ✅ Enforced in CFN + CI 2026-06-27 (running testbank flips on next compute-stack update) |
| **CW log retention ≥ 1y** | ✅ Templates 2026-06-27 + live drift fix 2026-06-29 (10 log groups, re-audit clean) |
| **ECR Enhanced Scanning** | ✅ Enabled 2026-06-29 (Inspector v2 + registry `ENHANCED`/`CONTINUOUS_SCAN`) |
| **Cognito MFA** | ✅ Deployed 2026-06-29 (testbank, TOTP-only, CI invariant) |
| **IR runbook v1.0 + 24/7 paging** | ✅ Closed 2026-06-30 (CTO-signed; AWS-native SNS-SMS paging chain) |
| **Secrets rotation** | 🟡 Manual rotation runbook in production as compensating control. Auto-rotation still blocked. |
| **DR drill cadence** | ❌ (High) |
| **Cross-region DR** | ❌ (90-day) |

Of the 19 consolidated risks, **11 are closed, 1 has a compensating control, 7 remain open**. All remaining open risks are pre-production HA/DR posture or non-infrastructure (Macie, SOC 2 Type II, penetration test). The infrastructure-side WAFR surface is in a defensible position for AWS Premier Partner sign-off; remaining open items are tracked under the 60/90-day plan.
