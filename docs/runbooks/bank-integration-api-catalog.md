# Bank Integration API Catalog

**Audience:** the bank's engineering team during onboarding.
**Goal:** one document listing every Fortress endpoint a bank ever calls, what it does, how to authenticate, and which one to wire for which use case.

There are **two integration surfaces**. Treat them as separate APIs even though they share auth primitives.

| Surface | Where it runs | Who calls it | Purpose |
|---|---|---|---|
| **A — Fortress Cloud P1** | `https://api.fortresslabs.dev` (over public internet) | Bank backend (UpdateService) | Pull the regulatory corpus into the bank's local Aurora replica |
| **B — Pipeline 2** | Inside the bank's VPC (per-bank deploy) | Bank's payments/KYC/loan systems + CCO dashboard | Real-time validation, screening, and CCO governance |

Cross-references:
- Auth security baseline → `docs/runbooks/auth-security-baseline.md`
- OFAC contract → `docs/runbooks/ofac-screening-api-contract.md`
- P1 readiness → `docs/runbooks/p1-screening-readiness.md`

---

## Tool selection — Postman vs Dashboard vs production code

Three "callers" exist for a bank. Pick the right one per action.

| Tool | Auth | Use it for |
|---|---|---|
| **Postman** (or curl) | HMAC headers | Smoke-testing the integration during onboarding; verifying contracts/edge cases; ad-hoc ops checks |
| **Dashboard** (SPA + Cognito) | Cognito JWT | All governance actions: test generation, rule status, sign-off, exports, approvals — anything that needs an *authenticated human* in the audit trail |
| **Production code** (bank's app) | HMAC headers | Hot path (validators, screening) and scheduled sync |

### When to use Postman

- During onboarding: verify your HMAC key works against `/health`, `/license/verify`, `/validate/wire-transfer`, `/api/v1/screen/sdn`, and one `/api/sync/*` endpoint.
- After a release: smoke-test contracts didn't change.
- Ops debugging: replay a single screening, check `/health/sync` cursor freshness.

Postman should **not** be used for test generation, sign-offs, or rule status changes — those need a Cognito-authenticated user attached to the audit record (see "When to use the Dashboard" below).

### When to use the Dashboard

| Action | Why Dashboard, not Postman |
|---|---|
| `POST /api/v1/rules/{rule_id}/generate-test` | Test generation is a CCO action; output gets PR'd into the bank's repo via `github_service.py`. Postman bypasses CCO attribution + checkpoint approvals. |
| `POST /api/v1/rules/bulk-generate` | Same — bulk version. |
| `PATCH /api/v1/dashboard/rules/{rule_id}/status` (mark applicable / N/A) | Governance state change; CRO/CCO audit required. |
| `POST /api/v1/dashboard/changes/{change_ref}/signoff` | Sign-off is the audit event regulators ask for. Must be a Cognito user. |
| `POST /approvals` | Workflow checkpoint approval (CRO/CCO). |
| `POST /api/v1/dashboard/exports` | Regulator-ready exports; bound to the requesting user. |

Rule of thumb: **if the action changes governance state, use the Dashboard.** If it's stateless verification, Postman is fine.

### When to wire into production code

- Anything on the transaction hot path (`/validate/*`, `/api/v1/screen/sdn`).
- Daily sync cron (`/api/sync/*` family) inside the bank's UpdateService.
- Boot-time license check (`/license/verify`).
- Continuous `/health/sync` poll from monitoring.

---

## Postman setup

### 1. Environment variables

Create a Postman environment named e.g. `Fortress — testbank` with these variables. Mark `api_key` and `dashboard_jwt` as **Secret**.

| Variable | Example | Notes |
|---|---|---|
| `p1_base` | `https://api.fortresslabs.dev` | P1 (Fortress Cloud) |
| `p2_base` | `https://fortressapi.bank.com` | P2 (per-bank, internal) |
| `customer_id` | `testbank` | Lowercase, no underscore; matches Cognito pool + WAF |
| `api_key` | `<64-hex>` | Shown once at provisioning. Never commit. Store in Postman vault. |
| `dashboard_jwt` | `<jwt>` | Optional; for hitting dashboard endpoints from Postman. Copy from a logged-in browser session. Short-lived (≤60 min). |

### 2. Header presets

Save these as a Postman **Collection-level header** so every request inherits them:

**HMAC (P1 sync + P2 validators + P2 screening):**

```
X-Customer-Id: {{customer_id}}
X-Api-Key: {{api_key}}
Content-Type: application/json
Accept: application/json
```

**Cognito JWT (dashboard endpoints only):**

```
Authorization: Bearer {{dashboard_jwt}}
Content-Type: application/json
Accept: application/json
```

Use Postman's per-request "Headers" override when an endpoint needs a different style (e.g. `/api/sync/*` ignores the headers because creds go in the body — see below).

### 3. Body-credential exception (P1 `/api/sync/*` only)

The bulk sync endpoints on P1 take credentials in the **request body**, not headers. Use a body template like:

```json
{
  "customer_id": "{{customer_id}}",
  "api_key": "{{api_key}}",
  "from_version": 0
}
```

Replace `from_version` with the bank's last-known cursor on subsequent calls. This applies to: `/api/sync/postgres`, `/api/sync/embeddings`, `/api/sync/initial`, `/api/sync/obligations`, `/api/sync/deadlines`, `/api/sync/change-records`, `/api/sync/risk-scores`, `/api/sync/benchmarks`, `/api/sync/ofac-sdn`, `/api/sync/sdn-vectors`, `/api/sync/sdn-changes`, `/api/sync/sdn-health`, `/api/sync/enforcement-actions`, `/api/sync/counts`, `/api/sync/status`, `/license/verify`.

`/api/v1/rules`, `/api/v1/ofac/sdn-list`, `/api/v1/rules/changes`, and all P2 endpoints use the **header style** instead.

### 4. Suggested onboarding smoke-test collection

Run these in order during integration. Each should return 200 (or 401 if you've misconfigured headers/keys).

| # | Request | Expected |
|---|---|---|
| 1 | `GET {{p1_base}}/health` | `{"status":"ok"}` — no auth |
| 2 | `POST {{p1_base}}/license/verify` (body creds) | `{"valid": true, "tier": "..."}` |
| 3 | `GET {{p1_base}}/api/v1/rules?limit=5` (HMAC headers) | Returns 5 sample rules |
| 4 | `POST {{p1_base}}/api/sync/counts` (body creds) | Per-table row counts |
| 5 | `GET {{p2_base}}/health` | `{"status":"ok"}` |
| 6 | `POST {{p2_base}}/validate/wire-transfer` (HMAC headers, sample body) | `{"decision":"PASS",...}` |
| 7 | `POST {{p2_base}}/api/v1/screen/sdn` (HMAC headers, name="Vladimir Putin", request_kind="customer") | `{"decision":"HOLD","matches":[...]}` |
| 8 | `GET {{p2_base}}/api/v1/screen/sdn/{{screening_id}}` (from #7) | Same response, idempotent replay |
| 9 | `GET {{p2_base}}/health/sync` | Cursor + last sync timestamp |

If a request fails:
- **401** → key mismatch, wrong customer_id, or source IP not WAF-allowlisted. All three produce the same `Unauthorized` — check WAF first via Fortress ops.
- **403** → JWT role doesn't have permission for the action.
- **422** → request body fails Pydantic validation; the response body lists the field.
- **5xx** → check `/health/ready` and surface to Fortress ops.

### 5. Postman → production handoff

When the bank's engineers finish Postman testing, the **exact same headers and request bodies** are what they wire into production code. Two things change:

1. The HMAC key moves from the Postman vault into the bank's secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.). Never inline.
2. `from_version` cursors must be **persisted by the bank** between sync runs (Postman just resets them on each invocation).

Everything else — headers, payloads, response shape — is identical.

---

## Dashboard usage (governance actions)

The dashboard is the only supported surface for the following. Do not script these endpoints from Postman or production code.

| Workflow | Dashboard path | Underlying API |
|---|---|---|
| Generate test for a rule | Rules page → "Generate Test" button | `POST /api/v1/rules/{rule_id}/generate-test` |
| Bulk test generation | Rules page → multi-select → "Generate Tests" | `POST /api/v1/rules/bulk-generate` |
| Mark rule applicable / N/A | Rules page → rule row → status dropdown | `PATCH /api/v1/dashboard/rules/{rule_id}/status` |
| Sign off on a regulatory change | Changes page → change card → "Sign Off" | `POST /api/v1/dashboard/changes/{change_ref}/signoff` |
| Approve workflow checkpoint | Workflows page → checkpoint row → Approve/Reject | `POST /approvals` |
| Generate audit export | Exports page → "New Export" | `POST /api/v1/dashboard/exports` |

These actions all do three things Postman cannot:
1. Attach the **authenticated CCO/CRO identity** to the audit row.
2. Trigger the **agent pipeline + checkpoint approvals** (not just the raw API).
3. Surface progress + sandbox-validation results in the UI (test generation in particular runs Agents 4 → 5 → 6 over several minutes).

---

## Surface A — Fortress Cloud P1 (external, internet)

Base URL: `https://api.fortresslabs.dev`

### Auth (P1)

Two flavours coexist; pick per endpoint:

| Style | Where credentials go | Used by |
|---|---|---|
| **Header HMAC** | `X-Customer-Id: <bank>` + `X-Api-Key: <64-hex>` | `/api/v1/*` read endpoints and screening |
| **Body credentials** | `{"customer_id": "...", "api_key": "...", ...}` | All `/api/sync/*` bulk-sync endpoints (`src/api/sync_api.py`) |

Both validate against the same `client_credentials` row (SHA-256 hash). All requests must originate from the bank's WAF-allowlisted NAT EIP. Failure modes always return 401 with `Unauthorized` and never reveal which check failed.

### A1. Read-only public rules (`/api/v1/*`)

Source: `src/api/compliance_rules_api.py`. No customer data accepted.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/rules` | Current compliance rule corpus. Query params: `regulator`, `category`, `geography`, `effective_from`, `limit` |
| GET | `/api/v1/ofac/sdn-list` | Current OFAC SDN snapshot |
| GET | `/api/v1/rules/changes?since=<cursor>` | `regulatory_change_records` since cursor |

### A2. Bulk sync (`/api/sync/*`)

Source: `src/api/sync_api.py`. All take a body of shape `{"customer_id", "api_key", "from_version"}` (plus per-endpoint fields). Daily cron from the bank's UpdateService.

| Path | Purpose | Notes |
|---|---|---|
| `POST /api/sync/initial` | First-time full sync after onboarding | Run once on bring-up |
| `POST /api/sync/postgres` | Incremental rule diffs since `from_version` | Returns `added` / `updated` / `deleted` |
| `POST /api/sync/embeddings` | Same diff, but with pre-computed Titan v2 1024-dim vectors | Skips local Bedrock cost |
| `POST /api/sync/obligations` | Obligation sentences (Bedrock-extracted) per rule | Powers obligations panel |
| `POST /api/sync/deadlines` | Filing deadlines + `days_remaining` | Refreshed by `deadline_tracker.refresh_days_remaining()` |
| `POST /api/sync/change-records` | Detected rule mutations (semantic diff) | Drives "what changed" panel |
| `POST /api/sync/risk-scores` | Per-regulation enforcement-risk scores | From `enforcement_risk_scores` |
| `POST /api/sync/benchmarks` | FFIEC call-report peer benchmarks | Optional |
| `POST /api/sync/enforcement-actions` | OCC / FinCEN / CFPB enforcement actions feed | |
| `POST /api/sync/ofac-sdn` | SDN entity records | Legacy screener path |
| `POST /api/sync/sdn-vectors` | SDN name-variant embeddings + tsv | Vector screener mode |
| `POST /api/sync/sdn-changes` | SDN delta since last cursor | Daily |
| `POST /api/sync/sdn-health` | SDN ingest health report | Read after each sync |
| `POST /api/sync/counts` | Per-table row counts | Reconciliation |
| `POST /api/sync/status` | Sync-session state | |
| `POST /license/verify` | License + tier check | Block app boot on failure |

### A3. Health (P1)

| Path | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /health/ready` | Aurora + Bedrock connectivity |
| `GET /health/sync` | Sync subsystem state |

---

## Surface B — Pipeline 2 (internal to bank VPC)

Base URL: bank-internal (e.g. `https://fortressapi.bank.com`).

### Auth (P2)

Dual auth, chosen per request:

| Style | Used by | Required role |
|---|---|---|
| **HMAC header** (`X-Api-Key` + `X-Customer-Id`) | Bank's backend services | n/a (machine) |
| **Cognito JWT** (`Authorization: Bearer <jwt>`) | CCO dashboard SPA | group → role (`CCO`, `CRO`, `CIO`, `Analyst`, `Admin`) |

Screening endpoints accept either (`require_screening_auth` in `src/api/screening_api.py:40`). Validators are HMAC-only. Dashboard endpoints are JWT-only.

### B1. Production validators (hot path) — `/validate/*`

Source: `src/api/validation_api.py`. Customer PII never leaves bank VPC. Reads rules from the local Aurora replica → zero network calls during validation.

| Method | Path | Purpose |
|---|---|---|
| POST | `/validate/wire-transfer` | Wire BSA/AML compliance (CTR, travel rule, dual approval, sanctioned country) |
| POST | `/validate/ofac-screen` | Single-party OFAC screen (lightweight) |
| POST | `/validate/loan-application` | CFPB §1071 small-business loan validation |

**Common response envelope:**

```json
{
  "action": "string",
  "decision": "PASS | REVIEW | HOLD",
  "screening_id": "uuid|null",
  "final_score": 0.0,
  "violations": [
    {
      "regulation": "OFAC",
      "rule_id": "ofac-sdn-001",
      "description": "...",
      "severity": "block|warn|info",
      "recommendation": "...",
      "match_detail": { "...": "..." }
    }
  ],
  "screening_timestamp": "ISO-8601",
  "latency_ms": 0.0
}
```

#### Example — wire validation

```http
POST /validate/wire-transfer
X-Customer-Id: firstnational
X-Api-Key: <64-hex>

{
  "amount": 25000,
  "currency": "USD",
  "sender_name": "Acme Corp",
  "beneficiary_name": "Globex SA",
  "beneficiary_country": "DE",
  "is_international": true,
  "approvals": [{"approver": "manager-1"}]
}
```

### B2. Production screening — `/api/v1/screen/*`

Source: `src/api/screening_api.py`. Heavier path than `/validate/ofac-screen` — full SDN match with idempotency, audit, and replay.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/screen/sdn` | Full SDN screen (name, country, DOB, address, party_role). Idempotent within 24h (returns `X-Idempotent: true`); cache invalidates on SDN refresh |
| GET | `/api/v1/screen/sdn/{screening_id}` | Replay a prior screening (audit / dispute) |

**`request_kind` tags the call site** (drives audit grouping):

| Value | When |
|---|---|
| `customer` | KYC onboarding |
| `transaction` | Wire / payment |
| `bulk` | Batch refresh |
| `monitoring` | Continuous portfolio sweep |
| `dashboard` | Analyst-initiated from UI |

#### Example — KYC screen

```http
POST /api/v1/screen/sdn
X-Customer-Id: firstnational
X-Api-Key: <64-hex>

{
  "name": "Jose Garcia",
  "country": "MX",
  "dob": "1979-04-12",
  "external_customer_id": "BANK-CUST-7781",
  "request_kind": "customer",
  "party_role": "primary"
}
```

### B3. CCO dashboard reads — `/api/v1/dashboard/*`

Source: `src/api/dashboard_api.py`. Cognito JWT only. Drive bank's own analytics tooling off these or call from the bundled SPA.

| Path | Purpose |
|---|---|
| `GET /api/v1/dashboard/overview` | Headline KPIs (rules, changes, deadlines, risk) |
| `GET /api/v1/dashboard/rules` | Per-rule status, signoff, mapping |
| `GET /api/v1/dashboard/changes` | Recent regulatory changes + semantic diff |
| `GET /api/v1/dashboard/deadlines` | Upcoming filings sorted by `days_remaining` |
| `GET /api/v1/dashboard/risk` | Aggregate risk scoring |
| `GET /api/v1/dashboard/coverage` | Rule → test coverage matrix |
| `GET /api/v1/dashboard/workflows` | In-flight agent workflows |
| `GET /api/v1/dashboard/ofac-health` | SDN ingest health |
| `GET /api/v1/dashboard/enforcement` | Recent enforcement actions tied to bank profile |
| `GET /api/v1/dashboard/obligations` | Obligation sentences per regulation |
| `GET /api/v1/dashboard/cro` | CRO roll-up |
| `GET /api/v1/dashboard/cio` | CIO tech panel |
| `GET /api/v1/dashboard/readiness` | Audit-readiness score |
| `GET /api/v1/dashboard/risk-matrix` | Likelihood × impact heatmap |
| `GET /api/v1/dashboard/findings` | Open findings list |
| `PATCH /api/v1/dashboard/rules/{rule_id}/status` | Mark rule applicable / N/A (CCO) |
| `POST /api/v1/dashboard/changes/{change_ref}/signoff` | Sign off on a regulatory change (CCO/CRO) |
| `POST /api/v1/dashboard/exports` | Generate audit export bundle |
| `GET /api/v1/dashboard/exports/{export_id}/download` | Download export |

### B4. Workflow / agent pipeline

| Method | Path | Purpose |
|---|---|---|
| POST | `/webhooks/jira` | JIRA story → P2 agent pipeline trigger |
| POST | `/approvals` | Approve / reject a workflow checkpoint |
| GET | `/approvals/{workflow_id}` | Approval state |
| GET | `/workflows/{workflow_id}` | Workflow status |
| GET | `/agents/status` | Agent health |
| POST | `/api/v1/rules/{rule_id}/generate-test` | Generate test code for a rule (CCO) |
| POST | `/api/v1/rules/bulk-generate` | Bulk generation |
| GET | `/api/v1/rules/{rule_id}/test-status` | Generation status |

### B5. Health (P2)

| Path | Purpose |
|---|---|
| `GET /health` | Liveness |
| `GET /health/ready` | Aurora + Bedrock + sync replica freshness |
| `GET /health/sync` | UpdateService heartbeat |

---

## Cheat sheet — which endpoint for which use case

| Bank use case | Call | Surface | Auth |
|---|---|---|---|
| "Is this wire OK to send?" | `POST /validate/wire-transfer` | B | HMAC |
| "Screen this new account opening" | `POST /api/v1/screen/sdn` with `request_kind=customer` | B | HMAC |
| "Screen the parties on a wire" | `POST /api/v1/screen/sdn` with `request_kind=transaction` | B | HMAC |
| "Does this loan meet CFPB §1071?" | `POST /validate/loan-application` | B | HMAC |
| "Pull the latest rules into our DB" | `POST /api/sync/postgres` (nightly cron) | A | Body creds |
| "Pull SDN delta" | `POST /api/sync/sdn-changes` | A | Body creds |
| "Show CCO a daily dashboard" | `/api/v1/dashboard/*` | B | Cognito JWT |
| "Replay a screening for audit" | `GET /api/v1/screen/sdn/{id}` | B | HMAC or JWT |
| "Sign off on a rule change" | `POST /api/v1/dashboard/changes/{ref}/signoff` | B | Cognito JWT (CCO/CRO) |
| "Generate test code for new rule" | `POST /api/v1/rules/{rule_id}/generate-test` | B | Cognito JWT (CCO) |
| "Verify license before app boot" | `POST /license/verify` | A | Body creds |
| "Reconcile row counts after sync" | `POST /api/sync/counts` | A | Body creds |

---

## Integration sequencing for new bank onboarding

1. **License + initial sync (Surface A).** Provision key → `/license/verify` → `/api/sync/initial` → `/api/sync/embeddings` → `/api/sync/ofac-sdn` (or `/sync/sdn-vectors` if vector mode).
2. **Deploy P2 inside bank VPC.** Aurora per bank, UpdateService running daily, Cognito pool stood up per `infra/cloudformation/pipeline2-dashboard.yml`.
3. **Wire the hot path (Surface B).** Payments → `/validate/wire-transfer`. KYC → `/api/v1/screen/sdn`. Loan origination → `/validate/loan-application`.
4. **Open the dashboard.** CCO / CRO / Analyst / Admin invited via bank IdP (federated to Cognito).
5. **Replay + audit.** All screenings retrievable via `GET /api/v1/screen/sdn/{id}`; exports via `/api/v1/dashboard/exports`.

---

## Change log

- 2026-06-07 — initial catalog. Endpoints enumerated from `src/api/`. Refresh whenever a route is added or removed.
- 2026-06-07 — added "Tool selection" (Postman vs Dashboard vs production code), Postman setup section (env vars, header presets, body-credential exception, smoke-test sequence, error decode), and Dashboard governance-action mapping.
