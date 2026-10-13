# REQ-001: Community Reinvestment Act (CRA) Compliance Coverage

## Overview
**Priority:** Critical
**Type:** New Feature — New Connector + Test Scenarios
**Source:** Competitor gap — Wolters Kluwer OneSumX
**Pipeline Impact:** Pipeline 1 (scraper + rules) + Pipeline 2 (validator + test generation)
**Status:** Backlog

## Business Context
CRA is one of the top three examination priority areas for regional banks. OCC, FDIC, and FRB
all grade CRA performance and publish ratings publicly. The 2023 interagency CRA modernization
rule changed assessment area delineation, evaluation criteria, and data collection requirements
significantly. No CRA connector, rules, or test scenarios currently exist in the system.
Absence of CRA coverage is a direct gap in any regional bank sales conversation — examiners
generate formal CRA findings that can block mergers and acquisitions.

## Requirements

### Functional Requirements
1. Create a `CraConnector` under `src/connectors/regulatory/usa/cra.py` covering:
   - Assessment area delineation rules (geographic boundaries, branching triggers)
   - Lending test: small business lending ratios, community development loan tracking
   - Investment test: qualified investment tracking, CDFI investment criteria
   - Service test: branch availability, community development service hours
   - 2023 modernization rule additions: retail lending subtest, digital delivery criteria
2. Add structured `regulations`, `requirements`, `kpis`, and `test_scenarios` for each CRA component
3. Scrape live sources: OCC CRA page, FDIC CRA guidance, FRB CRA handbook, FFIEC CRA examination procedures
4. Add `CRAValidator` in `src/services/validators.py`:
   - Lending test ratio check (CRA loans / total loans vs. peer benchmarks)
   - Assessment area coverage verification
   - Community development activity threshold check
5. Expose CRA validation via `src/api/validation_api.py`

### Pipeline 1 Changes
- Register `CraConnector` in connector registry (`src/connectors/registry.py`)
- Add CRA scrape to 24-hour `ScraperScheduler` cycle
- Add CRA rules to the `ComplianceRule` table sync (geography=`usa`, regulator=`occ`/`fdic`/`frb`, domain=`cra`)

### Pipeline 2 Changes
- Add `CRAValidator` to validation API
- Add CRA domain to `LocalComplianceProvider` rule lookup
- Ensure 6-agent pipeline recognizes `cra` as a known compliance domain when processing JIRA stories
- Add minimum 12 CRA test scenarios to the MCP compliance knowledge server

## Acceptance Criteria
- [ ] `CraConnector.extract_all_rules()` returns at minimum: 5 regulations, 10 requirements, 5 KPIs, 12 test scenarios
- [ ] CRA rules sync correctly from Pipeline 1 to Pipeline 2 via incremental diff
- [ ] `CRAValidator.validate()` returns correct action (`allow`/`review`/`block`) for lending test ratio inputs
- [ ] 6-agent pipeline generates CRA-aware tests from a JIRA story referencing CRA assessment areas
- [ ] All CRA test scenarios include regulator, rule_id, priority, given/when/then fields

## Technical Notes
- Three separate regulators examine CRA (OCC for national banks, FDIC for state non-member banks, FRB for state member banks) — connector must tag `regulator` correctly per bank charter type
- 2023 rule has staggered effective dates by asset size; connector must track effective dates per asset tier
- CRA ratings (Outstanding, Satisfactory, Needs to Improve, Substantial Noncompliance) should be modeled as an enum in `Violation.severity`
