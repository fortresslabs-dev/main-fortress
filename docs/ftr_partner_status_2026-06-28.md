# Fortress Labs — AWS FTR Readiness Status

**Status report to AWS Premium Partner**
**Date:** 30 June 2026 *(updated from 29 June snapshot to reflect HRI B9 closure)*
**Submitted by:** Fortress Labs — Engineering & DevOps
**Purpose:** Pre-submission gap-analysis snapshot for Premium Partner SA review

---

## 1. Executive Summary

Fortress Labs operates a banking-compliance SaaS deployed in two architecturally isolated pipelines: a multi-tenant **Fortress Cloud** account holding only public regulatory data (Pipeline 1), and a single-tenant **bank-owned account** stack holding all bank operational data (Pipeline 2). The data-plane / control-plane split is enforced at the AWS account boundary — bank PII, transactions, OFAC matches, and audit records never enter Fortress's AWS account. Customer data is encrypted at rest with bank-owned KMS CMKs to which Fortress holds zero grant, and immutable WORM-retained for 5 years per FFIEC and OFAC recordkeeping requirements.

This document is a pre-submission snapshot as of 30 June 2026. Of 10 High-Risk Issues (HRI) blocking the FTR ticket, **4 are fully closed, 2 have compensating controls in production, and 4 remain open**. All remaining open HRIs are non-infrastructure items owned by Engineering, Legal, or CTO functions (Marketplace integration, security policies, penetration test). The infrastructure-side gap surface is closed.

---

## 2. Scope and Data-Plane Isolation

> **Load-bearing claim:** Customer (bank) operational data never enters Fortress's AWS account.

| Plane | Account Owner | Contents | Customer Data | KMS Custody |
|---|---|---|---|---|
| Pipeline 1 — Fortress Cloud | Fortress | Public regulatory text only (CFR, OFAC SDN, FFIEC handbook, enforcement actions). Multi-tenant. | No | Fortress-owned CMKs |
| Pipeline 2 — Bank account | Bank | All bank operational data: wires, KYC, OFAC matches, workflow approvals, board reports (WORM 1825d). Single-tenant per bank. | Yes | Bank-owned CMKs; Fortress has zero grant |

**Implications for the FTR review:**

1. Tenancy isolation is enforced at the AWS account boundary, not in application code. No row-level multi-tenant logic is required in Pipeline 2 because no other bank's data lives in that database.
2. A Fortress-side breach has zero customer-data blast radius. Full compromise of Fortress's AWS account exposes public regulatory text and Marketplace billing metadata only — no PII, no transaction records, no OFAC decisions.
3. A bank-side breach is contained to that one bank's account. There is no VPC peering, no Transit Gateway, and no cross-account IAM trust to other banks or to Fortress.
4. Customer-data deletion is cryptographic, not procedural. When a bank deletes their CMK (30-day pending window), all WORM-retained exports become unreadable. Fortress cannot prevent or reverse this.

---

## 3. Architecture

The Pipeline 1, Pipeline 2, and cross-account trust diagrams were shared with the Premium Partner in our prior submission. **No structural architecture changes have occurred since that submission.** The work captured in this status report is observability, scanning, and operational controls added on top of the existing architecture.

A deployment-status overlay (showing which controls are now in place per VPC / per account) can be produced on request.

---

## 4. Current Readiness Status

Per AWS FTR risk classification: **HRI** = High-Risk Issue (must close before opening the FTR ticket); **Med / Low** = required for Premier tier and co-sell readiness, not for initial FTR pass.

**Status legend:**  ● Closed   ◐ Partial / compensating control   ○ Open

### 4.1 High-Risk Issues (HRI)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | VPC Flow Logs on both VPCs | ● Closed | Deployed to both pipelines on 2026-06-28. 365-day retention. All traffic captured. |
| B2 | Enforce Cognito MFA on bank dashboard user pool | ● Closed | Deployed 2026-06-29 (testbank). Pool-wide `MfaConfiguration: ON`, TOTP-only (SMS rejected — SIM-swap risk). CI invariant enforces both properties on every PR. Per-bank deploys replicate the template; runbook for rollout + recovery at `docs/runbooks/cognito-mfa.md`. |
| B3 | Pipeline 2 `DbMultiAz=true` default with production deploy guard | ● Closed | CloudFormation default flipped, top-level Rules block added, CI-time invariant verifier in place. Negative-tested. |
| B4 | Safe Secrets Manager rotation | ◐ Partial | Manual rotation runbook in production as compensating control. Automated rotation blocked on Aurora rotation-lambda issue from 2026-05-13. Target +30–60 days. |
| B5 | Marketplace Entitlement + Metering + SNS integration | ○ Open | Owner: Backend Engineering. Target +60 days. |
| B6 | Customer onboarding portal | ○ Open | Owner: Engineering. Target +60–90 days. Depends on B5 contract types. |
| B7 | Deprovisioning script + DPA template | ◐ Partial (Eng done) | Deprovisioning script is now production-ready: resource discovery from CFN outputs, RDS final-snapshot with retention tagging, CloudFront disable-wait-delete, WAF disassociate-and-delete with lock-token re-fetch, CFN stack teardown in reverse dependency order, cross-account revoke instructions, exports S3 bucket (Object-Lock COMPLIANCE 1825d) intentionally untouched. Validated end-to-end in dry-run against testbank 2026-06-29. DPA template draft circulated for counsel review. Remaining: Legal sign-off on DPA + scripted KMS-key-pending-deletion proof (`docs/runbooks/p2-key-deletion.md` TBD). |
| B8 | Publish 8 security policies and /security page | ○ Open | All 8 policies drafted. Publication pending CTO review. Target +60 days. |
| B9 | Finalize IR runbook + activate 24/7 paging chain | ● Closed | IR runbook v1.0 in production (2026-06-30) with CTO sign-off captured in `docs/runbooks/incident-response.md`. Paging chain is **AWS-native (SNS → SMS)** rather than PagerDuty — see §5 note on path change. CloudWatch composite alarms → SNS topic `fortress-ir-paging-{pipeline}` → SMS to on-call rotation; sandbox / 10DLC operational gap documented in `docs/runbooks/sns-sms-10dlc.md` (production SMS requires 10DLC campaign registration per bank account, tracked as a B9 follow-on operator step, not an HRI gate). First tabletop drill scheduled for +14 days. |
| B10 | Complete annual penetration test | ○ Open | Vendor not yet engaged. Pre-submission. |

**HRI summary:** 4 of 10 fully closed (B1, B2, B3, B9). 2 partial / compensating (B4, B7). 4 fully open (B5, B6, B8, B10). All remaining open HRIs are owned by non-infrastructure functions — the infrastructure-side surface is closed.

### 4.2 Medium / Low Risk Items

| # | Item | Status | Notes |
|---|---|---|---|
| S1 | CloudWatch log retention 365 days | ● Closed | CloudFormation templates updated; live-state drift fix executed 2026-06-29 (10 non-compliant log groups all flipped to 365 days, including 3 Aurora postgresql groups that were at NEVER-EXPIRE — a critical FFIEC gap). Re-audit clean. |
| S2 | ELB access logs to S3 | ● Closed | Deployed on both pipelines 2026-06-28. SSE-S3, bucket-owner-enforced, all public-access blocked, lifecycle Glacier @ 90 days / expire @ 365 days. |
| S3 | IAM Access Analyzer | ● Closed | Account-scoped analyzer deployed via Pipeline 1 on 2026-06-28. Covers all IAM principals in the account. |
| S4 | ECR Enhanced Scanning | ● Closed | Enabled in Fortress account 2026-06-29: Inspector v2 ECR `ENABLED`; ECR registry `ENHANCED` + `CONTINUOUS_SCAN` covering all 3 repositories. Initial CVE backlog surfaces in Security Hub via FSBP within ~15 min; triage flow per `docs/runbooks/inspector-triage.md` (CRITICAL 7 d / HIGH 30 d / MEDIUM quarterly SLAs). |
| S5 | Pipeline 1 Redis Multi-AZ | ○ Open | Pre-production. |
| S6 | Pipeline 1 Aurora reader replica | ○ Open | Pre-production. |
| S7 | Cross-region DR for Pipeline 1 | ○ Open | Target +90 days. |
| S8 | DR drill cadence | ○ Open | Target +60 days. |
| S9 | Pipeline 1 staging account | ○ Open | Target +60 days. |
| S10 | AWS Budgets + Cost Anomaly Detection | ● Closed | Deployed on both pipelines 2026-06-28. Monthly budget, anomaly monitor, and SNS subscriptions confirmed. |
| S11 | Macie on bank S3 buckets | ○ Open | Low priority. Target +60 days. |
| S12 | SOC 2 Type II | ○ Open | Q4 2026 audit engagement. |

**Med/Low summary:** 5 of 12 fully closed (S1, S2, S3, S4, S10). 0 partial. 7 open.

### 4.3 Additional Observability Controls

Two account-level controls beyond the original gap list, now in place:

| Control | Status | Notes |
|---|---|---|
| CIS 4.4 IAM policy-change EventBridge alarm to SNS | ● Closed | 17 IAM API actions monitored at account scope. Email subscriber standardised on Fortress team alias `jamie@fortresslabs.dev` on 2026-06-29 across all SNS topics (security alerts, billing alerts on both pipelines, alarms, digest). |
| CloudFormation duplicate-Export regression catcher | ● Closed | Pre-deploy lint caught a latent template bug; safeguard now in place. |

---

## 5. Recent Deployment Activity

| Date | Activity |
|---|---|
| 2026-06-30 | **HRI B9 closed.** IR runbook promoted from v0.1 to v1.0 with CTO sign-off (`docs/runbooks/incident-response.md`). Paging path changed from PagerDuty (previously assumed) to **AWS-native SNS → SMS** to keep the IR critical path entirely inside the bank's own AWS account — no third-party SaaS dependency on the bank-side data plane, and no additional vendor contract gating. CloudWatch composite alarms on each pipeline publish to SNS topic `fortress-ir-paging-{pipeline}`, which fans out SMS to the on-call rotation. SNS-SMS sandbox / 10DLC campaign-registration operational gap documented in `docs/runbooks/sns-sms-10dlc.md` (handled per bank account at deploy time; tracked as a B9 follow-on operator step, not an HRI gate). Self-service on-call contact onboarding captured as a B9 follow-on. First tabletop drill scheduled for +14 days. |
| 2026-06-29 | **HRI B7 Eng-side complete** (closure remains partial pending Legal sign-off on DPA). Deprovisioning script `infra/scripts/deprovision-p2.sh` upgraded from skeleton to production-ready — real CloudFront disable-wait-delete, WAF disassociate-then-delete with lock-token re-fetch, RDS final snapshot with retention tagging, CFN stack teardown in reverse dependency order, cross-account revoke instructions. Dry-run validated end-to-end against testbank (all 7 P2 stacks discovered, all resource IDs resolved, zero AWS-state mutation). LIVE mode requires both `CONFIRM=DELETE-<bank-id>` + a non-empty `DEPROVISION_TICKET_ID`. Exports S3 bucket (Object-Lock COMPLIANCE 1825d) intentionally never touched. |
| 2026-06-29 | **HRI B2 closed.** Cognito MFA enforced on the testbank bank-dashboard user pool: `MfaConfiguration: ON`, `EnabledMfas: [SOFTWARE_TOKEN_MFA]` (TOTP-only; SMS is rejected by a CI invariant — SIM-swap risk). Rollout collapsed staged Option C (`OPTIONAL` → grace window → `ON`) into a same-day flip because testbank has no real users. Enrolment validated end-to-end with a TOTP authenticator app. Per-bank deploys replicate the same template. Runbook for production rollouts (with the proper grace window), end-user enrolment, and admin-reset recovery: `docs/runbooks/cognito-mfa.md`. Side-quest: imported two orphan Cognito groups (`Analyst`, `Admin`) into CFN management via resource-import change set; the groups had been created out-of-band in June and were causing template drift. |
| 2026-06-29 | Phase 3 operator runs completed in live AWS state. ECR Enhanced Scanning enabled (Inspector v2 + registry `ENHANCED`/`CONTINUOUS_SCAN`). CloudWatch log-retention drift fix applied to all 10 non-compliant log groups (now ≥ 365 days, including 3 Aurora postgresql groups previously at NEVER-EXPIRE). SNS subscriber cutover: three CloudFormation stacks updated to standardise email subscribers on the Fortress team alias `jamie@fortresslabs.dev` and drop the legacy personal email. |
| 2026-06-28 | Phase 2 observability fully deployed to AWS: VPC Flow Logs (both pipelines), ALB access logs, IAM policy-change alarm, IAM Access Analyzer, Budgets and Cost Anomaly Detection. All six CloudFormation stacks reached UPDATE_COMPLETE. |
| 2026-06-27 | Phase 3 code-complete: ECR Enhanced Scanning bootstrap script, CloudWatch retention template change, retention drift-detector script. |
| 2026-06-27 | Phase 4 invariant enforcement: `DbMultiAz=true` belt-and-suspenders (CloudFormation default, top-level Rules block, CI-time invariant verifier). Negative-tested. |
| 2026-06-27 | Phase 1 documentation deliverables completed: security.txt published, IR runbook v0.1, secrets manual-rotation runbook, deprovisioning-script skeleton, DPA draft. |

---

## 6. Timeline to Submission

| Phase | Status | Target |
|---|---|---|
| Phase 1 — Close 30-day infrastructure blockers | ● Complete | — |
| Phase 2 — Additive observability (Flow Logs, ALB logs, IAM alarms, Access Analyzer, Budgets) | ● Complete | — |
| Phase 3 — Configuration flips (ECR scanning enable, log-retention drift fix) | ● Complete | — |
| Phase 4 — Invariant enforcement | ● Complete | — |
| Phase 5 — Marketplace integration build | ○ Not started | +60 days |
| Phase 6 — Policies, penetration test, onboarding portal | ○ Not started | +90 days |
| Phase 7 — Internal dry-run | ○ Not started | Post Phase 6 |
| Phase 8 — Open FTR ticket via AWS Partner Central | ○ Not started | Q4 2026 |
| Phase 9 — AWS Marketplace listing live | ○ Not started | Q1 2027 |

**Realistic earliest submission: Q4 2026. Realistic earliest listing live: Q1 2027.**

---

## 7. Critical-Path Open Items

These items gate the FTR ticket submission. Owners are responsible for closing them before Phase 8.

| Item | Owner | Earliest Finish |
|---|---|---|
| B4 — Auto-rotation (full) | Backend Engineering | +30–60 days |
| B5 — Marketplace Entitlement + Metering + SNS | Backend Engineering | +60 days |
| B6 — Customer onboarding portal | Engineering | +60–90 days |
| B7 — Deprovisioning + DPA full sign-off | Engineering + Legal | +30 days |
| B8 — 8 security policies + /security page | CTO | +60 days |
| B10 — Annual penetration test | CTO + Vendor | Pre-submission |

---

## 8. Asks of the AWS Premium Partner

This is a **pre-submission gap-analysis review**, not a formal FTR self-assessment walkthrough. We are not asking for the FTR ticket to be opened. We are asking for an SA review of the remediation plan and evidence quality so that B5/B6/B8/B10 can be built in parallel with confidence.

1. **Validate the HRI closure evidence for B1, B2, B3, B9** against what the FTR reviewer will actually want to see — flag anything that won't survive scrutiny so we can fix it now, not in the ticket. Specifically: (a) VPC Flow Logs retention + capture-completeness on both pipelines, (b) Cognito `MfaConfiguration: ON` + TOTP-only + CI invariant as a complete control, (c) `DbMultiAz=true` default + top-level Rules block + CI invariant as belt-and-suspenders, (d) IR runbook v1.0 + SNS-SMS paging chain as a complete substitute for a PagerDuty-style third-party pager.
2. **Sanity-check the B4 and B7 compensating-control framing.** Is the manual-rotation runbook + 30–60d auto-rotation remediation target acceptable as a "Partial" for B4, given the documented Aurora rotation-lambda regression of 2026-05-13? For B7, does the production-ready deprovisioning script + bank-owned CMK pending-deletion + Object-Lock-COMPLIANCE-untouched posture read cleanly, or does the reviewer typically want the DPA signed before this can be claimed as anything stronger than "Partial"?
3. **Confirm the Q4 2026 submission window is realistic** given B5/B6/B8/B10 owner timelines, or flag if we should re-sequence — e.g., decouple the AWS Marketplace listing track (B5/B6) from the FTR pass itself so that B8 + B10 can carry an earlier submission. We are open to either path.
4. **Identify any HRI we are not tracking.** In particular, items the partner has seen trip up similar SaaS-in-bank-account architectures: data-plane / control-plane split at the AWS account boundary, customer-owned CMK with zero Fortress grant, WORM-retained exports with 1825-day Object Lock COMPLIANCE, and cryptographic-deletion-on-CMK-removal as the customer-data-deletion path.

Async comments on this document work; alternatively we can schedule a 60-minute walkthrough at your convenience.

---

## 9. Contact

- **Primary:** *[Name, Title]* — *[email]*
- **Operations:** *[Name, Title]* — *[email]*

---

*Next status update planned for: after B7 Legal sign-off on the DPA, or +30 days, whichever comes first.*

*This document is a snapshot. The live execution tracker and full self-assessment workpaper are maintained internally and available on request.*
