# Product Requirements Index — Competitive Gap Analysis

**Source:** 4-competitor gap analysis (Ascent RegTech, Wolters Kluwer OneSumX,
Thomson Reuters Regulatory Intelligence, Compliance.ai / Corlytics)
**Scope:** US regional banks only
**Date:** 2026-04-16

---

## Implementation Sequencing Rule

**All requirements must be implemented Pipeline 1 first, then Pipeline 2.**

Pipeline 1 (Fortress Cloud) is the source of truth — it scrapes regulators, stores rules,
runs the sync API, and produces the data Pipeline 2 consumes. Nothing in Pipeline 2 can be
built without its Pipeline 1 counterpart being complete and syncing correctly first.

| Pipeline 1 Role | Pipeline 2 Role |
|---|---|
| New connector — scrape source + extract rules | Register domain in `LocalComplianceProvider` |
| Store rules in `ComplianceRule` table | Receive rules via `UpdateService` sync |
| Expose rules via sync API (`/api/v1/sync/*`) | Validators and agents consume local rules |
| Run `ScraperScheduler` cycle daily | Run `UpdateService` sync daily |
| Build and validate data model migrations | Apply same migrations on customer DB |

**Exceptions (Pipeline 2 only — no Pipeline 1 counterpart needed):**
- REQ-016 GRC Integration — outbound push to customer GRC systems (ServiceNow, Archer);
  entirely customer-side, no Fortress Cloud involvement
- REQ-018 Policy Mapping — internal bank policy documents must never leave the VPC;
  all policy metadata stored in Pipeline 2 DB only

**Note — REQ-009 Fair Lending:** Previously marked P2-only but corrected. Pipeline 1 requires
three new URL groups added to `CfpbConnector` and `FdicConnector` (HMDA aggregate API,
FFIEC census flat files, FDIC BankFind). Pipeline 2 `FairLendingAnalyzer` cannot function
without that synced aggregate data.

---

## Priority Tiers

### Critical — Address to Enter Most Regional Bank Sales Conversations

| ID | Requirement | Type | P1 First | Then P2 | Source Competitor |
|---|---|---|---|---|---|
| [REQ-001](REQ-001_CRA_Compliance_Coverage.md) | CRA Compliance Coverage | New Connector + Validator | Connector + rules + sync | CRAValidator + test gen | OneSumX |
| [REQ-002](REQ-002_CALL_Report_FFIEC_Reporting.md) | CALL Report / FFIEC Reporting | New Connector + Validator | Connector + rules + sync | CallReportValidator + test gen | OneSumX |
| [REQ-010](REQ-010_Obligation_Universe_Coverage_Score.md) | Full Obligation Universe + Coverage Score | Enhancement + New Service | ObligationExtractor + sync | CoverageScorer + API + Dashboard | Compliance.ai |

---

### High — Required for Competitive Differentiation

| ID | Requirement | Type | P1 First | Then P2 | Source Competitor |
|---|---|---|---|---|---|
| [REQ-003](REQ-003_FFIEC_Cybersecurity_IT_Risk.md) | FFIEC Cybersecurity / IT Risk | New Connector | Connector + rules + sync | Domain registration + test gen | OneSumX |
| [REQ-004](REQ-004_Interest_Rate_Risk.md) | Interest Rate Risk (IRR) Program | New Connector + Validator | Connector + rules + sync | IRRProgramValidator + test gen | OneSumX |
| [REQ-005](REQ-005_CRE_Concentration_Risk.md) | CRE Concentration Risk | New Connector + Validator | Connector + rules + sync | CREConcentrationValidator + test gen | OneSumX |
| [REQ-011](REQ-011_Enforcement_Intelligence.md) | Enforcement Action Intelligence | New Connector + Scoring | Connector + risk scores + sync | Risk score consumption + Dashboard | Corlytics |
| [REQ-012](REQ-012_Compliance_Deadline_Calendar.md) | Compliance Deadline Calendar | New Dashboard + Enhancement | DeadlineTracker + sync | DeadlineAlertService + API + Dashboard | Thomson Reuters |
| [REQ-013](REQ-013_Regulatory_Change_Management.md) | Regulatory Change Management + Sign-Off | New Workflow + Data Model | Change detection + LLM summary + sync | Sign-off workflow + API + Dashboard | Ascent + TR |
| [REQ-014](REQ-014_State_Banking_Regulations.md) | State Banking Regulations (5 states) | New Connectors | 5 state connectors + sync | State domain lookup + test gen | Thomson Reuters |
| [REQ-019](REQ-019_Compliance_Officer_Dashboard.md) | Compliance Officer Master Dashboard | New Dashboard | All P1 data feeds completed | Aggregation API + Dashboard | All 4 competitors |

---

### Medium — Required for Full Platform Parity

| ID | Requirement | Type | P1 First | Then P2 | Source Competitor |
|---|---|---|---|---|---|
| [REQ-006](REQ-006_CECL_Credit_Risk.md) | CECL / Credit Risk Model Governance | New Connector | Connector + rules + sync | Domain registration + test gen | OneSumX |
| [REQ-007](REQ-007_Liquidity_Risk.md) | Liquidity Risk / CFP | New Connector + Validator | Connector + rules + sync | LiquidityRiskValidator + test gen | OneSumX |
| [REQ-008](REQ-008_Operational_Risk_RCSA.md) | Operational Risk / RCSA / Loss Events | New Connector + Data Model | Connector + rules + sync | Data model + RCSA workflow + test gen | OneSumX |
| [REQ-009](REQ-009_Fair_Lending_Analytics.md) | Fair Lending Statistical Analysis | Enhancement + New Service | 3 new URL groups added to CfpbConnector + FdicConnector; HMDA aggregate API, FFIEC census flat files, FDIC BankFind synced to P2 | FairLendingAnalyzer + API consumes synced aggregate data | OneSumX |
| [REQ-015](REQ-015_Interpretive_Guidance.md) | Interpretive Guidance Scraping | P1 Scraper Enhancement | Enhance existing connectors + sync | Richer MCP knowledge corpus auto-benefits | Thomson Reuters |
| [REQ-016](REQ-016_GRC_Integration.md) | GRC Integration (ServiceNow, Archer) | New Integration | Not required *(P2 only exception)* | Webhook + ServiceNow + Archer connectors | Compliance.ai |
| [REQ-017](REQ-017_Obligation_Level_Change_Delta.md) | Obligation-Level Change Delta | Enhancement | ObligationDiffService + change_delta col + sync | UpdateService processes delta | Compliance.ai |
| [REQ-018](REQ-018_Policy_Mapping.md) | Policy-to-Regulation Mapping | New Feature + Dashboard | Not required *(P2 only exception)* | Data model + API + Dashboard | Ascent |

---

### Low-Medium — Expansion / Board-Level Features

| ID | Requirement | Type | P1 First | Then P2 | Source Competitor |
|---|---|---|---|---|---|
| [REQ-020](REQ-020_Peer_Benchmarking.md) | Peer Benchmarking | New Data Service + Dashboard | PeerBenchmarkConnector + aggregate + sync | Benchmark comparison API + Dashboard | Corlytics |

---

## Dependency Map

Some requirements depend on others being completed first:

```
REQ-010 (Obligation Universe)
  └── REQ-017 (Change Delta)      — needs obligation IDs
  └── REQ-019 (Dashboard Panel 5) — needs coverage scores
  └── REQ-020 (Peer Benchmarking) — needs coverage scores to benchmark

REQ-013 (Change Management)
  └── REQ-017 (Change Delta)      — change delta populates change records

REQ-011 (Enforcement Intelligence)
  └── REQ-019 (Dashboard Panel 4) — enforcement scores feed heatmap

REQ-012 (Deadline Calendar)
  └── REQ-019 (Dashboard Panel 2) — deadlines feed calendar panel

REQ-018 (Policy Mapping)
  └── REQ-010 (optional)          — obligation-level mapping if available
  └── REQ-019 (Dashboard Panel 6) — policy gaps feed dashboard

REQ-019 (Master Dashboard) — depends on all panels having at least empty state fallback
```

---

## New Data Models Required (Alembic Migrations)

| Migration | Tables Added | Required By |
|---|---|---|
| 007 | `obligations` | REQ-010 |
| 008 | `enforcement_actions` | REQ-011 |
| 009 | `compliance_deadlines` | REQ-012 |
| 010 | `regulatory_change_records`, `change_record_comments` | REQ-013 |
| 011 | `operational_loss_events`, `rcsa_entries` | REQ-008 |
| 012 | `bank_policies`, `policy_regulation_mappings` | REQ-018 |
| 013 | `peer_benchmarks` | REQ-020 |
| 014 | Alter `compliance_rules` — add `previous_content`, `change_delta`, `change_type` | REQ-017 |

---

## New API Endpoints Summary

All new endpoints live in **Pipeline 2** unless noted.

| Endpoint | Method | Requirement |
|---|---|---|
| `/api/v1/coverage/report` | GET | REQ-010 |
| `/api/v1/enforcement/risk-scores` | GET | REQ-011 |
| `/api/v1/enforcement/recent` | GET | REQ-011 |
| `/api/v1/deadlines` | GET | REQ-012 |
| `/api/v1/deadlines/alerts` | GET | REQ-012 |
| `/api/v1/change-records` | GET | REQ-013 |
| `/api/v1/change-records/{id}/sign-off` | POST | REQ-013 |
| `/api/v1/fair-lending/analyze` | POST | REQ-009 |
| `/api/v1/integrations/configure` | POST | REQ-016 |
| `/api/v1/policies` | GET/POST | REQ-018 |
| `/api/v1/policies/coverage-gap` | GET | REQ-018 |
| `/api/v1/dashboard/compliance-overview` | GET | REQ-019 |
| `/api/v1/benchmarks/comparison` | GET | REQ-020 |

---

## New Connectors Required (Pipeline 1)

| Connector File | Regulation Area | Requirement |
|---|---|---|
| `usa/cra.py` | Community Reinvestment Act | REQ-001 |
| `usa/ffiec_call_report.py` | CALL Report / FFIEC | REQ-002 |
| `usa/ffiec_cybersecurity.py` | FFIEC Cybersecurity / CAT | REQ-003 |
| `usa/irr.py` | Interest Rate Risk | REQ-004 |
| `usa/cre_concentration.py` | CRE Concentration Risk | REQ-005 |
| `usa/cecl.py` | CECL / Credit Risk | REQ-006 |
| `usa/liquidity_risk.py` | Liquidity Risk / CFP | REQ-007 |
| `usa/operational_risk.py` | Operational Risk / RCSA | REQ-008 |
| `usa/enforcement_actions.py` | Enforcement Action Database | REQ-011 |
| `usa/peer_benchmarks.py` | Peer Benchmark Data | REQ-020 |
| `usa/states/nydfs.py` | New York DFS | REQ-014 |
| `usa/states/ca_dfpi.py` | California DFPI | REQ-014 |
| `usa/states/tx_dob.py` | Texas DOB | REQ-014 |
| `usa/states/il_idfpr.py` | Illinois IDFPR | REQ-014 |
| `usa/states/fl_ofr.py` | Florida OFR | REQ-014 |
