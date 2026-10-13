# REQ-006: CECL / Credit Risk Model Governance

## Overview
**Priority:** Medium
**Type:** New Feature — New Connector + Test Scenarios
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 (scraper + rules) + Pipeline 2 (test generation)
**Status:** Backlog

## Business Context
CECL (Current Expected Credit Losses — ASC 326) is now fully adopted by all US banks. Regulators
no longer examine CECL adoption — they examine CECL model governance, the quality of lifetime
loss estimates, and the adequacy of the Allowance for Credit Losses (ACL). OCC Model Risk
Management guidance (OCC 2011-12 / SR 11-7) applies directly to CECL models. FDIC
examiners routinely challenge ACL methodology and the assumptions behind loss forecasts.
No CECL-related rules or test scenarios currently exist in the system.

## Requirements

### Functional Requirements
1. Create a `CeclConnector` under `src/connectors/regulatory/usa/cecl.py`:
   - FASB ASC 326 standard requirements: lifetime expected loss estimation, reasonable and
     supportable forecast period, reversion to historical loss rates
   - OCC Model Risk Management guidance (OCC 2011-12) applied to CECL models:
     model development documentation, validation, ongoing monitoring, model inventory
   - Interagency Policy Statement on ACL (2020): documentation requirements, governance,
     qualitative factor framework
   - FDIC examiner expectations: board-approved ACL methodology, quarterly ACL adequacy review,
     documentation of qualitative factor adjustments
2. Add structured requirements covering:
   - Written ACL methodology document (board-approved)
   - Model inventory entry for each CECL model in use
   - Independent model validation: annual at minimum for material models
   - Qualitative factor (Q-factor) documentation: justification for each adjustment
   - Back-testing: comparison of prior period loss estimates to actual losses
3. Add minimum 8 test scenarios covering: ACL documentation completeness, model validation
   cadence, Q-factor justification, back-testing evidence, board approval trail

### Pipeline 1 Changes
- Register `CeclConnector` in connector registry
- Store under geography=`usa`, regulator=`fdic`/`occ`, domain=`cecl`

### Pipeline 2 Changes
- Add `cecl` domain to `LocalComplianceProvider`
- Add CECL test scenarios to MCP compliance knowledge server
- 6-agent pipeline: recognize `cecl`, `allowance`, `acl`, `loan_loss` as compliance domains

## Acceptance Criteria
- [ ] Connector returns: 4 regulations, 8 requirements, 4 KPIs, 8 test scenarios
- [ ] Model validation frequency rule present (annual for material models)
- [ ] ACL methodology documentation checklist modeled as `required_field` rule_types
- [ ] Test scenarios cover: methodology documentation, validation independence, Q-factor
      documentation, back-testing, board approval

## Technical Notes
- CECL models are bank-developed; validator input schema should accept model governance
  metadata (last validation date, validator independence flag, documentation completeness score)
- ASC 326 is an accounting standard (FASB), not a bank regulation — model it under a
  separate `standard_body: "FASB"` field alongside the regulatory sources
- Distinguish between CECL methodology (qualitative) and CECL calculation (quantitative);
  this requirement covers governance and methodology, not the mathematical calculation engine
