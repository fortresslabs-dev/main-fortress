# REQ-010: Full Obligation Universe and Compliance Coverage Score

## Overview
**Priority:** High
**Type:** Enhancement — All Connectors + New Scoring Service + Dashboard Component
**Source:** Competitor gap — Compliance.ai / Corlytics
**Pipeline Impact:** Pipeline 1 (obligation extraction) + Pipeline 2 (coverage scoring) + New Dashboard
**Status:** Backlog

## Business Context
The current connector architecture manually codes `rule_type`-based requirements, covering
a curated subset of each regulation. Compliance.ai extracts obligations at sentence/clause
level using NLP — every "shall," "must," and "is required to" becomes a discrete traceable
obligation. Without a full obligation universe, the system cannot tell a bank what percentage
of a regulation's obligations have test coverage. A compliance officer evaluating the product
will ask: "Show me my coverage." Currently there is no answer. This requirement adds AI-powered
obligation extraction and a coverage score per regulation.

## Requirements

### Functional Requirements
1. Add an `ObligationExtractor` service in `src/services/obligation_extractor.py`:
   - Input: raw regulatory text from scraped documents (already stored in `scraped_documents`)
   - Process: use Claude API (via existing Bedrock/Anthropic integration) to extract discrete
     obligations — each sentence containing "shall," "must," "required to," "prohibited from,"
     "may not" becomes a candidate obligation
   - Output: structured `Obligation` objects with fields:
     `obligation_id`, `regulation_id`, `source_text`, `obligation_type`
     (shall/must/prohibited/required), `section_reference`, `extracted_at`
   - Deduplicate obligations using embedding similarity (Weaviate already available)
2. Add `ObligationCoverageScorer` in `src/services/obligation_coverage_scorer.py`:
   - Map existing `requirements` and `test_scenarios` to extracted obligations
   - Calculate per-regulation coverage: `(obligations_with_tests / total_obligations) * 100`
   - Identify uncovered obligations (extracted but no test scenario exists)
   - Output: `CoverageReport` per regulation with score, covered list, uncovered list
3. Add `Obligation` data model in `src/models/database.py`:
   - `obligations` table: id, regulation_id, obligation_id, source_text, obligation_type,
     section_reference, has_test_coverage (bool), coverage_updated_at
   - Alembic migration
4. New API endpoint: `GET /api/v1/coverage/report` — returns per-regulation coverage scores

### Pipeline 1 Changes
- `ObligationExtractor` runs after each scrape cycle in `ScraperScheduler`
- Extracted obligations stored in `obligations` table and synced to Pipeline 2
- Add obligations to sync payload in `/api/v1/sync/postgres` incremental diff

### Pipeline 2 Changes
- `ObligationCoverageScorer` runs after each sync cycle in `UpdateService`
- Expose coverage scores via `GET /api/v1/coverage/report` endpoint
- Add `obligation_coverage` domain to `LocalComplianceProvider`
- 6-agent pipeline: the Generation agent should check uncovered obligations for the
  relevant regulation when generating tests from a JIRA story, prioritizing gaps

## Acceptance Criteria
- [ ] `ObligationExtractor` successfully extracts obligations from at least one scraped document
      (OCC BSA/AML handbook) and stores to `obligations` table
- [ ] Coverage score correctly calculates for OCC BSA/AML: (manually coded requirements
      / extracted obligations) * 100
- [ ] `GET /api/v1/coverage/report` returns JSON with per-regulation scores
- [ ] Uncovered obligations surfaced in Generation agent context when generating tests
- [ ] Alembic migration creates `obligations` table without breaking existing tables

## Technical Notes
- Obligation extraction is LLM-dependent — use `LLM_MOCK_MODE=true` fallback that
  returns a predefined obligation set for tests
- Initial pass: run extraction on existing `scraped_documents` already in the DB
  (no new scraping needed for first iteration)
- Weaviate embedding deduplication: use cosine similarity threshold of 0.92 — obligations
  with higher similarity treated as duplicates
- Coverage mapping is approximate (LLM-assisted): use the Understanding agent prompt
  pattern to match requirement text to obligation text — not a hard string match
