# REQ-002: CALL Report / FFIEC Regulatory Reporting

## Overview
**Priority:** Critical
**Type:** New Feature — New Connector + Validator + Test Scenarios
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 (scraper + rules) + Pipeline 2 (validator + test generation)
**Status:** Backlog

## Business Context
Every FDIC-insured institution files the CALL Report (FFIEC 031/041/051) every quarter. It is
the single largest recurring regulatory filing burden for a regional bank and feeds every capital
ratio, asset quality, and earnings metric that examiners review. The FFIEC maintains the Call
Report schedules, instructions, and edit checks. Currently the system references "FFIEC" in the
regulator mapping but has zero FFIEC connector, rules, or test scenarios. This gap means the
system cannot generate or validate tests for the most frequently filed regulatory document in US
banking.

## Requirements

### Functional Requirements
1. Create an `FfiecCallReportConnector` under `src/connectors/regulatory/usa/ffiec_call_report.py`:
   - FFIEC 031 schedules (commercial banks with domestic and foreign offices)
   - FFIEC 041 schedules (commercial banks with domestic offices only)
   - FFIEC 051 schedules (small banks — assets under $5B)
   - Key schedules: RC (balance sheet), RC-C (loans), RC-R (capital), RC-K (averages),
     RI (income), RI-A (retained earnings)
   - FFIEC edit checks: validity edits, quality edits, intraseries edits
2. Add structured requirements covering:
   - Filing deadline: 30 calendar days after quarter-end (March 31, June 30, Sep 30, Dec 31)
   - Data integrity rules: cross-schedule reconciliation (RC total assets = sum of components)
   - Capital ratio calculations from RC-R: CET1, Tier 1, Total RBC, Leverage ratio
   - Allowance for Credit Losses (ACL) consistency between RI and RC-C
3. Add `CallReportValidator` in `src/services/validators.py`:
   - Balance sheet identity check (assets = liabilities + equity)
   - Capital ratio threshold validation
   - Filing deadline countdown with early warnings at 20 and 25 days
   - Required schedule completeness check
4. Scrape live sources: FFIEC CALL Report forms and instructions pages, FFIEC edit check lists

### Pipeline 1 Changes
- Register `FfiecCallReportConnector` in connector registry
- Add CALL Report scrape to `ScraperScheduler` — quarterly cadence (not daily, rules are stable)
  with daily check of FFIEC update notices
- Store rules under geography=`usa`, regulator=`ffiec`, domain=`call_report`

### Pipeline 2 Changes
- Add `CallReportValidator` to validation API
- Add `call_report` domain to `LocalComplianceProvider`
- Add CALL Report test scenarios to MCP compliance knowledge server (minimum 10 scenarios)
- 6-agent pipeline: add FFIEC CALL Report as a recognized domain in test generation prompts

## Acceptance Criteria
- [ ] Connector returns at minimum: 4 regulations (one per major schedule group), 15 requirements, 8 KPIs, 10 test scenarios
- [ ] `CallReportValidator` correctly flags balance sheet that does not balance
- [ ] `CallReportValidator` returns deadline alert when fewer than 10 days remain to filing
- [ ] Capital ratio rules from RC-R align with existing FDIC capital ratio rules (no contradiction)
- [ ] FFIEC rules sync correctly via Pipeline 1 → Pipeline 2 incremental diff

## Technical Notes
- FFIEC 051 is the most relevant form for community and smaller regional banks (assets < $5B)
- Edit check logic is published by FFIEC as machine-readable CSV — connector should parse this
  directly rather than scraping HTML where possible
- CALL Report schedules have ~80+ line items; model as `required_field` rule_type with
  schedule code (e.g., `RC-R-I-1`) as field_name
- Avoid duplicating capital ratio thresholds already in `FdicConnector` — reference existing
  FDIC-CAPITAL-001 rules and add FFIEC cross-reference metadata
