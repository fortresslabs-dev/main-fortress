# Fortress Labs — AWS Foundational Technical Review (FTR)

> **Self-assessment for AWS Marketplace SaaS listing.** Written 2026-06-23, re-classified to AWS HRI/Med/Low rubric 2026-06-26. This is the pre-submission gap map — several items below are currently ❌ and tie to the 30/60/90-day plan in `docs/aws_well_architected_review_p1_p2.md`. Do **not** submit to AWS Partner Network until every **HRI** item in §13.1 is closed.

| Field | Value |
|---|---|
| Listing model | SaaS Contract + SaaS Subscription (per-bank annual contract) |
| Product type | Banking compliance SaaS — Pipeline 1 (Fortress Cloud) + Pipeline 2 (Bank Account BYOC) |
| Deployment model | Hybrid: P1 multi-tenant SaaS (Fortress account) + P2 deployed into customer's AWS account via CloudFormation |
| AWS Partner tier (target) | Advanced Tier → Premier (post-FTR pass) |
| Co-sell readiness (target) | ACE-eligible after FTR pass + 3 customer references |
| Primary region | us-east-1 (Bedrock inference profile `us.*` cross-region) |
| Compute | 100% ECS Fargate (Graviton where supported) |
| Companion doc | `docs/aws_well_architected_review_p1_p2.md` (WAFR) |

---

## Scope (read this first)

> **The single most load-bearing sentence in this submission:** customer (bank) operational data — PII, transaction records, OFAC matches, KYC payloads, board reports — **never enters Fortress's AWS account**. The data plane and the SaaS control plane are in different AWS accounts owned by different parties.

| Plane | Account owner | What it holds | Customer data? | KMS custody |
|---|---|---|---|---|
| **Pipeline 1 (P1)** — Fortress Cloud | **Fortress** | Public regulatory text only (CFR, OFAC SDN public lists, FFIEC handbook excerpts, enforcement actions). Multi-tenant. | **No** | Fortress CMKs |
| **Pipeline 2 (P2)** — Bank account | **Bank** | All bank operational data: wires, KYC, OFAC match decisions, workflow approvals, board reports (WORM 1825d). Single-tenant per bank. | **Yes** | Bank-owned CMKs only — Fortress has zero grant |

### What this means for the FTR reviewer

1. **Tenancy isolation is enforced at the AWS account boundary**, not in application code. There is no `bank_id` row filter in P2 because there is no other bank's data in the P2 database to filter against. See §8.
2. **A Fortress-side breach has zero customer-data blast radius.** A full compromise of Fortress's AWS account exposes public regulatory text (already public) and Marketplace billing metadata — no PII, no transaction records, no OFAC decisions.
3. **A bank-side breach is contained to that one bank's account.** No VPC peering, no Transit Gateway, no cross-account IAM trust to other banks or to Fortress.
4. **Customer-data-deletion proof is cryptographic, not procedural.** The bank deletes their CMK (30-day pending window); all WORM-retained exports become unreadable. Fortress cannot prevent or reverse this.

### What this means for our own gap list

- Gaps in P1 (Fortress account) are **availability / integrity** risks — they cannot become **confidentiality** breaches of customer data, because customer data isn't there.
- Gaps in P2 (bank account) are deployed *into the bank's account by the bank's IT* via CloudFormation templates we publish. Fortress closes them in templates; banks pick them up on next deploy.
- The §13 hard-blocker list is sequenced with this in mind: P1 hardening (VPC Flow Logs, secrets rotation runbook) and P2 template hardening (DbMultiAz default, Cognito MFA) are both required, but they fail in *different directions*.

### Where to read more

- Detailed shared-responsibility table — §1.3
- Tenancy isolation proof + reviewer Q&A — §8
- Customer-data-deletion proof — §11.4
- Execution tracker (18-item Phase-1 plan, ordered by blast radius) — `docs/ftr_tracker.md`

---

## 0. Why this FTR matters

The FTR is the AWS Partner Network gate that unlocks two adjacent — but distinct — programs. **We are pursuing the Marketplace path first; ISV Accelerate is a follow-on after the listing is live.** This disambiguation matters because the two have different scoping, different reviewer focus, and different timelines.

### 0.1 Primary path — AWS Marketplace SaaS listing

This is what FTR §9 and §11 are written for. FTR pass is a hard prerequisite for any Marketplace SaaS listing.

- Banks procure Fortress through their **existing AWS spend** (committed-use discount applies), eliminating a 60–90 day enterprise procurement cycle.
- Marketplace handles billing, EULA acceptance, metering, and renewal — Fortress focuses on product, not collections.
- The reviewer for this path cares most about §8 (multi-tenancy isolation), §9 (Marketplace API integration), and §11 (customer lifecycle).

### 0.2 Secondary path — ISV Accelerate (co-sell)

Eligible **only after** FTR pass + at least 3 customer references on the Marketplace listing. We are not driving this in parallel with the initial Marketplace submission.

- AWS account teams can put Fortress in front of CCO / CIO targets at named-account banks.
- AWS sellers earn ACE credit for sourcing customers to our Marketplace listing.
- Reviewer focus extends to §2 (IAM posture) and §10 (incident response maturity) more than Marketplace alone.

### 0.3 Why FTR matters beyond AWS programs

- **Bank procurement signal** — third-party-risk teams treat "FTR-passed on AWS Marketplace" as a baseline diligence pass; some banks have a documented exception path for FTR-passed vendors.
- **Series A diligence** — FTR pass + WAFR sign-off are two of the three AWS artifacts investors expect for an AI-on-AWS SaaS (the third is the SOC 2 Type II report).

### 0.4 FTR submission characteristics

The FTR is **checklist-based** with one of three risk classifications per control, per the AWS Partner-published rubric:

- **HRI (High-Risk Issue)** — must be remediated before the FTR can pass. Equivalent to what older drafts of this doc called "BLOCKING".
- **Medium** — recommended remediation; not pass-blocking, but the reviewer expects a closure date with an owner.
- **Low** — nice-to-have improvements; tracked, not gated.

This is NOT a maturity scorecard like WAFR — a single unaddressed HRI blocks submission. Every gap table in §2–§12 below carries an explicit **Risk** column using this rubric; §13 is the consolidated submission checklist sorted by risk.

---

## 1. Listing scope and shared-responsibility statement

### 1.1 What Fortress operates

- **Pipeline 1 (Fortress Cloud account)** — multi-tenant regulatory knowledge base, scrapers, sync API. Fortress is fully responsible for runtime, patching, scaling, backups, and incident response.
- **Marketplace contract entitlement service** — issues per-bank API keys, manages bank registration in WAF allowlist, meters daily.
- **Customer-facing portal** (TBD — see §11) for self-service onboarding, key rotation, support.

### 1.2 What the customer (bank) operates

- **Pipeline 2 stack** deployed into the bank's AWS account via CloudFormation templates we publish.
- **Cognito user pool** (bank IT owns user lifecycle, group membership, MFA enforcement).
- **All bank operational data** — RDS, Redis, exports S3 bucket are encrypted with bank-owned KMS CMKs.
- **CloudTrail in the bank account** — bank SOC reviews; Fortress has no read grant.

### 1.3 Shared-responsibility diagram (verbatim from this doc into Marketplace listing)

| Responsibility | Fortress | Bank | Notes |
|---|---|---|---|
| Application code (P1) | ✅ | — | Fortress deploys P1 from our pipeline |
| Application code (P2) | ✅ | — | We publish images to public ECR; bank pulls |
| Infrastructure deploy (P2 CFN) | ✅ (templates) | ✅ (execute) | Bank IT runs `infra/scripts/deploy-p2.sh` in their account |
| OS / runtime patching | ✅ | — | Fargate-managed |
| RDS / Aurora patching | ✅ | ✅ | Fortress publishes minor-version bumps in CFN; bank's maintenance window applies |
| KMS key custody (P2) | — | ✅ | All P2 keys are bank-owned (`BankAccountFullAccess` only) |
| User identity (P2) | — | ✅ | Cognito user pool, MFA, group membership |
| Audit trail review (P2) | — | ✅ | CloudTrail in bank account |
| Incident response (P1) | ✅ | — | Fortress on-call |
| Incident response (P2) | ✅ (app) | ✅ (env) | Joint runbook, see §10 |
| Customer data deletion | ✅ (deprovisioning runbook) | ✅ (executes) | See §11.3 |

---

## 2. Identity and Access Management

### 2.1 Root account controls

| Control | Required | Current state | Evidence | Status |
|---|---|---|---|---|
| Root account has MFA (hardware preferred) | Yes | Fortress account: virtual MFA enabled; bank accounts: bank IT's responsibility (called out in onboarding runbook) | AWS console screenshot (separate artifact) | ⚠️  Hardware MFA pending |
| Root account access keys deleted | Yes | Fortress: none; bank: covered in onboarding checklist | n/a | ✅ |
| Root account email is monitored shared inbox | Yes | `aws-root@fortresslabs.dev` → SNS-SMS paging chain (see `docs/runbooks/incident-response.md` §4.1) | — | ✅ |
| Root account login alarm (CloudWatch) | Yes | CloudTrail `ConsoleLogin` event → SNS via Security Hub | Security Hub PCI/FSBP findings | ✅ |

### 2.2 IAM user vs. role posture

| Control | Required | Current state | Status |
|---|---|---|---|
| No IAM users for application access | Yes | All ECS workloads use task roles; CI/CD uses GitHub OIDC | ✅ |
| Federated identity for human access | Yes | IAM Identity Center (planned for Fortress) | ⚠️  in progress |
| All IAM users (if any) have MFA | Yes | n/a — no IAM users | ✅ |
| Service-to-service auth via IAM roles | Yes | ECS task roles + execution roles per workload | ✅ |
| Cross-account access via IAM role assumption, not access keys | Yes | n/a — no cross-account IAM trust (see WAFR §2.3) | ✅ |

### 2.3 Least privilege

- Every IAM policy statement uses `Sid:` for traceability (WAFR §4.2).
- KMS decrypt scoped to specific CMK ARNs, not `*` (with documented exception for ECS execution role at task-pull time).
- Bedrock policy explicitly enumerates regional foundation-model ARNs for cross-region routing.
- Bucket policies enforce KMS encryption + Object Lock COMPLIANCE on every PUT to the exports bucket.

### 2.4 Customer-facing IAM (P2 in bank account)

- Cognito user pool is the only identity surface for end users.
- **5 RBAC groups**: CCO, CRO, CIO, Analyst, Admin — each with explicit dashboard route allowlist.
- Password policy: 12+ chars, requires upper / lower / number / symbol.
- Token TTL: 60 minutes (access + ID); refresh token 30 days.
- AdminCreateUser only — no public self-signup.

### 2.5 Gaps

| # | Gap | FTR control | Risk | Closure |
|---|---|---|---|---|
| I1 | Cognito MFA optional, not enforced | IAM-04 | **HRI** | ✅ Closed 2026-06-29 — pool-wide `MfaConfiguration: ON`, TOTP-only (SMS rejected). Applies to all groups (Analyst + Admin included; broader than original "CCO/CRO/CIO only" framing). See B2. |
| I2 | No IAM Identity Center for human Fortress employee access | IAM-02 | Med | 60d |
| I3 | Hardware MFA on root not confirmed for Fortress account | IAM-01 | **HRI** | Pre-submission |

---

## 3. Network Security

### 3.1 VPC posture

| Control | Required | Current state | Status |
|---|---|---|---|
| All workloads in private subnets | Yes | ECS tasks, RDS, Redis all in private subnets | ✅ |
| Public subnets only for ALB + NAT | Yes | Confirmed in `pipeline{1,2}-vpc.yml` | ✅ |
| No `0.0.0.0/0` ingress on data-tier SGs | Yes | RDS/Redis SG ingress is only the ECS task SG | ✅ |
| `PubliclyAccessible: false` on RDS/Aurora | Yes | Confirmed | ✅ |
| VPC Flow Logs enabled | Yes | **❌ Not enabled** — see WAFR S2 | ❌ **HRI** |
| WAF on all public ALBs | Yes | WAFv2 on P1 ALB + P2 ALB; **P2 dashboard CloudFront has no WAF** | ⚠️  Partial |
| AWS Shield Standard | Yes | Default-on for all ALB + CloudFront | ✅ |
| AWS Shield Advanced | Recommended | Not subscribed | ⚠️  Med-term |

### 3.2 Egress controls

- Bedrock, S3, SSM, Secrets Manager, CloudWatch Logs, ECR (DKR + API), STS — **7 VPC endpoints**, calls never traverse NAT.
- NAT egress restricted to TCP/443 only via NACL.
- Scraper egress targets are the documented connector domain list (federal + state regulators); no wildcard outbound.

### 3.3 Customer ingress

- **P1**: `api.fortresslabs.dev` ALB — WAF IPSet allowlist of bank NAT EIPs, rate limit 100/5min on `/api/sync`, AWSManagedRulesCommonRuleSet.
- **P2**: `fortressapi.{bank}.com` ALB — Cognito JWT required on every protected route, WAF rate limit 1000/IP.
- **P2 dashboard**: `dashboard.{bank}.com` CloudFront → S3 + OAC; bank's IT can attach an IPSet block via the runbook (currently bespoke per-bank — see WAFR Gap §4.3).

### 3.4 Gaps

| # | Gap | FTR control | Risk | Closure |
|---|---|---|---|---|
| N1 | VPC Flow Logs not enabled | NET-04 | **HRI** | 30d (WAFR S2; tracker B1) |
| N2 | P2 dashboard CloudFront has no WAF | NET-03 | Med | 60d |

---

## 4. Data Protection

### 4.1 Encryption at rest

| Asset | Mechanism | Key | Status |
|---|---|---|---|
| P1 Aurora | KMS CMK `alias/fortress-rds-key` | Fortress CMK | ✅ |
| P1 Redis | KMS CMK `alias/fortress-redis-key` | Fortress CMK | ✅ |
| P1 S3 buckets | `aws:kms` with CMK | Fortress CMK | ✅ |
| P1 Secrets Manager | KMS CMK per-secret | Fortress CMK | ✅ |
| P2 RDS | KMS CMK `alias/fortress-rds-{bank}` | **Bank CMK** | ✅ |
| P2 Redis | KMS CMK `alias/fortress-redis-{bank}` | **Bank CMK** | ✅ |
| P2 Secrets Manager (8 secrets) | KMS CMK | **Bank CMK** | ✅ |
| P2 Exports S3 (WORM 1825d) | `aws:kms` + Object Lock COMPLIANCE | **Bank CMK** | ✅ |
| P2 Dashboard S3 (SPA assets) | SSE-S3 (public assets, no PII) | — | ✅ |
| CloudWatch Logs | KMS-encrypted | Fortress / Bank CMK | ✅ |

### 4.2 Encryption in transit

| Channel | Mechanism | Status |
|---|---|---|
| Public ingress (ALB + CloudFront) | TLSv1.2_2021 minimum, ACM cert | ✅ |
| ECS → Aurora / RDS | `?ssl=require` enforced in DATABASE_URL | ✅ |
| ECS → Redis | `rediss://` + `TransitEncryptionMode: required` | ✅ |
| ECS → AWS APIs | VPC endpoints with TLS | ✅ |
| Cross-account (P2 → P1 sync) | HTTPS only, HMAC-signed | ✅ |
| Exports S3 PUT/GET | `aws:SecureTransport: true` enforced via bucket policy (`DenyHTTPS` statement) | ✅ |
| ECS → Bedrock | TLS via VPC endpoint | ✅ |

### 4.3 KMS key management

- All CMKs have `EnableKeyRotation: true` (annual).
- Per-bank exports CMK has 30-day pending-deletion window on key delete (prevents accidental loss-of-keys → loss-of-data within the WORM retention).
- Key policies follow least-privilege: `BankAccountFullAccess` to `arn:aws:iam::${AWS::AccountId}:root` only; no Fortress principal exists in any bank-side CMK policy.

### 4.4 Secrets management

| Control | Required | Current | Status |
|---|---|---|---|
| No secrets in code, env, or repo | Yes | All secrets via Secrets Manager / SSM Parameter Store | ✅ |
| Secrets referenced by ARN in task definitions | Yes | Full ARN incl. `-XXXXXX` suffix per ECS docs | ✅ (per `feedback_ecs_secrets_arn_format`) |
| Automated rotation enabled | Yes | **❌ Disabled** on P1 + P2 — AWS-managed PostgreSQLSingleUser broke Aurora 2026-05-13 | ❌ **HRI** |
| Manual rotation runbook | Required if auto-rotation off | Documented in template comments, not in a runbook | ⚠️  Partial |

### 4.5 Customer data classification

| Data class | Pipeline | Location | Encryption |
|---|---|---|---|
| Public regulatory text (CFR, OFAC SDN public lists) | P1 | Fortress Aurora + S3 | Fortress CMK |
| Bank operational data (wires, KYC, OFAC matches) | P2 | Bank RDS + Redis | **Bank CMK only** |
| Bank workflow approvals (CCO sign-offs) | P2 | Bank RDS | **Bank CMK only** |
| Bank exports (board reports, regulator filings) | P2 | Bank S3 Object Lock COMPLIANCE | **Bank CMK only** |
| Authentication tokens | P2 | Bank Cognito user pool | AWS-managed |

**Headline:** Bank PII / transaction data never enters Fortress's AWS account. This is enforced architecturally (no IAM trust, no VPC peering, no shared key), not just policy.

### 4.6 Gaps

| # | Gap | FTR control | Risk | Closure |
|---|---|---|---|---|
| D1 | Secrets Manager rotation disabled | DATA-04 | **HRI** | Pre-prod cutover |
| D2 | No documented Secrets Manager rotation runbook (compensating control for D1) | DATA-04 | Med | 30d (tracker D2) |

---

## 5. Logging, Monitoring, Detection

### 5.1 Required FTR logging

| Source | Destination | Retention | Status |
|---|---|---|---|
| CloudTrail (management events, multi-region) | S3 with log-file validation | bucket lifecycle to Glacier @ 365d | ✅ |
| CloudTrail (data events on exports bucket) | Separate trail in bank account | Object-Lock bucket retention | ✅ |
| VPC Flow Logs | CW Logs | — | ❌ **HRI** |
| ELB access logs | S3 | — | ⚠️  Not enabled |
| WAF logs | CW Metrics + SampledRequests | n/a | ⚠️  Partial — recommend Kinesis Firehose → S3 |
| Aurora / RDS logs | CW Logs `[postgresql, upgrade]` | 30d | ✅ — but retention low |
| Application logs (ECS) | CW Logs (`awslogs` driver) | 30d / 2557d (exports worker) | ⚠️  — FFIEC wants ≥1y |
| GuardDuty | findings → Security Hub | — | ✅ |
| Security Hub | PCI DSS v3.2.1 + AWS FSBP v1.0.0 | — | ✅ |

### 5.2 Required CloudWatch alarms

| Alarm | Source | Status |
|---|---|---|
| ECS task failed-to-start | Container Insights | ⚠️  implicit via ECS event |
| Aurora CPU > 80% | RDS CW | ⚠️  Recommended, not templated |
| Aurora storage > 80% | RDS CW (Serverless v2 auto-scales) | ✅ N/A |
| RDS connection saturation | RDS CW | ⚠️  Not templated |
| Exports render-fail-rate | Custom CW metric | ✅ (`pipeline2-exports-alarms.yml`) |
| Exports sign-off SLA breach | Custom CW metric | ✅ |
| Exports retention near expiry | Custom CW metric | ✅ |
| Quarterly drill failed | EventBridge + CW | ✅ |
| WAF rate-limit triggered | WAFv2 CW metric | ⚠️  Not templated |
| GuardDuty High severity | EventBridge → SNS | ✅ |
| Root account login | CloudTrail → CW Events | ✅ (via Security Hub) |
| IAM policy change | CloudTrail → CW Events | ⚠️  Not templated |

### 5.3 Detection & response

- **GuardDuty enabled** on Fortress + bank accounts with Runtime monitoring, Fargate agent, RDS login monitoring.
- **Security Hub enabled** with PCI DSS v3.2.1 + AWS FSBP v1.0.0 standards.
- **IAM Access Analyzer** — ⚠️  not yet scheduled (WAFR S6, 30d).
- **AWS Config** — ⚠️  not enabled. FTR recommends but does not require for SaaS Marketplace.

### 5.4 Gaps

| # | Gap | FTR control | Risk | Closure |
|---|---|---|---|---|
| L1 | VPC Flow Logs not enabled (dup of N1) | LOG-01 | **HRI** | 30d (tracker B1) |
| L2 | CW log retention 30d vs FFIEC 1y | LOG-02 | Med | 30d (tracker S1/L2) |
| L3 | ELB access logs not enabled | LOG-03 | Med | 30d (tracker S2/L3) |
| L4 | IAM policy change alarm missing | DET-02 | Med | 30d (tracker L4) |
| L5 | IAM Access Analyzer not scheduled | DET-04 | Med | 30d (tracker S3/L5) |

---

## 6. Operational Excellence & Resilience

### 6.1 Backup & recovery

| Asset | Backup | Retention | Status |
|---|---|---|---|
| P1 Aurora | continuous + daily snapshot | 7d | ⚠️  FFIEC recommends 35d |
| P2 RDS | continuous + daily snapshot | 7d | ⚠️  |
| P2 Exports S3 | Object Lock COMPLIANCE 1825d + versioning | 5yr immutable | ✅ |
| Aurora schema | alembic in git | git history | ✅ |
| Secrets | manual export documented in runbook | n/a | ⚠️  |

### 6.2 High availability

| Component | HA posture | Status |
|---|---|---|
| P1 ALB | multi-AZ by default | ✅ |
| P1 ECS Fargate | spread across private subnets in 2 AZs | ✅ |
| P1 Aurora | single writer, no reader | ❌ Critical for prod (WAFR R2) |
| P1 Redis | single-node | ❌ Critical for prod (WAFR R1) |
| P2 ALB | multi-AZ | ✅ |
| P2 ECS Fargate | 2 AZs, autoscale 2-10 | ✅ |
| P2 RDS | `DbMultiAz` parameter — **defaults to false in trial** | ❌ Critical (WAFR R3) |
| P2 Redis | 2-node, MultiAZ, auto-failover | ✅ |

### 6.3 DR

- No cross-region replica anywhere (WAFR R4). Acceptable for SaaS Marketplace launch; bank-grade prod cutover demands it.
- No DR drill cadence outside the quarterly exports re-attestation (WAFR R5).
- Documented RTO/RPO targets — see WAFR §5.1.

### 6.4 Deployment & rollback

- Blue-green via ECS rolling deploy with `minimumHealthyPercent=100`, `maximumPercent=200`.
- **Rollback is database-gated** (alembic forward-only — see WAFR §3.1 gap 1). Roll-forward is the documented pattern.
- Pre-prod parity gap — no staging account for P1 (WAFR §3.1 gap).

### 6.5 Patching

- **OS / runtime**: Fargate-managed — Fortress does not own.
- **App dependencies**: dependabot on the repo; CI/CD runs `pip-audit` + `npm audit` on every PR (`.github/workflows/ci-cd.yml`).
- **RDS / Aurora minor versions**: tracked via `AutoMinorVersionUpgrade: true` in CFN; major versions are a CFN parameter change with a documented runbook.

### 6.6 Gaps

| # | Gap | FTR control | Risk | Closure |
|---|---|---|---|---|
| O1 | P2 `DbMultiAz` default `false` (prod risk) | REL-01 | **HRI** | 30d (tracker B3) — blocking for production banks |
| O2 | P1 Redis single-node | REL-02 | Med | Pre-prod cutover |
| O3 | P1 Aurora single writer | REL-03 | Med | Pre-prod cutover |
| O4 | No DR drill cadence | REL-04 | Med | 60d |
| O5 | No automated post-deploy smoke test | OPS-02 | Low | 30d |

---

## 7. Vulnerability & Patch Management

| Control | Required | Status |
|---|---|---|
| Dependency scanning in CI/CD | Yes | dependabot + `pip-audit` + `npm audit` on every PR | ✅ |
| Container image scanning | Yes | ECR enhanced scanning recommended; **basic scanning enabled by default** | ⚠️  Upgrade to enhanced |
| SAST | Yes | `ruff` + `bandit` in CI | ✅ |
| Secret scanning in code | Yes | GitHub secret scanning + push protection | ✅ |
| Web app vulnerability scanning | Recommended | Not yet — recommend Inspector for ECR + dynamic scan | ⚠️  |
| Penetration test (annual) | Yes | Scheduled with third-party — see §12 | ⚠️  Scheduled |
| Bug bounty program | Recommended | Not yet | ⚠️  Med-term |

### 7.1 Gaps

| # | Gap | Risk | Closure |
|---|---|---|---|
| V1 | Upgrade ECR scanning to Enhanced (Inspector) | Med | 30d (tracker S4/V1) |
| V2 | Annual third-party pen test schedule | **HRI** | Confirm before submission |
| V3 | Coordinated disclosure / `security.txt` published | Low | 30d (tracker V3) |

---

## 8. Multi-tenancy isolation (the headline FTR control for SaaS)

This is the highest-stakes FTR section for a banking SaaS. AWS Partner reviewers will spend most time here.

### 8.1 Tenant data isolation

| Mechanism | Implementation | Status |
|---|---|---|
| Account-level isolation (preferred) | Each bank gets its OWN AWS account (P2 stack) | ✅ **Strongest possible** |
| Network isolation | No VPC peering, no Transit Gateway, no shared subnets | ✅ |
| Identity isolation | Cognito user pool is per-bank (lives in bank's account) | ✅ |
| Compute isolation | ECS task per bank in bank's account | ✅ |
| Data isolation | RDS, Redis, exports S3 per bank in bank's account | ✅ |
| Key isolation | Per-bank CMK owned by bank's account; Fortress has zero key custody | ✅ |
| Audit trail isolation | Per-bank CloudTrail in bank's account | ✅ |

### 8.2 Tenant data isolation in P1 (shared multi-tenant surface)

P1 holds *no bank operational data* — only public regulatory text. Tenant boundary is the API key allowlist + WAF IP allowlist. The threat model is:

- Bank A's API key is leaked → bank A's sync rate-limit is hit → no impact to bank B.
- Bank A's NAT IP is spoofed → WAFv2 IPSet match still requires the HMAC signature on the request body.
- Aurora row-level access — N/A (data is the same for all banks; nothing to leak).

### 8.3 Cross-tenant access scenarios — explicit denials

| Scenario | Why it cannot happen | Evidence |
|---|---|---|
| Fortress employee SSH into bank RDS | No bastion, no Session Manager grant, RDS in private subnet, bank-owned IAM | `pipeline2-data.yml` SG + `BankAccountFullAccess` key policy |
| Fortress reads bank exports artifact | No Fortress principal in bank S3 bucket policy; bucket CMK key policy excludes Fortress | `pipeline2-exports-bucket.yml` |
| Bank A reads bank B data | Banks are in separate AWS accounts; no IAM trust | n/a — accounts are roots of trust |
| Fortress code change auto-deploys into bank | Bank IT explicitly runs `deploy-p2.sh`; CFN updates require bank's `cloudformation:UpdateStack` | Onboarding runbook |

### 8.4 FTR reviewer prompts (anticipated)

> *"How do you prevent your application code from reading another tenant's data?"*

Application code in P2 runs in the bank's account with the bank's task role. There is no `bank_id` filter in the application because there is no other bank's data in the database to filter against. The isolation is at the AWS account boundary, not at the row level.

> *"What happens if a tenant's credentials are compromised?"*

Rotate via `scripts/rotate-bank-credentials.sh` (target: documented in 30d closure). The compromised credentials only grant access to the compromised bank's P1 sync API; they do not grant access to any other bank or to P1 internals.

> *"How do you handle a 'noisy neighbor' in your multi-tenant P1 surface?"*

WAF per-IP rate limit on `/api/sync` is 100/5min — a runaway tenant hits the rate limit before degrading the shared Aurora. Aurora Serverless v2 auto-scales 2–16 ACU to absorb bursts. Per-tenant SLO instrumentation is a WAFR §6.6 gap (60d).

### 8.5 Status

✅ **Multi-tenancy isolation is the architectural keystone and a clean pass.** This is the section investors and bank CISOs will single out as best-in-class.

---

## 9. AWS Marketplace integration

### 9.1 Listing model

- **SaaS Contract** — bank commits to an annual contract via Marketplace; Marketplace handles billing.
- Entitlement API call on every API key issuance (verify the bank's AWS account ID has an active entitlement).
- Metering API daily — meter on `banks_active`, `sync_cycles_completed`, `artifacts_rendered`.

### 9.2 Required integrations

| Integration | Required | Current state | Status |
|---|---|---|---|
| AWS Marketplace SaaS Entitlement API | Yes | **❌ Not implemented** | ❌ **HRI** |
| AWS Marketplace Metering API (per-bank daily) | Yes | **❌ Not implemented** | ❌ **HRI** |
| SNS topic for subscription notifications | Yes | **❌ Not subscribed** | ❌ **HRI** |
| Customer-facing onboarding URL post-subscribe | Yes | **❌ Portal not built** — see §11 | ❌ **HRI** |
| Buyer registration form | Yes | **❌ Not built** | ❌ **HRI** |

### 9.3 Listing artifacts (separate from this doc)

- Product description (≤300 char + 4 long-form sections).
- Architecture diagram (lift from WAFR §2.1, §2.2).
- Pricing page.
- 1-page solution brief (PDF).
- Support contact, support SLA.
- EULA / terms of service.
- Public security page (`fortresslabs.dev/security`).

### 9.4 Gaps

All §9 gaps are HRI **for the Marketplace listing path** specifically. They are not FTR-core security controls — the FTR reviewer evaluates them as a precondition for the listing, not as a security-posture finding. If we ever pivoted to ISV-Accelerate-only (no Marketplace), M1–M4 would drop out.

| # | Gap | Risk | Closure |
|---|---|---|---|
| M1 | Build Marketplace Entitlement API client | **HRI (listing)** | 30–60d |
| M2 | Build Marketplace Metering API client + daily cron | **HRI (listing)** | 30–60d |
| M3 | Build SNS subscription event handler | **HRI (listing)** | 30d |
| M4 | Build customer portal — onboarding, key issuance, support | **HRI (listing)** | 60–90d |
| M5 | Publish public security page + support SLA + EULA | **HRI (listing)** | 30d |

---

## 10. Incident Response

### 10.1 Required artifacts

| Artifact | Status |
|---|---|
| Documented IR plan (RACI, escalation, comms templates) | ✅ `docs/runbooks/incident-response.md` v1.0 (2026-06-30) |
| 24/7 on-call rotation | ✅ Primary + Backup paged in parallel via SNS-SMS; rotation table in IR runbook §3.1 |
| Customer notification SLA (e.g., 24h for data incident) | ✅ IR runbook §6 (template + cadence) |
| Quarterly tabletop exercise | ⚠️  Target 2026-08-29 (IR3, deferred follow-on) |
| Post-incident review template | ✅ IR runbook §8 |

### 10.2 Detection inputs

- GuardDuty findings → Security Hub → SNS `fortress-p1-security-alerts-${Environment}` → SMS (primary + backup) + email.
- CloudWatch alarms → same SNS topic → same paging chain.
- IAM policy mutations (CIS 4.4) → EventBridge `IamPolicyChangeRule` → SNS → paging chain.
- Access Analyzer ACTIVE findings → EventBridge `AccessAnalyzerFindingRule` → SNS → paging chain.
- Customer-reported incident → `security@fortresslabs.dev` (24h ack SLA).

### 10.3 Coordination with bank IT (P2)

- Joint runbook: who declares an incident (bank CCO has dashboard outage signal, Fortress has app exception signal).
- Bank's CloudTrail is the source of truth for forensics; Fortress provides app logs.
- Fortress commits to providing forensic data within 4h of bank's request during an active incident.

### 10.4 Gaps

| # | Gap | Risk | Closure |
|---|---|---|---|
| IR1 | Finalize IR runbook + sign-off by eng leadership | **HRI** | ✅ Closed 2026-06-30. Runbook v1.0 at `docs/runbooks/incident-response.md`. Sign-off block is in §10. |
| IR2 | On-call paging chain live | Med | ✅ Closed 2026-06-30. AWS-only SNS-SMS chain in `pipeline1-security.yml`; CI invariant `check_p1_security_sms_paging` enforces presence. v1.1 will add a 15-min unack rescue Lambda (deferred). |
| IR3 | First tabletop scheduled | Med | 60d (target 2026-08-29) |

---

## 11. Customer lifecycle (Marketplace-required)

### 11.1 Onboarding (post-subscribe → first usable session)

Target: **45 minutes** from Marketplace subscribe to first dashboard login.

1. Buyer subscribes on Marketplace → SNS event → Fortress portal.
2. Buyer fills registration: AWS account ID, primary contact, technical contact, bank identifier slug.
3. Fortress portal generates the per-bank HMAC key, registers the bank's NAT EIP candidates in the P1 WAF IPSet.
4. Buyer downloads CFN templates + `bank.env` + signed deployment instructions.
5. Bank IT runs `infra/scripts/deploy-p2.sh` (≈25-min deploy).
6. Bank IT runs `dashboard/deploy.sh` for the CCO dashboard.
7. Cognito admin creates first 5 users (CCO/CRO/CIO/Analyst/Admin).
8. CCO logs in → dashboard loads → first sync triggers from P1.

### 11.2 Ongoing support

- Tier 1: `support@fortresslabs.dev` (4h business-hours response).
- Tier 2: Slack Connect shared channel with bank (target SLA: 2h business-hours).
- Tier 3: Emergency SNS-SMS paging to on-call rotation (target: 30min, 24/7) — see `docs/runbooks/incident-response.md` §4.1.

### 11.3 Deprovisioning (Marketplace-required)

When a bank cancels:

1. Bank receives 30-day notice; data export window opens.
2. Fortress portal generates a final consolidated artifact bundle (board reports + raw JSON for all sync cycles in the bank's S3 exports bucket).
3. Bank IT confirms data export.
4. Bank IT runs `infra/scripts/deprovision-p2.sh`:
   - Deletes ECS service + task definitions.
   - Deletes RDS, Redis (final snapshot retained per bank policy).
   - Deletes Cognito user pool.
   - Empties + deletes dashboard S3 bucket.
   - **Does NOT delete exports bucket** — Object Lock COMPLIANCE prevents this. Bank IT must wait for retention expiry OR formally request KMS key deletion (30-day pending window), accepting that all artifacts become cryptographically unreadable.
5. Fortress portal revokes the bank's HMAC key and removes the NAT EIP from the P1 WAF IPSet.
6. Fortress retains: zero bank operational data (it never left the bank account).

### 11.4 Customer-data-deletion proof (FTR-required)

> *"How do you prove a customer's data is gone?"*

The strongest possible proof: **Fortress's account never held the customer's operational data.** Bank operational data lives in bank-owned RDS / Redis / S3 encrypted with bank-owned CMKs. When the bank deletes the CMK (30-day pending window), the data is cryptographically destroyed.

What Fortress retains (legitimate-interest basis under bank's data processing agreement):
- Marketplace subscription metadata (account ID, contract ID, start/end dates).
- Aggregate billing metrics (sync count, artifact count — no content).
- Support ticket history (subject + non-sensitive body).
- Audit-trail of HMAC key issuances / rotations.

### 11.5 Gaps

| # | Gap | Risk | Closure |
|---|---|---|---|
| C1 | Customer portal not built | **HRI (listing)** | 60–90d |
| C2 | Deprovisioning script not written | **HRI** | 30d (tracker C2 — skeleton first) |
| C3 | Data Processing Agreement template not drafted | **HRI** | 30d (tracker C3 — draft first; legal sign-off separate) |

---

## 12. Compliance and security program

### 12.1 Current attestations

| Attestation | Status | Target |
|---|---|---|
| AWS Well-Architected Review (P1 + P2) | ✅ Self-assessment complete (`docs/aws_well_architected_review_p1_p2.md`); partner-led review pending | Pre-Marketplace submission |
| AWS FTR | ❌ This document — pre-submission self-assessment | Q3 2026 |
| SOC 2 Type II | ⚠️  In progress with auditor | Q4 2026 |
| PCI DSS | N/A — no card data; Security Hub PCI DSS standard enabled for control alignment | n/a |
| HIPAA | N/A | n/a |
| FFIEC IT Hdbk control mapping | ✅ documented in `docs/pci_ffiec_control_mapping_matrix.md` | Maintained |
| Annual third-party penetration test | ⚠️  Scheduled | Pre-Marketplace submission |

### 12.2 Policies (FTR expects these to exist as published artifacts)

| Policy | Status | Owner |
|---|---|---|
| Information security policy | ⚠️  Draft | CTO |
| Access control policy | ⚠️  Draft | CTO |
| Incident response policy | ⚠️  Draft | CTO |
| Acceptable use policy | ⚠️  Draft | CTO |
| Vulnerability management policy | ⚠️  Draft | CTO |
| Change management policy | ⚠️  Draft | CTO |
| Vendor / sub-processor list | ⚠️  Needed | CTO |
| Data retention & deletion policy | ⚠️  Needed (see §11.4) | CTO |

### 12.3 Gaps

| # | Gap | Risk | Closure |
|---|---|---|---|
| P1 | Publish 8 security policies | **HRI** | 60d |
| P2 | Complete SOC 2 Type II | Med | 90–180d |
| P3 | Complete annual pen test | **HRI** | Pre-submission |

---

## 13. Submission-readiness checklist

Per §0.4: AWS FTR uses **HRI / Med / Low** risk classification. Only HRI items block the FTR pass. The execution sequencing of the items below (which to do *first*, in what order, with what blast radius) lives in `docs/ftr_tracker.md`.

**Status legend:** ✅ closed (code + deployed where applicable) · 🟡 partial / compensating control in place · ❌ open. **Last updated 2026-06-28.**

### 13.1 High-Risk Issues (HRI) — must close before opening an FTR ticket with AWS

| # | Item | § | Risk | Owner | Status | Evidence | Target |
|---|---|---|---|---|---|---|---|
| B1 | Enable VPC Flow Logs on both VPCs | 3.4, 5.4 | **HRI** | Agent 1 | ✅ | Deployed 2026-06-28. Stacks `fortress-p1-vpc-production` + `fortress-p2-vpc-testbank`; Flow Logs `fl-0b8eb1bd065040ae3` (P1) + `fl-0d77722788efa8852` (P2), 365d retention, `TrafficType=ALL`. Tracker #11. | Closed |
| B2 | Enforce Cognito MFA on bank dashboard user pool | 2.5 | **HRI** | Agent 4 | ✅ | Deployed 2026-06-29 (testbank, account-wide will replicate to each bank deploy). `pipeline2-dashboard.yml` CognitoUserPool: `MfaConfiguration: ON`, `EnabledMfas: [SOFTWARE_TOKEN_MFA]` (SMS rejected — SIM-swap risk). CI invariant `check_p2_dashboard_mfa` in `scripts/verify_ftr_invariants.py` rejects any PR that downgrades to OFF or adds SMS_MFA. Runbook: `docs/runbooks/cognito-mfa.md`. Tracker B2 entry in Phase 5. | Closed |
| B3 | Flip P2 `DbMultiAz` default to `true` + block prod deploys when false | 6.6 | **HRI** | Agent 1 | ✅ | Commit `8389d15` — CFN default flipped, new `Rules` block + `scripts/verify_ftr_invariants.py` wired into `ci-cd.yml`. Tracker #18. | Closed |
| B4 | Deploy safe Secrets Manager rotation (or document compensating control via D2 runbook) | 4.6 | **HRI** | Eng | 🟡 | D2 manual rotation runbook landed (Phase 1, tracker D2); auto-rotation still disabled — see memory `project_p1_secret_rotation_gap`. Blocked on fixing the PostgreSQLSingleUser path that broke Aurora 2026-05-13. | Pre-cutover |
| B5 | Build Marketplace Entitlement + Metering + SNS integration | 9.4 | **HRI (listing)** | Eng | ❌ | — | 60d |
| B6 | Build customer onboarding portal | 9.4, 11.5 | **HRI (listing)** | Eng | ❌ | — | 60–90d |
| B7 | Write deprovisioning script + DPA template | 11.5 | **HRI** | Eng + Legal | 🟡 | C2 skeleton `infra/scripts/deprovision-p2.sh` + C3 DPA draft landed. Legal sign-off + scripted KMS-key-pending-deletion proof still open. Tracker C2 + C3. | 30d |
| B8 | Publish 8 security policies + security page | 12.3 | **HRI** | CTO | ❌ | Drafts exist in §12.2 (all 8 ⚠️ Draft); none published, no `/security` page live. | 60d |
| B9 | Finalize + sign-off IR runbook + activate paging | 10.4 | **HRI** | CTO | ✅ | Closed 2026-06-30. AWS-only path (no SaaS) — IR runbook v1.0 (`docs/runbooks/incident-response.md`); SNS-SMS primary + backup subs on `SecurityAlertsTopic` (`pipeline1-security.yml`); CI invariant in `scripts/verify_ftr_invariants.py`. CTO sign-off block in runbook §10. First tabletop (IR3) deferred follow-on (target 2026-08-29). | Closed |
| B10 | Complete annual pen test | 12.3 | **HRI** | CTO | ❌ | Vendor not yet engaged. | Pre-submission |

**HRI summary:** 4 of 10 fully closed (B1, B2, B3, B9). 2 partial / compensating-control (B4, B7 — both Eng-side done, B7 blocked on Legal DPA sign-off). 4 fully open (B5, B6, B8, B10). Every fully-open item has a non-infra owner (Eng / CTO) — the infra-side surface area is closed.

### 13.2 Medium / Low Risk Issues — required for Premier + co-sell, not for initial FTR pass

| # | Item | § | Risk | Status | Evidence | Target |
|---|---|---|---|---|---|---|
| S1 | Bump CW log retention to 365d | 5.4 | Med | ✅ (templates) / 🟡 (drift) | Commit `286f468` — CFN log groups now 365d. Drift catcher `infra/scripts/audit-cw-log-retention.sh` reports 11 legacy groups still non-compliant (3 RDS NEVER-EXPIRE, 3 container-insights @1d, 1 legacy `/ecs/fortress-pipeline2` NEVER, 2 already-fixed-in-CFN waiting for next compute redeploy, 2 SMB-scope). Operator `--fix` run pending — adds ~12× storage on affected groups. Tracker #17. | 30d (drift fix) |
| S2 | ELB access logs enabled | 5.4 | Med | ✅ | Deployed 2026-06-28. P1 via CFN; P2 out-of-band (compute-stack drift, see memory `project_p2_compute_stack_drift_2026_06_28`). Buckets `fortress-p1-alb-logs-520385696621-production` + `fortress-p2-alb-logs-testbank-production`, SSE-S3 + BucketOwnerEnforced + all PAB + GLACIER@90d → expire@365d. Tracker #12. | Closed |
| S3 | IAM Access Analyzer scheduled | 2.5, 5.4 | Med | ✅ | Deployed 2026-06-28 (account-singleton — single P1 analyzer covers all IAM principals). `fortress-p1-account-analyzer-production` (Type=ACCOUNT, ACTIVE) + `AccessAnalyzerFindingRule` → `SecurityAlertsTopic`. Tracker #14. | Closed |
| S4 | ECR enhanced scanning | 7.1 | Med | 🟡 | Bootstrap script `infra/scripts/enable-ecr-enhanced-scanning.sh` (idempotent) + triage runbook `docs/runbooks/inspector-triage.md` (severity SLAs CRIT 7d / HIGH 30d / MED quarterly). Account-level enable not yet run — first scan will produce a CVE backlog ops must triage. Tracker #16. | 30d |
| S5 | P1 Redis MultiAZ HA | 6.6 | Med | ❌ | — | Pre-prod |
| S6 | P1 Aurora reader + writer | 6.6 | Med | ❌ | — | Pre-prod |
| S7 | Cross-region DR for P1 | 6.3 | Med | ❌ | — | 90d |
| S8 | DR drill cadence | 6.6 | Med | ❌ | — | 60d |
| S9 | P1 staging account | WAFR §3.1 | Med | ❌ | — | 60d |
| S10 | AWS Budgets + Cost Anomaly | WAFR §7.3 | Med | ✅ | Deployed 2026-06-28. Stacks `fortress-p1-billing-production` ($500/mo) + `fortress-p2-billing-testbank` ($500/mo). CUSTOM anomaly monitor + IMMEDIATE subscription @ $100 abs. Two confirmed SNS subscribers per topic. Tracker #15. | Closed |
| S11 | Macie on bank S3 | WAFR §4.5 | Low | ❌ | — | 60d |
| S12 | SOC 2 Type II | 12.1 | Med | ❌ | — | Q4 2026 |

**Med/Low summary:** 3 of 12 fully closed (S2, S3, S10). 2 partial (S1 templates done / 11 legacy groups pending operator `--fix`, S4 bootstrap done / Inspector enable + triage pending). 7 fully open (S5, S6, S7, S8, S9, S11, S12).

### 13.3 Bonus closed items (not in original §13, observable controls added during Phase 2)

The execution tracker added two account-level CIS controls that aren't enumerated in the original §13 tables but are relevant evidence for the FTR reviewer:

| Tracker # | Item | Status | Evidence |
|---|---|---|---|
| #13 (L4) | IAM policy-change EventBridge → SNS alarm (CIS 4.4 events) | ✅ | Deployed 2026-06-28. `fortress-p1-iam-policy-change-production` rule → `SecurityAlertsTopic`; 17 IAM actions matched. Account-singleton (single P1 rule covers all IAM mutations regardless of which P2 tenant triggers them). |
| #12 (S2) bonus | Duplicate-`Export:` regression catcher | ✅ | Commit `10fb33f` — un-hijacked `DigestTopicArn` export in P1 compute template that would have shadowed scrape-monitor downstream consumers on next deploy. |

---

## 14. Estimated submission timeline

The phasing below was the original plan (locked 2026-06-26). The "Actual" column is updated against on-disk evidence.

| Phase | Duration | Planned output | Actual (as of 2026-06-28) |
|---|---|---|---|
| **Phase 1 — close 30-day infra blockers** | 30 days | B1, B3 closed; B4 / B7 / B9 compensating controls in place | ✅ **Done.** B1 + B3 closed via CFN; B4 compensating (D2 runbook), B7 partial (C2 script + C3 DPA draft), B9 partial (IR runbook v0.1). All 18 tracker items ✅. |
| **Phase 2 — additive observability (new add-on)** | concurrent w/ Phase 1 | n/a (not in original plan) | ✅ **Done 2026-06-28.** #11 VPC Flow Logs, #12 ALB access logs, #13 IAM policy-change alarm, #14 Access Analyzer, #15 Budgets + CAD all deployed to AWS. Closes §13.2 S2, S3, S10. |
| **Phase 3 — config flips** | concurrent w/ Phase 1 | n/a | ✅ Code/scripts complete. #16 ECR Enhanced Scanning bootstrap script + triage runbook (run pending, operator-invoked). #17 CW retention 365d in templates (drift fix for 11 legacy groups pending). |
| **Phase 4 — invariants enforcement** | concurrent w/ Phase 1 | n/a | ✅ #18 done. CFN `Rules` block + CI-time `verify_ftr_invariants.py` blocks `DbMultiAz=false` in prod. |
| **Phase 5 — Marketplace integration build** | 30–60 days | B5, B6 in QA | ❌ Not started. **Critical path.** |
| **Phase 6 — policies, pen test, portal** | 60–90 days | B4 full / B6 / B7 full / B8 / B10 closed | ❌ Not started. **Critical path.** B2 Cognito MFA also here. |
| **Phase 7 — internal dry-run** | 1 week | Run through this doc end-to-end with eng leadership | ❌ Pending Phase 5 + 6. |
| **Phase 8 — open FTR ticket with AWS Partner Network** | 2–4 weeks | AWS Partner SA reviews artifacts | ❌ Pending Phase 7. |
| **Phase 9 — listing live** | 1–2 weeks post FTR pass | Marketplace listing visible | ❌ Pending Phase 8. |

### Critical-path open items (gate the FTR ticket)

| Item | Owner | Earliest start | Earliest finish |
|---|---|---|---|
| B4 auto-rotation (full) | Eng | now (blocked on Aurora rotation-lambda fix) | +30–60d |
| B5 Marketplace Entitlement + Metering + SNS | Eng | now | +60d |
| B6 onboarding portal | Eng | after B5 contract types stabilize | +60–90d |
| B7 DPA sign-off (Eng side closed) | Legal | now (Eng surface — deprovision script + key-deletion runbook — done; awaiting Legal review of `docs/legal/dpa_template.md`) | +30d |
| B8 8 policies + `/security` page | CTO | now (drafts in §12.2) | +60d |
| B10 annual pen test | CTO | after B5/B6/B8 — pen test should hit the merged surface | Pre-submission |

**Realistic earliest submission: Q4 2026.** Realistic earliest listing live: Q1 2027. **Infra-side work is no longer the bottleneck** — every remaining HRI item is owned outside Agent 1 (Eng, CTO, or Agent 4 for Cognito MFA).

---

## 15. Evidence index (for the AWS Partner reviewer)

| Topic | File |
|---|---|
| Companion WAFR | `docs/aws_well_architected_review_p1_p2.md` |
| Infrastructure (CFN templates) | `infra/cloudformation/pipeline{1,2}-*.yml` |
| Deploy scripts | `infra/scripts/setup-p1-auto.sh`, `infra/scripts/deploy-p2.sh`, `infra/scripts/register-bank-ip.sh` |
| Bank onboarding runbook | `infra/setup/PIPELINE1_SETUP.md` |
| Agent runbooks | `docs/runbooks/agent-{1,2,3,4}-*.md` |
| FFIEC / PCI control mapping | `docs/pci_ffiec_control_mapping_matrix.md` |
| OFAC calibration | `docs/runbooks/ofac-calibration-gold-set.md` |
| CI/CD pipeline | `.github/workflows/ci-cd.yml` |
| Code owners | `.github/CODEOWNERS` |

---

## 16. One-paragraph executive summary (for the Marketplace listing reviewer)

> Fortress Labs operates a banking-compliance SaaS deployed in two architecturally isolated pipelines: a multi-tenant Fortress Cloud account that holds only public regulatory text (Pipeline 1), and a single-tenant per-bank stack deployed into the bank's own AWS account that holds all bank operational data (Pipeline 2). Bank PII, transaction data, OFAC matches, and audit artifacts never leave the bank's account; they are encrypted at rest with bank-owned KMS CMKs to which Fortress holds zero grant, and immutable WORM-retained for 5 years per FFIEC and OFAC recordkeeping requirements. The platform runs on Fargate (Graviton), Aurora Serverless v2 (pgvector + tsvector — no OpenSearch), and Amazon Bedrock (Claude Sonnet 4.5/4.6, Haiku 4.5 via inference profiles). GuardDuty, Security Hub (PCI DSS + FSBP), and CloudTrail with log-file validation are always-on. This document is a pre-submission self-assessment; the §13 blocker list is the active execution plan toward Marketplace listing in Q1 2027.
