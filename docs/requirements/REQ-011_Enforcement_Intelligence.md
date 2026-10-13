# REQ-011: Enforcement Action Intelligence and Regulatory Risk Scoring

## Overview
**Priority:** High
**Type:** New Feature — New Data Service + New Connector + Dashboard Component
**Source:** Competitor gap — Compliance.ai / Corlytics
**Pipeline Impact:** Pipeline 1 (scraper + new data store) + Pipeline 2 (risk scoring) + New Dashboard
**Status:** Backlog

## Business Context
Corlytics' primary differentiator is an enforcement action database — every consent order,
civil money penalty (CMP), formal agreement, MRA, and MRAIA indexed by regulation, violation
type, bank size, and regulator. They use historical enforcement patterns to score which
regulations carry the highest enforcement risk. Currently the system knows what the regulations
say but has no data on where regulators have actually penalized banks. For a risk officer,
knowing "BSA/AML generated 23 consent orders last year, 78% cited inadequate transaction
monitoring" is more actionable than the regulation text. This information is entirely public
(OCC, FDIC, FRB all publish enforcement actions) and should be scraped and indexed.

## Requirements

### Functional Requirements
1. Create an `EnforcementActionConnector` under `src/connectors/regulatory/usa/enforcement_actions.py`:
   - OCC Enforcement Actions page: consent orders, formal agreements, CMPs
   - FDIC Enforcement Actions page: orders, agreements, CMPs
   - FRB Enforcement Actions page: written agreements, cease and desist orders, CMPs
   - FinCEN Civil Penalty Orders
   - Parse each action: institution name, institution asset size (where available), date,
     action type, regulations cited, violation description summary, penalty amount
2. Add `EnforcementAction` data model in `src/models/database.py`:
   - `enforcement_actions` table: id, regulator, institution_type, action_date, action_type,
     regulations_cited (JSON array), violation_summary, penalty_amount_usd, source_url,
     content_hash, scraped_at
3. Add `EnforcementRiskScorer` in `src/services/enforcement_risk_scorer.py`:
   - Per-regulation enforcement frequency: count of actions citing each regulation
     in the past 12/24/36 months
   - Penalty magnitude: average and total penalties per regulation
   - Trend score: is enforcement increasing or decreasing (rolling 12-month vs prior 12-month)
   - Combined risk score: frequency × magnitude × trend, normalized 0–100
4. New API endpoints:
   - `GET /api/v1/enforcement/risk-scores` — returns risk score per regulation
   - `GET /api/v1/enforcement/recent` — returns recent enforcement actions (last 90 days)
   - `GET /api/v1/enforcement/by-regulation/{regulation_id}` — actions for a specific regulation

### Pipeline 1 Changes
- Register `EnforcementActionConnector` in connector registry
- Add enforcement scrape to `ScraperScheduler` — weekly cadence (actions don't change daily)
- `EnforcementRiskScorer` runs after each enforcement scrape and updates scores
- Sync enforcement risk scores (not raw actions) to Pipeline 2 via sync API

### Pipeline 2 Changes
- Risk scores synced from Pipeline 1 and available via `LocalComplianceProvider`
- 6-agent pipeline: Generation agent incorporates enforcement risk score when prioritizing
  which test scenarios to generate first — high enforcement risk regulations get more tests
- Add enforcement risk score to `ValidationResult` output as `enforcement_risk_score` field

## Acceptance Criteria
- [ ] `EnforcementActionConnector` successfully scrapes and parses at least OCC and FDIC
      enforcement action pages
- [ ] `enforcement_actions` table stores parsed actions with regulation citations
- [ ] `EnforcementRiskScorer` returns a score for each regulation covered by the system
- [ ] Generation agent uses enforcement risk score in test prioritization
- [ ] `GET /api/v1/enforcement/risk-scores` returns JSON with scores and trend direction
- [ ] Weekly scrape does not duplicate existing actions (content_hash deduplication)

## Technical Notes
- Enforcement action PDFs from OCC/FDIC are the most information-dense source;
  use existing `scrape_pdf_url` utility from `_scraper_utils.py`
- Institution name matching for asset size lookup: cross-reference FDIC BankFind API
  (public) by institution name to get asset size — this enables filtering by peer group
- Violation descriptions in enforcement orders are natural language; use LLM summarization
  (same Bedrock integration) to extract `regulations_cited` and `violation_category`
- Do not store institution names in Pipeline 2 sync — sync only the aggregate risk scores
  per regulation. Individual bank identity is irrelevant to test generation.
