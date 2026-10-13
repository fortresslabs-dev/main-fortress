# Fortress Dashboard — Layman's Guide

Date: 2026-06-12
Audience: Bank executives, compliance ops, engineering reviewers, vendor due-diligence reviewers, anyone touching the tool for the first time.

This single document explains: what the dashboard shows, what business process each number represents, how to tell when a number is wrong and what to do, how the JIRA test-automation flow works end-to-end, how a developer proves the system is production-ready, which endpoints to call, and how to drive the whole thing from Postman with real credentials.

---

## 1. What this tool actually does (in one paragraph)

A bank has thousands of compliance rules from regulators (OCC, FinCEN, OFAC, CFPB, FFIEC, state DFIs, …). Each rule should be backed by an automated test in the bank's code so that wire transfers, KYC checks, loan applications, OFAC screening, etc. provably follow the rule. Fortress (a) **scrapes** those rules nightly into a single source of truth (Pipeline 1 — Fortress Cloud), (b) **syncs** them into the bank's own VPC (Pipeline 2 — runs inside the bank), and (c) gives the Chief Compliance Officer (CCO), Chief Risk Officer (CRO) and Chief Information Officer (CIO) **one dashboard** to see coverage, sign off on regulatory changes, and click a button to auto-generate a JIRA story + a real GitHub pull request containing the test.

---

## 2. Dashboard pages and the business question each one answers

The React dashboard lives in `dashboard/src/pages/` and every page talks to one HTTP endpoint in `src/api/dashboard_api.py`.

| Dashboard page | Business question | Backend endpoint | Source tables |
|---|---|---|---|
| Executive Today | "What changed in the last 24 h that I need to act on?" | `GET /api/v1/dashboard/screening/executive-today` | enforcement_actions, regulatory_change_records, workflow_executions |
| Readiness | "If a regulator examined us tomorrow, what's our score?" | `GET /api/v1/dashboard/readiness?lens=cco\|cro\|cio` | compliance_rules + workflow_executions + obligations + changes |
| Rules | "Show every rule, with risk, coverage, and bank override status." | `GET /api/v1/dashboard/rules` | compliance_rules + rule_overrides + workflow_executions |
| Obligations | "Are obligations extracted correctly and which ones have no test?" | `GET /api/v1/dashboard/obligations` | obligations |
| Changes | "Inbox of regulator changes I have to sign off." | `GET /api/v1/dashboard/changes` | regulatory_change_records |
| Enforcement | "Recent fines & consent orders — what are peers being punished for?" | `GET /api/v1/dashboard/enforcement` | enforcement_actions |
| Workflows | "Did the 6-agent JIRA pipeline runs succeed? How long did they take?" | `GET /api/v1/dashboard/workflows` | workflow_executions |
| OFAC Health | "Is the sanctions screener actually loaded and embedded?" | `GET /api/v1/dashboard/ofac-health` | ofac_sdn_entries, ofac_sdn_vectors |
| Investigation / Cases | "Show the wire-transfer / SDN match cases queued for an analyst." | `GET /api/v1/dashboard/screening/cases` | screening_cases |
| Rules Perf / Vector Intel | "Is the model still good? Where is recall dropping?" | `/screening/rules-perf`, `/screening/vector-intel` | derived metrics |
| Reports | "Generate a point-in-time, hash-versioned snapshot for the exam binder." | `POST /api/v1/dashboard/exports` | computed from all of the above |

Every endpoint requires a **Bearer JWT** (Cognito in prod, dev token in local). Role-based access is enforced server-side: CCO is the only role that can call `PATCH /rules/{id}/status` (override) or `POST /rules/{id}/generate-test`.

---

## 3. Is the data correct? How obligations and rules get corrected

### 3.1 Where the numbers come from

- `compliance_rules` — populated by Pipeline 1 scrapers (federal + state connectors in `src/connectors/regulatory/usa/`) and pulled into the bank by Pipeline 2 `update_service.py` once per day.
- `obligations` — extracted from the rule text by a regex-first/LLM-fallback extractor (see commit `278315d` and `docs/runbooks/scraper-extraction-architecture.md`).
- `regulatory_change_records` — written when a scrape detects a diff against the previous snapshot.
- `workflow_executions` — written when a CCO clicks "Generate Test" or a JIRA webhook fires.

### 3.2 The Obligations panel — what "correct" means

`GET /api/v1/dashboard/obligations` returns:

```json
{
  "total": 1842,
  "covered": 612,
  "coverage_pct": 33.2,
  "breakdown": [
    {"regulator": "FinCEN", "total": 412, "covered": 180, "coverage_pct": 43.7},
    ...
  ]
}
```

Two things must be true for this to be "correct":

1. **No `unknown` regulator bucket** — every obligation must be attributed (the UI text in `Obligations.jsx` says "no 'unknown' bucket" — that is the contract).
2. **`covered` count matches `workflow_executions WHERE status='completed'`** for the same rule the obligation was extracted from.

### 3.3 If an obligation looks wrong (extractor mis-parsed the rule)

The extractor is regex-first, LLM-fallback. Two repair paths:

- **Bad text only, rule still applies** — fix is in the scraper extraction layer. Re-run the extractor against the source via the `scraper-chunks/sdn_xml_raw/` or rule HTML cached in S3. See `docs/runbooks/scraper-extraction-architecture.md` for the golden-set + backfill flow.
- **Rule does not apply to this bank** — CCO clicks "Mark not applicable" in the Rules page, which calls `PATCH /api/v1/dashboard/rules/{rule_id}/status` with `{"status":"not_applicable","na_reason":"..."}`. The override lives in `rule_overrides` and the rule is excluded from the coverage denominator the next time the page loads. The CCO role is required; the endpoint rejects calls without an `na_reason`.

### 3.4 If a regulatory change record looks wrong

- CCO/CRO sign-off via `POST /api/v1/dashboard/changes/{change_ref}/signoff` with one of three decisions: `acknowledged`, `requires_action`, `not_applicable`. The mapping to DB status is in `dashboard_api.py:_DECISION_TO_STATUS`. This is the auditable corrective action.

---

## 4. When will a new test show up for JIRA automation?

This is the most-asked question, so here is the full clock.

### 4.1 The two trigger paths

**A — CCO clicks "Generate Test" in the dashboard (synchronous, fast):**

1. Frontend calls `POST /api/v1/rules/{rule_id}/generate-test` (auth: CCO).
2. `generate_test_api.py` looks up the rule, builds a JIRA description, calls `JIRAClient.create_story()` against the bank's project (default `COMP`, or `MDP` for the test bank — see memory `project_jira_mdp_idea_issuetype`).
3. A `workflow_executions` row is inserted in `running` status.
4. `BackgroundTasks` kicks off `process_workflow(workflow_id, jira_key, description)` — the 6-agent pipeline (Discovery → Understanding → Framework → Generation → Validation → Execution) runs in-process.
5. Agent 4 (`generation_bedrock.py`) calls Bedrock Claude Sonnet 4.x via the `us.` inference profile; Agent 6 opens a GitHub PR via `github_service.py`.
6. Endpoint returns immediately with `{"status":"pipeline_started","message":"6-agent pipeline will open a GitHub PR within ~5 minutes"}`.
7. The Workflows panel will show the run in `running`, then `completed` with `artifacts.github_pr_url` populated.

**Expected end-to-end time: ~5 minutes** from button-click to PR URL.

**B — JIRA webhook (asynchronous, integration path):**

1. Someone creates an issue in JIRA in the watched project with the `Idea` (or `Story`) issue type.
2. JIRA POSTs to `POST /webhooks/jira` (auth: Bearer or HMAC — see `webhooks.py:_authenticate`).
3. Cluster-wide FIFO advisory lock (`_WORKFLOW_LOCK_KEY = 0x70327367`) serializes Bedrock invocations.
4. Same `process_workflow` runs; PR shows up on the same timescale.

### 4.2 How you poll for the result

```
GET /api/v1/rules/{rule_id}/test-status
→ { "status": "completed", "pr_url": "https://github.com/<org>/<repo>/pull/123" }
```

Or in the dashboard, watch the **Workflows** panel — it auto-refreshes on page load.

### 4.3 Bulk path

`POST /api/v1/rules/bulk-generate` with `{"rule_ids":[...up to 20...]}`. It creates a JIRA story per rule in `pending`. The actual 6-agent run is then picked up by the webhook flow as JIRA processes those issues.

---

## 5. Production-readiness APIs — which endpoints prove the system is "ready"

There are **two distinct** concepts of readiness. Do not confuse them:

### 5.1 Operational readiness (infra is alive)

These are the endpoints SREs / load balancers call:

| Endpoint | What it proves | Pass criteria |
|---|---|---|
| `GET /health/` | DB, Redis, Bedrock, S3 reachable | HTTP 200, `status: "healthy"`. DB `down` flips it to 503 and ECS kills the task. Redis/Bedrock/S3 `degraded` is acceptable — they have fallbacks. |
| `GET /health/ready` | Process is up | HTTP 200 |
| `GET /health/sync` | Pipeline-2 has fresh data from Pipeline 1 | `status: "ok"` and `sync_age_hours <= 25`. `stale` or `failed` means UpdateService is broken and the dashboard data is decaying. |

### 5.2 Examination readiness (business signal)

This is the **CCO/CRO/CIO score** — what an auditor would see:

| Endpoint | What it returns | Meaning |
|---|---|---|
| `GET /api/v1/dashboard/readiness?lens=cco` | Persona-weighted 0–100 score + 10 dimensions + biggest drag | Below 70 = red. Drag tells you whether unsigned changes, untested high-risk rules, or stale sync is dragging it down. |
| `GET /api/v1/dashboard/readiness?lens=cro` | CRO-weighted same shape | Different weighting toward risk & residual. |
| `GET /api/v1/dashboard/readiness?lens=cio` | CIO-weighted same shape | Weights toward engine integrity + index health. |
| `GET /api/v1/dashboard/risk-matrix` | 3×3 grid (risk × coverage) + `danger_rule_ids` | The "danger" cell = high enforcement risk AND untested. These must go to zero before exam. |
| `GET /api/v1/dashboard/cro` | Residual-vs-appetite by domain + KRIs + peer signal | Whether residual risk in sanctions / BSA-AML / consumer / privacy / licensing exceeds appetite. |
| `GET /api/v1/dashboard/cio` | Pipeline success rate (7d), avg duration, index integrity, deployment-model attestations | "engine.index_integrity.ok = true" means OFAC vectors are ≥ 99 % embedded. |

**A bank is production-ready when:** `/health/` is 200, `/health/sync` is `ok`, `cco` readiness ≥ 80, `risk-matrix.danger_count == 0`, and `cio.engine.index_integrity.ok == true`.

---

## 6. Does it need testing before production? Yes — here is the gate

Production cut-over is gated by the CI demo-readiness pipeline (`.github/workflows/ci-cd.yml`, Phase 5):

1. `make lint` — ruff + black + py_compile on the three entrypoints.
2. `make schema-check` — verifies alembic head matches the live DB.
3. `make test-p1` + `make test-p2` — pytest unit + integration.
4. `pytest tests/demo` — golden-path smoke for the agent pipeline.
5. Dashboard Vitest unit tests (`dashboard/src/test/`).
6. Playwright F1–F5 (`dashboard/e2e/`) — the five user-flow gates the dashboard ships with.
7. Nightly staging cron — runs the above against the deployed staging stack so a fresh image is never the first one a human sees.

In addition, the rollout doc `docs/p2_dashboard_api_production_integration.md` is the deployment checklist (Cognito wiring, ALLOWED_ORIGINS, CORS, ACM, WAF). Every item there is mandatory before flipping `TrialMode=false`.

### 6.1 Hard production gates (already encoded in the codebase)

- `ALLOWED_ORIGINS` must be a **JSON array of explicit origins**. Empty or `*` makes the app refuse to start (`src/utils/config.py`, see memory `feedback_p2_allowed_origins_env`).
- `DEV_MODE=true` with `ENVIRONMENT=production` makes the JIRA webhook **refuse** to accept calls (it never silently bypasses auth in prod — `webhooks.py:_authenticate`).
- Cognito JWKS must be reachable; otherwise the dashboard endpoints return 503.
- The image deployed by ECS must match the task-def revision (see memory `feedback_p1_taskdef_semver_pinned`) — `force-new-deployment` alone does not deploy a new tag.

---

## 7. Developer's production-integration verification checklist

What an engineer should actually run after the dashboard + API are deployed to a bank's VPC:

```bash
# 1. Infra alive
curl -sS https://fortressapi.<bank>/health/ | jq
curl -sS https://fortressapi.<bank>/health/ready
curl -sS https://fortressapi.<bank>/health/sync | jq '.status, .sync_age_hours'

# 2. Cognito auth working — login in the browser at https://dashboard.<bank>,
#    open devtools, copy the Bearer token from the Network tab.
export TOKEN=eyJ...

# 3. Authenticated dashboard endpoints respond
curl -sS https://fortressapi.<bank>/api/v1/dashboard/overview \
  -H "Authorization: Bearer $TOKEN" | jq
curl -sS "https://fortressapi.<bank>/api/v1/dashboard/readiness?lens=cco" \
  -H "Authorization: Bearer $TOKEN" | jq '.score, .band, .biggest_drag'

# 4. Data integrity
curl -sS https://fortressapi.<bank>/api/v1/dashboard/obligations \
  -H "Authorization: Bearer $TOKEN" | jq '.total, .coverage_pct'
curl -sS https://fortressapi.<bank>/api/v1/dashboard/ofac-health \
  -H "Authorization: Bearer $TOKEN" | jq '.embedding_coverage_pct, .vector_mode_ready'

# 5. JIRA round-trip — pick the highest-risk untested rule, generate a test
RULE=$(curl -sS "https://fortressapi.<bank>/api/v1/dashboard/rules?sort_by=risk" \
  -H "Authorization: Bearer $TOKEN" | jq -r '.rules[0].rule_id')
curl -sS -X POST https://fortressapi.<bank>/api/v1/rules/$RULE/generate-test \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"priority":"High"}' | jq

# 6. Poll until status=completed and pr_url is populated
watch -n 30 "curl -sS https://fortressapi.<bank>/api/v1/rules/$RULE/test-status \
  -H 'Authorization: Bearer $TOKEN'"
```

Sign-off criteria for go-live:

1. `/health/` 200 for 24 h soak.
2. `/health/sync` reports `ok` after the next UpdateService cycle.
3. End-to-end JIRA generation produces a PR URL.
4. No CORS errors in CloudWatch logs.
5. WAF baseline tuned, no false positives during UAT.
6. CCO and CRO have each logged in via Cognito and the role-gated endpoints behave (CCO can call `PATCH /rules/{id}/status`, CIO cannot — should get 403).

---

## 8. Driving the API from Postman (with real credentials)

### 8.1 Get a Bearer token

**Production / staging (Cognito):**
1. In Postman, create a new request `POST` to `https://<cognito-domain>/oauth2/token`.
2. Body type `x-www-form-urlencoded`:
   - `grant_type=password`
   - `client_id=<COGNITO_CLIENT_ID>`
   - `username=<your-cognito-user>`
   - `password=<your-cognito-password>`
3. Send. Copy `id_token` from the response (NOT `access_token` — the API validates `id_token` claims against the Cognito app client).
4. Alternative: log in through the dashboard's hosted UI in a browser, open devtools → Network → any `/api/v1/...` request → copy the `Authorization: Bearer …` header.

**Local dev (`DEV_MODE=true`):**
- Set `PIPELINE2_API_TOKEN` in the API's env. Then send `Authorization: Bearer <that token>`. The dev token is **only** accepted when `DEV_MODE=true` AND `ENVIRONMENT != "production"`.

### 8.2 Postman collection — minimal request set

Create a Postman environment with variables:

| Variable | Example |
|---|---|
| `base_url` | `https://fortressapi.<bank>` |
| `token` | (paste id_token here) |

Then add these requests (all use header `Authorization: Bearer {{token}}`):

| Method | URL | Purpose |
|---|---|---|
| GET | `{{base_url}}/health/` | Smoke test, no auth |
| GET | `{{base_url}}/health/sync` | Data freshness |
| GET | `{{base_url}}/api/v1/dashboard/overview` | Headline KPIs |
| GET | `{{base_url}}/api/v1/dashboard/readiness?lens=cco` | Exam-readiness score |
| GET | `{{base_url}}/api/v1/dashboard/rules?sort_by=risk&page=1&page_size=10` | Top-risk rules |
| GET | `{{base_url}}/api/v1/dashboard/obligations` | Obligation correctness |
| GET | `{{base_url}}/api/v1/dashboard/ofac-health` | OFAC index integrity |
| GET | `{{base_url}}/api/v1/dashboard/workflows` | Pipeline runs |
| PATCH | `{{base_url}}/api/v1/dashboard/rules/<rule_id>/status` | Mark N/A (CCO only) |
| POST | `{{base_url}}/api/v1/dashboard/changes/<change_ref>/signoff` | Sign off (CCO/CRO) |
| POST | `{{base_url}}/api/v1/rules/<rule_id>/generate-test` | Kick off JIRA + PR |
| GET | `{{base_url}}/api/v1/rules/<rule_id>/test-status` | Poll for the PR URL |

Example body for the sign-off:

```json
{ "decision": "acknowledged", "note": "Reviewed by CCO, no policy change needed" }
```

Example body for marking N/A (must include `na_reason` or you get 400):

```json
{ "status": "not_applicable", "na_reason": "Bank does not offer money services business products" }
```

### 8.3 What you should see when it works

- `200 OK` with JSON. `health_score_pct`, `total_rules`, `tested_rules` are integers > 0.
- `readiness.score` is 0–100 with a `band` (`red`/`amber`/`green`) and a `biggest_drag` string.
- A successful generate-test returns `{"status": "pipeline_started", "jira_key": "<KEY>", "workflow_id": "<uuid>"}` within a second. Poll `test-status` every 30 s; expect `status` to move `running → completed` and `pr_url` to populate within ~5 min.

### 8.4 What 4xx/5xx errors tell you

| Status | Likely cause |
|---|---|
| 401 | Bearer token missing/expired, or Cognito JWKS not reachable from API VPC |
| 403 | Role on the JWT does not match the endpoint requirement (e.g. CIO trying to call PATCH) |
| 404 on `/generate-test` | `rule_id` not in `compliance_rules` or `is_active=false` |
| 502 on `/generate-test` | JIRA API call failed — check `JIRA_URL` / API token / project key (CCO logs show `jira_create_failed`) |
| 503 on `/health/` | DB is down. Everything else is non-critical. |
| 503 on dashboard endpoints | Cognito JWKS could not be fetched |

---

## 9. End-to-end story — what the bank actually gets

1. **Tonight 02:00 UTC** — Pipeline 1 scrapers (Fortress Cloud) pull every federal + state register, embed the rule text with Titan v2, write to Aurora pgvector.
2. **Tonight 03:00 UTC** — Pipeline 2 `update_service.py` inside the bank's VPC pulls the diff via `GET /sync/embeddings` from Pipeline 1 (HMAC-authenticated), runs `ANALYZE` on pgvector.
3. **08:00 local** — CCO opens `https://dashboard.<bank>`. Cognito login. The Executive Today and Readiness panels load.
4. CCO sees three new `regulatory_change_records`. Clicks "Sign off" on two as `acknowledged`, the third as `requires_action`.
5. CCO opens Rules, sorts by risk, sees a high-risk untested rule, clicks **Generate Test**. JIRA story is created in `COMP` (or `MDP`); within ~5 min a GitHub PR appears with a Bedrock-generated pytest that cites the CFR and tests the threshold boundary.
6. The bank's CI runs the test against staging; engineer reviews, merges; the rule now shows `tested` in the matrix. Readiness score ticks up.
7. **At exam time** the CCO calls `POST /api/v1/dashboard/exports` to produce a point-in-time, hash-versioned snapshot for the binder.

That is the business benefit: regulator → vector store → coverage matrix → JIRA → PR → green readiness score, all in a single working day, with auditable sign-offs at every step.

---

## 10. SDN screening — the API a bank application calls

The bank's own KYC system, wire-transfer gateway, or customer-onboarding app does **not** use the dashboard. It calls a dedicated production endpoint over HTTPS with HMAC headers. There are three SDN entry points — pick the right one for your use case.

### 10.1 Production endpoint (recommended for bank backends)

**`POST https://fortressapi.<bank>/api/v1/screen/sdn`** — defined in `src/api/screening_api.py:121`.

Auth (dual — either works):
- **HMAC** (bank backend → API): headers `X-Customer-Id: <bank_id>` and `X-Api-Key: <64-char hex key>`. Validated against `client_credentials` by `sync_auth.py:require_sync_auth`. Issue/rotate via `scripts/register-bank.sh` and the `client_admin_api.py` provision/rotate endpoints.
- **Cognito Bearer** (dashboard → API): `Authorization: Bearer <id_token>`, role must be `Analyst`, `CCO`, `CRO`, or `Admin`.

Request body:

```json
{
  "name": "Vladimir Petrov",
  "country": "RU",
  "dob": "1975-04-12",
  "address": "Moscow, Russia",
  "external_customer_id": "BANK-CUST-7741",
  "request_kind": "transaction",
  "request_group_id": "WIRE-2026-06-12-00831",
  "party_role": "originator"
}
```

Field notes:
- `name` (required, 1–300 chars).
- `country` is ISO 3166-1 alpha-2 (`US`, `RU`, `IR`, …) — improves precision.
- `request_kind` ∈ `customer | transaction | bulk | monitoring | dashboard`. Use `customer` for KYC onboarding, `transaction` for wire screening, `monitoring` for periodic re-screen.
- `external_customer_id` is **your** customer ID. Fortress stores it for audit and replay; it never leaves the bank's VPC.
- `sdn_number` (optional) lets you re-check a specific SDN entry instead of a free-text name.

Response (`200 OK`):

```json
{
  "screening_id": "scr_01HZ9X…",
  "decision": "HOLD",
  "final_score": 96.4,
  "matches": [
    {
      "sdn_number": "12345",
      "matched_name": "PETROV, Vladimir Ilyich",
      "is_primary": true,
      "entity_type": "individual",
      "programs": ["UKRAINE-EO13662", "RUSSIA-EO14024"],
      "country": "RU",
      "scores": {"name": 0.97, "vector": 0.93, "country": 1.0},
      "reason": "Vector + fuzzy name match on primary SDN entry"
    }
  ],
  "screened_at": "2026-06-12T14:22:08.117Z",
  "latency_ms": 84.2
}
```

Action mapping for the bank's app:
- `decision: "PASS"` → proceed.
- `decision: "REVIEW"` → queue for an analyst (the dashboard's Cases panel surfaces these via `screening_events` → `screening_cases`).
- `decision: "HOLD"` → block the transaction / freeze onboarding, surface to compliance ops immediately. Per OFAC, a true hit must not be released without OFAC guidance.

### 10.2 Idempotency

Same `name + country + dob + address` within 24 h returns the **same** `screening_id` with header `X-Idempotent: true`. The cache auto-invalidates when the SDN list refreshes (any `ofac_sdn_vectors.last_seen_at` newer than the cached row → recompute). This means it is safe for the bank to retry on network failure without producing duplicate audit rows.

### 10.3 Replay a prior decision

**`GET https://fortressapi.<bank>/api/v1/screen/sdn/{screening_id}`** — returns the exact same body the original POST returned, for audit and exam evidence. Same dual auth.

### 10.4 The simpler trial endpoint (NOT for production)

**`POST https://fortressapi.<bank>/validate/ofac-screen`** — defined in `src/api/validation_api.py:228`. Public, rate-limited, no auth, no audit row, no idempotency. Useful for proof-of-value demos and unit tests. **Do not wire a production banking workflow to this endpoint** — it does not persist `screening_events` and therefore produces no examiner-grade audit trail.

Body:
```json
{ "party_name": "Vladimir Petrov", "party_country": "RU" }
```

### 10.5 The dashboard's analyst search

**`POST https://fortressapi.<bank>/api/v1/dashboard/screening/search`** — Cognito-only, Analyst+ role. Same engine, but scores are remapped to 0–100 integers + officer-friendly labels for human review. This is what the **SDN Search** page in the dashboard calls. Not intended for backend integration.

### 10.6 Postman setup for the production endpoint

Environment variables:

| Variable | Value |
|---|---|
| `base_url` | `https://fortressapi.<bank>` |
| `bank_id` | `firstnational` |
| `api_key` | (64-char hex, from `scripts/register-bank.sh` output) |

Request:
- Method: `POST`
- URL: `{{base_url}}/api/v1/screen/sdn`
- Headers:
  - `X-Customer-Id: {{bank_id}}`
  - `X-Api-Key: {{api_key}}`
  - `Content-Type: application/json`
- Body (raw JSON): see §10.1 above.

Smoke-test sequence:

1. Pre-flight: `GET {{base_url}}/api/v1/dashboard/ofac-health` (Cognito Bearer) — confirm `vector_mode_ready: true` and `embedding_coverage_pct >= 99.0`. If false, screening will fall back to legacy fuzzy mode (set by `OFAC_SCREENER_MODE`).
2. Known-clear: POST with `{"name":"John Q Customer","country":"US"}` → expect `decision: "PASS"`, empty `matches`.
3. Known-hit: POST with a name your team verified against the current SDN list → expect `decision: "HOLD"`, `matches[].sdn_number` populated, `final_score > 90`.
4. Idempotency: POST the same body again within 60 s → response header `X-Idempotent: true`, identical `screening_id`.
5. Replay: `GET {{base_url}}/api/v1/screen/sdn/{screening_id}` → same body returned.
6. Wrong key: change `X-Api-Key` → expect `401 Unauthorized` (and the API log line `sync_auth_missing_headers` or a failed `validate` will appear in CloudWatch). The error body deliberately does not say *why* it failed.

### 10.7 Production integration checklist for the bank's app

1. Bank's NAT Gateway EIP added to the WAF allowlist on `fortressapi.<bank>` (Pipeline 2 ALB).
2. Bank provisioned via `scripts/register-bank.sh` → received `customer_id` + a one-time `X-Api-Key`. Key is hashed in `client_credentials`; rotate via `POST /internal/admin/clients/{id}/rotate`.
3. Bank stores `X-Api-Key` in its own secrets manager — never in source.
4. Bank's wire/KYC service calls `POST /api/v1/screen/sdn` synchronously before commit; on `HOLD`/`REVIEW`, blocks the transaction and posts to the operator queue.
5. On every `200 OK`, persist `screening_id` against the bank's transaction id for audit join.
6. Monitor latency: `latency_ms` p99 should stay under 250 ms; if it spikes, check `/health/` (vector index integrity) and `/api/v1/dashboard/ofac-health`.
7. On scheduled SDN refresh (nightly via Pipeline 1), the bank does nothing — the next call gets a fresh decision automatically because the idempotency cache invalidates on refresh.

---

## 11. APIs the bank's app calls during a live transaction

This section answers a different question from the dashboard ones above: **which endpoints does a bank's existing core-banking, wire, KYC, or loan-origination app actually wire into its transaction flow?** The deployment ground rules live in `docs/p2_dashboard_api_production_integration.md`; this section is the matching menu of endpoints, every one of which runs **inside the bank's own VPC** (the customer-data-stays-local guarantee).

### 11.1 Endpoint menu by transaction type

| Transaction in the bank's app | Endpoint to call | Auth | What it returns | Use it for |
|---|---|---|---|---|
| Customer onboarding / KYC | `POST /api/v1/screen/sdn` | HMAC `X-Api-Key` + `X-Customer-Id` | `decision`, `screening_id`, matches, audit row | Block, queue-for-review, or pass a new customer |
| Wire-transfer pre-commit check | `POST /validate/wire-transfer` | None (rate-limited per IP) | `decision`, violations (CTR, SAR, dual-approval, structuring), recommendations | BSA/AML gate before debit |
| Wire-transfer party screen (originator + beneficiary) | `POST /api/v1/screen/sdn` (call twice — once per party, with `party_role`) | HMAC | Same as KYC | OFAC must-clear before settlement |
| Small-business loan origination | `POST /validate/loan-application` | None (rate-limited per IP) | `decision`, CFPB 1071 violations | Section 1071 demographic-data + reporting gate |
| Periodic re-screen (nightly customer rerun) | `POST /api/v1/screen/sdn` with `request_kind=monitoring` | HMAC | New decision if SDN list changed, else idempotent replay | Cron job in the bank's app |
| Audit / replay a prior decision | `GET /api/v1/screen/sdn/{screening_id}` | HMAC | Original response verbatim | Examiner evidence, dispute, retry idempotency |
| Pull authoritative rule definitions | `GET /api/v1/rules?regulator=ofac&domain=ofac` | None (public, read-only) | Compliance rule rows | Display in app UI, internal lookup |
| Pull rule changes since X | `GET /api/v1/rules/changes?since=2026-06-01` | None | Diff feed | App-side cache invalidation |
| Public SDN list dump | `GET /api/v1/ofac/sdn-list` | None | Up to 20k entries | Local cache for ultra-low-latency pre-filter (don't substitute for `/screen/sdn`) |

**Heuristic for picking screening endpoint:**
- Need an audit row + screening_id + idempotency + bank attribution → `/api/v1/screen/sdn` (always use this in production).
- Just sanity-checking from a unit test / demo → `/validate/ofac-screen`.

### 11.2 Wire-transfer call — example

```http
POST /validate/wire-transfer
Content-Type: application/json

{
  "amount": 12500.00,
  "currency": "USD",
  "sender_name": "Acme LLC",
  "beneficiary_name": "Beta Holdings Ltd",
  "beneficiary_country": "AE",
  "is_international": true,
  "approvals": [{"approver_id": "U123", "role": "wire_officer"}]
}
```

Response (the bank's app must honour `decision`):

```json
{
  "action": "block",
  "decision": "HOLD",
  "screening_id": null,
  "final_score": 0.0,
  "violations": [
    {
      "regulation": "BSA",
      "rule_id": "CTR-31CFR1010.311",
      "description": "Cash transaction ≥ $10,000 requires CTR filing within 15 days",
      "severity": "HIGH",
      "recommendation": "File FinCEN Form 112 (CTR) before settlement",
      "match_detail": null
    }
  ],
  "screening_timestamp": "2026-06-12T14:31:02Z",
  "latency_ms": 47.1
}
```

Action mapping the bank's app implements:
- `decision: PASS` → debit.
- `decision: REVIEW` → hold in pending queue, route to wire ops.
- `decision: HOLD` → reject the wire, surface violations to teller.

Thresholds (kept in `compliance_rule_resolver.py`) the response reflects:
- CTR: $10,000
- SAR: $5,000
- Travel rule: $3,000
- Dual-approval: $10,000
- Beneficial ownership: 20 % (per FinCEN CHG-2026-0412)

### 11.3 Loan-application call — example

```http
POST /validate/loan-application
Content-Type: application/json

{
  "race_of_principal_owner": "Asian",
  "sex_of_principal_owner": "Female",
  "ethnicity_of_principal_owner": "Not Hispanic or Latino",
  "naics_code": "541512",
  "business_gross_revenue": 850000,
  "time_in_business": "5-10 years",
  "number_of_workers": 12,
  "minority_owned_business": true,
  "women_owned_business": true
}
```

Returns CFPB Section 1071 verdict (PASS/REVIEW/HOLD) plus required-field violations so the app's loan-officer UI can surface them inline.

### 11.4 Approvals — when /validate returns REVIEW

If a wire or KYC decision is `REVIEW`, the bank's app should:

1. Create a workflow row (this is implicit — `/api/v1/screen/sdn` writes `screening_events` and the dashboard's Cases panel picks it up).
2. Wait for a CCO/CRO to act in the dashboard. The dashboard calls `POST /approvals/` (`src/api/approvals.py:22`) with `checkpoint` and `decision: approved|rejected`.
3. The bank's app can poll `GET /approvals/{workflow_id}` for the decision and then proceed/abort.

For wires this is the dual-approval path. For SDN cases it is the OFAC-officer release path.

### 11.5 Rate limits & SLOs the bank's app must respect

- `/validate/*` — 60 req/min per IP by default (`FORTRESS_P2_VALIDATE_RPM`). Burst above this returns `429 Retry-After: <s>`. Override via env if your test bank needs higher.
- `/api/v1/screen/sdn` — no fixed rate limit; backed by HMAC quota in `client_credentials`. Each bank's key has its own bucket.
- Target latency: wire/loan ≤ 100 ms p99, SDN ≤ 250 ms p99 (idempotent replays cap at ~20 ms).

### 11.6 What the bank's app must log on every call

For exam evidence, persist these three values in the bank's transaction store alongside its own transaction id:

1. `screening_id` (for `/screen/sdn`) or the response's `screening_timestamp` (for `/validate/*`).
2. `decision`.
3. The raw `violations` / `matches` array.

This is the join key examiners use when the dashboard's audit export ties bank transactions back to Fortress decisions.

### 11.7 Testing the integration before go-live

Run, in order:

1. `GET /health/` and `GET /health/sync` — both must be `ok` (see §5.1).
2. `GET /api/v1/dashboard/ofac-health` — `vector_mode_ready: true`, `embedding_coverage_pct ≥ 99.0`.
3. Postman synthetic-transaction suite (one request per endpoint in §11.1) — every one returns the documented schema; OFAC PASS+HOLD pair behaves as expected; idempotency header is present on the 2nd identical SDN call.
4. UAT load test against the bank's staging app at the bank's normal daily peak × 1.5 — confirm no 429s, latency budgets hold.
5. Negative-path test: wrong `X-Api-Key` → 401; SDN list refresh mid-soak → cache invalidates → fresh decision returned.
6. Sign-off gates in `docs/p2_dashboard_api_production_integration.md` §10 — Cognito auth, CORS, WAF, role mapping all green for 24 h.

Only after all six pass does the bank flip `TrialMode=false` and point the live wire/KYC/loan paths at `https://fortressapi.<bank>`.

---

## 12. Where to look next

- `docs/p2_dashboard_api_production_integration.md` — the production cut-over checklist (DNS, ACM, Cognito wiring, CORS).
- `docs/runbooks/agent-2-pipeline1.md` — Pipeline 1 scraper + ingestion.
- `docs/runbooks/agent-4-sync.md` — UpdateService, JIRA webhooks, dashboard.
- `docs/runbooks/scraper-extraction-architecture.md` — golden-set + backfill for obligation corrections.
- `docs/runbooks/ofac-calibration-gold-set.md` — OFAC threshold tuning for legacy → vector cut-over.
- `src/api/dashboard_api.py` — every endpoint above, with the exact SQL it runs.
- `dashboard/src/services/api.js` — the contract the frontend uses.
