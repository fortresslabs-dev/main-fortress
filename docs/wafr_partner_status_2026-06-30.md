# Fortress Labs — AWS Well-Architected Framework Review (WAFR) Status

**Status report to AWS Premium Partner**
**Date:** 30 June 2026
**Submitted by:** Fortress Labs — Engineering & DevOps
**Purpose:** Pre-workshop snapshot for Premium Partner-led WAFR

> **Note on scope.** This document is the WAFR pre-workshop packet. It is **distinct from** our parallel AWS Foundational Technical Review (FTR) submission (`ftr_partner_status_2026-06-28.md`). The FTR is the AWS Marketplace listing gate and tracks the 10 Marketplace HRIs (B1–B10). WAFR is the architectural assessment run inside the AWS Well-Architected Tool against the 6 pillars and applicable lenses. **The two programs share evidence but have separate scoring frameworks** — please do not conflate FTR "HRIs" (B-numbers) with WA Tool "HRIs" (questions flagged as High Risk in the Tool).

---

## 1. Executive Summary

Fortress Labs operates a banking-compliance SaaS deployed in two architecturally isolated pipelines: a multi-tenant **Fortress Cloud** account holding only public regulatory data (Pipeline 1), and a single-tenant **bank-owned account** stack holding all bank operational data (Pipeline 2). The data-plane / control-plane split is enforced at the AWS account boundary — bank PII, transactions, OFAC matches, and audit records never enter Fortress's AWS account. Customer data is encrypted at rest with bank-owned KMS CMKs to which Fortress holds zero grant, and immutable WORM-retained for 5 years per FFIEC and OFAC recordkeeping requirements.

Against our consolidated 19-item cross-pillar risk register (Security + Reliability + Operational Excellence + Cost), **11 risks are closed, 1 has a compensating control in production, and 7 remain open**. All remaining open risks are pre-production HA/DR posture (P1 Redis/Aurora HA, cross-region DR, DR drill cadence, P1 staging account) or non-infrastructure (Macie, SOC 2 Type II, annual penetration test). The infrastructure-side WAFR surface is in a defensible position for Premier Partner sign-off on Security, Cost, Sustainability, and the bank-side of Reliability; Pipeline 1's HA posture and the cross-region DR story are the two areas we expect the partner to flag.

---

## 2. Workload Definition (for entry into the WA Tool)

| Field | Value |
|---|---|
| Workload name | Fortress Compliance Platform — P1 + P2 |
| Description | Banking-compliance SaaS: regulatory ingestion (P1, multi-tenant) + bank-deployed agent pipeline (P2, single-tenant per bank). |
| Review owner | Fortress Labs Engineering (jamie@fortresslabs.dev) |
| Environment | PRODUCTION (testbank P2 is the reference bank deployment) |
| AWS Regions | us-east-1 primary. Bedrock `us.*` inference profile fans out to us-east-1 / us-east-2 / us-west-2. |
| Industry | Financial Services |
| Industry type | Regulated banking-adjacent SaaS |
| Account model | Dual-account: Fortress Cloud + per-bank account (multi-account strategy enforced at IAM + network boundary) |
| Architecture design | Cloud-native, IaC-defined, immutable infrastructure |
| Review type | Premium Partner-led WAFR (Standard, not Lite) |
| Lenses | See §3. |

---

## 3. Recommended Lens Selection

The base 6 pillars alone undercount risk in a regulated SaaS architecture. We recommend the partner apply the following lenses in addition to the base review:

| Lens | Applicability | Why |
|---|---|---|
| **Base 6 pillars** | Required | Operational Excellence, Security, Reliability, Performance Efficiency, Cost Optimization, Sustainability. |
| **SaaS Lens** | **Strongly recommended** | Multi-tenant Fortress Cloud (P1) and tenant-isolated bank-account deployment (P2) are exactly the patterns this lens scores. Tenant onboarding, tenant offboarding, noisy-neighbor isolation, per-tenant cost allocation are all live concerns. |
| **Financial Services Industry Lens** | **Strongly recommended** | FFIEC IT Handbook, BSA/AML §1020.210, OCC Heightened Standards, OFAC 31 CFR 501 are our actual compliance drivers. The FSI Lens maps these to specific WA questions and is what bank CCO/CIO sponsors will recognise. |
| **Security Lens** | Recommended | Adds depth to the Security pillar — particularly relevant given our bank-owned-CMK posture, WORM Object Lock retention, and cross-account trust model. |
| **Serverless Lens** | Skip | We're 100% Fargate, not Lambda-fronted. Not applicable. |
| **Reliability Lens** | Optional | The base Reliability pillar will surface our P1 HA gaps adequately. |

Open question for the partner: confirm whether the **FSI Lens** is generally available in your engagement profile, or whether it is an add-on we need to request in advance.

---

## 4. Architecture

Pipeline 1 (Fortress Cloud), Pipeline 2 (per-bank), and the cross-account trust model are documented in `docs/aws_well_architected_review_p1_p2.md` §2.1–§2.3 with ASCII diagrams and a per-resource breakdown. **No structural architecture changes have occurred** since that document was first written (2026-06-23) — the work captured in this snapshot is observability, scanning, identity, and operational controls added on top of the existing architecture.

The headline architectural decision the partner will encounter:

> **Multi-account strategy enforced at the AWS account boundary.** Bank PII, transactions, OFAC matches, and audit records live exclusively in the bank's own AWS account, encrypted with bank-owned KMS CMKs to which Fortress holds zero grant. There is no VPC peering, no Transit Gateway, no shared KMS key, no IAM trust from Fortress into any bank account. Cross-account API calls are authenticated via per-bank HMAC tokens and constrained by P1's WAFv2 IPSet (only registered bank NAT EIPs).

A deployment-status overlay (showing which controls are now in place per VPC / per account) can be produced on request.

---

## 5. Current Readiness — Consolidated Risk Register

Status legend:  ● Closed   ◐ Partial / compensating control   ○ Open

**WAFR HRI definition:** in this register, "High" or "Critical" severity items that remain ❌ open are the WAFR HRIs we expect the partner to flag. This is the WA Tool's "High Risk Issue" concept and is **independent of FTR HRIs (B-numbers)** — see the §0 scope note.

### 5.1 Security Pillar

| # | Risk | Severity | Status |
|---|---|---|---|
| 1 | Secrets Manager rotation auto-pipe disabled (P1 + P2) | Critical | ◐ Compensating control — manual rotation runbook in production (`docs/runbooks/secrets-rotation.md`). Auto-rotation blocked on Aurora rotation-lambda regression of 2026-05-13. Target +30–60d. |
| 2 | VPC Flow Logs not enabled | High | ● Closed 2026-06-28 — both pipelines, 365d retention. |
| 3 | CW log retention 30d (vs FFIEC 1y) | High | ● Closed 2026-06-29 — templates updated 2026-06-27; live drift fix executed 2026-06-29 (10 log groups flipped to 365d, including 3 Aurora postgresql groups previously at NEVER-EXPIRE). |
| 4 | Cognito MFA not enforced on bank dashboard | High | ● Closed 2026-06-29 — testbank pool `MfaConfiguration: ON`, `EnabledMfas: [SOFTWARE_TOKEN_MFA]` (TOTP-only, SMS rejected by CI invariant due to SIM-swap risk). |
| 13 | No CloudFront WAF on P2 dashboard | Med | ○ Open. Target +60d. |
| 14 | No IAM Access Analyzer | Med | ● Closed 2026-06-28 — account-singleton via P1. |
| 15 | No Macie / DLP on S3 | Med | ○ Open. Target +60d. |
| 16 | No ALB access logging | Med | ● Closed 2026-06-28 — both pipelines, SSE-S3, BucketOwnerEnforced, Glacier @ 90d. |
| 17 | No CIS 4.4 IAM policy-change alarm | Med | ● Closed 2026-06-28 — account-singleton via P1; 17 IAM actions matched. |
| 18 | No ECR Enhanced Scanning | Med | ● Closed 2026-06-29 — Inspector v2 + registry `ENHANCED`/`CONTINUOUS_SCAN`. Triage SLAs per `docs/runbooks/inspector-triage.md`. |

### 5.2 Reliability Pillar

| # | Risk | Severity | Status |
|---|---|---|---|
| 5 | P1 Redis single-node | Critical (prod) | ○ Open. Pre-prod. |
| 6 | P1 Aurora single writer | Critical (prod) | ○ Open. Pre-prod. |
| 7 | P2 `DbMultiAz` default false | Critical (prod) | ● Closed in code 2026-06-27 — CFN default flipped, top-level Rules block, CI invariant. Running testbank flips on next compute-stack update. |
| 8 | No cross-region DR | High | ○ Open. Target +90d. |
| 9 | No DR drill cadence | High | ○ Open. Target +60d. |

### 5.3 Operational Excellence Pillar

| # | Risk | Severity | Status |
|---|---|---|---|
| 10 | No staging account for P1 | High | ○ Open. Target +60d. |
| 11 | No CFN drift detection | Med | ○ Open. Target +30d. |
| 19 | IR runbook + 24/7 paging chain | High | ● Closed 2026-06-30 — runbook v1.0 with CTO sign-off; AWS-native SNS-SMS paging chain (SNS topic per pipeline → SMS to on-call rotation; 10DLC handled per bank account at deploy time). First tabletop drill scheduled +14d. |

### 5.4 Cost Optimization Pillar

| # | Risk | Severity | Status |
|---|---|---|---|
| 12 | No AWS Budgets / Cost Anomaly Detection | Med | ● Closed 2026-06-28 — both pipelines. $500/mo budget, $100 abs anomaly subscription, two confirmed SNS subscribers per topic. |

### 5.5 Summary

**11 of 19 fully closed. 1 partial / compensating (Secrets rotation). 7 fully open** — all are pre-production HA/DR posture (R5, R6, R8, R9, R10) or pending Med-severity items (CloudFront WAF, Macie, CFN drift detection). The infrastructure-side gap surface for a pilot deployment is closed; the items remaining for production cutover are the standard "make this multi-region" + "stand up staging" plan.

**Expected WAFR HRIs (pillar questions we expect the Tool to flag as High Risk):**

1. Reliability — REL 9 "How do you back up data?" — backup retention is 7d, FFIEC recommends 35d for core-banking-adjacent.
2. Reliability — REL 10 "How do you use fault isolation to protect your workload?" — no cross-region failover.
3. Reliability — REL 11 "How do you design your workload to withstand component failures?" — P1 Aurora single writer, P1 Redis single-node.
4. Operational Excellence — OPS 6 "How do you mitigate deployment risks?" — no staging account for P1.
5. Security (Macie) — SEC 8 "How do you protect your data at rest?" — sensitive-data scanning not enabled on S3.

We've called these out explicitly so the partner can validate whether our self-assessment matches what the WA Tool will actually score.

---

## 6. Recent Deployment Activity

| Date | Activity |
|---|---|
| 2026-06-30 | **Risk 19 closed.** IR runbook promoted to v1.0 with CTO sign-off. Paging path changed from PagerDuty (originally assumed) to **AWS-native SNS → SMS** to keep the IR critical path entirely inside the bank's own AWS account — no third-party SaaS dependency on the bank-side data plane, and no additional vendor contract gating. CloudWatch composite alarms on each pipeline publish to SNS topic `fortress-ir-paging-{pipeline}`, which fans out SMS to the on-call rotation. SNS-SMS sandbox / 10DLC campaign-registration operational gap documented in `docs/runbooks/sns-sms-10dlc.md` (handled per bank account at deploy time). |
| 2026-06-29 | **Risk 4 closed.** Cognito MFA enforced on testbank bank-dashboard user pool: `MfaConfiguration: ON`, `EnabledMfas: [SOFTWARE_TOKEN_MFA]` (TOTP-only; SMS rejected by a CI invariant — SIM-swap risk). Per-bank deploys replicate the template. Runbook for production rollouts (with grace window), end-user enrolment, and admin-reset recovery: `docs/runbooks/cognito-mfa.md`. |
| 2026-06-29 | **Risk 18 closed.** ECR Enhanced Scanning enabled in Fortress account: Inspector v2 ECR `ENABLED`; ECR registry `ENHANCED` + `CONTINUOUS_SCAN` covering all 3 repositories. Initial CVE backlog surfaces in Security Hub via FSBP within ~15 min; triage flow per `docs/runbooks/inspector-triage.md` (CRITICAL 7d / HIGH 30d / MEDIUM quarterly SLAs). |
| 2026-06-29 | **Risk 3 closed.** CloudWatch log-retention live-state drift fix executed: all 10 non-compliant log groups flipped to 365d, including 3 Aurora postgresql groups previously at NEVER-EXPIRE (a critical FFIEC gap). Re-audit clean. |
| 2026-06-28 | Phase 2 observability deployed: VPC Flow Logs (both pipelines, Risks 2 closed), ALB access logs (Risk 16 closed), IAM policy-change alarm (Risk 17 closed), IAM Access Analyzer (Risk 14 closed), Budgets + Cost Anomaly Detection (Risk 12 closed). All six CloudFormation stacks reached UPDATE_COMPLETE. |
| 2026-06-27 | Phase 4 invariant enforcement: `DbMultiAz=true` belt-and-suspenders (Risk 7 closed in code) — CloudFormation default + top-level Rules block + CI-time invariant verifier. Negative-tested. |

---

## 7. Timeline to WAFR Sign-off

| Phase | Status | Target |
|---|---|---|
| Phase 1 — Internal pre-read complete (this document + workpaper refreshed) | ● Complete | — |
| Phase 2 — Partner pre-read review (async, ~1 week) | ○ Pending kickoff | T+0 |
| Phase 3 — Partner-led WAFR workshop in WA Tool | ○ Pending | T+2 weeks |
| Phase 4 — Address Critical + High WA Tool HRIs flagged in workshop | ○ Pending | T+6 weeks |
| Phase 5 — Reassessment with partner SA | ○ Pending | T+10 weeks |
| Phase 6 — Final WAFR sign-off + AWS Well-Architected badge | ○ Pending | T+12 weeks |

Realistic earliest sign-off: **Q4 2026**, aligned with the FTR submission window.

---

## 8. Critical-Path Open Items

These items are expected to drive WAFR HRI flags. We are not asking the partner to wait on them — we want the workshop to validate them as the right things to be working on.

| # | Item | Pillar | Owner | Target |
|---|---|---|---|---|
| 5 | P1 Redis MultiAZ / replication-group failover | Reliability | Eng | +90d (prod cutover) |
| 6 | P1 Aurora reader replica | Reliability | Eng | +90d (prod cutover) |
| 8 | Cross-region DR for P1 + bank-side runbook | Reliability | Eng + Bank | +90d |
| 9 | DR drill cadence (game-day + automated quarterly) | Reliability | Eng | +60d |
| 10 | P1 staging account (mirror prod CFN) | OpEx | Eng | +60d |
| 11 | CFN drift detection scheduled + alarm | OpEx | Agent 1 | +30d |
| 1 | Secrets Manager rotation auto-pipe (Aurora-safe) | Security | Eng | +30–60d |
| 15 | Macie on bank S3 buckets | Security | Agent 1 | +60d |
| 13 | CloudFront WAF on P2 dashboard | Security | Agent 1 | +60d |

---

## 9. Asks of the AWS Premium Partner

This is a **pre-workshop snapshot** for the WAFR. We are asking for partner SA validation of our self-assessment before the formal workshop runs in the WA Tool, so the workshop time is spent on the right pillar questions rather than on calibrating our framing.

1. **Confirm the recommended lens selection** (base 6 + SaaS Lens + FSI Lens + Security Lens). In particular, confirm whether the **Financial Services Industry Lens** is available in your engagement profile or whether we need to request it as an add-on in advance. If FSI Lens is unavailable, advise how to map our FFIEC / BSA-AML / OCC / OFAC compliance drivers onto base-pillar questions without losing fidelity.
2. **Validate our list of expected WAFR HRIs in §5.5.** Specifically, are REL 9 (backup retention), REL 10 (cross-region fault isolation), REL 11 (component failure design — P1 single writer / single-node Redis), OPS 6 (no P1 staging), and SEC 8 (no Macie) the right WA Tool questions to expect High-Risk flags on, or are we missing any?
3. **Sanity-check the "closed-in-code, not-yet-live" framing on Risk 7** (P2 `DbMultiAz` default flipped to `true` + Rules block + CI invariant). The WA Tool is presumably asking about the running state, not the template. Should this be reported as Improved-Pending-Deploy in the Tool, or do we need to wait until the next P2 compute-stack update physically flips the testbank Aurora before claiming closure? (See `project_p2_compute_stack_drift_2026_06_28` for why the redeploy is deferred.)
4. **Pressure-test our architecture answers on the patterns the WA Tool's canned answer choices don't always handle cleanly:**
   - Multi-tenant data isolation enforced at the AWS account boundary, not in application code.
   - Customer-owned KMS CMK with zero Fortress IAM grant (`Sid: BankAccountFullAccess` to the bank account root only — no Fortress principal in the policy).
   - WORM Object Lock COMPLIANCE 1825d on the exports bucket as the durability + tamper-resistance answer (vs. cross-region replication).
   - Cryptographic deletion via bank CMK pending-deletion as the customer-data-deletion path — not a procedural delete.
5. **Recommendation on review cadence and re-WAFR triggers.** Should we plan for a re-WAFR per major version, per pilot bank graduating to production, per AWS region added, or annually? What do you typically see for SaaS workloads at our scale?

Async comments on this document work; alternatively we can schedule a 90-minute pre-workshop call at your convenience. The full workpaper (`docs/aws_well_architected_review_p1_p2.md`) is the appendix to this snapshot and the source of truth for evidence and CFN file paths.

---

## 10. Companion Documents

| Document | Purpose |
|---|---|
| `docs/aws_well_architected_review_p1_p2.md` | Full WAFR workpaper — pillar-by-pillar, encryption inventory, IAM walkthrough, audit-trail map, 30/60/90 remediation plan, CFN evidence index. Refreshed 2026-06-30. |
| `docs/ftr_partner_status_2026-06-28.md` | Parallel FTR (Marketplace listing) status snapshot. Share for context; the two reviews are independent. |
| `docs/aws_ftr_marketplace_p1_p2.md` | Full FTR self-assessment workpaper. |
| `docs/ftr_tracker.md` | Live execution tracker (internal). |

---

## 11. Contact

- **Primary:** Jamie Rahman, Engineering — jamie@fortresslabs.dev
- **Operations:** *[Name, Title]* — *[email]*

---

*Next status update planned for: after the partner pre-read review, or +14 days, whichever comes first.*

*This document is a snapshot. The full WAFR workpaper and live execution tracker are maintained internally and available on request.*
