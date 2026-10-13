# REQ-018: Policy-to-Regulation Mapping

## Overview
**Priority:** Medium
**Type:** New Feature — New Data Model + API + Dashboard Component
**Source:** Competitor gap — Ascent RegTech
**Pipeline Impact:** Pipeline 2 only (new data model + API) + New Dashboard
**Implementation Order:** Pipeline 2 only — bank policy documents and mappings are
confidential internal data. Storing or syncing them to Fortress Cloud (P1) is not
permitted. Exception to the P1-first rule: policies link to regulations that already
exist in Pipeline 2 via the normal P1 → P2 sync. No new P1 work required before
starting this requirement.
**Status:** Backlog

## Business Context
Ascent maps internal bank policies (e.g., "Wire Transfer Policy v3.2") to the specific
regulatory obligations they satisfy. Examiners ask: "Show me your BSA policy and which
regulations it covers." The bank needs to demonstrate that each regulation is addressed
by at least one documented internal policy. Currently Fortress generates tests from
regulations but has no way for a bank to upload their policies and link them to the
regulatory obligations being tested. This capability completes the compliance traceability
chain: Regulation → Obligation → Internal Policy → Test Scenario.

## Requirements

### Functional Requirements

#### Policy Data Model
1. Add to `src/models/database.py`:
   - `bank_policies` table: id, policy_name, policy_version, policy_owner, policy_category
     (bsa/aml, lending, operations, it_risk, etc.), effective_date, last_reviewed_date,
     next_review_date, document_url (internal link or description), status
     (active/under_review/retired), created_at
   - `policy_regulation_mappings` table: id, policy_id, regulation_id, obligation_id (nullable,
     FK to obligations table from REQ-010), mapping_rationale (text), mapped_by, mapped_at
   - Alembic migration

#### Policy Management API
2. New API endpoints in Pipeline 2:
   - `POST /api/v1/policies` — create a policy record
   - `GET /api/v1/policies` — list all policies with coverage summary
   - `GET /api/v1/policies/{id}` — detail with mapped regulations and obligations
   - `POST /api/v1/policies/{id}/map-regulation` — create a policy-to-regulation mapping
   - `DELETE /api/v1/policies/{id}/map-regulation/{mapping_id}` — remove a mapping
   - `GET /api/v1/policies/coverage-gap` — regulations/obligations with no mapped policy

#### AI-Assisted Mapping Suggestion
3. Add `PolicyMappingSuggester` in `src/services/policy_mapping_suggester.py`:
   - Input: policy name + policy category
   - Process: query LLM with the policy category to suggest which regulations and
     obligations the policy likely covers
   - Output: list of suggested `regulation_id` values with confidence scores
   - Used by the API: `POST /api/v1/policies/{id}/suggest-mappings`

#### Policy Coverage Dashboard (New Panel)
4. New dashboard panel: `Policy Coverage`
   - Matrix view: regulations (rows) vs policies (columns), with checkmarks where mapped
   - Coverage gap indicator: regulations with zero mapped policies highlighted in red
   - Policy health: policies past next_review_date highlighted in amber
   - Drill-down: click a regulation to see all policies that cover it

### Pipeline 2 Changes (only)
- `bank_policies` and `policy_regulation_mappings` tables live in Pipeline 2 only
  (these are internal bank documents — never sync to Pipeline 1)
- New API endpoints added to Pipeline 2 app
- `PolicyMappingSuggester` uses existing Bedrock/Anthropic client
- Policy coverage gaps feed into compliance coverage score (REQ-010) as an additional
  dimension: a regulation can have test coverage but zero policy coverage — both gaps shown

### Pipeline 1 Changes
- None — policy documents are internal to the bank and must not leave the VPC

## Acceptance Criteria
- [ ] `bank_policies` and `policy_regulation_mappings` tables created with migration
- [ ] `POST /api/v1/policies` successfully creates a policy record
- [ ] `POST /api/v1/policies/{id}/map-regulation` creates a valid mapping to an existing regulation
- [ ] `GET /api/v1/policies/coverage-gap` returns regulations with no mapped policies
- [ ] `PolicyMappingSuggester` returns at least one regulation suggestion for a
      "BSA/AML Compliance Policy" input
- [ ] Policy data never appears in Pipeline 1 sync payload

## Technical Notes
- Policy document content (the full text of the policy) is NOT stored in the system —
  only metadata and mappings. Banks are sensitive about storing policy documents in
  vendor systems; `document_url` is a reference link, not stored content
- `next_review_date` calculation: default review cycle is annual; set to
  `effective_date + 365 days` if not explicitly provided
- Mapping rationale field should be free text capturing why this policy satisfies
  the regulation — this is the examiner-facing evidence text
- REQ-010 dependency: if `obligations` table exists (REQ-010 implemented), allow
  mapping at the obligation level for higher granularity; otherwise map at regulation level
