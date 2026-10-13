# REQ-005: Commercial Real Estate (CRE) Concentration Risk

## Overview
**Priority:** High
**Type:** New Feature — New Connector + Validator + Test Scenarios
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 (scraper + rules) + Pipeline 2 (validator + test generation)
**Status:** Backlog

## Business Context
The 2006 Interagency Guidance on Concentrations in Commercial Real Estate Lending defines
hard thresholds that trigger enhanced supervisory scrutiny. Regional banks in growth markets
routinely exceed these thresholds. When a bank's CRE loans exceed 300% of Tier 1 capital,
or construction/land development loans exceed 100% of Tier 1 capital, examiners apply a
higher-intensity examination and expect documented concentration risk management. No CRE
concentration rules, validator, or test scenarios currently exist.

## Requirements

### Functional Requirements
1. Create a `CreConcentrationConnector` under `src/connectors/regulatory/usa/cre_concentration.py`:
   - 2006 Interagency CRE Guidance thresholds: CRE/Tier1 Capital > 300%, C&D/Tier1 Capital > 100%
   - OCC/FDIC/FRB CRE concentration supervisory expectations for banks exceeding thresholds:
     board oversight, stress testing, underwriting standards, portfolio monitoring
   - Portfolio stress testing requirements for concentrated CRE portfolios
   - Updated guidance post-COVID (2023 OCC bulletin on CRE risk)
2. Add structured requirements covering:
   - CRE concentration threshold calculation methodology
   - Required controls when thresholds are exceeded: written CRE concentration policy,
     quarterly board reporting, CRE stress test, enhanced underwriting standards
   - Loan-to-value (LTV) limits by property type (supervisory LTV limits — 12 CFR Part 34 Appendix A)
3. Add `CREConcentrationValidator` in `src/services/validators.py`:
   - Concentration ratio calculation: total CRE loans / Tier 1 capital
   - Construction & development ratio: C&D loans / Tier 1 capital
   - LTV limit check by property type (residential, commercial, raw land, etc.)
   - Trigger enhanced scrutiny flag when either ratio breaches threshold
4. Add minimum 8 test scenarios

### Pipeline 1 Changes
- Register `CreConcentrationConnector` in connector registry
- Store under geography=`usa`, regulator=`occ`/`fdic`/`frb`, domain=`cre_concentration`

### Pipeline 2 Changes
- Add `CREConcentrationValidator` to validation API
- Add `cre_concentration` domain to `LocalComplianceProvider`
- Add CRE test scenarios to MCP compliance knowledge server

## Acceptance Criteria
- [ ] Connector returns: 3 regulations, 8 requirements, 5 KPIs, 8 test scenarios
- [ ] `CREConcentrationValidator` returns `block` action when CRE/capital ratio > 300%
- [ ] `CREConcentrationValidator` returns `review` action when ratio is 250%–300% (approaching threshold)
- [ ] LTV limit check returns correct limit for each of 6 property types
- [ ] Test scenarios cover: CRE threshold breach, C&D threshold breach, LTV exceedance,
      stress test requirement trigger, board reporting requirement

## Technical Notes
- Tier 1 capital input is required for ratio calculation; validator input schema should include
  `tier1_capital`, `total_cre_loans`, `construction_development_loans`, `property_type`, `ltv`
- LTV supervisory limits (12 CFR Part 34, Appendix A): raw land 65%, land development 75%,
  commercial/multifamily 80%, 1–4 family 85%, improved property 85% — model as lookup table
- "CRE" definition for concentration purposes includes: non-farm nonresidential, multifamily,
  and construction loans — must match the FFIEC CALL Report definition exactly
