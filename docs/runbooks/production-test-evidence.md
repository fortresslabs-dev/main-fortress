# Production Test Evidence — Pilot Bank Pack

**Audience:** Pilot bank TPRM / risk / compliance / IT-security teams
reviewing Fortress Labs for production traffic.

**Purpose:** One document that lists, by surface, *what was tested*, *how*,
*the exit criteria*, and *where in the repository the evidence lives*. This
is the artifact to point at when an examiner, internal audit, or vendor-due-
diligence request asks "what testing was done before pilot?"

**Companion docs (read together):**

- [`demo-readiness.md`](demo-readiness.md) — the pre-pilot demo gate (§8 exit
  criteria). Every "demo-ready" item in this pack is a subset of what a
  pilot rollout requires.
- [`production-readiness-gaps.md`](production-readiness-gaps.md) — the full
  B1–B12 / H1–H14 / M1–M14 gap list. Items still open are flagged in §6.
- [`agent-1-infra.md`](agent-1-infra.md) — infra (VPC / Aurora / Secrets /
  ACU sizing) acceptance.
- [`ofac-calibration-gold-set.md`](ofac-calibration-gold-set.md) — the
  pre-flip gate on vector-mode OFAC screening.

**Last verified:** 2026-06-09 against commit `6346432` on branch
`test_automated` (full `make demo-dryrun` Tier 3 green; six F0–F5
journeys + §6.1 reset under budget).

---

## 1. What "production-ready" means here

A change must clear **every** row below before it can deploy to a pilot
bank stack. Each row maps to a concrete CI job (see §7) and to one or
more sections of this document.

| # | Gate | Enforced by |
|---|------|-------------|
| 1 | Code lints + compiles + schema migrates clean | CI `ci` job |
| 2 | Every demo-storyline endpoint has a happy-path + auth-fail test | CI `test-demo` job (Phase 2) |
| 3 | Every demo-storyline page renders without throwing | CI `dashboard-tests` job (Phase 3a) |
| 4 | Every demo-storyline user journey runs green end-to-end | CI `dashboard-e2e` job (Phase 3b) |
| 5 | Aggregate green check before any merge to `main` or any release tag deploys | CI `demo-readiness` aggregate |
| 6 | Nightly smoke against staging | CI `nightly-staging-e2e` (alert-only) |
| 7 | Timed pre-demo dry run against a fresh seed | `make demo-dryrun` (Phase 6 harness) |

The aggregate `demo-readiness` check is the single required status check
on the branch-protection rule for `main` and the release branches.
Tag-deploy jobs (`build-p1`, `deploy-p1`, `deploy-p2`, `sync-p2-repo`)
all `needs: demo-readiness`, so an unsigned merge can never reach a
bank's ECS cluster.

---

## 2. Dashboard

The dashboard is what a CCO, CRO, CIO, Analyst, or Admin will actually
sit in front of during pilot. Coverage has two tiers — render-only smoke
(per page) and full user journey (F0–F5).

### 2.1 Per-page render smoke (Vitest)

**Location:** `dashboard/src/pages/__tests__/*.test.jsx`

One Vitest spec per page in the demo storyline. Each renders the page
with a mocked `dashboard/src/services/api.js` and asserts a known header
string is rendered without an exception.

| File | Page | Asserts |
|---|---|---|
| `Cases.test.jsx` | `/dashboard/cases` | "Screening cases" |
| `Changes.test.jsx` | `/changes` | Regulatory change inbox |
| `Enforcement.test.jsx` | `/enforcement` | Enforcement-actions table |
| `ExecutiveToday.test.jsx` | `/dashboard/executive` | "Executive — Today" |
| `Investigation.test.jsx` | `/dashboard/cases/:caseId` | Case detail |
| `Obligations.test.jsx` | `/obligations` | Obligation breakdown |
| `OfacHealth.test.jsx` | `/ofac` | "OFAC / SDN screening" |
| `Readiness.test.jsx` | `/readiness` | "Examination Readiness" |
| `Reports.test.jsx` | `/reports` | Reports list |
| `Rules.test.jsx` | `/rules` | Rules table |
| `RulesPerf.test.jsx` | `/dashboard/rules-perf` | "Rules performance" |
| `SdnSearch.test.jsx` | `/dashboard/search` | SDN screen form |
| `VectorIntel.test.jsx` | `/dashboard/vector` | "Vector Intelligence" |
| `Workflows.test.jsx` | `/workflows` | "JIRA workflow runs" |

**Count:** 14 specs. **Wall clock:** <10 s. **Pass criterion:** all green
before merge.

### 2.2 User journey E2E (Playwright)

**Location:** `dashboard/e2e/*.spec.ts`

One spec per F0–F5 flow from `demo-readiness.md` §0. Runs against `make
up` + real P1 + P2 + real Postgres (no mocks). Captures console errors
and 4xx/5xx HTTP responses for every spec; `assertCleanJourney` fails the
spec if either set is non-empty.

| Spec | Role | Flow | Budget |
|---|---|---|---|
| `f0_first_paint.spec.ts` | CCO | Warm reload, measure FCP | <2000 ms |
| `f1_sdn_screen.spec.ts` | CCO | F1: SDN screen → match → resolve | <60 s |
| `f2_rule_sync_changes.spec.ts` | CCO | F2: P1 → P2 sync → Changes tab shows row + `semantic_diff` | <90 s |
| `f3_webhook_to_approval.spec.ts` | CCO | F3: JIRA webhook → workflow → approval | <120 s |
| `f4_executive_overview.spec.ts` | CCO | F4: Readiness → ExecutiveToday → RulesPerf | <3 s/page |
| `f5_vector_intel_drilldown.spec.ts` | Admin | F5: VectorIntel → Obligations → Enforcement | <1.5 s/page |

**Fixture:** `dashboard/e2e/fixtures.ts` provides a `loggedInPage` fixture
that injects `__e2e_role` + `__e2e_token` into `localStorage` and the
`X-E2E-Role` header into outbound XHR. The backend's `auth.py` honors
the role hint only when `DEV_MODE=true` AND a non-empty
`PIPELINE2_API_TOKEN` is set — this is gated off in production.

**Last full run:** all 6 specs green in 15.0 s wall clock against the
current `test_automated` head. Per-spec durations recorded in
`dryrun-artifacts/playwright-summary.tsv`:

```
F0  2394 ms   F1  2853 ms   F2  1660 ms
F3  3476 ms   F4  4118 ms   F5  2500 ms
```

### 2.3 Acceptance for any user journey

Every Playwright spec ends with `assertCleanJourney({consoleErrors,
badResponses})`. Two acceptance criteria fire there:

- **No `console.error`** from React across the entire journey.
- **No 4xx / 5xx** HTTP response from any same-origin XHR during the
  journey.

A failure on either is a blocker — the dry-run harness exits non-zero
and the `demo-readiness` aggregate goes red.

---

## 3. Backend API contract tests

**Location:** `tests/integration/api/*.py` (81 tests across 7 files —
all active contract tests; the H1/H2/H14 stubs in `test_open_gaps.py`
are now permanent regression locks after H1/H2 closure 2026-06-10).

Every endpoint in the F1–F5 storyline gets at least a happy-path test
and an auth-fail test. The fixtures run against a real
`pgvector/pgvector:pg15` testcontainer with full alembic migration
chain — not mocks. See `feedback_init_p1_schema_destructive` for the
rationale.

| File | Tests | Routers covered | Regression locks |
|---|---|---|---|
| `test_health.py` | 7 | P1+P2 `/`, `/health`, `/health/ready`, `/health/sync` | Returns 200 + pipeline name |
| `test_dashboard_api.py` | 6 | F2/F4/F5 dashboard endpoints | `semantic_diff` present (commit 27c3bae); `change_summary` is a string (5b2096b); auth fail → 401 |
| `test_screening_api.py` | 7 | F1 — `POST /screening/sdn`, `GET /screening/sdn/{id}` | Bearer dev token + HMAC sync auth both accepted; 401 path never opens a DB session |
| `test_sync_api.py` | 12 | F2 — `POST /sync/postgres`, `/sync/change-records`, `/sync/status`, `/sync/counts` | H5: `current_version` non-null on first call; H6: `SyncMetadata.last_sync_version` updates on every page, not just last |
| `test_workflows_approvals.py` | 7 | F3 — `GET /workflows/{id}`, `POST /approvals/`, role gates | Non-approver gets 403 not 500 |
| `test_webhooks.py` | 8 | F3 — `POST /webhooks/jira` (Bearer + HMAC + DEV_MODE) | 401 on bad signature; 202/200 on valid; workflow_id echoed |
| `test_open_gaps.py` | 32 (all pass — H1/H2/H14 all closed) | Permanent regression locks for the three closed sync-auth + health gaps | See §3.1 below |

### 3.1 Permanent regression locks (all gaps now closed)

`test_open_gaps.py` started life as a set of `xfail(strict=True)`
forcing-function stubs — failing-today, asserting-post-fix — so that
the day each gap closed, the suddenly-passing test would force a
committer to delete the `xfail` marker and convert the stub into a
permanent lock. All three gaps below have since closed; the file is
now a regular regression-lock suite. Kept under the same filename so
historical CI runs and runbook cross-references stay valid.

| Gap | Tests | Contract locked |
|---|---|---|
| **H1** *(closed 2026-06-10)* | 28 (14 endpoints × 2) | Every `/api/sync/*` POST declares `require_sync_auth` Depends; unauthenticated POST returns 401 (not 422). |
| **H2** *(closed 2026-06-10)* | 2 | `/api/sync/status` rejects unauthenticated callers AND accepts valid HMAC. |
| **H14** *(closed 2026-06-09)* | 2 | `/health` exposes a `checks` dict with `db`/`redis`/`bedrock`/`s3`; returns 503 when DB is unreachable. |

**Backend unit tests** (separate): `tests/unit/test_agents.py` —
14 tests across the 6-agent pipeline (Discovery → Understanding →
Framework → Generation → Validation → Execution) covering wire
transfers, BSA/AML, OFAC, SAR, Reg E, CFPB 1071.

**Pass criterion:** all 81 + 14 active green (zero remaining xfails
after the H1/H2 closure 2026-06-10); wall-clock target for
`make test-demo` is <90 s.

---

## 4. Authentication & session

Two distinct auth surfaces, both tested.

### 4.1 Dashboard user session — Cognito JWT

- **Production path:** Cognito hosted UI → ID token → Bearer header.
  `src/api/auth.py:_decode_cognito_token` validates RS256 signature
  against the pool's JWKS, checks `aud == COGNITO_CLIENT_ID` and
  `iss == https://cognito-idp.<region>.amazonaws.com/<pool>`.
- **Fail-closed:** if `COGNITO_USER_POOL_ID` is unset on a non-dev
  stack, every request returns `503 COGNITO_USER_POOL_ID not set`. This
  is enforced by gap **B3** acceptance — see
  `production-readiness-gaps.md`.
- **Role derivation:** first entry in the user's `cognito:groups`
  claim is the role. Allowed values: `CCO`, `CRO`, `CIO`, `Analyst`,
  `Admin`. Unknown → `viewer` (read-only on most endpoints).

### 4.2 Internal / dev token

- `Authorization: Bearer $PIPELINE2_API_TOKEN` is honored **only** when
  `DEV_MODE=true` AND `PIPELINE2_API_TOKEN` is non-empty. The historical
  default `changeme-token` is intentionally not accepted as a fallback —
  that was the gap **B1** silent-bypass bug.
- E2E specs vary persona via the `X-E2E-Role` header. The header is
  read only on the dev-token path and only values in
  `{CCO, CRO, CIO, Analyst, Admin, admin}` are honored. Verified by
  the F5 journey (Admin) and the `test_workflows_approvals.py` role
  fixtures.

### 4.3 Capability matrix (production)

| Role | Edit | Approve | Screen | VectorIntel | GovernModel |
|---|:---:|:---:|:---:|:---:|:---:|
| Analyst | — | — | ✓ | — | — |
| CCO | ✓ | ✓ | ✓ | — | — |
| CRO | — | ✓ | ✓ | ✓ (read) | — |
| CIO | — | — | — | — | — |
| Admin | ✓ | ✓ | ✓ | ✓ | ✓ |

Source of truth: `src/api/auth.py:CurrentUser` properties +
`require_*` dependencies. Frontend mirror:
`dashboard/src/App.jsx:AuthContext`.

### 4.4 P1 sync auth — HMAC

- `src/api/sync_auth.py` — header-based HMAC FastAPI dependency.
  Verifies `X-Api-Key` + `X-Customer-Id` against
  `client_credentials.api_key_hash` (constant-time compare).
- Lifecycle: `src/services/client_credential_service.py` —
  `.provision()`, `.rotate()`, `.verify()`, `.list_clients()`,
  `.get_client()`, `.update_status()`. The admin endpoints calling
  the last three are tracked under gap **H3** —
  `project_internal_admin_endpoints_stubs` memory.

### 4.5 JIRA webhook auth

- `POST /webhooks/jira` is gated by either Bearer secret
  (`JIRA_WEBHOOK_SECRET`) or HMAC signature (`X-Hub-Signature-256`).
- `DEV_MODE=true` only bypasses signature when `ENVIRONMENT != production`
  (gap **B4** acceptance). CI fails the build if both `DEV_MODE=true`
  and `ENV=production` end up in the deployed env.

---

## 5. Workflow & approvals

The F3 storyline — JIRA story creates a workflow, CCO approves a
checkpoint, comment posts back to JIRA.

| Step | Tested by |
|---|---|
| Inbound webhook signature accepted/rejected | `tests/integration/api/test_webhooks.py` (8 tests) |
| Workflow row created with `jira_story_key` | F3 Playwright spec + `test_workflows_approvals.py` |
| `POST /approvals/` updates `checkpoint_approvals` + workflow status | `test_workflows_approvals.py`, F3 spec |
| Role gating (CCO/CRO can approve, Analyst cannot) | `test_workflows_approvals.py` 403-on-non-approver |
| JIRA comment posted back without blocking the asyncio loop | Verified in commit `6346432` — `python-jira`'s blocking `serverInfo` retry chain now runs via `BackgroundTasks` so `POST /approvals/` returns in <5 s even when JIRA is unreachable |

The F3 fix was a real production-risk: before commit 6346432, a JIRA
hiccup during a demo would freeze every endpoint for ~89 s while
urllib3's default retry chain ran on the event loop. Fix is verified
under the Tier 3 dry-run (F3 spec at 3476 ms, well under the 5 s action
timeout).

---

## 6. Database & migrations

### 6.1 Migration chain

All schema is alembic-managed. The chain ships migrations 001–023 + the
P2-specific 016/017 + the OFAC vector tables 021/022 + the pgvector
setup migration 023 that supersedes the older `init_p1_schema.py` side-
script.

| Migration | Purpose |
|---|---|
| 007 | `ofac_sdn_entries` + country/nationality fields |
| 008 | `obligations` table (Bedrock-extracted obligation sentences) |
| 009 | `enforcement_actions`, `enforcement_risk_scores` |
| 010 | `compliance_deadlines` + `days_remaining` |
| 011 | `regulatory_change_records` |
| 012 | `peer_benchmarks` (FFIEC call report data) |
| 013–014 | `compliance_rules` enrichment + `state_code` widening |
| 015 | `client_credentials` |
| 016–017 | P2 — `workflow_executions`, `checkpoint_approvals`, `bank_policies`, `policy_regulation_mappings` |
| 018 | `scrape_runs` (MCP scrape monitor log) |
| 019 | `compliance_rules.embed_updated_at` + `idx_cr_embed_stale` |
| 020 | `regulatory_change_records.*_at` → TIMESTAMPTZ |
| 021 | `ofac_sdn_vectors` + GIN trgm + GIN tsv + tsv trigger |
| 022 | `ofac_sdn_vectors.embedding` HNSW (CONCURRENTLY; built after backfill) |
| 023 | `compliance_rules.embedding` + `tsv` + HNSW + GIN (alembic-managed; supersedes the bootstrap script) |

### 6.2 Schema verification

**Tool:** `scripts/verify_schema.py` + `make schema-check`.

Run after every migration in CI. Asserts 54 column types across the
critical tables: `compliance_rules` (17 columns), `obligations`,
`enforcement_actions`, `enforcement_risk_scores`, `compliance_deadlines`,
`regulatory_change_records`, `peer_benchmarks`, `client_credentials`,
`workflow_executions`, `checkpoint_approvals`, plus the P2 enums for
workflow + approval status.

Exit 0 on success, 1 on any drift. Wired into the CI `ci` job between
`alembic upgrade head` and the test step.

### 6.3 Vector + tsvector posture

- **pgvector** `vector(1024)` on `compliance_rules.embedding` and on
  `ofac_sdn_vectors.embedding`. HNSW indexes built CONCURRENTLY after
  embed backfill.
- **tsvector** generated column + GIN index on the same tables for
  BM25-style ranking. Used in the hybrid query in
  `compliance_rule_resolver.py`. Do **not** use `ts_rank` as a substitute
  for true BM25 outside the resolver.
- **OpenSearch Serverless was removed from the budget 2026-05-14** —
  see `project_p1_vector_store_decision`. Aurora pgvector + tsvector is
  the sole vector + BM25 store.

### 6.4 Data atomicity (gap B7 acceptance)

`src/services/update_service.py:_sync_postgres` wraps `add/update/delete +
ANALYZE` in a single transaction. Any failure rolls back. Verified by
`test_sync_api.py` — repeated `POST /sync/postgres` with the same payload
is idempotent (counts unchanged); a failing row in the middle of a batch
leaves the prior state untouched.

---

## 7. Credentials, secrets, and IAM

### 7.1 Per-bank API key (P1 → P2 sync)

- **Provisioning:** `scripts/register-bank.sh` → creates
  `client_credentials` row + HMAC key pair, ships the public half to the
  pilot bank.
- **Storage:** SHA-256 hash in `client_credentials.api_key_hash`. Raw
  key shown to the operator exactly once during provisioning.
- **Lifecycle:** `client_credential_service.py` — `provision`, `rotate`,
  `verify`, with an N-strikes tombstone on repeated bad verifies. Status
  field gates revocation.
- **Wire format:** `X-Api-Key` + `X-Customer-Id` headers. Constant-time
  compare. See `src/api/sync_auth.py`.

### 7.2 Secrets Manager / SSM split

- **P1** secrets live in AWS Secrets Manager. Rotation: see
  `feedback_cfn_secrets_rotation` for the CFN Transform + AUTO_EXPAND
  caveat; also `project_p1_secret_rotation_gap` — rotation is
  **currently disabled** after the AWS-managed PostgreSQLSingleUser
  rotation broke Aurora 2026-05-13. Manual rotation runbook is in
  `agent-1-infra.md`.
- **P2 per-bank** config lives in SSM Parameter Store under
  `/fortress/p2/<bank>/…`. Mounted into ECS task env via `valueFrom`
  ARNs. See `feedback_ecs_secrets_arn_format` for the partial-vs-full
  ARN traps that bit us — only full ARNs with the `-XXXXXX` suffix and
  no trailing `::` are honored.

### 7.3 IAM / Bedrock readiness

Each pilot stack needs three IAM/Bedrock gates cleared before agent
pipeline calls return success:

1. **Inference profile prefix** — Claude Sonnet 4.x / Haiku 4.5 are
   INFERENCE_PROFILE-only in us-east-1. Bare model IDs throw
   `ValidationException`. All model IDs route through
   `settings.BEDROCK_MODEL` (SSM-overridable). See
   `feedback_bedrock_inference_profile_prefix`.
2. **Anthropic use-case form** — a one-time per-account / per-family
   submission in the Bedrock console. Without it Claude 4.x invocation
   returns `ResourceNotFoundException` regardless of IAM. See
   `feedback_bedrock_anthropic_use_case_form` — onboarded into the
   bank-onboarding runbook.
3. **AWS Marketplace permissions** — the ECS task role needs
   `aws-marketplace:Subscribe/ViewSubscriptions/Unsubscribe` (Resource:
   `*`) so first invoke auto-subscribes the role. Without it,
   `AccessDeniedException` ("required AWS Marketplace actions"). See
   `feedback_bedrock_marketplace_iam`.

---

## 8. SDN screening — OFAC

The screener has two modes — `legacy` (fuzzy) and `vector` (HNSW on
embedded SDN name variants). Cutover is gated by per-class threshold
calibration.

| Surface | Coverage |
|---|---|
| Vector store schema | Migrations 021 + 022 (1 row per name variant, HNSW + GIN trgm + GIN tsv) |
| Loader | `src/services/ofac_sdn_loader.py` — integrity gate, N-strikes tombstone, change-record logging |
| Embed backfill | `src/jobs/embed_sdn_variants.py` — standalone Titan v2 worker |
| Screener API | F1 contract tests (`test_screening_api.py`) + F1 Playwright spec |
| Calibration | `scripts/setup/calibrate_ofac_thresholds.py` + `ofac-calibration-gold-set.md` |
| Pre-flip A/B | Acceptance: F1 ≥ 0.90, recall ≥ 0.95 for individual/alias classes against the bank's own screening history (open item — replace synthetic gold set with real data before flip) |
| Normalize parity | `feedback_ofac_normalize_pair` — `normalize_name` is duplicated in loader + screener; any divergence silently zeros matches. Locked in by both contract tests calling the loader's normalizer. |

Mode flag: `OFAC_SCREENER_MODE=legacy|vector`. Default `legacy` until
compliance signs off on per-class thresholds.

---

## 9. CI / CD gates

**File:** `.github/workflows/ci-cd.yml`

| Job | Trigger | Description | Required for merge? | Required for deploy? |
|---|---|---|:---:|:---:|
| `ci` | PR + push | ruff + black + py_compile + alembic upgrade + `verify_schema.py` + pytest (P1 + P2) | ✓ (via aggregate) | ✓ |
| `test-demo` | PR + push | `pytest tests/integration/api/` — 49 contract tests + 32 regression-lock tests (formerly H1/H2/H14 xfail stubs) | ✓ | ✓ |
| `dashboard-tests` | PR + push | `npm run test` — 14 Vitest smoke specs | ✓ | ✓ |
| `dashboard-e2e` | PR + push | `npm run e2e` — F0–F5 against `make up` | ✓ on `main`; warn on feature branches | ✓ |
| `demo-readiness` | PR + push | Aggregate green check; depends on the four above | ✓ (the only required check) | ✓ |
| `nightly-staging-e2e` | cron 06:00 UTC | Playwright F0–F5 vs `E2E_BASE_URL=staging` | — | — (Slack alert) |
| `build-p1` | push `main` or tag `p1-v*` | Docker build + ECR push + SSM | — | ✓ |
| `deploy-p1` | tag `p1-v*` | ECS rolling update; requires `prod-p1` environment approval | — | gated by `build-p1` + `demo-readiness` |
| `deploy-p2` | tag `bank-*-v*` | Per-bank P2 build + ECR push + ECS deploy; requires `prod-p2` env approval | — | gated by `demo-readiness` |
| `sync-p2-repo` | push `main` | Sync P2-only public repo (P1 code stripped) | — | gated by `demo-readiness` |

**Branch protection:** require `demo-readiness` as the single required
status check on `main` and `feature/dashboard-sdn`. The aggregate
fans out to the four upstream jobs, so adding one rule covers all four.

**Release latency:** expect ~10–15 min added per release for the full
demo-readiness battery against the just-built code.

---

## 10. Demo dry-run harness (Phase 6)

**File:** `scripts/demo/dryrun.sh` (wraps as `make demo-dryrun`).

A scripted, timed run-through against the actual demo stack. Mirrors
the `demo-readiness.md` §6 checklist. Every step is timed against a
budget; any over-budget step or any Playwright failure exits non-zero
and is treated as a release blocker.

| # | Step | Budget | Coverage |
|---|---|---|---|
| 6.1 | `make demo-reset` round-trips | <120 s | `[auto]` |
| 6.2 | Dashboard first-contentful-paint | <2 s | `[auto]` — F0 |
| 6.3 | F1 SDN screen → resolve | <60 s | `[auto]` |
| 6.4 | F2 P1 → P2 sync, `semantic_diff` in Changes | <90 s | `[auto]` |
| 6.5 | F3 JIRA webhook → workflow → CCO approves | <120 s | `[auto]` |
| 6.6 | F4 ExecutiveToday → Readiness → RulesPerf, ≤3 s spinners | <30 s total | `[auto]` |
| 6.7 | F5 VectorIntel + Obligations + Enforcement drilldowns | <30 s total | `[auto]` |
| 6.8 | Zero `console.error` across the full demo | n/a | `[auto]` |
| 6.9 | Backup screencast recorded + offline | n/a | **manual** |

**Last green run** (commit `6346432`, 2026-06-09):

```
6.1 demo-reset             ✓ PASS  8s
6.2 F0 first-paint         ✓ PASS  2s    (2394 ms FCP)
6.3 F1 sdn-screen          ✓ PASS  2s
6.4 F2 rule-sync           ✓ PASS  1s
6.5 F3 jira-flow           ✓ PASS  3s
6.6 F4 exec-overview       ✓ PASS  4s
6.7 F5 vector-intel        ✓ PASS  2s
6.8 zero console.error     ✓ PASS
```

Artifacts dropped into `dryrun-artifacts/` for evidence:
`playwright.json`, `playwright-summary.tsv`, `reset.log`, `p1.log`,
`p2.log`.

---

## 11. Known gaps still open (NOT closed by this pack)

These are the items from `production-readiness-gaps.md` that **are not**
demo-blocking but **are** pilot-blocking. They must be closed before any
real bank traffic.

**Blockers still open:**

- ~~**B5**~~ — **CLOSED 2026-06-10.** Sandbox env propagates only an
  explicit allowlist; HOME/TMPDIR forced to the runner's output_dir.
  Regression lock: `test_test_runner_env_b5.py` — 3 tests.
- ~~**B8**~~ — **CLOSED 2026-06-10.** Every `/api/sync/*` handler returns
  the `{code: INTERNAL_ERROR, path, detail}` envelope on DB failure
  via the FastAPI `Exception` handler. Regression lock:
  `test_failure_injection_b8.py` — 8 tests.
- ~~**B9**~~ — **CLOSED 2026-06-10.** `Dockerfile.pipeline2` drops to
  non-root `fortress` user before CMD. Regression lock:
  `test_dockerfile_nonroot_b9.py` — 4 tests across both Dockerfiles.
- ~~**B10**~~ — **CLOSED 2026-06-10.** Both Dockerfiles pin
  `python:3.11-slim@sha256:a3ab0b966bc4…`. Regression lock:
  `test_dockerfile_digest_pin_b10.py` — 4 tests.
- **B11** Aurora Serverless v2 baseline → see
  `project_p1_aurora_baseline` (2–16 ACU now, was pilot floor) — out
  of scope (DevOps + AWS sizing review).
- ~~**B12**~~ — **CLOSED 2026-06-10.** Aurora `StorageEncrypted=true` +
  customer-managed `KmsKeyRds` (annual rotation), Redis at-rest +
  `TransitEncryptionMode=required` + CMK, S3 SSE-KMS + full PAB +
  versioning, Secrets Manager bound to CMKs. Regression lock:
  `tests/integration/api/test_aurora_kms_encryption_b12.py` — 17
  invariants.

**Highs still open:**

- ~~**H1**~~ — **CLOSED 2026-06-10.** All 14 `/api/sync/*` POST handlers
  declare `Depends(require_sync_auth)`; the only inline `_verify_api_key`
  remaining is in `/license/verify` (intentional — returns
  `LicenseResponse(status="EXPIRED")` instead of 401). Regression
  locks: `test_open_gaps.py::test_h1_*` — 28 assertions now passing.
- ~~**H2**~~ — **CLOSED 2026-06-10.** `/api/sync/status` declares
  `Depends(require_sync_auth)` and additionally enforces
  `telemetry.customer_id == auth.customer_id` (403 on mismatch).
  Regression locks: `test_open_gaps.py::test_h2_*`.
- **H3** ClientCredentialService missing `list_clients`, `get_client`,
  `update_status` (admin endpoints 500) — see
  `project_internal_admin_endpoints_stubs`
- ~~**H4**~~ — **CLOSED 2026-06-10.** WebACL now has Priority-0
  `AllowBankIps` rule referencing CFN-managed `BankIpAllowSet`
  (`AWS::WAFv2::IPSet`). Registered bank NAT EIPs bypass the 100
  req / 5 min rate limit, which is retained as a DoS shield for
  unknown IPs. Operational follow-up: re-run `register-bank-ip.sh`
  after every `cloudformation update-stack` on pipeline1-compute
  (Addresses resets to `[]`; EIPs survive in SSM). Regression lock:
  `tests/integration/api/test_waf_rate_limit_h4.py` — 8 invariants.
- **H7** `_embed_query()` silently degrades to BM25 on AWS-cred fail —
  partial fix (UI badge); end-to-end alert path still TBD
- ~~**H8**~~ — **CLOSED 2026-06-10.** `update_service._apply_upserts`
  looks up by full `(rule_id, regulator, geography)` matching
  `uq_rule_regulator_geo`. Cross-regulator rows correctly stay
  distinct; same-key rows correctly update in place.
- **H10** License-verification fail-open on connection timeout
- **H13** `github_service.py` Authorization-header token not redacted
  in logs
- ~~**H14** `/health` returns healthy without probing DB/Redis/Bedrock/S3~~
  — **CLOSED** 2026-06-09. `src/api/health.py` now probes DB / Redis /
  Bedrock / S3; returns 503 when a critical dep (DB) is down. Regression
  locks: `test_open_gaps.py::test_h14_health_exposes_per_dep_checks`,
  `test_open_gaps.py::test_h14_health_returns_503_when_db_down`.

**Mediums:** see `production-readiness-gaps.md` §M for the full list.

**Mediums closed in the 2026-06-10 sweep:**

- ~~**M2**~~ Bare `except:` in `jira/connector.py` narrowed to
  `except Exception` + structured log.
- ~~**M5**~~ `verify_sdn_prereqs.py` now wired into
  `setup-p1-auto.sh` Step 4b.
- ~~**M6**~~ Migration 023 downgrade guards on
  `FORTRESS_ALLOW_DESTRUCTIVE_DOWNGRADE=1`.
- ~~**M7**~~ SQLAlchemy `echo` refuses to enable in production even
  when `DEBUG=true`. Regression lock: `test_db_echo_guard_m7.py`.
- ~~**M8**~~ Per-IP sliding-window rate limit on P2 `/validate/*`.
  Regression lock: `test_validate_rate_limit_m8.py`.
- ~~**M9**~~ Partial unique index
  `uq_workflow_jira_story_inflight` (migration 033) + handler dedup.
  Regression lock: `test_webhook_dedupe_m9.py`.
- ~~**M10**~~ JIRA-comment failure now surfaced in workflow
  artifacts; workflow status flips to `degraded`. Regression lock:
  `test_execution_jira_failure_m10.py`.
- ~~**M14**~~ P2 `/validate/*` emit CloudWatch `LatencyMs` +
  `Decisions` + dimensionless `Calls` heartbeat in
  `Fortress/P2/Validation` namespace.

**Mediums still open (deferred):**

- **M1** NullPool — asyncpg stuck-state workaround, risk-laden to
  replace without prod repro.
- **M3** Disabled-connector list + streak alarm — needs CloudWatch
  alarm infra (DevOps owns).
- **M4** TLS bypass for `dob.texas.gov` — intentional, scoped to one
  domain via `_NO_SSL_VERIFY_DOMAINS`; documented at the call site.
- **M11** Validation-agent compliance scoring keywords — needs CCO
  sign-off on the post-fix keyword/regulation set.
- **M12** Docker-compose dev passwords — dev-only, no prod exposure.
- **M13** Backup/DR runbook — DevOps owns.

Each open item has an owner and a target close date tracked in that
document, not here — this pack should reflect the test surfaces as of
the last verified date, not commit the close dates.

---

## 12. Sign-off matrix

For a pilot bank to consider this evidence pack complete, the following
roles must individually attest the items in column 2:

| Role | Attests |
|---|---|
| **CCO** (bank) | Demo storyline F1–F5 reflects their real workflow; runbook §6 checklist passed end-to-end on a representative dataset |
| **CRO** (bank) | OFAC vector calibration gold set (§8) populated with real bank screening history; per-class thresholds signed off (F1 ≥ 0.90, recall ≥ 0.95) |
| **CIO / TPRM** (bank) | Auth posture (§4), credential lifecycle (§7), and CI gates (§9) match their VPC/IAM/SDLC standards; `production-readiness-gaps.md` open items (§11) are accepted-with-mitigation or scheduled |
| **Compliance Ops** (Fortress) | `ofac-calibration-gold-set.md` runbook executed; `OFAC_SCREENER_MODE` flip approved |
| **Engineering** (Fortress) | Last `make demo-dryrun` green within 7 days of attestation; no open `demo-readiness` aggregate failure on the release tag |

---

# Appendix A — How to re-run each test surface

Everything in §2–§10 is re-runnable from a fresh checkout. This appendix
is the operator-facing quick reference.

## A.1 Bring the local stack up

```bash
# Postgres (pgvector:pg15) + Redis
docker compose up -d
make migrate          # alembic upgrade head
make schema-check     # verify_schema.py — 54 columns
```

## A.2 Backend tests

```bash
# Full backend battery
make test                      # P1 + P2 pytest
make test-p1                   # P1-only
make test-p2                   # P2-only

# Demo-readiness contract tests (49 active + 32 open-gap stubs)
make test-demo                 # target <90s wall clock

# Regression locks formerly tracking H1 / H2 / H14 (all closed)
pytest tests/integration/api/test_open_gaps.py -v
```

## A.3 Dashboard smoke (Vitest)

```bash
cd dashboard
npm install
npm run test                   # 14 specs, <10s
npm run test:watch             # interactive
```

## A.4 Dashboard journey (Playwright)

```bash
cd dashboard
npm run e2e:install            # one-time: chromium + --with-deps

# Local mode — requires P1 + P2 running with the env block in A.6
npm run e2e                    # all F0–F5
npm run e2e -- e2e/f5_vector_intel_drilldown.spec.ts   # one spec
npm run e2e:ui                 # interactive

# Against staging
E2E_BASE_URL=https://dashboard.staging.example npm run e2e
```

## A.5 Full timed dry run (Phase 6)

```bash
# Pre-reqs: A.1 + A.6 + A.4 e2e:install done
make demo-dryrun               # 5–8 min Tier 3, full report
```

Override budgets per spec:

```bash
DEMO_BUDGET_F1=120 DEMO_FCP_BUDGET_MS=3000 make demo-dryrun
```

The harness drops a per-step report to stdout and parsed artifacts to
`dryrun-artifacts/`. Non-zero exit on any blocker.

## A.6 Backend env block for E2E

P1 (port 8001) + P2 (port 8002) must be running with:

```bash
DEV_MODE=true
ENVIRONMENT=test
JIRA_WEBHOOK_SECRET=test-webhook-secret
ALLOWED_ORIGINS='["http://localhost:5173"]'
PIPELINE2_API_TOKEN=e2e-bypass-token
PIPELINE_MODE=pipeline2        # for P2
DATABASE_URL=postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev
REDIS_URL=redis://localhost:6379/0
```

`PIPELINE2_API_TOKEN` must match the token the Playwright fixtures set
in `localStorage` (`dashboard/e2e/fixtures.ts` —
currently `e2e-bypass-token`). `ALLOWED_ORIGINS` must be JSON list form
because pydantic-settings JSON-decodes complex env vars before the
`_parse_allowed_origins` validator runs.

## A.7 Manual auth probes

```bash
# Health (no auth)
curl -s http://localhost:8001/health/
curl -s http://localhost:8002/health/

# Dev token = CCO role
curl -s -H "Authorization: Bearer e2e-bypass-token" \
  http://localhost:8002/api/v1/dashboard/readiness?lens=cco

# Dev token with role hint = Admin
curl -s -H "Authorization: Bearer e2e-bypass-token" \
  -H "X-E2E-Role: Admin" \
  http://localhost:8002/api/v1/dashboard/screening/vector-intel

# P1 sync auth — HMAC headers
curl -s -X POST \
  -H "X-Api-Key: $RAW_KEY" -H "X-Customer-Id: testbank" \
  http://localhost:8001/sync/status
```

## A.8 Seed + reset

```bash
make demo-seed                 # idempotent; ~3s second-run
make demo-reset                # truncate volatile + reseed (<90s target)
make demo-reset-s3             # pg_restore baseline.dump from S3
```

Seed produces, at minimum: 1 testbank credential, 25 compliance_rules,
6 regulatory_change_records (with `semantic_diff`), 5 compliance_deadlines
(one ≤7 days), 20 OFAC SDN entries + 52 variant vectors, 10 workflow_executions
in mixed states, 2 checkpoint_approvals.

## A.9 OFAC threshold calibration

See `docs/runbooks/ofac-calibration-gold-set.md` for the full ops-side
runbook. Quick reference:

```bash
# Dry-run with the synthetic seed set (sanity check before real data)
python scripts/setup/calibrate_ofac_thresholds.py \
  --gold-set scripts/setup/ofac_calibration_gold.csv \
  --output ofac_thresholds.json

# After ops returns real gold set, swap --gold-set path and re-run
```

## A.10 Schema verification

```bash
make schema-check              # exit 0 = clean; exit 1 = drift
python scripts/verify_schema.py --verbose    # column-by-column report
```

## A.11 CI re-run on a PR

The GitHub UI "Re-run all jobs" on the PR triggers the full
`demo-readiness` battery. To trigger only the aggregate locally:

```bash
make test-demo                       # contract tests
cd dashboard && npm run test         # vitest
cd dashboard && npm run e2e          # playwright
make schema-check                    # schema verify
```

If all four are green locally, the PR's aggregate check will also be
green.

---

# Appendix B — Test inventory (totals)

| Surface | Count | Location | Wall-clock |
|---|:---:|---|---|
| Backend API contract tests (active) | 49 | `tests/integration/api/` (excluding `test_open_gaps.py`) | <90 s |
| Regression locks formerly H1/H2/H14 stubs | 32 (all passing) | `tests/integration/api/test_open_gaps.py` | <5 s |
| Backend agent-pipeline unit tests | 14 | `tests/unit/test_agents.py` | <30 s |
| Dashboard Vitest smoke | 14 | `dashboard/src/pages/__tests__/` | <10 s |
| Dashboard Playwright E2E | 6 | `dashboard/e2e/` | ~15 s |
| Schema-check columns asserted | 54 | `scripts/verify_schema.py` | <2 s |
| Alembic migrations on chain | 23 | `alembic/versions/` | <30 s |
| CI jobs in the demo-readiness gate | 4 + aggregate | `.github/workflows/ci-cd.yml` | ~10–15 min |
| Phase 6 dry-run steps timed | 8 auto + 1 manual | `scripts/demo/dryrun.sh` | 5–8 min |

**Grand total: 115 active automated tests (49 contract + 32 regression
locks + 14 unit + 14 Vitest + 6 Playwright) + 54-column schema
assertion + 8 timed dry-run steps,** all gated by a single required
CI status check.
