# Banking Production Readiness — Gap Analysis

**Status:** NOT READY for pilot bank traffic.
**Drilled:** 2026-06-03 across P1 (sync API + scraper), P2 (agents + validators + dashboard), and security/ops.
**Verdict:** 12 blockers, 14 high, 13 medium across three surfaces. Blockers must clear before pilot onboarding.

This document is the authoritative gap list. The "Current action items" section in `CLAUDE.md` covers infra deploy steps; this file covers the *code/security/observability* gaps surfaced by drilling into the source.

---

## 1. Blockers (must fix before any pilot)

### Auth / secrets

| # | Gap | Where | Impact |
|---|-----|-------|--------|
| B1 | `PIPELINE2_API_KEY` defaults to `"changeme-token"` | `src/utils/config.py` | Any leaked image/checkout = valid P2 token |
| B2 | CORS wildcard `["*"]` + `allow_credentials=True` + `allow_headers=["*"]` | `src/utils/config.py`, `src/main_pipeline2.py:75-78` | Cross-origin theft of dashboard/JWT sessions |
| B3 | JWT fallback to plaintext `_DEV_TOKEN` when `COGNITO_USER_POOL_ID` empty | `src/api/auth.py:119-124` | Prod deploy missing one env var silently disables auth |
| B4 | `DEV_MODE=true` disables JIRA webhook auth — no env-gate against prod | `src/api/webhooks.py:60-62` | Webhook forgery |
| ~~B5~~ | **CLOSED 2026-06-10.** `SandboxTestRunner._build_sandbox_env` propagates only an explicit allowlist (PATH, LANG, LC_ALL, TERM, PYTHONUSERBASE) and forces HOME/TMPDIR to the runner's output_dir + PYTHONDONTWRITEBYTECODE=1. PYTHONPATH is intentionally excluded so a generated test cannot `import src.*`. Regression lock: `tests/integration/api/test_test_runner_env_b5.py` (3 tests, asserts every known secret env name is filtered). | `src/services/test_runner.py:22-67` | — |

### Sync correctness

| # | Gap | Where | Impact |
|---|-----|-------|--------|
| B6 | `update_service.py` `resp.raise_for_status()` on `/sync/postgres` is uncaught; crashes daemon thread | `src/services/update_service.py:337` | Daily sync halts indefinitely on any P1 transient failure |
| B7 | Add/update/delete + ANALYZE not atomic — separate commits | `src/services/update_service.py:345-378` | Partial sync leaves rules table inconsistent |
| B8 | Zero try/except in any of 12 P1 sync endpoints; raw `_mapping` access on embeddings | `src/api/sync_api.py:170-710` (esp. 275) | Any DB hiccup → 500 to P2 customers |

### Container / infra

| # | Gap | Where | Impact |
|---|-----|-------|--------|
| B9 | `Dockerfile.pipeline2` runs as root (P1 correctly drops to `fortress:fortress` at line 35) | `Dockerfile.pipeline2:73` | Container breakout → host compromise |
| B10 | Base images unpinned (`python:3.11-slim`, no digest) | All Dockerfiles | Non-reproducible builds; supply-chain drift |
| B11 | Aurora Serverless v2 max 2 ACU (~4 GB), Redis `cache.t4g.micro` (1 GB) — labeled "pilot sizing" | `infra/cloudformation/pipeline1-data.yml:19-34` | Concurrent sync + scrape + embed will OOM |
| ~~B12~~ | **CLOSED 2026-06-10.** Aurora `StorageEncrypted=true` + customer-managed `KmsKeyRds` (annual rotation) already in `pipeline1-data.yml`; Redis at-rest + transit-mode=`required` + CMK; S3 SSE-KMS + full PAB + versioning; Secrets Manager bound to CMKs. Regression lock: `tests/integration/api/test_aurora_kms_encryption_b12.py` (17 invariants). | `infra/cloudformation/pipeline1-data.yml` | — |

---

## 2. High severity (1–2 sprints)

### P1 sync API

- ~~**H1**~~ — **CLOSED 2026-06-10.** All 14 protected `/api/sync/*` POST handlers in `src/api/sync_api.py` declare `auth: dict = Depends(require_sync_auth)`. The only inline `_verify_api_key` call left is in `/license/verify` (line 426), which intentionally returns a `LicenseResponse` with `status="EXPIRED"` rather than 401 — that's the documented contract for the license check endpoint. Regression locks: `tests/integration/api/test_open_gaps.py::test_h1_*` (28 assertions across 14 endpoints).
- ~~**H2**~~ — **CLOSED 2026-06-10.** `/api/sync/status` (`src/api/sync_api.py:374-377`) declares `Depends(require_sync_auth)`; the handler additionally enforces that `telemetry.customer_id` matches the authenticated identity, returning 403 on mismatch to stop a caller from poisoning telemetry for a customer they know but don't own. Regression locks: `test_open_gaps.py::test_h2_sync_status_rejects_unauthenticated` + `::test_h2_sync_status_accepts_valid_hmac`.
- **H3** — `ClientCredentialService` missing `list_clients()`, `get_client()`, `update_status()` — confirms memory `project_internal_admin_endpoints_stubs`. `GET /internal/admin/clients` still 500s.
- ~~**H4**~~ — **CLOSED 2026-06-10.** `BankIpAllowSet` (`AWS::WAFv2::IPSet`, REGIONAL, IPV4) is now CFN-managed in `pipeline1-compute.yml`, and the WebACL has an `AllowBankIps` rule at Priority 0 that references it via `IPSetReferenceStatement`. Registered bank NAT EIPs bypass the rate limit entirely; the 100 req / 5 min rate-limit rule (Priority 1, scope-down `/api/sync`) remains as a DoS shield for unknown IPs. `register-bank-ip.sh` updated for the new flow + redeploy trap (CFN update resets `Addresses` to `[]`, EIPs survive in SSM under `/fortress/banks/<bank>/nat-eip`). Regression lock: `tests/integration/api/test_waf_rate_limit_h4.py` (8 invariants).
- **H5** — `SyncVersion` initialization assumes `id=1` exists; no create-if-missing. First call after fresh schema returns NULL not 0. `src/api/sync_api.py:153-159`.
- **H6** — `/sync/initial` updates `SyncMetadata` only on last page (line 327). Mid-pagination customers see stale `last_sync_version`.

### P2 dashboard / validators

- **H7** — `compliance_rule_resolver.py` Tier 3 `_embed_query()` silently degrades to BM25 on AWS-cred failure; no log, no `source="pgvector_degraded"` marker. `src/services/compliance_rule_resolver.py:414-440`.
- ~~**H8**~~ — **CLOSED 2026-06-10.** `update_service._apply_upserts` looks up the existing row by the full compound key `(rule_id, regulator, geography)` matching the existing `uq_rule_regulator_geo` unique constraint (`src/models/database.py:117`). Same `rule_id` under different regulators correctly stays on separate rows; same `rule_id` under the same regulator+geography correctly updates in place. The gap doc's earlier suggestion to add `content_hash`/`sync_version` to the unique constraint was incorrect — that would *prevent* legitimate updates of the same rule across versions.
- **H9** — Validators hardcode thresholds (`OFAC_SIMILARITY_THRESHOLD=0.85`, `REQUIRED_1071_FIELDS`). P2 won't pick up P1 threshold changes without redeploy. `src/services/validators.py:31, 420-430`. Conflicts with the "drive everything from compliance_rules" design.
- **H10** — License-verification fail-open: connection timeout to P1 license endpoint returns `True`. Expired license undetectable. `src/services/update_service.py:1116-1134`.
- **H11** — Webhook generic `except Exception` marks workflow FAILED on transient errors with no retry. `src/api/webhooks.py:159-161`.
- **H12** — Dashboard PATCH endpoints (`/dashboard/rules/{id}/status`) and several GETs lack visible `require_cco` gates. `src/api/dashboard_api.py:16-26`. Audit all CCO-write endpoints.
- **H13** — `github_service.py` Authorization-header token not redacted in httpx logs. `src/services/github_service.py:20-25`.

### Cross-cutting

- ~~**H14**~~ — **CLOSED 2026-06-09.** `src/api/health.py` now probes DB / Redis / Bedrock / S3, returns 503 when a critical dep (DB) is down, and surfaces per-dep status under `checks`. Regression locks: `tests/integration/api/test_open_gaps.py::test_h14_health_exposes_per_dep_checks` and `::test_h14_health_returns_503_when_db_down`. Remaining sub-gap (`/health/sync` lacks `last_error`/`retry_count`/`next_scheduled_sync`) tracked under M-series next sprint.

---

## 3. Medium severity

- **M1** — `NullPool` on SQLAlchemy engine (workaround for asyncpg stuck-state); ~100ms per query, will exceed connection limits under load. `src/services/database.py:4, 23`.
- ~~**M2**~~ — **CLOSED 2026-06-10.** Bare `except:` narrowed to `except Exception` + structured log so the failure mode (auth vs. timeout vs. JSON) is distinguishable, and KeyboardInterrupt no longer swallowed.
- **M3** — Scraper has no disabled-connector list; broken connectors cycle indefinitely. No streak-failure alarm. `src/services/scraper_scheduler.py:374-391, 438`.
- **M4** — TLS verification disabled for `dob.texas.gov` via `_NO_SSL_VERIFY_DOMAINS`. `src/connectors/regulatory/usa/_scraper_utils.py:121`.
- ~~**M5**~~ — **CLOSED 2026-06-10.** `infra/scripts/setup-p1-auto.sh` now runs `scripts/setup/verify_sdn_prereqs.py` as Step 4b right after schema bootstrap; failure aborts deploy with a clear message before `/sync/ofac-sdn` traffic can flow.
- ~~**M6**~~ — **CLOSED 2026-06-10.** Migration 023 downgrade now guards on `FORTRESS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` and refuses to drop `compliance_rules.embedding` (which would discard ~25k vectors + force a multi-hour Titan re-embed) without an explicit ack.
- ~~**M7**~~ — **CLOSED 2026-06-10.** `src/services/database.py:init_db` computes `echo_sql = settings.DEBUG and settings.ENVIRONMENT not in {"production","prod"}`. A stray `DEBUG=true` on a prod stack no longer logs bound API keys / passwords. Regression lock: `tests/integration/api/test_db_echo_guard_m7.py` (5 tests).
- ~~**M8**~~ — **CLOSED 2026-06-10.** P2 `/validate/*` endpoints carry a sliding-window per-IP rate limit (default 60 req / 60 s, override via `FORTRESS_P2_VALIDATE_RPM`). 429 + Retry-After on breach. Regression lock: `tests/integration/api/test_validate_rate_limit_m8.py` (4 tests).
- ~~**M9**~~ — **CLOSED 2026-06-10.** Migration 033 adds partial unique index `uq_workflow_jira_story_inflight ON workflow_executions(jira_story_id) WHERE status IN ('pending','running')`. Handler reuses the in-flight row; concurrent INSERT race surfaces as `IntegrityError` and the handler resolves the existing row. Regression lock: `tests/integration/api/test_webhook_dedupe_m9.py` (2 tests).
- ~~**M10**~~ — **CLOSED 2026-06-10.** `_post_jira_comment` now returns `(posted_ok, error_or_None)`; execution result includes `jira_comment_attempted`/`jira_comment_posted`/`jira_comment_error` in artifacts and status flips to `"degraded"` when the comment fails. Regression lock: `tests/integration/api/test_execution_jira_failure_m10.py` (3 tests).
- **M11** — Validation-agent compliance scoring uses hardcoded OFAC/SAR/CTR keywords; non-AML rules under-score (note says "pending follow-up"). `src/agents/validation.py:40-56, 82-100`.
- **M12** — Docker-compose hardcodes `fortress_dev` passwords for Postgres + Redis in stdout. `docker-compose.pipeline1.yml:8, 23, 43`.
- **M13** — No backup/DR docs: no Aurora PITR window, snapshot retention, RTO/RPO, or restore-drill runbook.
- ~~**M14**~~ — **CLOSED 2026-06-10.** `validation_api._emit_validation_metric` pushes `Fortress/P2/Validation` PutMetricData on every `/validate/*` call: `LatencyMs` (Bank+Endpoint dims), `Decisions` (+Decision dim), and a dimensionless `Calls` heartbeat (per `feedback_cw_alarm_dimension_split`). Emission is best-effort — a Bedrock or CW outage can't propagate into the customer's serving path.

---

## 4. Test coverage

- `tests/integration/test_pipeline1_e2e.py` covers `/health` and PII rejection only. **Zero** tests for the 12 sync endpoints (auth path, diff logic, pagination, parity).
- `tests/integration/test_pipeline2_e2e.py` covers `FortressAPIClient` PII, `ComplianceService` fallback, `PluginManager`. **Zero** end-to-end webhook → 6-agent → JIRA → GitHub PR test.

---

## 5. Already-known infra deploys (from CLAUDE.md, restated for completeness)

- Run `./infra/scripts/setup-p1-auto.sh`.
- CNAME `api.fortresslabs.dev → ALB DNS` for ACM.
- Register first pilot bank.
- Verify ParadeDB `pg_search` on Aurora PG15 with AWS.
- Run `verify_sdn_prereqs.py`, embed backfill, then `alembic upgrade 022`.
- A/B legacy vs vector OFAC on 1k synthetic + 1k real wires.
- Confirm migrations 016/017 in P2 Aurora.
- Verify SSM injection for `PIPELINE_MODE=pipeline2` on ECS.

---

## 6. Suggested fix sequencing

1. **Day 0** — B1, B2, B3, B4 (auth defaults) + B9, B10 (container hardening). One PR each; small surface.
2. **Day 1-2** — B5 (env filtering in test runner), B6/B7 (sync retry + atomic), B8 + H1/H2 (sync auth refactor + error handling).
3. **Day 3-5** — B11 (Aurora/Redis sizing review with AWS), B12 (KMS + encryption-at-rest CFN change), H3 (admin endpoints), H8 (upsert key fix).
4. **Sprint 2** — H4–H7, H9–H14 + medium items. Add integration tests for sync endpoints (M-coverage) and webhook pipeline.
5. **Pre-launch** — DR restore drill (M13), CloudWatch dashboards (M14), backup/PITR documentation.
