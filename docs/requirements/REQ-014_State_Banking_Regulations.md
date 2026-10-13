# REQ-014: State Banking Regulation Coverage

## Overview
**Priority:** High
**Type:** New Feature — New Connectors (per state) + Test Scenarios
**Source:** Competitor gap — Thomson Reuters Regulatory Intelligence
**Pipeline Impact:** Pipeline 1 (new connectors + scraper) + Pipeline 2 (validation + test generation)
**Status:** Backlog

## Business Context
The entire connector set is federal-only. Most regional banks are state-chartered and supervised
by both their state banking department AND either the FDIC or FRB. State regulations can
impose requirements beyond the federal floor — particularly in cybersecurity (NYDFS 23 NYCRR
500), consumer protection (California DFPI), and privacy (California CCPA for bank data).
Thomson Reuters covers all 50 state banking departments. Building full 50-state coverage is
not practical as an initial release; instead this requirement covers the five highest-priority
states for regional bank clients and establishes the connector pattern for future expansion.

## Requirements

### Functional Requirements

#### Phase 1: Five Priority States
1. Create state connectors under `src/connectors/regulatory/usa/states/`:
   - `nydfs.py` — New York DFS: 23 NYCRR 500 (Cybersecurity Regulation), Part 504
     (BSA/AML transaction monitoring), virtual currency guidance
   - `ca_dfpi.py` — California DFPI: CCPA applicability to bank data,
     California Consumer Financial Protection Law (CCFPL), CRA state requirements
   - `tx_dob.py` — Texas DOB: state bank charter requirements, consumer lending rules,
     money services business regulations
   - `il_idfpr.py` — Illinois IDFPR: state bank examination requirements,
     Consumer Installment Loan Act, Sales Finance Agency Act
   - `fl_ofr.py` — Florida OFR: state bank supervision, consumer finance rules,
     mortgage lending requirements
2. Each state connector follows the same interface as federal connectors (extends `RegulatoryConnector`)
3. Tag all state rules with `geography: "usa-{state_code}"` (e.g., `usa-ny`, `usa-ca`)
4. Add `state_code` field to `ComplianceRule` model and sync metadata

#### NYDFS 23 NYCRR 500 Priority (immediate value)
5. NYDFS cybersecurity regulation deserves elevated priority — it is the most frequently
   cited state regulation in bank compliance discussions:
   - Annual penetration testing requirement
   - CISO designation requirement
   - 72-hour incident notification to NYDFS (stricter than federal 36-hour OCC rule)
   - Annual certification of compliance by board/senior officer
   - Encryption requirements for nonpublic information in transit and at rest
   - Multi-factor authentication requirements
   - Third-party service provider security policy

### Pipeline 1 Changes
- Register all five state connectors in connector registry
- Add state regulation scrapes to `ScraperScheduler` — weekly cadence (state rules change
  less frequently than federal)
- State rules stored with `geography` = state code, participate in full sync cycle

### Pipeline 2 Changes
- `LocalComplianceProvider` lookup supports `geography` filter — allow both federal (`usa`)
  and state-specific (`usa-ny`) lookups
- Add state domain to test generation context: if a JIRA story mentions the bank is in
  New York, include NYDFS rules alongside OCC/FDIC rules
- Add state-specific test scenarios to MCP compliance knowledge server

## Acceptance Criteria
- [ ] `NydfsConnector.extract_all_rules()` returns 23 NYCRR 500 requirements including
      72-hour notification, CISO requirement, annual penetration testing
- [ ] State rules correctly tagged with state-specific geography codes
- [ ] `LocalComplianceProvider` returns state rules when queried with state geography filter
- [ ] NYDFS 72-hour notification requirement does not conflict with OCC 36-hour rule
      (both stored, bank applies the stricter one)
- [ ] 6-agent pipeline includes state rules in test generation when state context is provided

## Technical Notes
- State connector pattern: state regulators frequently restructure their websites;
  add extra fallback handling and static rule baseline for all state connectors
  (same pattern as existing federal connectors)
- Geography code convention: use ISO 3166-2 US state codes prefixed with "usa-"
  (e.g., `usa-ny`, `usa-ca`, `usa-tx`)
- Conflict resolution: when a state rule is stricter than the federal equivalent,
  tag the state rule with `supersedes_federal: true` and the federal rule ID it references
- Phase 2 (future): expand to remaining 45 states using the same connector pattern —
  this requirement establishes the pattern; expansion is a separate backlog item
