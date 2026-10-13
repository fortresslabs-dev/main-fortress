# `/customer-screen` — Implementation Plan

Status: design, 2026-06-04 · Owner: P2 validators + dashboard
Scope: end-to-end build of the **first** of the four screening endpoints from
`ofac-screening-api-contract.md`. This is the reference adapter that the other
three (`transaction-screen`, `bulk-screen`, `monitoring/runs`) will reuse.

This doc is implementation-blocked until all five sections below are reviewed.

---

## 1 · API specification

### Path
`POST /validate/v1/customer-screen` (P2 only — mounted on `main_pipeline2.py`)

Auth: existing P2 `Bearer` (`src/api/auth.py`) — same as `/validate/ofac-screen`.

### Request

```json
{
  "external_customer_id": "cust_123",
  "first_name": "John",
  "middle_name": null,
  "last_name": "Smith",
  "date_of_birth": "1985-01-01",
  "nationality": "US",
  "country_of_residence": "US",
  "address": "Los Angeles, CA",
  "government_id": {
    "type": "passport",
    "number": "X1234567",
    "country": "US"
  },
  "actor": {
    "kind": "api",
    "user_id": null
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `external_customer_id` | yes | Bank's customer key. Echoed back; never sent upstream. |
| `first_name` + `last_name` | yes | Joined with `" "` → `party_name` for the engine. `middle_name` interpolated if present. |
| `date_of_birth` | no | Stored in audit row; **not** used in score in v1 (Phase 2: tiebreaker for individual matches). |
| `nationality` | no | Maps to engine `party_country` (ISO-3166-1 alpha-2). Falls back to `country_of_residence`. |
| `address` | no | Audit only. |
| `government_id` | no | Audit only. Truncated in logs (last-4 mask). |
| `actor.kind` | yes | `api`, `dashboard`, `bulk`, `monitoring`. |
| `actor.user_id` | conditional | Required when `actor.kind=dashboard`. |

### Response — PASS

```json
{
  "screening_id": "SCR-01J8...",
  "external_customer_id": "cust_123",
  "decision": "PASS",
  "final_score": 0.18,
  "matches": [],
  "case_id": null,
  "engine_mode": "vector",
  "sdn_data_age_hours": 4.2,
  "latency_ms": 38.4,
  "screened_at": "2026-06-04T14:02:11Z"
}
```

### Response — REVIEW / HOLD

```json
{
  "screening_id": "SCR-01J8...",
  "external_customer_id": "cust_123",
  "decision": "HOLD",
  "final_score": 0.92,
  "matches": [
    {
      "sdn_number": "12345",
      "matched_name": "MOHAMMED ALI",
      "is_primary": false,
      "entity_type": "individual",
      "programs": ["SDGT"],
      "country": "AE",
      "scores": { "trgm": 0.91, "fuzz": 0.88, "vector": 0.85, "final": 0.92 }
    }
  ],
  "case_id": "CASE-01J8...",
  "engine_mode": "vector",
  "sdn_data_age_hours": 4.2,
  "latency_ms": 41.0,
  "screened_at": "2026-06-04T14:02:11Z"
}
```

### Errors

| HTTP | Code | Trigger |
|---|---|---|
| 400 | `INVALID_INPUT` | Missing name parts, bad country code |
| 401 | `AUTH_FAILED` | Bearer missing/invalid |
| 422 | `VALIDATION_ERROR` | Pydantic schema failure (FastAPI default) |
| 503 | `SDN_STALE` | `_enforce_freshness()` raised. Body includes `sdn_data_age_hours`. |
| 503 | `ENGINE_DEGRADED` | Vector mode failed AND `OFAC_SCREENER_MODE` flag forbids legacy fallback |

### Idempotency

`criteria_hash` = SHA256 of `{first_name, last_name, dob, nationality}` (canonical sort, lowercased). Same payload within 60s returns the cached `screening_id` + decision — no duplicate audit row. Implemented as a lookup against `screening_events.criteria_hash` with a `created_at > now() - interval '60 seconds'` filter.

Why: bank UIs frequently re-submit on user double-click. We do NOT want duplicate cases or audit noise.

---

## 2 · Dashboard usage

Two distinct flows. Both use the same `/customer-screen` endpoint with `actor.kind=dashboard`.

### Flow A — Ad-hoc name screening (compliance officer)

CCO opens **OFAC → Screen a name** (new sub-tab). Types `Mohammed Ali`. Optionally fills DOB, country. Clicks **Screen**.

Response renders inline:

- Decision pill (green PASS / amber REVIEW / red HOLD)
- `final_score` as a 0–100 number with the per-engine breakdown bar (`trgm` / `fuzz` / `vector`)
- Match cards: SDN number → matched name → programs → country → subscores
- "Open case" button (already opened automatically if REVIEW/HOLD — button becomes "View case CASE-…")

Required because regulators expect explainability: the breakdown bar is the visual answer to *"why was this score 0.92?"*.

### Flow B — Onboarding decision queue

When the bank's onboarding system calls `/customer-screen` (`actor.kind=api`), REVIEW/HOLD events create `screening_cases` rows. Compliance officer works the queue at **OFAC → Open cases**.

Queue table columns: `case_id`, `priority`, `external_customer_id`, `matched_name`, `final_score`, `programs`, `event_count`, `created_at`, `assignee`.

Click row → **Case detail** page:
- Customer profile (from latest event's `criteria`)
- All linked `screening_events` in a timeline (initial + monitoring re-hits)
- Match details with subscore bars
- Reviewer panel: assign-to-me, action dropdown (`cleared_false_positive` / `confirmed_true_positive` / `escalated` / `needs_more_info`), notes textarea, evidence upload (JSON metadata, file → S3 outside scope of v1)
- "Resolve" button → writes `status`, `resolved_at`, `resolved_by_user_id`

### Permissions

Re-use existing role map from `dashboard/src/App.jsx`:
- `viewer` — read-only on cases + ad-hoc screen (no resolve, no assign)
- `CCO` — full case workflow
- `CRO`, `admin` — same as CCO + can override blocked transactions (future)

---

## 3 · Dependency check

### Python (`requirements-pipeline2.txt`) — all present, no additions

| Package | Pinned | Used for |
|---|---|---|
| `asyncpg>=0.29.0` | yes | DB driver |
| `sqlalchemy[asyncio]` | yes | session + `text()` raw SQL |
| `pgvector>=0.2.5` | yes | `<=>` cosine operator |
| `rapidfuzz>=3.6.0` | yes | `token_sort_ratio` in `_rerank` |
| `boto3==1.34.34` | yes | SSM threshold lookup (cached) |
| `pydantic>=2` | yes | request/response models |
| `fastapi` | yes | router |
| `structlog` | yes | audit logging |

### Frontend (`dashboard/package.json`) — all present, no additions

| Package | Used for |
|---|---|
| `axios` | `services/api.js` extensions |
| `react-router-dom` | new routes `/screening`, `/screening/cases/:id` |
| `recharts` | subscore breakdown bar |
| `lucide-react` | icons for decision pills |
| `aws-amplify` | unchanged — same Cognito JWT for new endpoints |

### Infra deps — already provisioned

- Aurora PG15 with pgvector + pg_trgm + unaccent (migrations 021/023)
- `ofac_sdn_vectors` populated by `src/jobs/embed_sdn_variants.py`
- SSM under `/fortress/{client}/` injected at boot (`src/utils/config.py:_inject_ssm_parameters`)

### Net new dependencies: **none**. Build can start as-is.

---

## 4 · Schema review

### Already in place (migration 028)

- `screening_events` (audit log) — covers all fields the contract needs
- `screening_cases` (workflow row) — covers reviewer fields
- `screening_case_events` (many-to-one join)

### Schema gaps identified during this review — **none for v1**

Deliberate non-additions:
- **No SQLAlchemy ORM models.** Matches the `ofac_sdn_vectors` pattern (`021_ofac_sdn_vectors.py`) — DB access is raw SQL via `text()`. Adding ORM doubles the maintenance burden for a 3-table substrate.
- **No `screening_case_actions` action-history table.** Reviewer history can be reconstructed from `screening_cases.updated_at` + an append-only `reviewer_notes` JSONB array. Defer to v2 if regulators ask for a separate table.
- **No FK to `client_credentials`.** `actor_user_id` is opaque string. Cross-system join risk > FK benefit for an audit table.
- **No `evidence_uploads` table.** File uploads route to S3 in v2; for v1 the `reviewer_evidence` JSONB column holds metadata only.

### Schema decisions to confirm before code lands

1. **JSONB vs JSON.** Migration 028 uses `JSONB`. Existing P2 tables (`workflow_executions.context_data`) use `JSON`. Acceptable to mix? — Yes, JSONB is strictly better for the indexed lookups we'll do (`criteria_hash` filter, dashboard queue WHERE filters). Keep JSONB.
2. **`final_score` precision.** `NUMERIC(5,4)` → 0.0000–9.9999. Sufficient for `[0.0, 1.0]` range. Confirmed.
3. **External `case_id` / `screening_id` format.** Current contract examples use `CASE-01J8...` / `SCR-01J8...` (ULID-ish). Concrete format = `<prefix>-<ulid>`. Add a `ulid` Python lib? — No, generate as `f"SCR-{uuid7_or_ulid_hex}"`. Use stdlib `uuid.uuid4().hex[:26].upper()` for v1 to avoid the dep.

---

## 5 · Migration & rollout plan

### Order of operations

1. **Apply migration 028** to P2 Aurora via `make migrate` (or `alembic upgrade head`). Idempotent — `IF NOT EXISTS` everywhere. Verify with:
   ```bash
   make schema-check  # scripts/verify_schema.py
   ```
2. **Seed SSM thresholds** (compliance gate):
   ```
   /fortress/{client}/ofac/threshold/individual  = 0.92  (HOLD)
   /fortress/{client}/ofac/threshold/individual_review = 0.78
   /fortress/{client}/ofac/threshold/alias       = 0.90 / 0.75
   /fortress/{client}/ofac/threshold/entity      = 0.88 / 0.72
   ```
   Until these exist, the adapter falls back to a single threshold (`OFAC_SIMILARITY_THRESHOLD = 0.85`) and emits HOLD only (no REVIEW band). Log a warning per request so missing thresholds are visible.

3. **Deploy P2 with feature flag**:
   ```
   SCREENING_API_V1_ENABLED=true|false   # default false
   ```
   The new router is mounted but the endpoint returns 404 unless the flag is true. Lets us deploy to all pilot banks first, then enable per-bank.

4. **Deploy dashboard** with new `/screening` route gated by the same flag (read from a `/health` endpoint that exposes feature flags).

5. **Per-bank enablement** — set `SCREENING_API_V1_ENABLED=true` in the bank's ECS task def, force new deployment. Verify with the ad-hoc screen flow first.

### Rollback

- **Code:** flip flag to false → endpoint 404s, dashboard hides tab. No data loss.
- **Schema:** `alembic downgrade 027` drops the three tables. Safe pre-launch. Post-launch, prefer to leave the schema in place and disable the flag; downgrade would lose audit history.

### Dependencies on open action items (already in CLAUDE.md)

- [ ] Compliance ops returns real OFAC gold-set
- [ ] Calibration produces per-class thresholds with F1 ≥ 0.90, recall ≥ 0.95
- [ ] Thresholds loaded to SSM

The endpoint can ship with the single-threshold fallback BEFORE calibration completes, but PASS/REVIEW/HOLD will degrade to PASS/HOLD-only. Communicate this to ops in the rollout email.

---

## 6 · Dashboard UI changes — specific

### Files to add

```
dashboard/src/
├── pages/
│   ├── ScreeningSearch.jsx      # ad-hoc form (Flow A)
│   ├── ScreeningCases.jsx       # queue (Flow B)
│   └── ScreeningCaseDetail.jsx  # case workflow
├── components/
│   ├── DecisionPill.jsx         # PASS/REVIEW/HOLD badge
│   ├── SubscoreBar.jsx          # recharts horizontal bar for trgm/fuzz/vector/final
│   └── MatchCard.jsx            # single match render
└── services/
    └── api.js                   # add screeningApi block
```

### `services/api.js` extension

```js
export const screeningApi = {
  customerScreen: (body) => api.post('/validate/v1/customer-screen', body),
  listCases:      (params) => api.get('/validate/v1/cases', { params }),
  getCase:        (id)   => api.get(`/validate/v1/cases/${id}`),
  updateCase:     (id, body) => api.patch(`/validate/v1/cases/${id}`, body),
  getEvent:       (id)   => api.get(`/validate/v1/events/${id}`),
}
```

(The `cases` and `events` endpoints are spec-stubs — full design lives in the API contract; build alongside `/customer-screen`.)

### `App.jsx` routes

Add inside existing `<Routes>`:

```jsx
<Route path="/screening"              element={<ScreeningSearch />} />
<Route path="/screening/cases"        element={<ScreeningCases />} />
<Route path="/screening/cases/:id"    element={<ScreeningCaseDetail />} />
```

### `Navbar.jsx` tab insertion

Add after `OFAC` tab:

```js
{ to:'/screening',        label:'Screening' },
{ to:'/screening/cases',  label:'Cases',  badge:'queue' },
```

Keep `/ofac` as the system-health page; `/screening` is the operational page. Rationale: separating concerns lets the OFAC page stay a one-glance "is the engine healthy" view for non-CCO roles (CRO, CIO).

### Empty / loading states

Match the existing `ftr-loading` / amber-banner pattern in `OfacHealth.jsx`. Reuse the `ftr-tablecard` CSS class for case queue. No new CSS variables needed.

---

## 7 · Open questions for review

1. **Idempotency window.** 60s reasonable, or should it be configurable per-bank?
2. **Case key.** `(external_customer_id, sdn_number)` partial-unique allows reopening after `cleared/closed`. Should we also auto-close cases after N days of no new events? (Suggest: no — let ops decide manually.)
3. **Dashboard sub-tab vs. top-level.** Current proposal adds `/screening` at top level. Alternative: nest under `/ofac/screen` and `/ofac/cases`. (Recommendation: top-level — these are operational pages, not health.)
4. **Per-class threshold defaults** before calibration. Use single 0.85 (HOLD-only) or seed safe high defaults (`HOLD=0.95, REVIEW=0.85`) and document as conservative-by-design?
5. **Feature-flag plumbing.** New `SCREENING_API_V1_ENABLED` setting in `Settings` (config.py) + a `/health` field exposing it? Or rely on SSM-injected env var only?

Once these are resolved I'll move to building the adapter, threshold-resolver, event/case writer, and the three new dashboard pages.
