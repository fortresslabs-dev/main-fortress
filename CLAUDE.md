# CLAUDE.md — Fortress Labs Agent Workspace

Loaded by Claude Code on every session. Keep this file small. Runbooks live
under `docs/runbooks/`; setup and ops live under `docs/setup/`.

**Single repo — two pipelines:**
- `PIPELINE_MODE=pipeline1` → Fortress Cloud (scraper + sync API, EventBridge-triggered ECS)
- `PIPELINE_MODE=pipeline2` → Bank account (6-agent pipeline + validators + CCO dashboard, ECS auto-scale)

**No EC2, no Weaviate in production.** Aurora pgvector + tsvector is the sole
vector + BM25 store (OpenSearch Serverless was removed from the budget
2026-05-14 — see memory `project_p1_vector_store_decision`).

---

## Detailed runbooks (open the file that matches the task)

| Topic | File |
|---|---|
| Agent 1 — Infrastructure debugger (CFN, ECS, Aurora, Secrets) | `docs/runbooks/agent-1-infra.md` |
| Agent 2 — Pipeline 1 code + ingestion (scraper, embeddings, sync API) | `docs/runbooks/agent-2-pipeline1.md` |
| Agent 3 — Issue monitor + auto-fix (health checks, alarms, schema regression) | `docs/runbooks/agent-3-monitor.md` |
| Agent 4 — P2 sync + workflow monitor (UpdateService, JIRA webhooks, dashboard) | `docs/runbooks/agent-4-sync.md` |
| Scraper architecture (storage, S3-first, S3-diff, rule validity, MCP scrape monitor) | `docs/runbooks/scraper-architecture.md` |
| Scraper extraction architecture (LLM + Pydantic tool_use, golden set, backfill) | `docs/runbooks/scraper-extraction-architecture.md` |
| Embedding backfill — heal `compliance_rules.embedding IS NULL` (Titan v2) on P1, propagate to P2 | `docs/runbooks/embedding-backfill.md` |
| Prod rollout — test-gen accuracy (enum fix, Fix A, grounding) + obligation embeddings (migration 052, backfill, enum-case coupling) | `docs/runbooks/prod-rollout-testgen.md` |
| Prod schema verification — one-shot checks for enum labels, obligations.embedding, rcr.client_id/updated_at, readiness tables (run before rollout) | `docs/runbooks/prod-schema-verification.md` |
| 6-agent test-generation pipeline — how it works, real `FINCEN-1010-660-R3` example, failure analysis (as-is baseline) | `docs/design/6-agent-test-generation-pipeline.md` |
| Dashboard data map — every page → API endpoint → Postgres tables → aggregation, with flow + ER diagrams | `docs/design/dashboard-data-map.md` |
| Dashboard completion SME review — 7 work items as business use cases, fixes, SME verification, Mermaid diagrams | `docs/compliance/dashboard-completion-sme-review.md` |
| Operations — IDE/CLI setup, P1 auto-deploy, bank registration, MCP servers, connectors | `docs/setup/operations.md` |
| Developer AWS onboarding — tiered access (none/Bedrock/full), Identity Center provisioning, `fortress-local` SSO profile | `docs/setup/developer-aws-onboarding.md` |
| Local dev & debug — run P1/P2/dashboard locally, hot-reload, P1→P2 sync, debugging | `docs/setup/local-dev.md` |
| BSA/AML onboarding — end-to-end SME/dev guide (code walkthrough, 6-agent pipeline, CCO validation, rule→test, bank handoff, dashboard pending actions) | `docs/runbooks/bsa-aml-onboarding.md` |

Each agent persona's trigger phrases are at the top of its runbook file.

---

## Quick reference — most-used commands

```bash
# ── Local dev ─────────────────────────────────────────────────────────
docker compose up -d                          # postgres (pgvector:pg15) + redis
docker compose exec pipeline1 python -m alembic upgrade head
curl http://localhost:8001/health             # P1
curl http://localhost:8002/health             # P2

# ── Syntax check (run before every push) ──────────────────────────────
python -m py_compile src/main_pipeline1.py
python -m py_compile src/main_pipeline2.py
python -m py_compile src/utils/config.py
ruff check src/ --fix
black src/

# ── Migrations ────────────────────────────────────────────────────────
python -m alembic upgrade head
python -m alembic current
python -m alembic history --verbose
python -m alembic downgrade -1

# ── Docker build ──────────────────────────────────────────────────────
docker build -f Dockerfile.pipeline1 -t fortress/pipeline1:dev .
docker build -f Dockerfile.pipeline2 -t fortress/pipeline2:dev .

# ── AWS ECS rolling deploy ────────────────────────────────────────────
aws ecs update-service \
  --cluster fortress-pipeline1-production \
  --service fortress-pipeline1-api-production \
  --force-new-deployment

# ── P2 sync health ────────────────────────────────────────────────────
curl -H "Authorization: Bearer $JWT" https://fortressapi.bank.com/health/sync
```

See `docs/runbooks/agent-2-pipeline1.md` for the one-off scrape `run-task` form
and full ingestion-flow test.

---

## Architecture quick map

```
src/
├── main_pipeline1.py        # P1 FastAPI app — sync API + scraper endpoints
├── main_pipeline2.py        # P2 FastAPI app — validators + agents + CCO dashboard
├── agents/
│   ├── discovery.py         # Agent 1 — JIRA story analysis
│   ├── understanding.py     # Agent 2 — hybrid pgvector + BM25 compliance search
│   ├── framework.py         # Agent 3 — test framework selection
│   ├── generation_bedrock.py # Agent 4 — test code via Bedrock claude-sonnet-4
│   ├── validation.py        # Agent 5 — compliance rule validation
│   └── execution.py         # Agent 6 — sandbox pytest run, JIRA post
├── api/
│   ├── sync_api.py          # P1 — 8 sync endpoints for P2 UpdateService
│   ├── validation_api.py    # P2 — wire/OFAC/loan validators
│   ├── webhooks.py          # P2 — JIRA webhook → BackgroundTasks → agent pipeline
│   ├── auth.py              # P2 — Bearer token auth
│   ├── sync_auth.py         # P1 — header-based HMAC auth dep
│   └── workflows.py         # P2 — agent workflow status + approvals
├── services/
│   ├── scraper_scheduler.py         # P1 — orchestrates 17 federal + 5 state connectors
│   ├── update_service.py            # P2 — daily pull from P1; pgvector ANALYZE after sync
│   ├── validators.py                # P2 — WireTransferValidator, OFACScreener, LoanApplicationValidator
│   ├── compliance_rule_resolver.py  # P2 — 3-tier: Postgres → Redis → pgvector → fallback
│   ├── client_credential_service.py # P1 — HMAC key lifecycle
│   ├── deadline_tracker.py          # P1 seed + P2 refresh_days_remaining()
│   ├── weaviate_client.py           # DEPRECATED — kept for import compat, not called in prod
│   ├── github_service.py            # P2 — create PR with generated test code
│   └── test_runner.py               # P2 — sandbox pytest execution
├── mcp_servers/                     # MCP protocol servers for agent tool use
│   ├── compliance_knowledge/        # backed by ComplianceService
│   ├── test_patterns/
│   ├── banking_workflows/
│   └── scrape_monitor/              # logs every connector result to S3/Aurora/CW
├── connectors/regulatory/usa/       # 12 federal + 5 state (states/) connectors
├── models/database.py               # All SQLAlchemy models (P1 + P2)
└── utils/config.py                  # PIPELINE_MODE → Secrets Mgr (P1) or SSM (P2)
```

---

## Schema — migrations 007–023

| Migration | Table | Pipeline | Purpose |
|---|---|---|---|
| 007 | `ofac_sdn_entries` +country/nationality | P1 | SDN country field |
| 008 | `obligations` | P1 | Obligation sentences from Bedrock |
| 009 | `enforcement_actions`, `enforcement_risk_scores` | P1 | Enforcement risk per regulation |
| 010 | `compliance_deadlines` | P1 | Filing deadlines with days_remaining |
| 011 | `regulatory_change_records` | P1+P2 | Detected rule mutations |
| 012 | `peer_benchmarks` | P1 | FFIEC call report peer data |
| 013 | `compliance_rules` enrichment | P1 | cfr_citation, state_code, jurisdiction_level, enforcement_risk_score, obligation_count |
| 014 | `compliance_rules.state_code` widen | P1+P2 | 10→20 chars for compound state codes |
| 015 | `client_credentials` | P1 | Per-bank API key registry |
| 016 | `workflow_executions`, `checkpoint_approvals` | P2 | Core P2 tables |
| 017 | `bank_policies`, `policy_regulation_mappings`, `regulatory_change_records` | P2 | Policy gap dashboard panel 6 |
| 018 | `scrape_runs` | P1 | scrape_monitor MCP log table |
| 019 | `compliance_rules.embed_updated_at` + `idx_cr_embed_stale` | P1+P2 | stale-embedding tracker (predicate decoupled from `embedding` column) |
| 020 | `regulatory_change_records.*_at` → TIMESTAMPTZ | P1+P2 | tz-aware change timestamps |
| 021 | `ofac_sdn_vectors` + GIN trgm/tsv + tsv trigger | P1 | SDN vector screener — 1 row per name variant |
| 022 | `ofac_sdn_vectors.embedding` HNSW (CONCURRENTLY) | P1 | HNSW index built after embed backfill |
| 023 | `compliance_rules.embedding`/`tsv` + HNSW + GIN | P1+P2 | pgvector + tsvector setup formerly only in `init_p1_schema.py` |
| 037 | `regulatory_change_records.change_delta` (JSONB) | P1+P2 | Per-record diff at detection time — fixes drawer showing rule's *latest* delta instead of the record's |
| 049–050 | *(reconciliation placeholders)* | — | No-op scripts so the alembic DAG is contiguous — the deployed DB was stamped at `050` but those scripts were absent from this checkout; `compliance_rules` is fully explained by `048`, so `049/050` touched other objects. Bootstrap fresh DBs from the canonical repo. |
| 051 | `compliance_rules.severity` + `idx_cr_severity` + `ck_cr_severity_vocab` | P1+P2 | Editorial Critical/High/Medium/Low grade from the FinCEN Part 1010 obligation map (`fincen_part1010` connector) — distinct from the computed `enforcement_risk_score` |

`scripts/setup/init_p1_schema.py` previously created the pgvector / tsvector columns and HNSW/GIN indexes outside alembic — migration 023 supersedes that path. The script is preserved for legacy bootstrap but should not be used on populated DBs (see memory `feedback_init_p1_schema_destructive`).

**Critical constants in `compliance_rule_resolver.py` — keep in sync with DB:**

```python
"beneficial_ownership_pct": 20.0   # FinCEN CHG-2026-0412 (April 11, 2026) — was 25%
"wire_dual_approval_threshold": 10000.0
"ctr_threshold": 10000.0
"sar_threshold": 5000.0
"travel_rule_threshold": 3000.0
"ofac_fuzzy_threshold": 85.0       # 85% similarity → BLOCK
```

**Vector search:** pgvector `vector(1024)` on `compliance_rules.embedding` +
generated `tsv` tsvector column for BM25-style ranking. Do **not** use
`ts_rank` as a substitute for true BM25 outside the resolver's hybrid query.
`weaviate_client.py` is still imported by Tier 3 of `compliance_rule_resolver.py`
but not reachable in prod — do not delete yet.

---

## Makefile quick reference

```
make up | migrate | p1 | p2 | p1-debug | p2-debug
make p1-live | p2-live     # real LLM via direct Claude API (no AWS; needs ANTHROPIC_API_KEY)
make test | test-p1 | test-p2 | lint | schema-check
make build-p1 | build-p2 | build-both
make push-p1 | push-p2 BANK_ID=firstnational
make deploy-p1 | deploy-p2-bank BANK_ID=firstnational
make logs-p1 | logs-p2 BANK_ID=firstnational
make clean
```

---

## Current action items

### Infra (Agent 1)
- [x] VPC + data + compute + security CloudFormation templates
- [x] `infra/scripts/setup-p1-auto.sh` + `infra/setup/PIPELINE1_SETUP.md`
- [x] `scripts/register-bank.sh` + `infra/scripts/register-bank-ip.sh`
- [ ] Run `./infra/scripts/setup-p1-auto.sh` to deploy all P1 stacks
- [ ] After deploy: CNAME `api.fortresslabs.dev → ALB DNS` for ACM validation
- [ ] Register first pilot bank
- [ ] Verify `pg_search` (ParadeDB) on Aurora PG15 with AWS team

### Code — P1 ingestion (Agent 2)
- [x] `update_service.py` `_refresh_vector_index()` (pgvector ANALYZE)
- [x] `sync_api.py` `/sync/embeddings` (pre-computed vectors)
- [x] embedding + tsv columns in schema
- [x] Decommission `weaviate_client.py` references in `compliance_rule_resolver.py` Tier 3 (confirmed clean 2026-05-21)
- [x] `scripts/setup/initialize_weaviate.py` removed (deprecated file no longer exists)

### OFAC SDN vector store (rollout — migrations 021/022)
- [x] migration 021: `ofac_sdn_vectors` + GIN trgm + GIN tsv + tsv trigger
- [x] migration 022: HNSW vector index (run *after* embed backfill)
- [x] `src/services/ofac_sdn_loader.py` — normalize, integrity gate, N-strikes tombstone, change-record logging
- [x] `src/jobs/embed_sdn_variants.py` — standalone Titan v2 embed worker
- [x] Raw `SDN_ADVANCED.XML` → S3 `scraper-chunks/sdn_xml_raw/` (gzip + KMS)
- [x] `OFACScreener` feature flag `OFAC_SCREENER_MODE=legacy|vector`
- [x] `scripts/setup/calibrate_ofac_thresholds.py` + synthetic seed gold-set CSV
- [x] `docs/runbooks/ofac-calibration-gold-set.md` runbook (ops handoff)
- [ ] Run `scripts/setup/verify_sdn_prereqs.py` against prod Aurora
- [ ] After 021 applies + first full scrape: run `python -m src.jobs.embed_sdn_variants` then `alembic upgrade 022`
- [ ] Compliance ops: replace TBD placeholders in `ofac_calibration_gold.csv` with real bank screening history (per runbook section 4)
- [ ] Dry-run `scripts/setup/calibrate_ofac_thresholds.py` against prod Aurora (sanity check before ops returns real gold set)
- [ ] After ops returns verified gold: run calibration, get compliance sign-off on per-class thresholds (F1 ≥ 0.90, recall ≥ 0.95 for individual/alias)
- [ ] Load approved thresholds into SSM under `/fortress/p2/ofac/threshold/{class}` and wire `OFACScreener` to read them
- [ ] A/B compare legacy vs vector scores on 1k synthetic + 1k real wires before flipping default mode

### Monitor (Agent 3)
- [x] Weaviate removed from `docker-compose.yml`; `pgvector/pgvector:pg15` in use
- [x] `scripts/verify_schema.py` + `make schema-check`
- [x] `.github/workflows/ci-cd.yml` lint + migrate + schema-check + test
- [x] `.github/workflows/ci-cd.yml` demo-readiness gate (test-demo + Vitest + Playwright F1–F5 + nightly staging cron) — Phase 5
- [x] `.github/CODEOWNERS`
- [x] Add `/health/sync` endpoint to `src/api/health.py` (live since a5eb738; DB-backed via LocalSyncStatus + ComplianceRule count)
- [ ] Verify SSM injection for `PIPELINE_MODE=pipeline2` on ECS

### Sync + workflow (Agent 4)
- [x] `client_credential_service.py` `.verify()` alias
- [x] `src/api/sync_auth.py` header-based HMAC FastAPI dep
- [x] `update_service.py` full sync cycle with embeddings
- [ ] End-to-end UpdateService test after P1 deploys
- [ ] Test JIRA webhook → agent pipeline with pilot bank
- [ ] Confirm migrations 016/017 in P2 Aurora
