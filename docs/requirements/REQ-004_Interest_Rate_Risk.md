# REQ-004: Interest Rate Risk (IRR) Program Testing

## Overview
**Priority:** High
**Type:** New Feature — New Connector + Validator + Test Scenarios
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 (scraper + rules) + Pipeline 2 (validator + test generation)
**Status:** Backlog

## Business Context
Interest Rate Risk is a primary examination focus for FDIC and OCC at every regional bank
with a mortgage or fixed-rate loan portfolio. The FDIC Risk Management Manual devotes an
entire section to IRR. Examiners expect a documented IRR policy, board-approved risk limits,
rate shock scenario analysis (±200/300/400 bps), NII (Net Interest Income) and EVE (Economic
Value of Equity) analysis, and documented escalation triggers when limits are breached. FDIC
and OCC cite IRR policy gaps frequently in community bank exam findings. The current FDIC
connector covers capital ratios only — IRR program requirements are entirely absent.

## Requirements

### Functional Requirements
1. Create an `IrrConnector` under `src/connectors/regulatory/usa/irr.py`:
   - FDIC Risk Management Manual — IRR section requirements
   - OCC Comptroller's Handbook — Interest Rate Risk booklet
   - Interagency Advisory on Interest Rate Risk (2010/2012) — applies to all institutions
   - Required program components: written IRR policy, board oversight, measurement systems,
     stress scenarios, limits, model validation, audit
2. Add structured requirements covering:
   - Written IRR policy with board-approved limits (NII sensitivity, EVE sensitivity)
   - Minimum stress scenarios: ±100, ±200, ±300, ±400 basis point parallel rate shocks
   - Monitoring frequency: quarterly for community banks, monthly for high-risk profiles
   - Model validation: annual independent review of IRR measurement models
   - Escalation triggers: breach of NII limit → management notification → board notification
3. Add `IRRProgramValidator` in `src/services/validators.py`:
   - Program completeness check: verify all required policy components are documented
   - Limit breach detector: flag when NII sensitivity or EVE sensitivity exceeds board-set limits
   - Stress scenario coverage: verify ±200 bps minimum is included in analysis
   - Monitoring frequency check: validate quarterly measurement cadence
4. Add minimum 10 test scenarios

### Pipeline 1 Changes
- Register `IrrConnector` in connector registry
- Add IRR scrape to `ScraperScheduler` daily cycle — FDIC and OCC IRR guidance pages
- Store under geography=`usa`, regulator=`fdic`/`occ`, domain=`interest_rate_risk`

### Pipeline 2 Changes
- Add `IRRProgramValidator` to validation API
- Add `interest_rate_risk` domain to `LocalComplianceProvider`
- Add IRR test scenarios to MCP compliance knowledge server
- 6-agent pipeline: recognize `irr`, `interest_rate_risk`, `rate_shock` as compliance domains

## Acceptance Criteria
- [ ] Connector returns: 4 regulations, 10 requirements, 6 KPIs, 10 test scenarios
- [ ] `IRRProgramValidator` correctly flags missing stress scenarios (e.g., ±200 bps not present)
- [ ] Limit breach detection returns `block` action when NII sensitivity exceeds provided limit
- [ ] Test scenarios cover: policy completeness, rate shock coverage, limit breach escalation,
      board reporting frequency, model validation cadence
- [ ] Rules sync correctly Pipeline 1 → Pipeline 2

## Technical Notes
- IRR validation inputs are program-level (policy documents, model outputs) not transaction-level;
  validator input schema should accept structured program metadata rather than transaction dicts
- EVE and NII sensitivity figures are bank-computed — validator accepts them as inputs and checks
  against the bank's own board-approved limits (passed in as parameters), not fixed regulatory thresholds
- Distinguish between community bank IRR guidance and Enhanced Prudential Standards
  (applies to $100B+ banks only — out of scope for regional bank focus)
