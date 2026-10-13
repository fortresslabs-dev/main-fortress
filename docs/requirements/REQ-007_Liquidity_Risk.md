# REQ-007: Liquidity Risk and Contingency Funding Plan

## Overview
**Priority:** Medium-High
**Type:** New Feature — New Connector + Validator + Test Scenarios
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 (scraper + rules) + Pipeline 2 (validator + test generation)
**Status:** Backlog

## Business Context
After the 2023 bank failures (SVB, Signature, First Republic), liquidity risk examination
intensity increased significantly for regional banks across all three federal regulators.
FDIC expects all insured institutions — regardless of size — to maintain a documented
Contingency Funding Plan (CFP). OCC published updated liquidity risk guidance emphasizing
concentration of funding sources, uninsured deposit ratios, and availability of contingent
liquidity sources. No liquidity risk rules, validator, or test scenarios currently exist.

## Requirements

### Functional Requirements
1. Create a `LiquidityRiskConnector` under `src/connectors/regulatory/usa/liquidity_risk.py`:
   - FDIC Risk Management Manual — Liquidity section
   - OCC Comptroller's Handbook — Liquidity booklet
   - Interagency Policy Statement on Funding and Liquidity Risk Management (2010)
   - Post-2023 OCC/FDIC liquidity supervisory guidance updates
   - Required program components: written liquidity policy, CFP, liquidity stress testing,
     board oversight, liquidity metrics monitoring
2. Add structured requirements covering:
   - Contingency Funding Plan: at minimum three stress scenarios (mild, moderate, severe),
     identified contingent liquidity sources (FHLB, Fed discount window, brokered deposits),
     trigger metrics and escalation procedures
   - Liquidity monitoring metrics: loan-to-deposit ratio, uninsured deposit ratio,
     reliance on wholesale funding, available liquidity coverage ratio
   - CFP testing: annual CFP exercise, board-approved trigger thresholds
   - Uninsured deposit monitoring: flag when uninsured deposits exceed 50% of total deposits
3. Add `LiquidityRiskValidator` in `src/services/validators.py`:
   - CFP completeness check: verify stress scenarios, contingent sources, triggers are documented
   - Uninsured deposit ratio check: flag when ratio exceeds configurable threshold
   - Loan-to-deposit ratio check against policy limits
   - Wholesale funding concentration check
4. Add minimum 8 test scenarios

### Pipeline 1 Changes
- Register `LiquidityRiskConnector` in connector registry
- Store under geography=`usa`, regulator=`fdic`/`occ`, domain=`liquidity_risk`

### Pipeline 2 Changes
- Add `LiquidityRiskValidator` to validation API
- Add `liquidity_risk` domain to `LocalComplianceProvider`
- Add liquidity test scenarios to MCP compliance knowledge server

## Acceptance Criteria
- [ ] Connector returns: 3 regulations, 8 requirements, 6 KPIs, 8 test scenarios
- [ ] `LiquidityRiskValidator` flags CFP missing stress scenario coverage
- [ ] Uninsured deposit ratio check flags when ratio > 50%
- [ ] Post-2023 OCC guidance updates reflected in connector requirements
- [ ] Test scenarios cover: CFP completeness, stress scenario coverage, trigger threshold
      documentation, board reporting, contingent source documentation

## Technical Notes
- LCR (Liquidity Coverage Ratio) and NSFR (Net Stable Funding Ratio) apply to banks with
  $100B+ in assets — explicitly exclude from this connector and note inapplicability to
  regional banks under $100B
- CFP validation inputs are program-level metadata; validator schema should accept:
  `cfp_last_tested_date`, `stress_scenarios_count`, `contingent_sources`, `trigger_thresholds`,
  `uninsured_deposit_ratio`, `loan_to_deposit_ratio`
- FHLB (Federal Home Loan Bank) access is a primary contingent liquidity source for regional
  banks — model as a specific contingent source type in requirements
