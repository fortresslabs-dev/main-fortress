# REQ-013: Regulatory Change Management — Impact Assessment and Sign-Off Workflow

## Overview
**Priority:** High
**Type:** New Feature — Workflow + New Data Model + Enhancement to Pipeline 1 and Pipeline 2
**Source:** Competitor gaps — Ascent RegTech + Thomson Reuters Regulatory Intelligence
**Pipeline Impact:** Pipeline 1 (change detection) + Pipeline 2 (workflow + audit trail) + New Dashboard
**Status:** Backlog

## Business Context
When a compliance rule's SHA-256 hash changes, the system detects it and syncs the diff.
But there is no workflow for: "New change detected → assigned to responsible business line →
impact rated → remediation plan created → sign-off recorded." FDIC and OCC examiners
specifically ask for documented evidence that management reviewed each regulatory change and
updated internal controls accordingly. Both Ascent and Thomson Reuters sell this sign-off
workflow as a primary product. It is the most common examiner ask that current Fortress
architecture cannot satisfy.

## Requirements

### Functional Requirements

#### Change Record Data Model
1. Add `RegulatoryChangeRecord` to `src/models/database.py`:
   - `regulatory_change_records` table: id, regulation_id, rule_id, detected_at,
     previous_hash, new_hash, change_summary (LLM-generated), impact_rating
     (High/Medium/Low/None), assigned_to (user/team), status
     (detected/in_review/impact_assessed/remediation_planned/signed_off/closed),
     reviewer_name, reviewer_role, signed_off_at, notes, created_at
   - `change_record_comments` table: id, change_record_id, author, comment_text, created_at
   - Alembic migration for both tables

#### Change Detection Enhancement
2. Enhance `ScraperScheduler` / `UpdateService` in Pipeline 1:
   - When a hash change is detected, generate an LLM-based `change_summary`:
     "What changed in this rule, expressed in plain English for a compliance officer"
   - Create a `RegulatoryChangeRecord` with status=`detected`
   - Tag the record with the regulation's category and regulator for routing

#### Impact Assessment Workflow
3. New API endpoints:
   - `GET /api/v1/change-records` — all change records, filterable by status/regulator/date
   - `GET /api/v1/change-records/{id}` — detail view with diff, summary, and current status
   - `PATCH /api/v1/change-records/{id}` — update: assign, rate impact, add notes
   - `POST /api/v1/change-records/{id}/sign-off` — record reviewer sign-off with timestamp
   - `GET /api/v1/change-records/{id}/audit-trail` — full history of status transitions

#### Regulatory Change Management Dashboard (New)
4. New dashboard page: `Regulatory Change Log`
   - Change feed: chronological list of detected changes, newest first
   - Status lanes: Kanban-style (Detected → In Review → Impact Assessed → Signed Off → Closed)
   - Filter by: regulator, impact rating, assigned owner, date range
   - Each card shows: regulation name, change summary, days since detection, current status
   - Sign-off action: one-click sign-off with reviewer name/role capture
   - Audit trail view: full status history per change record for examiner evidence export

### Pipeline 1 Changes
- After each scrape cycle, `ScraperScheduler` calls LLM to generate `change_summary`
  for any rules with updated hashes
- Create `RegulatoryChangeRecord` entries in Pipeline 1 DB
- Sync change records (without customer data) to Pipeline 2 via sync API

### Pipeline 2 Changes
- `UpdateService` creates local `RegulatoryChangeRecord` entries on sync
- Expose change management API endpoints from Pipeline 2
- Add change management test scenarios to MCP compliance knowledge server
- 6-agent pipeline: if a JIRA story references a recent regulatory change, the Discovery
  agent pulls the corresponding `RegulatoryChangeRecord` as context

## Acceptance Criteria
- [ ] `regulatory_change_records` and `change_record_comments` tables created with migration
- [ ] When a hash change is detected, a change record is auto-created with LLM-generated summary
- [ ] `POST /api/v1/change-records/{id}/sign-off` records reviewer name, role, and timestamp
- [ ] `GET /api/v1/change-records/{id}/audit-trail` returns full status history
- [ ] Dashboard displays Kanban lanes with correct record counts
- [ ] Sign-off trail can be exported as PDF for examiner evidence
- [ ] All status transitions are logged with timestamp and actor

## Technical Notes
- LLM change summary generation runs synchronously after hash change detection —
  use the existing Bedrock/Anthropic client; add to `LLM_MOCK_MODE=true` path for tests
- Sign-off records must be immutable after creation — implement as append-only entries
  in `change_record_comments` with type=`sign_off` rather than mutable fields
- Examiner evidence export: the audit trail endpoint should support
  `Accept: application/pdf` header to generate a formatted PDF of the change history
  (use existing Python PDF library or return structured JSON for front-end rendering)
- Do not require authentication for MVP — use `reviewer_name` and `reviewer_role` as
  free-text fields captured at sign-off time; formal auth integration is a follow-on
