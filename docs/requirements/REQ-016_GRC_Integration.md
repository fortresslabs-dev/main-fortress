# REQ-016: GRC System Integration (ServiceNow, RSA Archer)

## Overview
**Priority:** Medium
**Type:** New Integration
**Source:** Competitor gap — Compliance.ai / Corlytics
**Pipeline Impact:** Pipeline 2 only (outbound push to customer GRC systems)
**Implementation Order:** Pipeline 2 only — this is an outbound integration from the
customer VPC to the bank's own GRC tools (ServiceNow, Archer). No Fortress Cloud (P1)
involvement required. Exception to the P1-first rule: no regulatory data is created
here, only consumed and forwarded.
**Status:** Backlog

## Business Context
Compliance.ai integrates with ServiceNow GRC, RSA Archer, and MetricStream — the platforms
regional banks already use to manage their control frameworks and risk registers. Fortress
integrates with JIRA (developer workflow) but not GRC platforms (risk and compliance officer
workflow). Banks that run Archer for their control inventory or ServiceNow for IT risk
management will ask for this integration on day one of a sales evaluation. Without it,
test results and compliance findings stay siloed in Fortress and never flow into the bank's
existing risk management system of record. This integration lives entirely in Pipeline 2 —
customer GRC systems are inside the VPC alongside Pipeline 2.

## Requirements

### Functional Requirements

#### Outbound Integration Framework
1. Create an integration framework in `src/integrations/`:
   - `src/integrations/base.py` — `BaseGRCIntegration` abstract class:
     `push_finding(finding: ValidationResult) -> bool`
     `push_test_result(result: dict) -> bool`
     `push_change_record(change: RegulatoryChangeRecord) -> bool`
     `health_check() -> bool`
   - `src/integrations/servicenow.py` — ServiceNow GRC connector
   - `src/integrations/archer.py` — RSA Archer connector
   - `src/integrations/webhook.py` — Generic webhook connector (covers MetricStream
     and any other GRC via configurable POST endpoint)

#### ServiceNow GRC Integration
2. `ServiceNowIntegration`:
   - Push `ValidationResult` violations as ServiceNow GRC Issues
   - Push `RegulatoryChangeRecord` as ServiceNow Policy Exceptions or Change Tasks
   - Map Fortress `severity` (high/medium/low) to ServiceNow Priority (1/2/3)
   - Authenticate via ServiceNow OAuth2 client credentials
   - Configuration: `SERVICENOW_INSTANCE_URL`, `SERVICENOW_CLIENT_ID`,
     `SERVICENOW_CLIENT_SECRET` env vars

#### RSA Archer Integration
3. `ArcherIntegration`:
   - Push validation findings as Archer Issues records
   - Push regulatory change records as Archer Policy Change records
   - Map to Archer application IDs (configurable per deployment)
   - Authenticate via Archer REST API token
   - Configuration: `ARCHER_BASE_URL`, `ARCHER_INSTANCE_NAME`, `ARCHER_API_TOKEN` env vars

#### Generic Webhook Integration
4. `WebhookIntegration`:
   - Configurable POST endpoint URL
   - Standardized JSON payload format (Fortress native schema)
   - HMAC signature header for payload authentication
   - Retry with exponential backoff on failure (3 retries)
   - Configuration: `GRC_WEBHOOK_URL`, `GRC_WEBHOOK_SECRET` env vars

#### Integration Configuration API
5. New API endpoints in Pipeline 2:
   - `POST /api/v1/integrations/configure` — set integration type and credentials
   - `GET /api/v1/integrations/status` — health check for configured integrations
   - `POST /api/v1/integrations/test` — send a test payload to verify connectivity

### Pipeline 2 Changes
- Add `src/integrations/` module
- Extend `src/api/validation_api.py`: after producing a `ValidationResult`, check if
  a GRC integration is configured and push the result
- Extend change management workflow (REQ-013): when a change record is signed off,
  push to GRC integration if configured
- Add integration configuration to `src/utils/config.py` Settings class
- Integration credentials stored encrypted in Pipeline 2 DB (never sent to Pipeline 1)

### Pipeline 1 Changes
- None — GRC integrations are customer-side only (Pipeline 2)

## Acceptance Criteria
- [ ] `WebhookIntegration.push_finding()` successfully POSTs a `ValidationResult` to a
      test endpoint with correct HMAC signature
- [ ] `ServiceNowIntegration` health check returns True when credentials are valid
- [ ] `ArcherIntegration` health check returns True when credentials are valid
- [ ] `GET /api/v1/integrations/status` returns integration type and last successful push time
- [ ] Integration failures do not block the validation result — push is fire-and-forget
      with logged error on failure
- [ ] All integration credentials stored encrypted, not in plaintext

## Technical Notes
- Integration pushes must be non-blocking — use `asyncio.create_task()` for the push
  so the validation response is not delayed by GRC latency
- Sensitive credentials (ServiceNow client secret, Archer token): encrypt at rest using
  Fernet symmetric encryption keyed from `SECRET_KEY` settings field
- ServiceNow and Archer integrations require network access from the customer VPC to
  the respective SaaS endpoints — document this as a deployment prerequisite
- Generic webhook is the MVP delivery vehicle — build it first, ServiceNow and Archer
  integrations follow in subsequent sprints as they require customer-specific testing
