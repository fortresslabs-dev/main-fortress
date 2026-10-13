# Demo Readiness — End-to-End Test Plan

**Goal:** Get P1 + P2 + dashboard to a state where a 30–45 minute live customer
demo runs without 500s, blank screens, or auth surprises.

**Out of scope for *demo* (still required for *pilot*):** the full B1–B12 / H1–H12
list in `production-readiness-gaps.md`. This plan picks the subset that is
visible to a customer in a browser or a curl during the demo. Items deferred to
pilot are tagged `→ pilot`.

**Owners:** Agent 2 (P1 API + sync), Agent 4 (P2 dashboard + workflows),
Agent 3 (CI gates).

**Timeline:** ~8 working days of focused work. Suggested ordering: Phase 1 → 2 →
3 → 5 in parallel with 4 → 6.

---

## 0. Demo storyline (the only flows we test)

Lock this list before writing tests. Any test for a flow not on this list is
deferred.

| # | Flow | Pages | Endpoints |
|---|------|-------|-----------|
| F1 | **SDN screen → match → resolve** | `SdnSearch.jsx`, `OfacHealth.jsx` | `POST /screening/sdn`, `GET /screening/sdn/{id}`, `GET /dashboard/ofac-health` |
| F2 | **Rule sync from P1 → dashboard "Changes" tab** | `Changes.jsx`, `Rules.jsx` | `POST /sync/postgres`, `POST /sync/change-records`, `GET /dashboard/changes`, `GET /dashboard/rules` |
| F3 | **JIRA webhook → workflow → CCO approval** | `Workflows.jsx`, `Cases.jsx` | `POST /webhooks/jira`, `GET /workflows/{id}`, `POST /approvals/` |
| F4 | **Executive landing — overview + readiness + risk** | `ExecutiveToday.jsx`, `Readiness.jsx`, `RulesPerf.jsx` | `GET /dashboard/overview`, `/readiness`, `/risk`, `/coverage`, `/risk-matrix` |
| F5 | **Vector intel + obligations drilldown** | `VectorIntel.jsx`, `Obligations.jsx`, `Enforcement.jsx` | `GET /dashboard/obligations`, `/enforcement`, `/cro`, `/cio` |

Pages **not in scope for demo** (render-only smoke, no journey test): `Investigation.jsx`, `Reports.jsx`.

---

## 1. Phase 1 — Demo-blocking subset of `production-readiness-gaps.md`

Pull these *forward* of all other prod-readiness work. Reference the gap doc for
exact line numbers.

| ID | Why demo-blocking | Acceptance |
|----|-------------------|------------|
| B2  | CORS `["*"]` + `allow_credentials=True` will fire console errors visible to anyone watching DevTools | `src/utils/config.py` reads `ALLOWED_ORIGINS` from SSM; `main_pipeline2.py:75-78` uses explicit list; preflight from demo origin returns 200, others 403 |
| B3  | JWT plaintext fallback bypasses auth if any one env var is missing on the demo stack | `src/api/auth.py:119-124` raises on startup if `COGNITO_USER_POOL_ID` empty in non-dev mode; `DEV_MODE` defaults to `false` |
| B4  | Webhook auth disabled if `DEV_MODE=true` leaks into demo stage | `src/api/webhooks.py:60-62` gates on env != `production`; CI fails build if `DEV_MODE=true` and `ENV=production` |
| B6  | Sync daemon crashes mid-demo and never recovers if P1 hiccups | `update_service.py:337` wraps `raise_for_status` in try/except with exponential backoff and structured log |
| B7  | Mid-demo partial sync = inconsistent rule list shown on screen | `update_service.py:345-378` wraps add/update/delete + ANALYZE in single transaction; rollback on any failure |
| H5  | First call after a fresh test DB returns NULL `last_sync_version` and breaks the demo "what changed" panel | `SyncVersion` row created at startup if missing |
| H7  | If Bedrock creds wobble during demo, search silently falls back to BM25 with no UI marker | `compliance_rule_resolver.py:414-440` emits warning log + returns `source="pgvector_degraded"`; dashboard shows a small badge |

**Deferred to pilot:** B1 (token default), B5 (test runner env leak), B8 (sync_api try/except sweep), B9–B12 (container hardening, encryption, ACU sizing beyond demo profile).

---

## 2. Phase 2 — API contract tests (pytest)

**Target:** every endpoint in the F1–F5 table gets two tests — happy path +
auth-fail. ~30 tests total.

**Infrastructure:**
- Add `tests/integration/conftest.py` with a `seeded_db` fixture: tear up
  `pgvector/pgvector:pg15` via testcontainers, run `alembic upgrade head`, load
  fixtures from §5.
- New file per router: `tests/integration/api/test_<router>.py`.
- Hit real Aurora-compatible Postgres (not mocks). Reason: see memory
  `feedback_init_p1_schema_destructive` — schema-level bugs only surface against
  a real migration chain.
- Add `make test-demo` → runs only Phase 2 + Phase 3 tests, target <90 s wall
  clock.

**File map:**

```
tests/integration/api/
├── conftest.py                       # seeded_db, p1_client, p2_client, jwt(), hmac_headers()
├── test_screening_api.py             # F1 — POST /screening/sdn, GET /screening/sdn/{id}
├── test_dashboard_screening.py       # F1 — GET /dashboard/ofac-health
├── test_sync_api.py                  # F2 — postgres, change-records, status, counts
├── test_dashboard_api.py             # F2/F4/F5 — overview, rules, changes, readiness, risk,
│                                     #         coverage, obligations, enforcement, cro, cio
├── test_webhooks.py                  # F3 — POST /webhooks/jira (sig pass + sig fail)
├── test_workflows_approvals.py       # F3 — workflows GET, approvals POST + RBAC fail
└── test_health.py                    # /health, /health/sync, /health/ready
```

**Contract assertions to enforce (regression prevention, not new behavior):**

- Auth fail returns 401, not 500, and never opens a DB session (gap H1).
- Every list response has stable `{items, total, page}` shape.
- `/dashboard/changes` and `/dashboard/change_log` include `semantic_diff` (regression — `27c3bae` shipped it).
- `change_summary` on OFAC change records is a sentence string, not a JSON envelope (regression — `5b2096b`).
- `/sync/postgres` is idempotent: same payload twice → second call is a no-op, counts unchanged.
- `/sync/initial` updates `SyncMetadata.last_sync_version` on every page, not just last (gap H6).

---

## 3. Phase 3 — Dashboard smoke + journey tests

`dashboard/package.json` has no test runner today. Add:

```json
"scripts": {
  "test":     "vitest run",
  "test:ui":  "vitest",
  "e2e":      "playwright test",
  "e2e:ui":   "playwright test --ui"
}
```

**Vitest (smoke):** one test per page. Render the page with a mocked
`dashboard/src/services/api.js` and assert it does not throw and renders a
known header string. ~14 tests, <10 s wall clock.

```
dashboard/src/pages/__tests__/
├── ExecutiveToday.test.jsx
├── Changes.test.jsx
├── Rules.test.jsx
├── SdnSearch.test.jsx
├── OfacHealth.test.jsx
├── Workflows.test.jsx
├── Cases.test.jsx
├── Readiness.test.jsx
├── RulesPerf.test.jsx
├── VectorIntel.test.jsx
├── Obligations.test.jsx
├── Enforcement.test.jsx
├── Investigation.test.jsx
└── Reports.test.jsx
```

**Playwright (journey):** one spec per F1–F5 flow against `make up` + real P1/P2.
Run against staging in CI nightly; run against local for dev.

```
dashboard/e2e/
├── f1_sdn_screen.spec.ts
├── f2_rule_sync_changes.spec.ts
├── f3_webhook_to_approval.spec.ts
├── f4_executive_overview.spec.ts
└── f5_vector_intel_drilldown.spec.ts
```

**Acceptance for each journey:**
- Page loads <2 s on staging (T-2 dry-run will catch >2 s).
- No 4xx/5xx in network panel during the journey.
- No `console.error` from React (proxy via `page.on('console')`).
- Screenshot captured on failure → uploaded as CI artifact.

**Time-box fallback:** if Phase 3 slips, drop Vitest entirely and ship only
Playwright. The 14-page render smoke is nice-to-have; the 5 journeys are
demo-critical.

---

## 4. Phase 4 — Demo fixtures + reset script

The demo must run identically twice in a row. Today there is no seeded demo
state.

**Deliverables:**

1. `scripts/demo/seed.py` — idempotent loader that creates:
   - 1 bank: `testbank` (matches memory `project_p2_test_bank_id`)
   - 1 CCO user + JWT
   - ~20 SDN matches across name classes (individual, alias, entity, vessel)
     drawn from `tests/golden/`
   - 5 workflow_executions in mixed states: 2 awaiting CCO approval, 1 approved
     today, 1 rejected, 1 in-progress
   - 3 regulatory_change_records dated within last 7 days, all with
     `semantic_diff` populated
   - 10 compliance_rules touching CFR 1010, OFAC, BSA/AML
   - 2 compliance_deadlines (one ≤7 days out for the "urgent" panel)
2. `scripts/demo/reset.sh` — `pg_restore` from a snapshot in S3
   `s3://fortress-demo/snapshots/baseline.dump`, restart ECS service.
3. `make demo-seed` and `make demo-reset` Makefile targets.
4. CI job that runs `seed.py` against an ephemeral DB nightly and posts a
   freshness report.

**Reset target:** under 2 minutes wall clock from `make demo-reset` to
"dashboard loads with seed data". Verified by stopwatch in §6.

---

## 5. Phase 5 — CI wiring ✓ (shipped)

`.github/workflows/ci-cd.yml` gates merges to `main` and
`feature/dashboard-sdn` and gates the tag-deploy chain (`p1-v*`,
`bank-*-v*`) on the new `demo-readiness` aggregate check.

| Job | Trigger | Blocks merge? | Blocks deploy? |
|-----|---------|---------------|----------------|
| `ci` (lint + migrate + schema-check + P1/P2 tests) | PR + push | yes | yes (via aggregate) |
| `test-demo` (`pytest tests/integration/api/`) | PR + push | yes | yes (via aggregate) |
| `dashboard-tests` (Vitest smoke, 14 pages) | PR + push | yes | yes (via aggregate) |
| `dashboard-e2e` (Playwright F1–F5 vs make up + P1 + P2) | PR + push | yes on `main`, warn on `feature/dashboard-sdn` | yes |
| `demo-readiness` (aggregate green check) | PR + push | yes | yes |
| `nightly-staging-e2e` (Playwright vs staging) | `cron: 0 6 * * *` | no | no, posts to `SLACK_WEBHOOK_URL` on failure |

**Branch-protection setup (one-time, GitHub UI):** require
`demo-readiness` as a required status check on `main` and
`feature/dashboard-sdn`. The aggregate fans out to the four
upstream jobs, so adding one rule covers all four.

**Tag-deploy gate:** `build-p1`, `deploy-p2`, and `sync-p2-repo`
all `needs: demo-readiness` — a `p1-v*` or `bank-*-v*` push will
not deploy unless the full battery (incl. Playwright e2e against
the just-built code) passes first. Expect ~10–15 min added to
each release.

**Secrets the workflow looks up (optional but recommended):**

- `SLACK_WEBHOOK_URL` — nightly failure alerts. Without it the
  nightly job still runs but only logs a warning on failure.
- `STAGING_JIRA_WEBHOOK_SECRET` — used by F3 against staging.
  Without it the F3 spec will fail signature verification.

**Feature-branch warn behavior:** `dashboard-e2e` uses
`continue-on-error: ${{ github.event_name == 'pull_request' && github.base_ref != 'main' }}`
so a flaky local Playwright run can't block a WIP review against
`feature/dashboard-sdn`. The hard gate still fires on the PR
back into `main` and on the release tag.

---

## 6. Phase 6 — Pre-demo dry run (T-2 days) ✓ (harness shipped)

A scripted, timed run-through against the actual demo stack. Treat any
deviation from the script as a blocker; do not "fix it live."

**Single-command harness:** `make demo-dryrun` (wraps
`scripts/demo/dryrun.sh`). It times every §6 step against the budget
below, prints a pass/fail report, and exits non-zero on any blocker.
Per-spec budgets are overridable via env (`DEMO_BUDGET_F1` …
`DEMO_BUDGET_F5`, `DEMO_FCP_BUDGET_MS`).

**Pre-reqs before running the harness:**

- `make up && make migrate` already run.
- P1 (8001) + P2 (8002) running with `DEV_MODE=true ENVIRONMENT=test
  JIRA_WEBHOOK_SECRET=test-webhook-secret
  ALLOWED_ORIGINS='["http://localhost:5173"]'
  PIPELINE2_API_TOKEN=e2e-bypass-token`. CORS, dev token, and JIRA
  signature are all non-optional — without them every dashboard XHR
  either fails preflight (gap B2), returns 503 from the auth layer
  (`auth.py:156`), or 401 from the webhook handler. The runtime guard
  in `config.py` only catches an empty allowlist in
  `ENVIRONMENT=production`, not `test`. `ALLOWED_ORIGINS` must be JSON
  list form because pydantic-settings JSON-decodes complex env vars
  before the `_parse_allowed_origins` validator runs. The
  `PIPELINE2_API_TOKEN` value must match the token the Playwright
  fixtures set in `localStorage` (`dashboard/e2e/fixtures.ts:33` —
  currently `e2e-bypass-token`).
- `cd dashboard && npm install && npm run e2e:install` already run. The
  `e2e:install` script uses `--with-deps` so the chromium binary AND its
  system libs (`libatk-1.0.so.0` etc.) are installed in one shot. If the
  script is ever changed to omit `--with-deps`, run
  `sudo npx playwright install-deps chromium` manually.

**Checklist (rows tagged `[auto]` are enforced by `make demo-dryrun`):**

| # | Step | Budget | Coverage |
|---|------|--------|----------|
| 6.1 | `make demo-reset` round-trips | <120 s | `[auto]` |
| 6.2 | Dashboard first-contentful-paint after login | <2 s | `[auto]` — `f0_first_paint.spec.ts` |
| 6.3 | F1: paste SDN-matching name → screen → resolve | <60 s | `[auto]` — `f1_sdn_screen.spec.ts` |
| 6.4 | F2: P1 → P2 sync from admin → Changes shows row + `semantic_diff` | <90 s | `[auto]` — `f2_rule_sync_changes.spec.ts` |
| 6.5 | F3: POST JIRA webhook → workflow lands → CCO approves | <120 s | `[auto]` — `f3_webhook_to_approval.spec.ts` |
| 6.6 | F4: ExecutiveToday → Readiness → RulesPerf, spinners ≤3 s | <30 s total | `[auto]` — `f4_executive_overview.spec.ts` |
| 6.7 | F5: VectorIntel <1 s; Obligations + Enforcement drilldowns render | <30 s total | `[auto]` — `f5_vector_intel_drilldown.spec.ts` |
| 6.8 | Zero `console.error` across the full demo | n/a | `[auto]` — every F0–F5 spec asserts via `assertCleanJourney` |
| 6.9 | Backup screencast recorded + offline on presenter's machine | n/a | **manual** — harness prints reminder |

The harness drops logs + the parsed Playwright summary into
`dryrun-artifacts/` (override with `DEMO_DRYRUN_ARTIFACTS=…`).

**Validating the harness before T-2 (do not skip):** the first real
dry-run should not be the first time anyone exercises `dryrun.sh`.
Walk the ladder below — each tier catches a different class of failure
before T-2 day.

| Tier | What it proves | How | Time |
|------|----------------|-----|------|
| 1 — parser smoke | Report renders, budget logic fires, missing-spec rows show as FAIL, exit code is correct | Feed a synthetic Playwright JSON: `mkdir -p dryrun-artifacts && printf '{"suites":[{"specs":[{"title":"F1 — fake","tests":[{"status":"expected","results":[{"duration":3000}]}]}]}]}' > /tmp/pw.json` then run the parser block of `scripts/demo/dryrun.sh` (or just drop the JSON in `dryrun-artifacts/playwright.json` and re-run with `make demo-reset` skipped) | <5 s |
| 2 — reset only | `make demo-reset` hits the <120 s budget against the actual seed; the §6.1 row is real | `make demo-reset` standalone (no Playwright). Confirm seed completes; check elapsed line | <90 s |
| 3 — full dry-run | Every §6 row exercised end-to-end | Boot P1 + P2 with full env block above (`DEV_MODE` + `ENVIRONMENT=test` + `JIRA_WEBHOOK_SECRET` + `ALLOWED_ORIGINS` + `PIPELINE2_API_TOKEN`), then `make demo-dryrun` | 5–8 min |

Run **Tier 1 + Tier 3** at minimum before T-2; Tier 2 catches reset
regressions in isolation and is the right place to start if a previous
run reported §6.1 FAIL.

**Roll-forward rule:** if any step fails the dry run, fix-or-remove. Do not
ship a demo step that has not passed at least one clean dry run. A
non-zero exit from `make demo-dryrun` is a release blocker.

---

## 7. What this plan explicitly does *not* cover

- Load / concurrency testing (→ pilot).
- Pen-test of auth surfaces beyond B2/B3/B4 (→ pilot; see
  `docs/runbooks/auth-security-baseline.md`).
- Disaster recovery drills, multi-region failover (→ pilot).
- Browser matrix beyond Chrome stable (the customer will use Chrome — verify
  presenter's machine; everything else deferred).
- Mobile / responsive layouts.
- Localization / i18n.
- Full B1–B12 closure. Track separately in `production-readiness-gaps.md`.

---

## 8. Exit criteria — "demo ready"

All of:

1. Phase 1 acceptance rows checked off.
2. `make test-demo` green; 100% of F1–F5 endpoints covered with happy + auth
   tests.
3. `cd dashboard && npm run e2e` green for all five `f*_*.spec.ts` files
   against staging.
4. `make demo-reset` round-trips in <2 min.
5. One full dry run (§6) completed end-to-end with zero blockers.
6. Backup screencast recorded and accessible offline on presenter's machine.

When all six are true, mark the branch and ship the demo.
