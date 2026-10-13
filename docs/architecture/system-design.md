# TEXT MIND — System Architecture

## Overview

TEXT MIND is a dual-pipeline platform for banking compliance automation. Pipeline 1 runs in Fortress Cloud and handles regulatory data collection. Pipeline 2 runs inside the customer's VPC and handles transaction validation and test automation.

```
┌─────────────────────────────────────────────────────────────┐
│  PIPELINE 1 — Fortress Cloud (Master)                       │
│                                                             │
│  ScraperScheduler ──→ Connectors ──→ ComplianceRule DB      │
│       (24h cycle)      (OCC, OFAC,    (hash + version       │
│                         FinCEN, etc.)   tracked)            │
│                                                             │
│  Sync API ──────────────────────────────────────────────→   │
│    POST /sync/postgres    (incremental diffs)               │
│    POST /sync/initial     (full dump for Day 1)             │
│    POST /sync/weaviate    (vector metadata diffs)           │
│    POST /sync/status      (receive telemetry)               │
│    POST /sync/license/verify (auth check)                   │
│                                                             │
│  Internal Agent API (guarded route)                         │
│    POST /internal/agent/run → LangGraph compliance agent    │
│                                                             │
│  Stack: PostgreSQL · Redis · Weaviate · FastAPI             │
└─────────────────────────────────────────────────────────────┘
                           │
                           │ HTTPS — daily incremental sync
                           │ Only public regulatory data
                           │ Zero customer data sent upstream
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  PIPELINE 2 — Customer VPC (Replica)                        │
│                                                             │
│  UpdateService ──→ Local PostgreSQL ──→ LocalComplianceProvider
│    (daily sync)     (compliance_rules      (zero network calls)
│                      replica)                   │
│                                                 ▼
│  Validation Endpoints                     Validators
│    POST /validate/wire-transfer    →  WireTransferValidator
│    POST /validate/ofac-screen      →  OFACScreener
│    POST /validate/loan-application →  LoanApplicationValidator
│                                                             │
│  Test Automation (JIRA → 6-agent pipeline)                  │
│    POST /webhooks/jira → Orchestrator → 6 Agents → CI/CD   │
│                                                             │
│  Stack: PostgreSQL · Redis · Weaviate · FastAPI             │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. Entry Points

| File | Pipeline | Description |
|------|----------|-------------|
| `src/main_pipeline1.py` | 1 | Fortress Cloud — scraping, sync API, agent API |
| `src/main_pipeline2.py` | 2 | Customer VPC — validators, webhooks, test agents |
| `src/main.py` | Dev | Legacy unified entry for local development |

### 2. Regulatory Scraping (Pipeline 1)

**ScraperScheduler** (`src/services/scraper_scheduler.py`) runs as a background daemon thread:

1. Loads connectors via `PluginManager(geography="usa")`
2. Calls each connector's `get_compliance_rules()` (OCC, OFAC, FinCEN, FDIC, CFPB)
3. Computes SHA-256 hash of each rule for change detection
4. Upserts changed rules into `compliance_rules` table with monotonic version
5. Creates tombstone records in `deleted_rules` for rules no longer present
6. Bumps `sync_versions.current_version`
7. Sleeps for `SCRAPER_INTERVAL_HOURS` (default 24)

### 3. Sync Protocol (Option 3 — Master-Replica)

**Sync API** (`src/api/sync_api.py`) on Pipeline 1 serves compliance data to customer replicas:

- **Initial sync** (`POST /sync/initial`): Paginated full dump of all active rules. Used on first boot (version 0). Page size up to 50,000 rules.
- **Incremental sync** (`POST /sync/postgres`): Returns only rules added, updated, or deleted since `from_version`. Typical payload ~2 MB/day.
- **License verification** (`POST /sync/license/verify`): Validates customer API key before allowing sync.
- **Telemetry** (`POST /sync/status`): Receives sync results from customer (version, counts — no customer data).

**UpdateService** (`src/services/update_service.py`) on Pipeline 2 orchestrates the customer side:

1. Starts as daemon thread on app boot
2. Verifies license with Fortress Cloud
3. Checks local `sync_version` (0 = first boot → initial sync; >0 → incremental)
4. Applies diffs to local `compliance_rules` table
5. Indexes updated rules into local Weaviate for RAG
6. Records operation in `local_sync_status` table
7. Reports telemetry back to Fortress Cloud
8. Sleeps for `SYNC_INTERVAL_HOURS` (default 24)

### 4. Production Validators (Pipeline 2)

All validators read rules from **LocalComplianceProvider** (`src/services/local_compliance_provider.py`) — direct SQL queries against the local PostgreSQL replica. Zero network calls during validation.

| Validator | Regulation | Logic |
|-----------|-----------|-------|
| `WireTransferValidator` | OCC BSA/AML | Dual-approval check, CTR threshold ($10K+), Travel Rule (international) |
| `OFACScreener` | OFAC Sanctions | Fuzzy name matching against SDN list (SequenceMatcher, threshold 0.85) |
| `LoanApplicationValidator` | CFPB Section 1071 | Required demographic fields, NAICS code validation |

### 5. AI Agents (Pipeline 2)

Six specialized agents in a linear pipeline, orchestrated by `WorkflowEngine`:

| Agent | Role |
|-------|------|
| **Discovery** | Analyzes JIRA story, finds related code via Weaviate |
| **Understanding** | Generates test scenarios using RAG (Weaviate + compliance knowledge) |
| **Framework** | Sets up test infrastructure (pytest fixtures, conftest) |
| **Generation** | Creates test code using AWS Bedrock (Claude) |
| **Validation** | Runs tests in Docker sandbox |
| **Execution** | Commits to Git, triggers CI/CD |

**LangGraph Compliance Agent** (Pipeline 1 only): Autonomous agent that scrapes regulatory sources using a state machine with tool-calling nodes.

### 6. Data Models

Key SQLAlchemy models in `src/models/database.py`:

**Core models:**
- `ComplianceRule` — Regulatory rules with `hash` (SHA-256) and `version` (monotonic) for sync
- `OFACSDNEntry` — OFAC SDN list entries
- `ComplianceRuleChange` — Audit trail of rule changes
- `WorkflowExecution` — 6-agent pipeline execution state
- `CheckpointApproval` — Human approval records
- `TransactionValidation` / `OFACScreening` — Validation audit logs

**Sync models (Option 3):**
- `SyncVersion` — Singleton row with `current_version` counter (Pipeline 1)
- `DeletedRule` — Tombstone table for deleted rules (Pipeline 1)
- `SyncMetadata` — Per-customer sync tracking (Pipeline 1)
- `LocalSyncStatus` — Sync operation log (Pipeline 2)

### 7. Weaviate Collections

| Collection | Content | Used By |
|-----------|---------|---------|
| BankingWorkflow | Domain knowledge (wire, ACH, loan workflows) | Understanding Agent |
| ComplianceRule | Regulatory rule vectors | Understanding Agent, RAG |
| TestPattern | Test templates and patterns | Generation Agent |
| CodebaseChunk | Indexed client codebase | Discovery Agent |
| GeneratedTest | Historical generated tests | Learning feedback |
| ApprovalPattern | Past approval decisions | Auto-approval |

### 8. MCP Servers

Banking knowledge exposed via Model Context Protocol:

- `banking_workflows/` — Banking operations knowledge (wire transfers, ACH, etc.)
- `compliance_knowledge/` — Regulatory rules and interpretations
- `test_patterns/` — Reusable test templates for banking scenarios

### 9. Security

- **PII guard**: `FortressAPIClient` blocks outgoing requests containing PII fields (SSN, account numbers, etc.)
- **Internal route guard**: `/internal/*` endpoints on Pipeline 1 require `FORTRESS_INTERNAL_SECRET` header
- **Multi-stage Docker build**: `Dockerfile.pipeline2` strips scrapers, LangGraph agent, and internal APIs — customer image contains only Pipeline 2 code
- **Circuit breaker**: API client fails-open with cached rules if Fortress Cloud is unreachable
- **Data isolation**: Customer data never leaves the VPC — validators read from local DB only

---

## Data Flow Summary

### Regulatory Data (Pipeline 1 → Pipeline 2)
```
Web sources → Connectors → ComplianceRule (master) → Sync API → ComplianceRule (replica) → Validators
```

### Transaction Validation (Pipeline 2 — all local)
```
Customer request → validation_api → Validator → LocalComplianceProvider → local PostgreSQL → response
```

### Test Automation (Pipeline 2 — all local)
```
JIRA webhook → WorkflowEngine → 6 Agents → Weaviate RAG → test code → sandbox → CI/CD → JIRA update
```
