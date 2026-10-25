# Local Development & Debugging Guide

How to run and debug the three modules — **Pipeline 1 (P1)**, **Pipeline 2 (P2)**,
and the **Dashboard** — on your laptop, independently, with hot-reload and
breakpoints.

> TL;DR: `make up` starts Postgres+Redis. Run each module in its own terminal:
> `make p1` (P1 :8001), `make p2` (P2 :8002), `cd dashboard && npm run dev`
> (UI :5173). Use `LLM_MOCK_MODE=true` so you don't need Bedrock. For the UI,
> enable the dev-auth bypass (below) so you skip Cognito.

---

## 0. Architecture & ports

```
┌─────────────┐   /api/sync/*   ┌─────────────┐   /api/v1/dashboard/*   ┌──────────────┐
│ Pipeline 1  │◄────────────────│ Pipeline 2  │◄────────────────────────│  Dashboard   │
│ scraper +   │   (UpdateService│ agents +    │   (Vite proxy /api →8002)│  React SPA   │
│ sync API    │    pulls P1)    │ validators +│                          │  (Vite)      │
│ :8001       │                 │ dashboard   │                          │  :5173       │
└──────┬──────┘                 │ API  :8002  │                          └──────────────┘
       │                        └──────┬──────┘
       │  DATABASE_URL                 │  DATABASE_URL
       ▼                               ▼
  ┌──────────────────── Postgres (pgvector:pg15) :5432 ───────────────────┐
  │  fortress_dev  (P1, and P2 in the simple shared-DB setup)             │
  │  fortress_p2   (optional: P2's own replica for a realistic sync)      │
  └───────────────────────────────────────────────────────────────────────┘
                              Redis :6379
```

| Module | Port | Entry | Reads |
|---|---|---|---|
| Pipeline 1 | 8001 | `src/main_pipeline1:app` | `fortress_dev` |
| Pipeline 2 | 8002 | `src/main_pipeline2:app` | `fortress_dev` (or `fortress_p2`) |
| Dashboard | 5173 | `dashboard/` (Vite) | P2 API via proxy |
| Postgres | 5432 | docker | — |
| Redis | 6379 | docker | — |

Key env vars (defaults baked into the Makefile targets):
- `PIPELINE_MODE` = `pipeline1` | `pipeline2` — selects config/branch behavior.
- `DATABASE_URL` = `postgresql+asyncpg://fortress:fortress_dev@localhost:5432/<db>`
- `LLM_MOCK_MODE=true` — stub the LLM entirely (no creds at all). `make p1`/`p2`
  default to this, so **plain `make p2` does not produce real LLM output**.
- `LLM_PROVIDER` = `anthropic` (direct Claude API, no AWS) | `bedrock` (prod).
- `CLIENT_NAME` — P2 tenant id (dev default `devbank`).

**Three LLM modes for local dev:**
| Goal | Set | AWS needed? |
|---|---|---|
| Wire/pipeline/UI work, stubbed LLM | `LLM_MOCK_MODE=true` (the `make p1`/`p2` default) | None |
| **Real test generation via the direct Claude API** | `LLM_MOCK_MODE=false` + `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` | **None** — use `make p2-live` |
| Real LLM via Bedrock (prod parity) | `LLM_MOCK_MODE=false` + `LLM_PROVIDER=bedrock` + `fortress-local` profile | Bedrock invoke |

---

## 1. Prerequisites (one-time)

```bash
# Python deps (use a venv)
python3 -m venv .venv && source .venv/bin/activate
make install                 # requirements.txt + requirements-dev.txt

# Dashboard deps
cd dashboard && npm install && cd ..

# Docker Desktop running (for Postgres + Redis)
docker version
```

There are two ways to run the Python services. Pick per task:

| | **Host mode** (`make p1`/`p2`) | **Docker mode** (`docker compose`) |
|---|---|---|
| Hot reload | ✅ uvicorn `--reload` | ❌ rebuild image on change |
| Breakpoints / debugger | ✅ easy | ⚠️ needs debugpy in container |
| Matches prod runtime | ⚠️ close | ✅ identical (entrypoint + migrations) |
| Best for | **day-to-day dev & debug** | smoke-testing the real image / sync demo |

---

## 2. Start shared infra

```bash
make up            # postgres (pgvector:pg15) + redis, detached
make migrate       # alembic upgrade head against fortress_dev
make schema-check  # verify key columns/indexes exist
```

---

## 3. Module 1 — Pipeline 1 (scraper + sync API)

### Run (host mode, hot-reload)
```bash
make p1            # → http://localhost:8001  (uvicorn --reload, LLM_MOCK_MODE=true)
# health
curl localhost:8001/health
curl localhost:8001/health/sync
```

### Run a scrape (populate compliance_rules)
```bash
# One connector only (fast iteration) — env filter read by scraper_scheduler:
SCRAPER_ONLY_CONNECTORS=fincen_part1010 \
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
LLM_MOCK_MODE=true \
python -m scripts.run_compliance_agent      # or trigger via the P1 scrape entrypoint

# Inspect what landed
docker exec fortress-postgres psql -U fortress -d fortress_dev \
  -c "SELECT regulator, count(*) FROM compliance_rules GROUP BY 1 ORDER BY 2 DESC;"
```

### Debug
- `make p1-debug` → same as `make p1` + `LOG_LEVEL=debug`.
- Add a breakpoint: `import pdb; pdb.set_trace()` in any handler, or use the
  VS Code attach config in §7.
- Connectors live in `src/connectors/regulatory/usa/`; the orchestrator is
  `src/services/scraper_scheduler.py`. Because `--reload` is on, saving a file
  restarts the worker automatically.

---

## 4. Module 2 — Pipeline 2 (agents + validators + dashboard API)

### Run (host mode, hot-reload)
```bash
make p2            # → http://localhost:8002
curl localhost:8002/health
```
`make p2` sets `CLIENT_NAME=devbank` and points at `fortress_dev`.

### Realistic P1 → P2 sync (separate replica DB)
The simple `make p2` shares `fortress_dev`, so a "sync" is a no-op. To exercise
a real cross-DB pull (how it works in prod — separate Auroras):

```bash
# 1. Give P2 its own DB, schema-cloned from fortress_dev
docker exec fortress-postgres psql -U fortress -d fortress_dev -c "CREATE DATABASE fortress_p2 OWNER fortress;"
docker exec fortress-postgres sh -c "pg_dump -U fortress -s fortress_dev | psql -U fortress -d fortress_p2 -q"
docker exec fortress-postgres psql -U fortress -d fortress_p2 -c "DELETE FROM alembic_version; INSERT INTO alembic_version VALUES ('051');"
docker exec fortress-postgres psql -U fortress -d fortress_p2 -c "TRUNCATE compliance_rules; TRUNCATE local_sync_status;"

# 2. Register a dev client in P1 so sync auth passes (api_key_hash = sha256(key))
python3 - <<'PY'
import hashlib, pg8000.native as pg
c=pg.Connection(user="fortress",password="fortress_dev",host="localhost",port=5432,database="fortress_dev")
c.run("""INSERT INTO client_credentials (id,customer_id,bank_name,api_key_hash,is_active,tier,created_at)
         VALUES (gen_random_uuid(),'devbank','Dev Bank',:h,true,'standard',now())
         ON CONFLICT (customer_id) DO UPDATE SET api_key_hash=:h, is_active=true""",
      h=hashlib.sha256(b"devkey-123456").hexdigest())
PY

# 3. Run P2 against its own DB, pointed at P1 (update_service reads these names)
PIPELINE_MODE=pipeline2 CLIENT_NAME=devbank \
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_p2" \
REDIS_URL="redis://:fortress_dev@localhost:6379" \
FORTRESS_API_URL=http://localhost:8001 \
CUSTOMER_ID=devbank FORTRESS_API_KEY=devkey-123456 \
SYNC_INTERVAL_HOURS=0 LLM_MOCK_MODE=true \
uvicorn src.main_pipeline2:app --reload --port 8002

# 4. Trigger the sync (INTERNAL_SYNC_SECRET unset → auth skipped locally)
curl -X POST "http://localhost:8002/internal/sync/trigger?force_initial=true"
curl http://localhost:8002/internal/sync/counts        # verify rule counts
```
Sync env vars `update_service.py` actually reads: **`FORTRESS_API_URL`,
`CUSTOMER_ID`, `FORTRESS_API_KEY`**. `SYNC_INTERVAL_HOURS=0` disables the 24h
background loop so you drive it manually.

### Debug
- `make p2-debug` for debug logging.
- Agent pipeline: `src/agents/*`; validators: `src/services/validators.py`;
  sync: `src/services/update_service.py`; dashboard API: `src/api/dashboard_api.py`.

### Real test generation via the direct Claude API (no AWS)

The 6-agent test-generation pipeline normally calls Bedrock. For local dev you
can run it against the **direct Claude API** (`https://api.anthropic.com`) with
no AWS credentials, no `fortress-local` profile, and no Bedrock access.

`BedrockService` (the shared LLM call site for Agent 4 and enforcement
summarization) switches on `LLM_PROVIDER`: set it to `anthropic` and it builds
an `anthropic.Anthropic` client instead of a boto3 Bedrock client. The
configured Bedrock inference-profile model ID is auto-translated to its direct
form (`us.anthropic.claude-sonnet-4-5-20250929-v1:0` → `claude-sonnet-4-5-20250929`).

```bash
# 1. Put your key in .env (read via pydantic SettingsConfigDict):
#      LLM_PROVIDER=anthropic
#      ANTHROPIC_API_KEY=sk-ant-...
#    (.env.example already defaults LLM_PROVIDER=anthropic — just add the key)

# 2. Run P2 with the LLM live (mock off, direct Claude API):
make p2-live            # → :8002, LLM_MOCK_MODE=false LLM_PROVIDER=anthropic
#   make p1-live        # same for P1

# 3. Trigger generation and watch it call the Claude API:
#    - Dashboard: Rules page → "Generate test" (POST /rules/{id}/generate-test)
#    - JIRA webhook: POST /webhooks  (Vite proxies /webhooks → :8002)
#    Log line `anthropic_code_generated model_id=claude-sonnet-4-5-20250929`
#    confirms the direct API ran (vs `bedrock_code_generated` on the AWS path).
```

To pin a specific model regardless of the Bedrock ID, set
`ANTHROPIC_MODEL=claude-sonnet-4-5-20250929` (or another temperature-capable
model — the generator passes `temperature`, which newer tiers like Opus 4.7+ /
Sonnet 5 reject).

`LLM_PROVIDER=anthropic` covers **both** generation call sites:
`BedrockService` (Agent 4 test-gen + enforcement summarization, plain-text) and
`BedrockExtractor` (`src/extraction/bedrock_extractor.py`, forced tool_use —
penalty/obligation/enforcement extraction). Both route to the direct Claude API.

**Still Bedrock-only locally:** Titan **embeddings** have no Claude-API
equivalent, so vector search (`compliance_rules.embedding`, OFAC vectors,
obligation similarity in the grounding step) needs Bedrock or falls back to
`LLM_MOCK_MODE=true`. That's the one capability the direct Claude API can't
provide.

---

## 5. Module 3 — Dashboard (React SPA)

The SPA uses Cognito in prod. Locally there's no user pool, so use the built-in
**E2E auth bypass** (skips login, sends a dev bearer token) — this is what lets
you run/debug without Cognito.

### Run
```bash
# Terminal A: P2 must accept the dev token. Easiest via docker-compose.override.yml
#   (already present for this repo) which sets on the pipeline2 service:
#     DEV_MODE: "true"
#     PIPELINE2_API_TOKEN: "e2e-bypass"
# If running P2 in HOST mode instead, export the same two before `uvicorn`:
export DEV_MODE=true PIPELINE2_API_TOKEN=e2e-bypass

# Terminal B: dashboard with the matching frontend bypass
cd dashboard
VITE_E2E_AUTH_BYPASS=1 npm run dev     # → http://localhost:5173
```
Open **http://localhost:5173** (hostname must be `localhost` — the bypass is
gated to it). No login screen; you're a dev **CCO**. Vite proxies `/api` and
`/webhooks` → `:8002`.

How it fits together:
- Frontend (`src/main.jsx`, `src/App.jsx`): `VITE_E2E_AUTH_BYPASS=1` + localhost
  → mounts `App` directly and sends `Authorization: Bearer e2e-bypass`.
- Backend (`src/api/auth.py`): `DEV_MODE=true` **and** `PIPELINE2_API_TOKEN` set
  → accepts that exact token as a dev session. Pick a role with the
  `X-E2E-Role` header (CCO default).

### Debug
- Hot reload is automatic — edit any `dashboard/src/**` file and the browser
  updates. No rebuild.
- Pages: `dashboard/src/pages/*.jsx`; API client: `dashboard/src/services/api.js`
  (maps each panel to a `/api/v1/dashboard/*` endpoint).
- If a panel is empty: open devtools → Network, find the `/api/...` call, check
  its status/body. 401 → token/bypass not set; 500 → check the P2 logs.
- Component/e2e tests: `npm test` (Vitest), `npm run e2e` (Playwright).

---

## 6. Databases & migrations

```bash
# Apply / inspect migrations (host)
make migrate
python -m alembic current
python -m alembic history --verbose
python -m alembic downgrade -1

# Query the DB without a psql client on the host — go through the container:
docker exec fortress-postgres psql -U fortress -d fortress_dev -c "\d compliance_rules"
docker exec fortress-postgres psql -U fortress -d fortress_dev -c "SELECT severity, count(*) FROM compliance_rules GROUP BY 1;"

# Or from Python (pg8000 is pure-python, no build needed):
python3 -c "import pg8000.native as pg; c=pg.Connection(user='fortress',password='fortress_dev',host='localhost',port=5432,database='fortress_dev'); print(c.run('SELECT count(*) FROM compliance_rules'))"
```

Migration chain note: this working copy carries `049`/`050` **reconciliation
placeholders** (the deployed DB was stamped `050` but those scripts weren't in
this checkout) and `051` adds `compliance_rules.severity`. See the migration
table in `CLAUDE.md`. Bootstrap *fresh* DBs from the canonical repo, not this copy.

---

## 7. Debugging toolbox

### VS Code — attach to a running uvicorn (host mode)
Run the service with debugpy:
```bash
PIPELINE_MODE=pipeline2 CLIENT_NAME=devbank LLM_MOCK_MODE=true \
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
python -m debugpy --listen 5678 -m uvicorn src.main_pipeline2:app --reload --port 8002
```
`.vscode/launch.json`:
```json
{ "version": "0.2.0", "configurations": [
  { "name": "Attach P2", "type": "python", "request": "attach",
    "connect": { "host": "localhost", "port": 5678 },
    "justMyCode": false }
]}
```
Set breakpoints in `src/**`, hit an endpoint, and it breaks.

### Docker mode logs & shell
```bash
docker compose up -d --build pipeline1 pipeline2
docker compose logs -f pipeline1 pipeline2
docker exec -it fortress-pipeline2 bash
docker inspect -f '{{.State.Health.Status}}' fortress-pipeline2
```
After editing Python in Docker mode you must `docker compose build <svc>` then
`docker compose up -d <svc>` — there's no hot reload in the image.

### Gotchas already hit in this repo (so you don't rediscover them)
- **Zero data in dashboard** → almost always auth: confirm `DEV_MODE=true` +
  `PIPELINE2_API_TOKEN=e2e-bypass` on P2 **and** `VITE_E2E_AUTH_BYPASS=1` on Vite.
- **Enum case** — `workflow_executions.status` labels are UPPERCASE
  (`COMPLETED`/`FAILED`/…). Raw-SQL comparisons must match case or Postgres
  throws `InvalidTextRepresentationError`.
- **`applicability_charter/_activities/_states`** are `text[]` in the DB — the
  ORM must declare `ARRAY(Text)`, not `JSONB`.
- **P2 initial-sync** needs `cfr_citation` on LAW rows
  (`ck_cr_law_requires_citation`); the insert path must carry it.
- **No AWS creds?** Keep `LLM_MOCK_MODE=true`; SSM injection failures at P2
  startup are logged and non-fatal locally.

---

## 8. One-command recipes

```bash
# Full local stack (Docker, prod-like) — P1 :8001, P2 :8002
docker compose up -d --build

# Dev loop (host, hot-reload) — three terminals
make up && make migrate
make p1                                   # terminal 1
make p2                                   # terminal 2
cd dashboard && VITE_E2E_AUTH_BYPASS=1 npm run dev   # terminal 3  (needs DEV_MODE on P2)

# Tests
make test      # full      │ make test-p1 │ make test-p2 │ make lint
cd dashboard && npm test   # Vitest       │ npm run e2e  (Playwright)

# Tear down (keep data) / wipe volumes
make down   │   make clean
```
