# DB Schema Mapping — Fortress Pipeline 1 → Pipeline 2 (Client VPC)

**Architecture:** Master-Replica Sync (Option 3)
**Last updated:** 2026-04-22

---

## Overview

Pipeline 1 (Fortress Cloud, AWS ECS) scrapes 18+ US regulatory sources daily and stores
all compliance data. Pipeline 2 (Customer VPC / on-prem Docker) pulls incremental diffs
via the Sync API and maintains a local replica. **Customer data never leaves the VPC** —
only `customer_id` and `sync_version` are sent outbound.

```
┌─────────────────────────────────────┐          ┌─────────────────────────────────────┐
│         FORTRESS CLOUD (P1)         │          │         CUSTOMER VPC (P2)           │
│                                     │          │                                     │
│  ScraperScheduler (24h cycle)       │  Sync    │  UpdateService (daemon thread)      │
│  ├─ OCC / FDIC / CFPB / OFAC ...   │ ──────► │  ├─ /sync/initial  (first boot)     │
│  └─ writes → compliance_rules       │   API    │  └─ /sync/postgres (daily delta)    │
│                                     │          │                                     │
│  PostgreSQL (source of truth)       │          │  PostgreSQL (local replica)         │
│  Weaviate  (vector index)           │          │  Weaviate  (local vector index)     │
│  Redis     (LLM cache)              │          │  Redis     (rule + LLM cache)       │
└─────────────────────────────────────┘          └─────────────────────────────────────┘
```

---

## 1. PostgreSQL Schema

### 1.1 Pipeline 1 — Fortress Cloud Tables

#### `compliance_rules` — Primary sync payload

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | Internal primary key |
| `rule_id` | VARCHAR(100) | Business key (e.g. `OCC-BSA-001`) |
| `regulator` | VARCHAR(50) | occ, cfpb, fincen, ofac, fdic, ffiec, frb, ncua |
| `category` | VARCHAR(100) | bsa_aml, section_1071, wire_transfer, sanctions, etc. |
| `geography` | VARCHAR(10) | usa, uk — default "usa" |
| `domain` | VARCHAR(100) | wire_transfer, loan, deposit, crypto, etc. |
| `rule_type` | VARCHAR(50) | dual_approval, ctr_threshold, required_field, etc. |
| `rule_text` | TEXT | Full regulatory rule text |
| `threshold_amount` | FLOAT | Monetary threshold (e.g. 10000.0 for CTR) |
| `threshold_currency` | VARCHAR(10) | USD (default) |
| `field_name` | VARCHAR(100) | For required_field rules — the field name |
| `effective_date` | DATETIME | Rule effective date |
| `source_url` | TEXT | Originating regulatory URL |
| `test_template` | TEXT | Auto-generated test template |
| `is_active` | BOOLEAN | Soft-delete flag |
| `hash` | VARCHAR(64) | SHA-256 of rule content — change detection |
| `version` | BIGINT | Monotonic sync version when last written |
| `cfr_citation` | TEXT | CFR citation (e.g. "12 CFR 21.11") |
| `state_code` | VARCHAR(10) | For state-level rules |
| `jurisdiction_level` | VARCHAR(10) | federal \| state |
| `enforcement_risk_score` | FLOAT | Computed from enforcement actions (0–100) |
| `previous_content` | TEXT | Prior rule_text before last change |
| `change_delta` | JSONB | Diff between previous and current content |
| `changed_obligations` | JSONB | Obligation IDs affected by the change |
| `change_type` | VARCHAR(20) | added \| modified \| removed \| clarified |
| `obligation_count` | INTEGER | Count of discrete obligations in this rule |
| `created_at` | DATETIME | |
| `updated_at` | DATETIME | Auto-updated on write |

**Indexes:** `rule_id`, `regulator`, `version`, composite `(geography, regulator, domain)`
**Unique:** `(rule_id, regulator, geography)`

---

#### `ofac_sdn_entries` — OFAC Specially Designated Nationals

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `sdn_number` | VARCHAR(50) UNIQUE | OFAC internal SDN identifier |
| `entity_name` | VARCHAR(500) | Primary name (indexed for fuzzy matching) |
| `entity_type` | VARCHAR(50) | individual, entity, vessel, aircraft |
| `programs` | JSON | ["SDGT", "IRAN"] |
| `aliases` | JSON | List of alternative names |
| `addresses` | JSON | Known addresses |
| `id_numbers` | JSON | Passport / national ID numbers |
| `remarks` | TEXT | OFAC notes |
| `country` | VARCHAR(100) | Country of origin |
| `nationality` | VARCHAR(100) | Nationality |
| `is_active` | BOOLEAN | |
| `source_updated_at` | DATETIME | Date of source SDN list used |
| `created_at` / `updated_at` | DATETIME | |

---

#### `sync_versions` — Global monotonic counter

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Always 1 (single row) |
| `current_version` | BIGINT | Increments on every scraper write cycle |
| `updated_at` | DATETIME | |

---

#### `sync_metadata` — Per-customer sync tracking

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `customer_id` | VARCHAR(100) UNIQUE | Client identifier |
| `last_sync_version` | BIGINT | Highest version synced to this customer |
| `last_sync_timestamp` | DATETIME | Wall-clock time of last sync |
| `sync_count` | INTEGER | Total syncs performed |
| `created_at` / `updated_at` | DATETIME | |

---

#### `deleted_rules` — Tombstone table

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `rule_id` | VARCHAR(100) | Rule that was deleted |
| `regulator` | VARCHAR(50) | |
| `geography` | VARCHAR(10) | |
| `deleted_at_version` | BIGINT | Version when deletion occurred |
| `deleted_at` | DATETIME | |

---

#### `compliance_rule_changes` — Delta audit log

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `rule_id` | VARCHAR(100) | |
| `change_type` | VARCHAR(20) | added, modified, removed |
| `old_value` | JSON | Previous full rule payload |
| `new_value` | JSON | New full rule payload |
| `changed_at` | DATETIME | |

---

#### Enrichment Tables (Pipeline 1 only)

| Table | Purpose | Key Columns |
|---|---|---|
| `obligations` | Discrete compliance obligations extracted from docs | `obligation_id`, `regulation_id`, `obligation_type`, `source_text`, `cfr_citation`, `has_test_coverage` |
| `coverage_snapshots` | Daily coverage % per regulator | `snapshot_date`, `regulator`, `covered_obligations`, `coverage_pct` |
| `enforcement_actions` | Scraped enforcement actions | `action_ref`, `regulator`, `action_type`, `action_date`, `penalty_amount_usd`, `regulations_cited` |
| `enforcement_risk_scores` | Computed risk scores per regulation | `regulation_id`, `risk_score`, `action_count`, `avg_penalty_usd`, `trend_direction` |
| `compliance_deadlines` | Upcoming regulatory deadlines | `deadline_key`, `deadline_date`, `days_remaining`, `has_test_coverage` |
| `regulatory_change_records` | Change management workflow | `change_ref`, `rule_id`, `impact_rating`, `status`, `semantic_diff`, `signed_off_at` |
| `change_record_comments` | Append-only audit trail for change records | `change_record_id`, `author`, `comment_type`, `status_from`, `status_to` |
| `peer_benchmarks` | Anonymised peer coverage benchmarks | `regulation_id`, `asset_tier`, `avg_coverage_pct`, `top_quartile_pct` |

---

### 1.2 Pipeline 2 — Client VPC Tables

#### `compliance_rules` — Local replica (same schema as Pipeline 1)

Populated entirely via Sync API. The `version` and `hash` columns are used to apply
incremental upserts and detect stale data.

#### `ofac_sdn_entries` — Local replica (same schema as Pipeline 1)

Used by `OFACScreener` for zero-network-call sanctions screening at validation time.

#### `local_sync_status` — Sync operation log

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `sync_version` | BIGINT | Version fetched in this run |
| `sync_timestamp` | DATETIME | Wall-clock time |
| `rules_added` | INTEGER | New rules inserted |
| `rules_updated` | INTEGER | Existing rules updated |
| `rules_deleted` | INTEGER | Tombstone deletes applied |
| `sync_status` | VARCHAR(20) | SUCCESS, PARTIAL, FAILED |
| `error_message` | TEXT | Set on failure |
| `sync_type` | VARCHAR(20) | initial \| incremental |

#### `transaction_validations` — Wire/Loan audit log (never leaves VPC)

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `validation_type` | VARCHAR(50) | wire_transfer, loan_application |
| `action` | VARCHAR(20) | allow, block, review |
| `violations_count` | INTEGER | |
| `violations` | JSON | Rule IDs violated (no customer PII) |
| `request_metadata` | JSON | Non-PII metadata only |
| `latency_ms` | FLOAT | |
| `created_at` | DATETIME | |

#### `ofac_screenings` — OFAC audit log (never leaves VPC)

| Column | Type | Description |
|---|---|---|
| `id` | UUID PK | |
| `action` | VARCHAR(20) | clear, block |
| `match_count` | INTEGER | |
| `matched_entries` | JSON | SDN numbers only (no customer names stored) |
| `latency_ms` | FLOAT | |
| `created_at` | DATETIME | |

#### `workflow_executions` + `checkpoint_approvals` — JIRA agent pipeline state

| Table | Key Columns |
|---|---|
| `workflow_executions` | `jira_story_id`, `jira_story_key`, `status` (pending/running/waiting_approval/completed/failed), `context_data`, `artifacts` |
| `checkpoint_approvals` | `workflow_id`, `checkpoint_name`, `status` (pending/approved/rejected), `approved_by`, `feedback` |

---

## 2. Sync API Endpoints

| Endpoint | Direction | Used By | Notes |
|---|---|---|---|
| `GET /api/v1/sync/initial?page=N` | P1 → P2 | First boot (version=0) | Paginated full dump of all active rules |
| `GET /api/v1/sync/postgres?since_version=X` | P1 → P2 | Daily delta | Returns changed/deleted rules since version X (~2 MB/day) |
| `POST /api/v1/sync/ack` | P2 → P1 | After successful sync | Updates `sync_metadata.last_sync_version` |

**Outbound from VPC (P2 → P1):** only `{ customer_id, current_local_version }` — no transaction data, no customer PII.

---

## 3. Weaviate Schema — Vector Collections

**Vectorizer:** AWS Bedrock Titan v2 (`amazon.titan-embed-text-v2:0`)
**Dimensions:** 1024
**Distance metric:** Cosine (threshold 0.35 — results beyond this distance are suppressed)
**ID strategy:** Deterministic UUID5 from collection name + key fields (enables upsert semantics)

### Collections

#### `ComplianceRule` — Structured regulatory rules

| Property | Type | Notes |
|---|---|---|
| `rule_id` | TEXT | |
| `regulator` | TEXT | |
| `category` | TEXT | |
| `rule_type` | TEXT | |
| `rule_text` | TEXT | Vectorized field |
| `source_url` | TEXT | |
| `geography` | TEXT | |
| `cfr_citation` | TEXT | |
| `domain` | TEXT | |
| `threshold_amount` | NUMBER | |
| `threshold_currency` | TEXT | |
| `effective_date` | TEXT | |
| `obligation_type` | TEXT | |
| `state_code` | TEXT | |
| `enforcement_risk_score` | NUMBER | |
| `change_summary` | TEXT | |

**Used by:** Agent 3 (Framework selection), Agent 5 (Validation against rules)

---

#### `ComplianceDocument` — Full-text scraped regulatory documents (chunked)

| Property | Type | Notes |
|---|---|---|
| `doc_id` | TEXT | First 12 chars of content_hash |
| `regulator` | TEXT | |
| `document_title` | TEXT | |
| `section_title` | TEXT | |
| `content` | TEXT | Chunk text (max 10,000 chars) — vectorized |
| `page_number` | INT | |
| `chunk_index` | INT | Position within document |
| `source_url` | TEXT | |
| `document_type` | TEXT | regulation, guidance, bulletin, etc. |
| `content_hash` | TEXT | SHA-256 of original content |
| `scraped_at` | TEXT | |
| `is_obligation` | BOOL | True if chunk contains mandatory language |
| `obligation_type` | TEXT | shall, must, prohibited, required, may_not |
| `cfr_citation` | TEXT | |
| `section_reference` | TEXT | |
| `state_code` | TEXT | |
| `jurisdiction` | TEXT | federal \| state |
| `regulation_id` | TEXT | |
| `doc_type` | TEXT | |
| `token_count` | INT | |

**Used by:** Agent 2 (Understanding / deep semantic analysis)

---

#### `ObligationRecord` — Discrete compliance obligations

| Property | Type | Notes |
|---|---|---|
| `obligation_id` | TEXT | |
| `regulation_id` | TEXT | |
| `regulator` | TEXT | |
| `source_text` | TEXT | Extracted obligation sentence — vectorized |
| `obligation_type` | TEXT | shall, must, prohibited, required, may_not |
| `section_reference` | TEXT | |
| `cfr_citation` | TEXT | |
| `has_test_coverage` | BOOL | Updated when test is generated |
| `extracted_at` | TEXT | |
| `doc_id` | TEXT | Back-reference to ComplianceDocument |

**Used by:** Coverage dashboard, obligation gap analysis

---

#### `EnforcementAction` — Regulatory enforcement actions

| Property | Type | Notes |
|---|---|---|
| `action_ref` | TEXT | |
| `regulator` | TEXT | |
| `action_type` | TEXT | Consent Order, Civil Money Penalty, etc. |
| `action_date` | TEXT | |
| `violation_summary` | TEXT | Vectorized |
| `regulations_cited` | TEXT | JSON list as string |
| `penalty_amount` | NUMBER | USD |
| `institution_type` | TEXT | bank, credit_union, msa, etc. |
| `asset_tier` | TEXT | small, mid, large |
| `source_url` | TEXT | |

**Used by:** Enforcement risk scoring, peer benchmarks

---

#### `RegulatoryChange` — Semantic change records

| Property | Type | Notes |
|---|---|---|
| `change_ref` | TEXT | |
| `rule_id` | TEXT | |
| `regulation_id` | TEXT | |
| `regulator` | TEXT | |
| `change_summary` | TEXT | LLM-generated summary — vectorized |
| `semantic_diff` | TEXT | Structured diff of obligations |
| `impact_rating` | TEXT | low, medium, high, critical |
| `change_type` | TEXT | added, modified, removed, clarified |
| `detected_at` | TEXT | |
| `status` | TEXT | detected, under_review, approved, dismissed |

**Used by:** Change management workflow, impact assessment

---

#### `BankingWorkflow` — Banking workflow patterns

| Property | Type | Notes |
|---|---|---|
| `workflow_name` | TEXT | |
| `description` | TEXT | Vectorized |
| `business_rules` | TEXT | |
| `test_scenarios` | TEXT | |

**Used by:** Agent 1 (Discovery), MCP `banking_workflows` server

---

#### `CodebaseChunk` — Indexed source code (developer tooling)

| Property | Type | Notes |
|---|---|---|
| `repo` | TEXT | Repository name |
| `file_path` | TEXT | Relative path from src root |
| `chunk_type` | TEXT | function, class, module |
| `name` | TEXT | Function/class name or file path |
| `code` | TEXT | Source code (max 8,000 chars) — vectorized |

**Used by:** MCP developer tools, code search

---

## 4. Redis Cache Keys

| Key Pattern | TTL | Content | Layer |
|---|---|---|---|
| `llm:{sha256_of_prompt}` | 86400s (24h) | Raw LLM response string | Pipeline 1 & 2 |
| `compliance:rules:{regulator}:{geography}` | Short | Serialized rule list from LocalComplianceProvider | Pipeline 2 |
| `ofac:sdn:{normalized_name}` | Short | Cached screening result | Pipeline 2 |
| `circuit_breaker:fortress_api` | Sliding | Last known-good rule snapshot for failover | Pipeline 2 |

**Circuit breaker:** `FortressAPIClient` (`src/services/fortress_api_client.py`) fails open —
if Fortress API is unreachable, validators use the Redis-cached rules from the last successful sync.
`LLM_CACHE_TTL` is configurable via env var (default 86400).

---

## 5. Data Residency Summary

| Data Class | Pipeline 1 (Fortress Cloud) | Pipeline 2 (Client VPC) | Crosses Boundary |
|---|---|---|---|
| Regulatory rules | Source (scraped) | Replica (synced) | Yes — inbound only |
| OFAC SDN list | Source | Replica | Yes — inbound only |
| Sync version / customer_id | Tracks per customer | Reads own version | Yes — version outbound |
| Transaction records | Never stored | Audit log only | **No** |
| OFAC screening results | Never stored | Audit log only | **No** |
| Customer PII | Never stored | Processing only | **No** |
| JIRA story data | Never stored | Local workflow state | **No** |
| LLM responses | Cached (public rules only) | Cached (local) | **No** |

---

## 6. Setup Sequence (Client VPC)

```bash
# 1. Start services
docker compose -f docker-compose.pipeline2.yml up -d --build

# 2. Run migrations
docker exec -it customer-pipeline2 alembic upgrade head

# 3. Pipeline 2 UpdateService runs initial sync automatically on first boot
#    (detects local version = 0, calls /api/v1/sync/initial)

# 4. Seed Weaviate from synced Postgres data
docker exec -it customer-pipeline2 python -m scripts.seed_weaviate

# 5. Verify sync status
GET /api/v1/sync/status
```

**Environment variables required in Pipeline 2:**

| Variable | Description |
|---|---|
| `FORTRESS_API_URL` | Fortress Cloud API base URL |
| `FORTRESS_API_KEY` | Issued per customer by Fortress |
| `CUSTOMER_ID` | Unique customer identifier |
| `SYNC_INTERVAL_HOURS` | Sync frequency (default 24) |
| `DATABASE_URL` | Customer's local PostgreSQL |
| `REDIS_URL` | Customer's local Redis |
| `WEAVIATE_URL` | Customer's local Weaviate |
| `AWS_REGION` | For Bedrock Titan vectorizer |
