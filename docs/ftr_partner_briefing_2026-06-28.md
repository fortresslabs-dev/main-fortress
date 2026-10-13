# Fortress Labs — FTR Readiness Briefing (In Progress)

> **Audience:** AWS Premium Partner SA + Fortress DevOps.
> **Purpose:** Share current FTR readiness posture as a single self-contained handout. This is a *snapshot* — for the live workpapers see `docs/aws_ftr_marketplace_p1_p2.md` (master self-assessment) and `docs/ftr_tracker.md` (execution tracker).
> **Snapshot date:** 2026-06-28.

---

## 1. Executive summary

Fortress Labs operates a banking-compliance SaaS deployed in two architecturally isolated pipelines: a multi-tenant **Fortress Cloud** account that holds only public regulatory text (Pipeline 1), and a single-tenant **per-bank stack** deployed into the bank's own AWS account that holds all bank operational data (Pipeline 2). Bank PII, transaction data, OFAC matches, and audit artifacts never leave the bank's account; they are encrypted at rest with **bank-owned KMS CMKs** to which Fortress holds zero grant, and immutable WORM-retained for 5 years per FFIEC and OFAC recordkeeping requirements. The platform runs on Fargate (Graviton), Aurora Serverless v2 (pgvector + tsvector — no OpenSearch), and Amazon Bedrock (Claude Sonnet 4.5 / Haiku 4.5 via inference profiles). GuardDuty, Security Hub (PCI DSS + FSBP), and CloudTrail with log-file validation are always-on.

This briefing is a pre-submission self-assessment; the §3 gap list is the active execution plan toward Marketplace listing in Q1 2027.

---

## 2. Scope & data-plane isolation

> **The single most load-bearing sentence in this submission:** customer (bank) operational data — PII, transaction records, OFAC matches, KYC payloads, board reports — **never enters Fortress's AWS account**. The data plane and the SaaS control plane are in different AWS accounts owned by different parties.

| Plane | Account owner | What it holds | Customer data? | KMS custody |
|---|---|---|---|---|
| **Pipeline 1 (P1)** — Fortress Cloud | **Fortress** | Public regulatory text only (CFR, OFAC SDN public lists, FFIEC handbook excerpts, enforcement actions). Multi-tenant. | **No** | Fortress CMKs |
| **Pipeline 2 (P2)** — Bank account | **Bank** | All bank operational data: wires, KYC, OFAC match decisions, workflow approvals, board reports (WORM 1825d). Single-tenant per bank. | **Yes** | Bank-owned CMKs only — Fortress has zero grant |

### What this means for the FTR reviewer

1. **Tenancy isolation is enforced at the AWS account boundary**, not in application code. There is no `bank_id` row filter in P2 because there is no other bank's data in the P2 database to filter against.
2. **A Fortress-side breach has zero customer-data blast radius.** A full compromise of Fortress's AWS account exposes public regulatory text (already public) and Marketplace billing metadata — no PII, no transaction records, no OFAC decisions.
3. **A bank-side breach is contained to that one bank's account.** No VPC peering, no Transit Gateway, no cross-account IAM trust to other banks or to Fortress.
4. **Customer-data-deletion proof is cryptographic, not procedural.** The bank deletes their CMK (30-day pending window); all WORM-retained exports become unreadable. Fortress cannot prevent or reverse this.

### Architecture diagrams (in companion WAFR)

- Pipeline 1 (Fortress Cloud) — `docs/aws_well_architected_review_p1_p2.md` §2.1
- Pipeline 2 (Bank-owned account) — `docs/aws_well_architected_review_p1_p2.md` §2.2
- Cross-account trust model — `docs/aws_well_architected_review_p1_p2.md` §2.3

---

## 3. Current FTR readiness status

Per AWS FTR risk classification: **HRI** (High-Risk Issue — must close pre-submission), **Med**, **Low**.

**Status legend:** ✅ closed (code + deployed where applicable) · 🟡 partial / compensating control in place · ❌ open.

### 3.1 HRI blockers (FTR §13.1)

| # | Item | § | Owner | Status | Evidence |
|---|---|---|---|---|---|
| B1 | VPC Flow Logs on both VPCs | 3.4, 5.4 | Agent 1 | ✅ | Deployed 2026-06-28. Stacks `fortress-p1-vpc-production` + `fortress-p2-vpc-testbank`; Flow Logs `fl-0b8eb1bd065040ae3` (P1) + `fl-0d77722788efa8852` (P2), 365d retention, `TrafficType=ALL`. |
| B2 | Enforce Cognito MFA on bank dashboard user pool | 2.5 | Agent 4 | ❌ | — |
| B3 | Flip P2 `DbMultiAz` default to `true` + block prod deploys when false | 6.6 | Agent 1 | ✅ | Commit `8389d15` — CFN default flipped, new `Rules` block + `scripts/verify_ftr_invariants.py` wired into `ci-cd.yml`. |
| B4 | Safe Secrets Manager rotation (or D2 compensating control) | 4.6 | Eng | 🟡 | D2 manual rotation runbook landed (`docs/runbooks/secrets-rotation.md`). Auto-rotation still disabled — blocked on fixing the PostgreSQLSingleUser path that broke Aurora 2026-05-13. |
| B5 | Marketplace Entitlement + Metering + SNS integration | 9.4 | Eng | ❌ | — |
| B6 | Customer onboarding portal | 9.4, 11.5 | Eng | ❌ | — |
| B7 | Deprovisioning script + DPA template | 11.5 | Eng + Legal | 🟡 | C2 skeleton `infra/scripts/deprovision-p2.sh` + C3 DPA draft landed. Legal sign-off + scripted KMS-key-pending-deletion proof still open. |
| B8 | Publish 8 security policies + security page | 12.3 | CTO | ❌ | Drafts exist (all ⚠️ Draft); none published, no `/security` page live. |
| B9 | Finalize + sign-off IR runbook + activate PagerDuty | 10.4 | CTO | 🟡 | IR runbook v0.1 landed at `docs/runbooks/incident-response.md`. Sign-off + PagerDuty integration + first tabletop drill still open. |
| B10 | Annual pen test | 12.3 | CTO | ❌ | Vendor not yet engaged. |

**HRI summary:** 2 of 10 fully closed (B1, B3). 3 partial / compensating-control (B4, B7, B9). 5 fully open (B2, B5, B6, B8, B10). **Every fully-open item has a non-infra owner** (Eng / CTO / Agent 4) — the infra-side surface area is closed.

### 3.2 Medium / Low items (FTR §13.2)

| # | Item | § | Risk | Status | Evidence |
|---|---|---|---|---|---|
| S1 | CW log retention 365d | 5.4 | Med | ✅ (templates) / 🟡 (drift) | Commit `286f468`. Drift catcher `infra/scripts/audit-cw-log-retention.sh` reports 12 legacy log groups still <365d / NEVER-EXPIRE (3 RDS at NEVER, 3 container-insights @1d, 1 legacy `/ecs/fortress-pipeline2`, 2 already-fixed-in-CFN waiting next redeploy, 3 misc). Operator `--fix` pending. |
| S2 | ELB access logs enabled | 5.4 | Med | ✅ | Deployed 2026-06-28. P1 via CFN; P2 out-of-band (compute-stack drift). Buckets `fortress-p1-alb-logs-520385696621-production` + `fortress-p2-alb-logs-testbank-production`, SSE-S3 + BucketOwnerEnforced + all PAB + GLACIER@90d → expire@365d. |
| S3 | IAM Access Analyzer | 2.5, 5.4 | Med | ✅ | Deployed 2026-06-28 (account-singleton via P1 — single analyzer covers all IAM principals). `fortress-p1-account-analyzer-production` (Type=ACCOUNT, ACTIVE) + finding rule → `SecurityAlertsTopic`. |
| S4 | ECR Enhanced Scanning | 7.1 | Med | 🟡 | Bootstrap script `infra/scripts/enable-ecr-enhanced-scanning.sh` (idempotent) + triage runbook `docs/runbooks/inspector-triage.md` (SLAs CRIT 7d / HIGH 30d / MED quarterly). Inspector ECR currently DISABLED — enable + triage pending. |
| S5 | P1 Redis MultiAZ | 6.6 | Med | ❌ | — |
| S6 | P1 Aurora reader + writer | 6.6 | Med | ❌ | — |
| S7 | Cross-region DR for P1 | 6.3 | Med | ❌ | — |
| S8 | DR drill cadence | 6.6 | Med | ❌ | — |
| S9 | P1 staging account | WAFR §3.1 | Med | ❌ | — |
| S10 | AWS Budgets + Cost Anomaly Detection | WAFR §7.3 | Med | ✅ | Deployed 2026-06-28. Stacks `fortress-p1-billing-production` ($500/mo) + `fortress-p2-billing-testbank` ($500/mo). CUSTOM anomaly monitor + IMMEDIATE subscription @ $100 abs. Two confirmed SNS subscribers per topic. |
| S11 | Macie on bank S3 | WAFR §4.5 | Low | ❌ | — |
| S12 | SOC 2 Type II | 12.1 | Med | ❌ | — |

**Med/Low summary:** 3 of 12 fully closed (S2, S3, S10). 2 partial (S1 templates done / 12 legacy groups pending operator `--fix`, S4 bootstrap done / Inspector enable pending). 7 fully open (S5, S6, S7, S8, S9, S11, S12).

### 3.3 Bonus controls (not in original §13, observable from this snapshot)

| Tracker # | Item | Status | Evidence |
|---|---|---|---|
| #13 (L4) | IAM policy-change EventBridge → SNS alarm (CIS 4.4 events) | ✅ | Deployed 2026-06-28. `fortress-p1-iam-policy-change-production` rule → `SecurityAlertsTopic`; 17 IAM actions matched. Account-singleton — single P1 rule covers all IAM mutations regardless of which P2 tenant triggers them. |
| #12 (S2) bonus | YAML duplicate-`Export:` regression catcher | ✅ | Commit `10fb33f` — un-hijacked `DigestTopicArn` export in P1 compute template that would have shadowed scrape-monitor downstream consumers on next deploy. |

---

## 4. Recent deployment activity

Latest entries from `docs/ftr_tracker.md` change log (full log lives in the tracker file):

| Date | Change |
|---|---|
| 2026-06-28 | **Phase 2 #11–#15 fully deployed to AWS.** VPC Flow Logs (B1), ALB access logs (S2), IAM policy-change alarm (L4), Access Analyzer (S3), Budgets + CAD (S10). All 6 CFN stacks UPDATE_COMPLETE. P2 #13/#14 covered account-wide via P1 (IAM is global-service-routed to us-east-1; `Type=ACCOUNT` analyzer is also account-singleton). Two template-deploy fixes landed in commit `9fc3cc2`: DIMENSIONAL→CUSTOM monitor (account-singleton conflict), string-serialized `ThresholdExpression` (CFN schema differs from cost-explorer SDK shape). |
| 2026-06-28 | Tracker + FTR doc §13.1 / §13.2 / §14 refreshed (commit `6f35110`) — Status + Evidence columns added; §14 timeline restructured to show actual vs planned progress + critical-path open-items table. |
| 2026-06-27 | Phase 1 docs closed: `security.txt`, IR runbook v0.1 (partial B9), manual secrets-rotation runbook (compensates B4), `deprovision-p2.sh` skeleton (partial B7), DPA draft (partial B7). |
| 2026-06-27 | Phase 4 #18 (B3) closed — `DbMultiAz=true` enforced via belt-and-suspenders: CFN default flip + top-level `Rules` block + `scripts/verify_ftr_invariants.py` CI guard. Negative-tested. |
| 2026-06-27 | Phase 3 deliverables landed: #16 ECR Enhanced Scanning bootstrap script + triage runbook (operator-invoked, run pending); #17 CW retention 30→365 in templates + drift catcher script. |

---

## 5. Timeline & critical path

| Phase | Duration | Planned output | Actual (2026-06-28) |
|---|---|---|---|
| **Phase 1 — close 30-day infra blockers** | 30 days | B1, B3 closed; B4 / B7 / B9 compensating controls | ✅ **Done.** B1 + B3 closed via CFN; B4 compensating (D2 runbook), B7 partial (C2 + C3), B9 partial (IR v0.1). All 18 tracker items ✅. |
| **Phase 2 — additive observability** | concurrent | n/a (new add-on) | ✅ **Deployed 2026-06-28.** Closes §13.2 S2, S3, S10. |
| **Phase 3 — config flips** | concurrent | n/a | ✅ Code/scripts complete. S4 bootstrap + S1 retention templates ready; operator-invoked runs pending. |
| **Phase 4 — invariants enforcement** | concurrent | n/a | ✅ #18 done. CFN `Rules` + CI guard block `DbMultiAz=false` in prod. |
| **Phase 5 — Marketplace integration build** | 30–60 days | B5, B6 in QA | ❌ Not started. **Critical path.** |
| **Phase 6 — policies, pen test, portal** | 60–90 days | B4 full / B6 / B7 full / B8 / B10 closed | ❌ Not started. **Critical path.** Also B2 (Cognito MFA). |
| **Phase 7 — internal dry-run** | 1 week | End-to-end walk-through with eng leadership | ❌ Pending Phase 5 + 6. |
| **Phase 8 — open FTR ticket via Partner Central** | 2–4 weeks | AWS Partner SA reviews artifacts | ❌ Pending Phase 7. |
| **Phase 9 — listing live** | 1–2 weeks post-pass | Marketplace listing visible | ❌ Pending Phase 8. |

### Critical-path open items (gate the FTR ticket)

| Item | Owner | Earliest start | Earliest finish |
|---|---|---|---|
| B2 Cognito MFA | Agent 4 | now | +30d |
| B4 auto-rotation (full) | Eng | now (blocked on Aurora rotation-lambda fix) | +30–60d |
| B5 Marketplace Entitlement + Metering + SNS | Eng | now | +60d |
| B6 onboarding portal | Eng | after B5 contract types stabilize | +60–90d |
| B7 full deprovision + DPA sign-off | Eng + Legal | now (skeleton + draft already in) | +30d |
| B8 8 policies + `/security` page | CTO | now (drafts exist) | +60d |
| B9 IR sign-off + PagerDuty | CTO | now | +30d |
| B10 annual pen test | CTO | after B5/B6/B8 — pen test should hit the merged surface | Pre-submission |

**Realistic earliest submission: Q4 2026.** **Realistic earliest listing live: Q1 2027.** **Infra-side work is no longer the bottleneck** — every remaining HRI has a non-Agent-1 owner.

---

## 6. Operationally activated vs code-complete

A submission-readiness snapshot needs to distinguish *committed code* from *running posture*. Three items are code-complete but not yet operationally activated:

| Item | Why not activated | What unblocks it |
|---|---|---|
| **S4 ECR Enhanced Scanning** | Inspector ECR account status = **DISABLED**. Bootstrap script written but not run. | One-shot: `infra/scripts/enable-ecr-enhanced-scanning.sh`. First scan produces a CVE backlog ops triages per `docs/runbooks/inspector-triage.md`. |
| **S1 CW retention drift** | 12 log groups still <365d / NEVER-EXPIRE (RDS, container-insights, legacy `/ecs/fortress-pipeline2`). | Operator decision + `infra/scripts/audit-cw-log-retention.sh --fix` (adds ~12× storage cost on affected groups). |
| **B3 DbMultiAz on existing P2 Aurora** | CFN default + CI guard are in code, but the *running* testbank Aurora won't flip to MultiAz until next P2 compute-stack update — deferred because of compute-stack drift (`project_p2_compute_stack_drift_2026_06_28`). | Reconcile P2 compute stack drift first; or roll the MultiAz flip via targeted RDS-only stack action. |

These are deliberate gating decisions, not oversights. The flips are operator-invoked because each has non-trivial side-effects (CVE triage workload, storage-cost ratchet, prod compute-stack update on drifted state).

---

## 7. Asks of the AWS Premium Partner SA

> *Placeholders — to be filled in by Fortress before sharing.*

- [ ] *Ask 1: ...*
- [ ] *Ask 2: ...*
- [ ] *Ask 3: ...*

---

## 8. Evidence index

The artifacts an FTR reviewer would actually examine.

| Topic | File / location |
|---|---|
| **Master self-assessment** (workpaper) | `docs/aws_ftr_marketplace_p1_p2.md` |
| **Execution tracker** (workpaper) | `docs/ftr_tracker.md` |
| **Companion WAFR** | `docs/aws_well_architected_review_p1_p2.md` |
| **Submission package staging** | `docs/ftr_evidence/` (01_wafr, 02_arch_diagram, 03_security_tools, 04_self_assessment) |
| **Infrastructure (CFN templates)** | `infra/cloudformation/pipeline{1,2}-*.yml` |
| **Deploy scripts** | `infra/scripts/setup-p1-auto.sh`, `infra/scripts/deploy-p2.sh`, `infra/scripts/register-bank-ip.sh` |
| **CI/CD pipeline** (includes FTR invariants check) | `.github/workflows/ci-cd.yml` |
| **Code owners** | `.github/CODEOWNERS` |
| **Bank onboarding runbook** | `infra/setup/PIPELINE1_SETUP.md` |
| **Agent runbooks** | `docs/runbooks/agent-{1,2,3,4}-*.md` |
| **Incident response runbook (v0.1)** | `docs/runbooks/incident-response.md` |
| **Secrets manual-rotation runbook** | `docs/runbooks/secrets-rotation.md` |
| **Inspector CVE triage runbook** | `docs/runbooks/inspector-triage.md` |
| **CW retention drift catcher** | `infra/scripts/audit-cw-log-retention.sh` |
| **FTR invariants CI guard** | `scripts/verify_ftr_invariants.py` |
| **DPA template (draft)** | `docs/legal/dpa_template.md` |
| **Deprovisioning script (skeleton)** | `infra/scripts/deprovision-p2.sh` |
| **FFIEC / PCI control mapping** | `docs/pci_ffiec_control_mapping_matrix.md` |
| **OFAC calibration runbook** | `docs/runbooks/ofac-calibration-gold-set.md` |

### Submission packaging (when the FTR ticket actually opens)

Per `docs/ftr_evidence/README.md`:

1. Zip `02_arch_diagram/` + `03_security_tools/` (the reviewer wants visual proof, not markdown).
2. Attach the WAFR completion record from `01_wafr/`.
3. Paste the FTR doc §13 status table into the Partner Central form as evidence per-control.
4. Link this repo's `docs/aws_ftr_marketplace_p1_p2.md` only if the reviewer asks (it's a workpaper, not a deliverable).
