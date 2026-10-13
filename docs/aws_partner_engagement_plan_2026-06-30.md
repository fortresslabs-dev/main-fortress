# Fortress Labs ↔ AWS Premium Partner — Engagement Plan

**Document type:** Proposed engagement plan (for partner countersignature)
**Date drafted:** 30 June 2026
**Drafted by:** Fortress Labs — Engineering & DevOps
**Parties:** Fortress Labs Inc. (the "Customer") and *[Premium Partner legal name]* (the "Partner")

---

## 1. Background

Fortress Labs operates a banking-compliance SaaS platform deployed across two architecturally isolated AWS pipelines. The platform is approaching readiness for two AWS programs in parallel:

1. **AWS Well-Architected Framework Review (WAFR)** — Premium Partner-led architectural assessment against the 6 pillars + applicable lenses.
2. **AWS Marketplace listing** — gated on completion of the AWS Foundational Technical Review (FTR) self-assessment and closure of the 10 FTR High-Risk Issues (B1–B10).

The Partner has confirmed availability to support both programs. This document proposes the engagement scope, deliverables, timeline, and roles. It is **not a contract** — commercial terms (rate card, payment schedule, SoW) are to be supplied by the Partner and attached as Appendix A before countersignature.

---

## 2. Engagement Scope — Two Workstreams

### Workstream A — WAFR (primary, near-term)

| Item | Detail |
|---|---|
| Workload | Fortress Compliance Platform — Pipeline 1 (Fortress Cloud) + Pipeline 2 (bank-owned account) |
| Region | us-east-1 primary; Bedrock `us.*` inference profile fans out to us-east-1 / us-east-2 / us-west-2 |
| Review type | Premium Partner-led WAFR, **Standard** (not Lite) |
| Lenses | Base 6 pillars + SaaS Lens + Financial Services Industry Lens + Security Lens *(to be confirmed by Partner in Phase 0)* |
| Pre-read packet | `docs/wafr_partner_status_2026-06-30.md` (cover) + `docs/aws_well_architected_review_p1_p2.md` (full workpaper) |
| WA Tool workspace | To be created in Partner's account; Fortress added as workload-team member |

### Workstream B — Marketplace Listing (secondary, Q4 2026 target)

| Item | Detail |
|---|---|
| Listing type | AWS Marketplace SaaS, contract-based with metered usage |
| FTR gate | 10 HRIs (B1–B10); current status 4 closed / 2 partial / 4 open |
| Pre-read packet | `docs/ftr_partner_status_2026-06-28.md` (cover) + `docs/aws_ftr_marketplace_p1_p2.md` (full self-assessment) |
| Target FTR ticket submission | Q4 2026 |
| Target listing-live | Q1 2027 |

---

## 3. Deliverables

### Workstream A — WAFR

| # | Deliverable | Owner | Format |
|---|---|---|---|
| A1 | Async pre-read review of WAFR cover doc + workpaper | Partner SA | Written feedback on §3 (lens selection), §5.5 (expected HRIs), §9 asks 1–4 |
| A2 | WA Tool workload created with confirmed lenses | Partner SA | WA Tool URL shared with Fortress |
| A3 | WAFR workshop (90 min, live) | Partner SA + Fortress Eng | Pillar-by-pillar walkthrough, HRI flagging in the Tool |
| A4 | Workshop output report | Partner SA | WA Tool-generated PDF + executive summary |
| A5 | HRI remediation guidance | Partner SA + Fortress Eng | Per-HRI written recommendation; Fortress executes |
| A6 | Reassessment session (60 min) | Partner SA + Fortress Eng | Tool-driven re-scoring after Fortress remediates Critical + High HRIs |
| A7 | Final WAFR sign-off + AWS Well-Architected badge | Partner SA | Badge issued via WA Tool; supporting letter for bank CCO/CIO sponsors |

### Workstream B — Marketplace Listing

| # | Deliverable | Owner | Format |
|---|---|---|---|
| B1 | Marketplace listing scoping call (60 min) | Partner SA + Fortress Eng | Confirm gating items, timeline, owner split |
| B2 | B5 — Marketplace Entitlement + Metering + SNS build review | Partner SA | Architecture review of Fortress's implementation; not implementation labour |
| B3 | B6 — Customer onboarding portal architecture review | Partner SA | UX + IAM + tenant-provisioning flow review |
| B4 | B8 — 8 security policy reviews + /security page review | Partner SA | Document review against AWS Partner standards |
| B5 | B10 — Pentest vendor introductions (≥ 2 vendors with banking SaaS experience) | Partner | Vendor short-list with engagement-cost ranges |
| B6 | FTR ticket submission support — Partner Central walkthrough | Partner SA | Live session to open the FTR ticket once HRIs are closed |
| B7 | Post-listing Marketplace optimization session (optional) | Partner SA | Conversion-rate review after first 60 days live |

---

## 4. Timeline

### Workstream A — WAFR

| Phase | Deliverable | Target (T = engagement kickoff) |
|---|---|---|
| Phase 0 | Pre-read packet handover + lens confirmation | T+0 |
| Phase 1 | Async pre-read review (A1) | T + 1 week |
| Phase 2 | WA Tool workload created (A2) | T + 1 week |
| Phase 3 | WAFR workshop (A3) | T + 2 weeks |
| Phase 4 | Fortress HRI remediation | T + 3–6 weeks |
| Phase 5 | Reassessment (A6) | T + 7 weeks |
| Phase 6 | Final WAFR sign-off + badge (A7) | T + 8 weeks |

### Workstream B — Marketplace Listing

| Phase | Deliverable | Target |
|---|---|---|
| Phase 0 | FTR snapshot review + scoping call (B1) | T + 2 weeks |
| Phase 1 | B5 / B6 architecture reviews (B2, B3) | T + 4–6 weeks |
| Phase 2 | B8 policy reviews (B4) | T + 6–8 weeks |
| Phase 3 | B10 pentest vendor intros (B5) | T + 2 weeks (parallel) |
| Phase 4 | Pentest engagement (vendor-led, partner observes) | T + 8–12 weeks |
| Phase 5 | FTR ticket submission (B6) | Q4 2026 |
| Phase 6 | Listing-live milestone | Q1 2027 |
| Phase 7 | Post-listing optimization (B7, optional) | Q1 2027 + 60 days |

**Sequencing decision (open question for Partner):** should the WAFR workshop run *before*, *parallel to*, or *after* the B5/B6 build? Recommendation requested from Partner in Phase 0 of Workstream A.

---

## 5. Roles and Responsibilities

| Activity | Fortress | Partner |
|---|---|---|
| Provide CFN templates, runbooks, evidence | ✅ | — |
| Grant Partner SA read-only IAM role into Fortress AWS account (Pipeline 1) for evidence verification | ✅ | — |
| Bank-account access | ❌ Not required — Partner reviews testbank evidence only via Fortress-shared exports | — |
| WA Tool workload creation + workspace management | — | ✅ |
| WAFR workshop facilitation | — | ✅ |
| HRI remediation labour | ✅ | — |
| HRI remediation guidance / pattern review | — | ✅ |
| WAFR badge issuance | — | ✅ |
| Marketplace listing copy + screenshots | ✅ | — |
| FTR ticket creation in Partner Central | ✅ (with Partner support) | ✅ |
| Pentest vendor selection + contract | ✅ | ✅ (intros only) |
| Pentest execution | — | ❌ Out of scope for Partner (third-party vendor) |
| Application code changes | ✅ | ❌ Not requested |

---

## 6. Assumptions

1. Partner SA has authority to issue the WAFR badge and to support FTR ticket submission via Partner Central.
2. Partner's engagement profile includes the **Financial Services Industry Lens**; if not, Fortress will accept base-pillar-only WAFR with FSI mappings appended as supplementary evidence.
3. Fortress will provide a read-only IAM role into the Fortress Cloud (Pipeline 1) AWS account scoped to `cloudformation:Describe*`, `ecs:Describe*`, `rds:Describe*`, `kms:DescribeKey`, `iam:Get*`, `iam:List*`, `s3:GetBucket*`, `s3:ListBucket`, `wafv2:Get*`, `wafv2:List*`. No bank-side account access is required or offered.
4. All review artefacts (WAFR badge, workshop report, HRI list) belong to Fortress and may be shared with pilot banks, investors, and FFIEC examiners under NDA.
5. Engagement may pause for up to 30 days at Fortress's request without re-scoping (e.g., to allow HRI remediation labour to complete).

---

## 7. Out of Scope

- Application code changes (Fortress Eng owns).
- Bedrock model selection or AI cost optimization.
- Cost optimization beyond what's already templated in CloudFormation.
- Other AWS programs not named in §2 (ISV Accelerate, Foundational Capabilities Assessment, Public Sector Partner, etc.) — these may be added as a separate engagement.
- Direct access to any bank-owned AWS account.
- Pentest execution (Partner provides vendor introductions only).
- Legal review of the DPA template (Fortress's external counsel owns).

---

## 8. Commercial Terms (to be supplied by Partner)

*To be filled in by Partner as Appendix A before countersignature. Expected sections:*

- Rate card (SA day-rate, workshop fixed fee, reassessment fixed fee)
- Total fixed-fee or T&M estimate per workstream
- Payment schedule (milestone-tied vs. monthly)
- Cancellation / pause provisions
- Confidentiality / NDA reference
- Subcontractor disclosure (if Partner intends to use offshore SA staff on any workstream)
- Partner-side change-management process

Fortress's standard MSA + NDA can be shared on request if Partner does not have one to propose.

---

## 9. Success Criteria

| Workstream | Success criterion |
|---|---|
| WAFR | Final WAFR sign-off issued; AWS Well-Architected badge granted; workshop report received; ≥ 90% of Critical + High HRIs closed within the remediation window. |
| Marketplace listing | FTR ticket opened in Q4 2026; FTR ticket passed without P0 reviewer findings; Marketplace listing live in Q1 2027; first paid customer transaction processed end-to-end through Marketplace Metering. |

---

## 10. Open Items Requiring Partner Input Before Sign-off

| # | Item | Owner | Resolution by |
|---|---|---|---|
| 1 | Confirm FSI Lens availability in Partner's engagement profile | Partner | Phase 0 |
| 2 | Recommend WAFR-vs-B5/B6 sequencing (before / parallel / after) | Partner | Phase 0 |
| 3 | Supply rate card + total estimate per workstream (Appendix A) | Partner | Before countersignature |
| 4 | Confirm WAFR badge issuance authority and timeline post-reassessment | Partner | Phase 0 |
| 5 | Disclose any subcontracting plan for SA staffing | Partner | Before countersignature |
| 6 | Identify lead SA (WAFR) + lead SA (Marketplace) if distinct | Partner | Phase 0 |

---

## 11. Reference Documents (shared with this plan)

| Doc | Purpose | Read time |
|---|---|---|
| `docs/wafr_partner_status_2026-06-30.md` | WAFR pre-workshop snapshot | ~5 min |
| `docs/aws_well_architected_review_p1_p2.md` | Full WAFR workpaper | ~30 min |
| `docs/ftr_partner_status_2026-06-28.md` | FTR snapshot for Marketplace workstream | ~5 min |
| `docs/aws_ftr_marketplace_p1_p2.md` | Full FTR self-assessment | ~30 min |

---

## 12. Sign-off

**Fortress Labs Inc.**

| Field | Value |
|---|---|
| Signatory | Jamie Rahman |
| Title | Engineering, Fortress Labs |
| Email | jamie@fortresslabs.dev |
| Signature | ______________________________ |
| Date | ______________________________ |

**Premium Partner — *[Partner legal name]***

| Field | Value |
|---|---|
| Signatory | *[Name]* |
| Title | *[Title]* |
| Email | *[Email]* |
| Signature | ______________________________ |
| Date | ______________________________ |

---

*This document is a proposed engagement plan. It becomes binding only when both parties countersign and Appendix A (Commercial Terms) is attached.*

*Next revision planned: after Partner returns Appendix A, lens confirmation, and answers to §10 open items.*
