# REQ-012: Compliance Deadline Calendar and Coverage Alert Dashboard

## Overview
**Priority:** High
**Type:** New Dashboard + Enhancement to Pipeline 1 and Pipeline 2
**Source:** Competitor gap — Thomson Reuters Regulatory Intelligence
**Pipeline Impact:** Pipeline 1 (deadline tracking) + Pipeline 2 (coverage alerts) + New Dashboard
**Status:** Backlog

## Business Context
TR's most-used feature for compliance officers is the forward-looking deadline calendar:
"You have 7 regulations with effective dates in the next 90 days, here are the ones where
your test coverage is zero." The current system detects published rule changes but has no
mechanism for surfacing upcoming compliance deadlines, counting days remaining, or alerting
when a deadline approaches with zero test coverage. Effective dates are either hardcoded in
connector files (e.g., CFPB 1071 July 2026) or absent entirely. This is the single highest-
value feature for a compliance officer evaluating the product.

## Requirements

### Functional Requirements

#### Deadline Data Model
1. Add `ComplianceDeadline` to `src/models/database.py`:
   - `compliance_deadlines` table: id, regulation_id, deadline_type
     (effective_date / filing_deadline / reporting_deadline / review_deadline),
     deadline_date, description, days_remaining (computed), has_test_coverage (bool),
     test_coverage_count (int), alert_sent_at, created_at
   - Alembic migration
2. Populate deadlines from existing connectors:
   - Extract all `effective_date` fields from existing regulation records
   - Extract all `filing_deadline` parameters from requirement records
   - Auto-calculate `days_remaining` daily via background task

#### Deadline Calendar API
3. New API endpoints:
   - `GET /api/v1/deadlines` — all upcoming deadlines, sorted by date, with coverage info
   - `GET /api/v1/deadlines/alerts` — deadlines within next N days (default 90) with zero
     or low test coverage
   - `GET /api/v1/deadlines/{deadline_id}` — details including linked regulations and test scenarios

#### Alert Service
4. Add `DeadlineAlertService` in `src/services/deadline_alert_service.py`:
   - Daily job: scan all deadlines, calculate days_remaining
   - Alert thresholds: 90 days, 60 days, 30 days, 14 days, 7 days
   - Alert content: deadline name, days remaining, current test coverage count,
     uncovered obligation count (from REQ-010 if implemented)
   - Alert delivery: initially via structured log entry and API flag;
     webhook/email delivery in future sprint

#### Compliance Deadline Dashboard (New)
5. New dashboard page: `Compliance Calendar`
   - Timeline view: all deadlines plotted on a 12-month rolling calendar
   - Coverage indicator per deadline: green (>80% coverage), amber (20–80%), red (<20%)
   - Filter by: regulator, regulation category, coverage status
   - Drill-down: click a deadline to see linked regulation, requirements, and test scenario count
   - Alert panel: deadlines within 30 days with zero test coverage highlighted in red

### Pipeline 1 Changes
- Add `DeadlineTracker` background task to `ScraperScheduler`: after each scrape cycle,
  scan newly updated regulations for effective dates and upsert into `compliance_deadlines`
- Sync `compliance_deadlines` to Pipeline 2 via `/api/v1/sync/postgres` incremental diff
- Add proposed rule (NPRM) tracking: when a proposed rule is scraped, create a
  `deadline_type: "proposed_effective_date"` entry with the proposed compliance date
  (even if not yet final) — marked as `is_proposed: true`

### Pipeline 2 Changes
- `compliance_deadlines` table synced from Pipeline 1
- `DeadlineAlertService` runs nightly in Pipeline 2
- 6-agent pipeline: Generation agent checks for approaching deadlines for the regulation
  being tested and flags if deadline is within 60 days
- API endpoints exposed from Pipeline 2 app

## Acceptance Criteria
- [ ] `compliance_deadlines` table created with Alembic migration
- [ ] All existing `effective_date` fields from current connectors populate deadline records
- [ ] CFPB 1071 July 2026 deadline visible in calendar with correct coverage count
- [ ] `DeadlineAlertService` correctly identifies deadlines within 30 days with zero coverage
- [ ] `GET /api/v1/deadlines/alerts` returns correct JSON sorted by urgency
- [ ] Dashboard displays 12-month rolling calendar with coverage color coding
- [ ] Generation agent includes deadline proximity in test prioritization logic

## Technical Notes
- `days_remaining` should be a computed column or updated by a nightly task — not stored
  as static data that goes stale
- Proposed rules (NPRMs) should be clearly flagged as `is_proposed: true` in the API
  response and displayed differently in the dashboard (dashed border vs solid)
- Initial seed: after first deployment, run a one-time migration job that scans all
  existing `compliance_rules` records for date fields and creates corresponding deadline records
