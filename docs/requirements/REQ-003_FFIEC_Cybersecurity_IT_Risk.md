# REQ-003: FFIEC Cybersecurity and IT Examination Risk

## Overview
**Priority:** High
**Type:** New Feature — New Connector + Test Scenarios
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 (scraper + rules) + Pipeline 2 (test generation)
**Status:** Backlog

## Business Context
FFIEC IT examination handbooks and the FFIEC Cybersecurity Assessment Tool (CAT) are now
a primary examination module for every bank regardless of size. Cyber findings are the
fastest-growing category of MRA (Matter Requiring Attention) citations from OCC, FDIC, and FRB.
Regional banks are expected to complete an annual self-assessment against CAT maturity levels
and maintain evidence of board-level oversight of cybersecurity risk. No FFIEC cybersecurity
connector, rules, or test scenarios currently exist in the system.

## Requirements

### Functional Requirements
1. Create an `FfiecCybersecurityConnector` under `src/connectors/regulatory/usa/ffiec_cybersecurity.py`:
   - FFIEC CAT five domains: Cyber Risk Management & Oversight, Threat Intelligence & Collaboration,
     Cybersecurity Controls, External Dependency Management, Cyber Incident Management & Resilience
   - CAT maturity levels: Baseline, Evolving, Intermediate, Advanced, Innovative
   - FFIEC IT Examination Handbooks: Management, Audit, Business Continuity, Development & Acquisition,
     Information Security, Operations, Outsourcing Technology Services, Retail Payment Systems,
     Wholesale Payment Systems
   - GLBA Safeguards Rule (16 CFR Part 314) — information security program requirements
   - Incident response notification requirements (OCC 2021-35, FDIC FIL-56-2022): 36-hour notification
     for computer security incidents
2. Add structured requirements covering:
   - Minimum CAT Baseline maturity controls (mandatory for all banks)
   - Information security program: written policy, designated officer, annual risk assessment,
     third-party oversight, employee training, incident response plan
   - Incident notification: 36-hour notification to primary federal regulator for significant
     computer security incidents
3. Add minimum 12 test scenarios covering: incident notification timelines, information security
   program completeness, third-party/vendor risk oversight
4. Scrape live sources: FFIEC IT handbook pages, FFIEC CAT assessment tool page, OCC cybersecurity
   bulletins, FDIC cybersecurity FILs

### Pipeline 1 Changes
- Register `FfiecCybersecurityConnector` in connector registry
- Add cybersecurity scrape to `ScraperScheduler` daily cycle
- Store under geography=`usa`, regulator=`ffiec`, domain=`cybersecurity`

### Pipeline 2 Changes
- Add `cybersecurity` domain to `LocalComplianceProvider`
- Add FFIEC cybersecurity scenarios to MCP compliance knowledge server
- 6-agent pipeline: recognize `cybersecurity`, `it_risk`, `incident_response` as compliance domains

## Acceptance Criteria
- [ ] Connector returns at minimum: 5 regulations, 12 requirements, 6 KPIs, 12 test scenarios
- [ ] 36-hour incident notification rule present with correct deadline parameters
- [ ] CAT Baseline controls modeled as `required_field` or `program_requirement` rule_type
- [ ] Test scenarios cover at minimum: incident notification, IS program annual review,
      third-party risk assessment, board reporting
- [ ] Rules sync correctly Pipeline 1 → Pipeline 2

## Technical Notes
- FFIEC CAT Excel tool has been superseded by FFIEC Cyber Risk Examination scope guidance (2024);
  connector should cover both for backward compatibility with banks still using CAT
- GLBA Safeguards Rule (FTC jurisdiction for non-bank lenders, but OCC/FDIC apply equivalent
  standards) — tag appropriately to avoid confusion with bank-specific rules
- 36-hour notification rule: applies to bank service providers too (BSCA Rule);
  model as separate requirement with `applies_to: ["bank", "bank_service_provider"]` parameter
