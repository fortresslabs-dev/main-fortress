# REQ-019: Compliance Officer Dashboard (Master Dashboard)

## Overview
**Priority:** High
**Type:** New Dashboard
**Source:** Synthesized gap across all 4 competitor analyses
**Pipeline Impact:** Pipeline 2 (API aggregation) + New Frontend Dashboard
**Status:** Backlog

## Business Context
The 6-agent pipeline and validation APIs serve the developer workflow well. The compliance
officer and risk officer have no purpose-built view. Every competitor — Ascent, OneSumX,
Thomson Reuters, Compliance.ai — leads with a compliance officer dashboard as their primary
UI. Without one, Fortress can only be sold to engineering teams, not to the compliance and
risk officers who control the compliance technology budget at regional banks. This dashboard
aggregates data from REQ-010 (coverage score), REQ-012 (deadline calendar), REQ-013
(change management), REQ-018 (policy mapping), and REQ-011 (enforcement intelligence)
into a single compliance officer home screen.

## Requirements

### Dashboard Panels

#### Panel 1: Compliance Health Score (Top of Page)
- Overall compliance test coverage percentage across all regulations
- Trend: up/down vs. last 30 days
- Breakdown by regulator (OCC, FDIC, CFPB, FinCEN, OFAC, CRA, FFIEC)
- RAG status: Green (>80% coverage), Amber (50–80%), Red (<50%)
- Requires: REQ-010 (obligation coverage score)

#### Panel 2: Upcoming Compliance Deadlines (Top Right)
- Next 5 deadlines sorted by urgency
- Each deadline shows: regulation name, days remaining, test coverage count
- Color coded: Red (<30 days), Amber (30–60 days), Green (>60 days)
- "View All" links to full compliance calendar (REQ-012)
- Requires: REQ-012 (deadline calendar)

#### Panel 3: Regulatory Change Inbox (Center)
- All open change records requiring review or sign-off
- Sorted by: days since detection (oldest first)
- Status badge per record: Detected / In Review / Awaiting Sign-Off
- One-click sign-off action from panel
- Count badge: "X changes require your attention"
- Requires: REQ-013 (change management workflow)

#### Panel 4: Enforcement Risk Heatmap (Center Right)
- Risk score per regulation displayed as a colored tile grid
- High enforcement risk = red tile, medium = amber, low = green
- Tooltip on hover: top violation pattern, penalty magnitude, trend direction
- Requires: REQ-011 (enforcement intelligence)

#### Panel 5: Test Coverage by Regulation (Bottom Left)
- Bar chart: each regulation on X axis, coverage % on Y axis
- Click a bar to drill into uncovered obligations for that regulation
- Requires: REQ-010 (obligation universe + coverage score)

#### Panel 6: Policy Coverage Gap (Bottom Center)
- Table of regulations with zero mapped internal policies
- Each row: regulation name, regulator, last examined date, action link
- Requires: REQ-018 (policy mapping)

#### Panel 7: Recent Validation Activity (Bottom Right)
- Last 10 validation results (wire transfers, OFAC screens, loan applications)
- Action (allow/block/review), regulation, timestamp, latency
- Quick stats: block rate today, review rate today
- Requires: existing `ValidationResult` data from validators

### API Aggregation Endpoint
- New endpoint: `GET /api/v1/dashboard/compliance-overview`
- Returns all panel data in a single response to minimize round trips
- Caches for 5 minutes (Redis, reuse existing cache infrastructure)
- Includes: health_score, upcoming_deadlines (5), open_change_records count,
  enforcement_risk_scores, coverage_by_regulation, policy_gaps, recent_validations (10)

### Pipeline 2 Changes
- Add `GET /api/v1/dashboard/compliance-overview` endpoint
- Aggregate data from: `compliance_rules`, `compliance_deadlines`, `regulatory_change_records`,
  `enforcement_risk_scores`, `obligations`, `bank_policies`, validation result logs
- Redis cache key: `dashboard:compliance_overview:{client_id}`, TTL 5 minutes

### Pipeline 1 Changes
- None — dashboard is a Pipeline 2 consumer of synced data

## Acceptance Criteria
- [ ] `GET /api/v1/dashboard/compliance-overview` returns all 7 panel data sets
- [ ] Response time < 500ms with Redis cache warm
- [ ] Graceful degradation: if REQ-010/011/012/013/018 not yet implemented, panel returns
      empty data with `available: false` flag rather than an error
- [ ] Dashboard renders correctly with 0 change records (empty state handling)
- [ ] Compliance health score updates within 24 hours of new test scenarios being added
- [ ] All panels accessible on a 1280x800 viewport without horizontal scroll

## Technical Notes
- Build the API endpoint first (backend-first approach) — frontend implementation
  follows once the data contract is stable
- Graceful degradation is critical: panels backed by unreleased requirements (REQ-010
  through REQ-018) must return `{ "available": false, "reason": "feature not enabled" }`
  not 500 errors — dashboard must be deployable before all dependencies are complete
- Client ID isolation: all dashboard queries must be scoped to `CLIENT_NAME` from settings
  to prevent data leakage between bank deployments
- The enforcement heatmap (Panel 4) uses only aggregate/public data — never bank-specific
  transaction data — so this panel can be served from Pipeline 1 sync data safely
