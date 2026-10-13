# REQ-008: Operational Risk — Loss Events, RCSA, and KRI Framework

## Overview
**Priority:** Medium
**Type:** New Feature — New Connector + Test Scenarios + Workflow
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 (scraper + rules) + Pipeline 2 (test generation + new workflow)
**Status:** Backlog

## Business Context
OCC guidance expects even community banks to maintain a basic operational risk framework:
track operational loss events, conduct periodic Risk and Control Self-Assessments (RCSA),
and monitor Key Risk Indicators (KRIs). FDIC examiners request the bank's risk register and
operational loss history during examinations. Currently no operational risk rules, loss event
tracking, or RCSA workflow exists in the system. This gap is visible in every exam-prep
conversation with a regional bank compliance or risk officer.

## Requirements

### Functional Requirements
1. Create an `OperationalRiskConnector` under `src/connectors/regulatory/usa/operational_risk.py`:
   - OCC Comptroller's Handbook — Operational Risk booklet
   - OCC Heightened Standards guidance (12 CFR Part 30, Appendix D) — applies to $50B+ banks
     but community bank examiners reference its framework expectations
   - Basel II/III operational risk category definitions (7 event type categories) as
     the industry-standard taxonomy for loss events
   - FDIC operational risk examination procedures
2. Add structured requirements covering:
   - Loss event database: capture date, event type (Basel category), business line,
     gross loss amount, recovery amount, root cause, corrective action
   - RCSA process: annual at minimum for all business lines, semi-annual for high-risk lines,
     risk rating methodology (inherent risk, control effectiveness, residual risk)
   - KRI framework: at minimum 5 KRIs per material business line, monthly monitoring,
     threshold-based escalation
   - Board reporting: quarterly operational risk dashboard to board or risk committee
3. Add minimum 10 test scenarios covering: loss event capture, RCSA completeness, KRI
   threshold breach escalation, board reporting cadence

### Pipeline 1 Changes
- Register `OperationalRiskConnector` in connector registry
- Store under geography=`usa`, regulator=`occ`/`fdic`, domain=`operational_risk`

### Pipeline 2 Changes
- Add `operational_risk` domain to `LocalComplianceProvider`
- Add operational risk test scenarios to MCP compliance knowledge server
- 6-agent pipeline: recognize `operational_risk`, `rcsa`, `loss_event`, `kri` as domains
- New data model in `src/models/database.py`:
  - `OperationalLossEvent` table: id, event_date, event_type (Basel category), business_line,
    gross_loss_usd, recovery_usd, root_cause, status, corrective_action, created_at
  - `RCSAEntry` table: id, business_line, risk_description, inherent_rating, control_effectiveness,
    residual_rating, review_date, next_review_date, owner
  - Add Alembic migration for both tables

## Acceptance Criteria
- [ ] Connector returns: 3 regulations, 10 requirements, 6 KPIs, 10 test scenarios
- [ ] `OperationalLossEvent` and `RCSAEntry` data models created with Alembic migration
- [ ] Test scenarios cover: loss event intake completeness, RCSA overdue detection,
      KRI breach escalation, board report generation trigger
- [ ] 6-agent pipeline generates operational risk test cases from JIRA stories referencing
      risk register or loss events

## Technical Notes
- Basel 7 operational loss event type categories: Internal Fraud, External Fraud,
  Employment Practices & Workplace Safety, Clients/Products/Business Practices,
  Damage to Physical Assets, Business Disruption & System Failures, Execution/Delivery/Process
  Management — model as an enum in `OperationalLossEvent.event_type`
- RCSA is a workflow, not a transaction — the 6-agent pipeline should generate test cases
  for the RCSA *process* (is the review overdue? is the residual rating documented?) rather
  than individual risk events
- Heightened Standards (12 CFR Part 30 App D) is aspirational for community banks;
  tag requirements from that source with `applies_to_assets_above_usd: 50_000_000_000`
  so banks under $50B see them as best practice rather than mandatory
