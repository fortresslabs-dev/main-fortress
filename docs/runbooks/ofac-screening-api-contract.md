# OFAC / SDN Screening — API Contract (v1)

Status: draft, 2026-06-04 · v0 surface shipped 2026-06-06 · Owner: P2 validators
Engine: `src/services/validators.py:OFACScreener.screen()` — all four endpoints
are thin adapters over the same call. Do **not** duplicate scoring logic in the
API layer.

> **v0 shipped:** the single endpoint described in
> `docs/runbooks/sdn-dashboard-completion.md` Phase 3 is live at
> `POST /api/v1/screen/sdn` (`src/api/screening_api.py`). See §0 below
> for the v0-vs-v1 delta. APIs 1–4 in §§3–6 remain the target shape.

---

## Design rules (read first)

1. **One engine.** Every endpoint resolves to `OFACScreener.screen(party_name, party_country)`. Multi-party = fan out + aggregate worst action. Batch = async fan-out over the same call.
2. **Three-tier decision** (`PASS` / `REVIEW` / `HOLD`) mapped from `final_score` via per-class SSM thresholds at `/fortress/p2/ofac/threshold/{individual|alias|entity}`. No hardcoded cutoffs in code.
3. **Subscores are first-class.** Engine must expose `trgm_score`, `fuzz_score`, `vector_score`, `final_score` per match. Required by audit/explainability — single blended number is not acceptable to compliance reviewers.
4. **Every call writes an audit row** (`screening_events` table — TBD migration) with `screening_id`, `user_id`, `criteria_hash`, `decision`, `matches[]`, `reviewer_action`, `ts`. Retention: 7yr. Append-only.
5. **Staleness gate stays.** `OFACScreener._enforce_freshness()` raises if SDN > 26h old (OFAC-SDN-DAILY-001) — surface as HTTP 503, not 200.

---

## Engine response shape (canonical)

Returned by `OFACScreener.screen()` — extended from current `ValidationResult`:

```json
{
  "screening_id": "SCR-01J7...",
  "decision": "PASS|REVIEW|HOLD",
  "final_score": 0.0,
  "matches": [
    {
      "sdn_number": "12345",
      "matched_name": "MOHAMMED ALI",
      "is_primary": false,
      "entity_type": "individual",
      "programs": ["SDGT"],
      "country": "AE",
      "scores": {
        "trgm": 0.91, "fuzz": 0.88, "vector": 0.85, "final": 0.90
      }
    }
  ],
  "engine_mode": "vector",
  "sdn_data_age_hours": 4.2,
  "latency_ms": 38.4,
  "screened_at": "2026-06-04T14:02:11Z"
}
```

API endpoints wrap this; they never recompute scores.

---

## §0 — Shipped surface (v0)

The dashboard rollout needed one ingress before the four-API v1 surface
lands. Implemented at:

| Method | Path | Source |
|---|---|---|
| POST | `/api/v1/screen/sdn` | `src/api/screening_api.py` |
| GET  | `/api/v1/screen/sdn/{screening_id}` | `src/api/screening_api.py` |

Behaviour mirrors §1's design rules:

- **One engine.** `OFACScreener.screen()` directly, with full kwargs
  (`actor_kind`, `external_customer_id`, `request_kind`, `party_role`).
- **Three-tier decision.** `OFAC_HOLD_THRESHOLD = 0.92` is the cutoff
  today; per-class SSM thresholds (§1 rule 2) will replace it once
  calibration signs off.
- **Audit row + case upsert** are written by the engine itself (see
  `OFACScreener._write_audit`). v0 does no further audit in the API layer.
- **Staleness gate.** Soft band (24–36h) bumps `decision` one level;
  hard band (>36h) returns **503** as `_enforce_freshness()` raises.
- **Idempotency.** 24h dedupe by `criteria_hash`; cache invalidates on
  any SDN refresh. Cache hits set `X-Idempotent: true`.
- **Auth: dual.** HMAC (`X-Customer-Id` + `X-Api-Key`) **or** Cognito
  Bearer from `Analyst | CCO | CRO | Admin`. HMAC wins if both supplied.

What v0 does **not** cover (deferred to APIs 1–4 below):

- Transaction-screen fan-out + `worst_party` aggregation.
- Bulk CSV + async job substrate.
- Continuous monitoring trigger.
- Per-class SSM thresholds.
- DOB/passport as scoring tiebreakers (still audit-only).

---

## API 1 — `POST /validate/v1/customer-screen`

Sync. KYC onboarding. Single party, full profile.

**Request**
```json
{
  "external_customer_id": "cust_123",
  "first_name": "John", "last_name": "Smith",
  "date_of_birth": "1985-01-01",
  "nationality": "US", "country_of_residence": "US",
  "address": "Los Angeles, CA",
  "government_id": { "type": "passport", "number": "X1234567" }
}
```

**Response** — engine shape + `external_customer_id` echoed. `decision=REVIEW|HOLD` triggers `screening_cases` row creation.

Notes: only `first_name + last_name` → `party_name` and `nationality` → `party_country` hit the engine today. DOB / address / passport are stored in the audit row for human review but do **not** enter the score (Phase 2: DOB as a tiebreaker for individual matches).

---

## API 2 — `POST /validate/v1/transaction-screen`

Sync. Wire / payment screening. Fans out to 4 parallel `screen()` calls.

**Request**
```json
{
  "transaction_id": "TXN-1001",
  "amount": 25000, "currency": "USD",
  "destination_country": "AE",
  "parties": {
    "sender":       { "name": "John Smith",        "country": "US" },
    "receiver":     { "name": "ABC Trading LLC",   "country": "AE" },
    "beneficiary":  { "name": "Mohammed Ali",      "country": "AE" },
    "intermediary": { "name": "Emirates NBD",      "country": "AE" }
  }
}
```

**Response**
```json
{
  "transaction_id": "TXN-1001",
  "decision": "HOLD",
  "worst_party": "beneficiary",
  "per_party": {
    "sender":       { "decision": "PASS",   "final_score": 0.12, "matches": [] },
    "receiver":     { "decision": "PASS",   "final_score": 0.20, "matches": [] },
    "beneficiary":  { "decision": "HOLD",   "final_score": 0.92, "matches": [/* engine match */] },
    "intermediary": { "decision": "REVIEW", "final_score": 0.78, "matches": [/* ... */] }
  },
  "screened_at": "...", "latency_ms": 84.1
}
```

Aggregation rule: overall `decision = max(per_party.decision)` under order `PASS < REVIEW < HOLD`.

---

## API 3 — `POST /validate/v1/bulk-screen`

Async. Existing-customer rescreen, portfolio review, M&A DD.

**Request:** `multipart/form-data` with `customers.csv` (columns: `external_customer_id, first_name, last_name, dob, nationality, address`). Optional `callback_url`.

**Immediate response**
```json
{ "job_id": "JOB-1001", "status": "QUEUED", "row_count": 250000,
  "status_url": "/validate/v1/bulk-screen/JOB-1001" }
```

**`GET /validate/v1/bulk-screen/{job_id}`**
```json
{
  "job_id": "JOB-1001", "status": "COMPLETED",
  "rows_total": 250000, "rows_processed": 250000,
  "decisions": { "PASS": 249983, "REVIEW": 14, "HOLD": 3 },
  "results_url": "s3://.../bulk-screen/JOB-1001/results.jsonl.gz",
  "started_at": "...", "finished_at": "..."
}
```

Worker: SQS-fed, calls `OFACScreener.screen()` per row at controlled concurrency. Results = JSONL of engine responses. Cases auto-opened for `REVIEW|HOLD`.

---

## API 4 — `POST /validate/v1/monitoring/runs`

Triggered by SDN delta detection (`regulatory_change_records.source = 'OFAC_SDN'`). Manual trigger also allowed.

**Request**
```json
{ "trigger": "sdn_delta|manual",
  "since": "2026-06-03T00:00:00Z",
  "customer_scope": "all|high_risk|country:AE" }
```

**Response**
```json
{ "monitoring_job_id": "MON-1001",
  "customers_scanned": 250000,
  "new_alerts": 17,
  "alerts_url": "/validate/v1/cases?monitoring_job_id=MON-1001",
  "completed_at": "..." }
```

Implementation: re-uses bulk-screen worker over the customer table, but only emits an alert when `(customer_id, sdn_number)` is new vs last screening for that customer.

---

## Errors (all endpoints)

| HTTP | Code | When |
|---|---|---|
| 400 | `INVALID_INPUT` | Missing required field, bad country code |
| 401/403 | `AUTH_FAILED` | Per `src/api/auth.py` |
| 422 | `CSV_PARSE_ERROR` | Bulk only — row + column reported |
| 503 | `SDN_STALE` | `_enforce_freshness()` raised; include `sdn_data_age_hours` |
| 503 | `ENGINE_DEGRADED` | Vector mode failed AND legacy fallback disabled |

---

## Open items before merging the contract

- [x] Add `screening_events` + `screening_cases` migrations (alembic 028)
- [x] Extend `Violation` (or add `MatchDetail`) to carry subscores — `MatchDetail` added in `validators.py`
- [x] Dual-emit `action` (legacy clear/block) + `decision` (PASS|REVIEW|HOLD) — `ValidationResult` carries both (2026-06-06); deprecate `action` after one P2 release
- [ ] Land per-class SSM thresholds (depends on calibration sign-off — see `ofac-calibration-gold-set.md` §6)
- [ ] Decide bulk worker substrate: extend existing `update_service` task runner vs. new SQS consumer
