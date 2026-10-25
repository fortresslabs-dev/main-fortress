# Fortress — Local Development Guide (Windows & macOS)

Run the whole stack on your laptop with plain **Python commands** (no `make`
required) plus Docker for Postgres/Redis. Covers: start infra → run the
**scraping job + ingest into Pipeline 1** → **Pipeline 2 sync from P1** →
**DB schema check / Postgres login from a SQL client** → run the APIs and
dashboard.

> This repo is a single codebase with two run modes selected by `PIPELINE_MODE`:
> - `pipeline1` — Fortress Cloud: regulatory **scraper + ingest + read-only sync API**
> - `pipeline2` — Bank side: 6-agent pipeline + validators + **UpdateService** (pulls from P1) + dashboard API

---

## 0. Ports, services, credentials

| Service | Host:Port | Notes |
|---|---|---|
| Pipeline 1 API | `localhost:8001` | `src.main_pipeline1:app` |
| Pipeline 2 API | `localhost:8002` | `src.main_pipeline2:app` |
| Dashboard (Vite) | `localhost:5173` | React SPA, proxies `/api` → `:8002` |
| Postgres (pgvector:pg15) | `localhost:5432` | **user** `fortress` · **pass** `fortress_dev` · **db** `fortress_dev` |
| Redis | `localhost:6379` | password `fortress_dev` |

The one connection string everything uses locally:

```
postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev
```

(SQLAlchemy/async form. SQL GUI clients use the plain form — see §7.)

---

## 1. Prerequisites (one-time)

Install on both OSes:
- **Python 3.11**  — `python --version`
- **Docker Desktop** (running) — `docker version`
- **Node 18+** (only if you run the dashboard) — `node --version`
- **Git**

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

### Windows (PowerShell)
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
```
> If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then re-run Activate.

---

## 2. Setting environment variables (read this once)

Every command below needs `DATABASE_URL` (and usually `LLM_MOCK_MODE=true` so
you don't need AWS/Bedrock). The syntax differs per shell — **set them once per
terminal**, then run the plain commands.

### macOS / Linux (bash/zsh)
```bash
export DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev"
export REDIS_URL="redis://:fortress_dev@localhost:6379"
export LLM_MOCK_MODE=true
```

### Windows (PowerShell)
```powershell
$env:DATABASE_URL = "postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev"
$env:REDIS_URL    = "redis://:fortress_dev@localhost:6379"
$env:LLM_MOCK_MODE = "true"
```

> `LLM_MOCK_MODE=true` stubs all Bedrock/LLM calls so the scraper and agents run
> with **no AWS credentials**. Drop it only when you want real LLM output.

---

## 2b. LLM provider — Claude or GPT (one variable, no AWS)

For **real** LLM output locally (test generation, extraction, enforcement
summarization) without AWS/Bedrock, one variable — **`LLM_PROVIDER`** — selects
the backend. Set it in `.env` (copied from `.env.example`) and turn the mock
off.

| `LLM_PROVIDER` | Backend | Key to set | AWS? |
|---|---|---|---|
| `anthropic` (or `claude`) | Direct **Claude API** (`api.anthropic.com`) | `ANTHROPIC_API_KEY=sk-ant-...` | No |
| `openai` (or `gpt`) | Direct **OpenAI/GPT API** (`api.openai.com`) | `OPENAI_API_KEY=sk-...` + `OPENAI_MODEL=gpt-4o` | No |
| `bedrock` | AWS Bedrock (prod parity) | — (needs `fortress-local` AWS profile) | Yes |

**Setup (both OSes):**
```bash
cp .env.example .env          # if you don't have .env yet
# then edit .env — pick ONE:
#   LLM_PROVIDER=claude   + ANTHROPIC_API_KEY=sk-ant-...
#   LLM_PROVIDER=gpt      + OPENAI_API_KEY=sk-...   (OPENAI_MODEL=gpt-4o)

make install                  # installs anthropic + openai SDKs (in a venv)
make p2-live                  # runs P2 with the mock OFF; provider comes from .env
#   make p1-live              # same for P1
```

**Trigger test generation** and confirm which provider ran — the log line is:
- Claude: `anthropic_code_generated model_id=claude-sonnet-4-5-20250929`
- GPT: `openai_code_generated model_id=gpt-4o`

Switching providers is just editing `LLM_PROVIDER` in `.env` and restarting —
no code change. Keys and model IDs live in `.env` (see `.env.example` for all
fields). Get keys from console.anthropic.com (Claude) or platform.openai.com (GPT).

> **One AWS-only exception:** Titan **embeddings** (vector search) have no
> direct-API equivalent. With `anthropic`/`openai`, embedding-dependent features
> fall back to `LLM_MOCK_MODE=true`; only Bedrock provides real embeddings.

---

## 3. Start Postgres + Redis (both OSes)

```bash
docker compose up -d postgres redis
docker compose ps        # both should be "healthy"
```

Stop later with `docker compose down` (keeps data) or `docker compose down -v` (wipes volumes).

---

## 4. Apply the database schema (migrations)

With env vars from §2 set:

```bash
python -m alembic upgrade head      # create all tables/indexes
python -m alembic current           # show the applied revision
python -m alembic history --verbose # full chain
```

---

## 5. Run the scraping job + ingest into Pipeline 1

The scraper fetches regulatory sources, extracts rules, and **writes them into
the `compliance_rules` table** (that write *is* the P1 ingest). One-shot entry
point: `scripts/run_scrape.py`.

### Scrape a single connector (fast — recommended for BSA/AML)
```bash
# env vars from §2 already set (DATABASE_URL + LLM_MOCK_MODE=true)
python -m scripts.run_scrape --connector fincen_part1010
```
`--connector` is **repeatable** and suffix-matched; or set
`SCRAPER_ONLY_CONNECTORS=fincen_part1010,occ_bsa` instead.

BSA/AML-relevant connectors (in `src/connectors/regulatory/usa/`):
`fincen_part1010`, `fincen_sar`, `occ_bsa`, `ffiec_bsa`, `ofac_sdn`.

### Scrape the full connector cycle (all 17 federal + 5 state)
```bash
python -m scripts.run_scrape
```

### Verify what was ingested
```bash
# via the container's psql (no local client needed)
docker exec fortress-postgres psql -U fortress -d fortress_dev \
  -c "SELECT regulator, count(*) FROM compliance_rules GROUP BY 1 ORDER BY 2 DESC;"

docker exec fortress-postgres psql -U fortress -d fortress_dev \
  -c "SELECT severity, count(*) FROM compliance_rules GROUP BY 1;"
```

> Alternative one-shot trigger: `python -m scripts.trigger_scrape` (same cycle,
> also writes JSON chunk files to `chunks/`). The long-running P1 API does **not**
> scrape on its own — the scrape script is the authoritative path.

### (Optional) Run the Pipeline 1 API
Serves the read-only sync endpoints that P2 pulls from.

**macOS / Linux**
```bash
PIPELINE_MODE=pipeline1 uvicorn src.main_pipeline1:app --reload --port 8001
```
**Windows (PowerShell)** — set mode, then run:
```powershell
$env:PIPELINE_MODE = "pipeline1"
uvicorn src.main_pipeline1:app --reload --port 8001
```
Check: `curl http://localhost:8001/health` and `/health/sync`.

---

## 6. Pipeline 2 — sync rules from Pipeline 1

P2's `UpdateService` pulls the rule corpus from P1 into P2's database. One-shot
entry point: `scripts/run_sync.py` (runs `UpdateService.sync_all()` once).

It reads three env vars: **`FORTRESS_API_URL`**, **`CUSTOMER_ID`**,
**`FORTRESS_API_KEY`**.

### Simplest local sync (shared DB)
By default P2 and P1 share `fortress_dev`, so this exercises the sync code path
against the rules you just scraped. Keep §2 env vars set, add the sync ones:

**macOS / Linux**
```bash
PIPELINE_MODE=pipeline2 \
FORTRESS_API_URL=http://localhost:8001 \
CUSTOMER_ID=devbank FORTRESS_API_KEY=devkey-123456 \
python -m scripts.run_sync
```
**Windows (PowerShell)**
```powershell
$env:PIPELINE_MODE    = "pipeline2"
$env:FORTRESS_API_URL = "http://localhost:8001"
$env:CUSTOMER_ID      = "devbank"
$env:FORTRESS_API_KEY = "devkey-123456"
python -m scripts.run_sync
```
> Requires the P1 API (§5) to be running so P2 has something to pull from.

### Or trigger sync through the running P2 API
```bash
# with P2 API up (see below), locally the internal secret is unset so auth is skipped
curl -X POST "http://localhost:8002/internal/sync/trigger?force_initial=true"
curl http://localhost:8002/internal/sync/counts     # confirm rule counts moved
```

### (Optional) Run the Pipeline 2 API
**macOS / Linux**
```bash
PIPELINE_MODE=pipeline2 CLIENT_NAME=devbank \
PIPELINE1_API_URL=http://localhost:8001 \
uvicorn src.main_pipeline2:app --reload --port 8002
```
**Windows (PowerShell)**
```powershell
$env:PIPELINE_MODE     = "pipeline2"
$env:CLIENT_NAME       = "devbank"
$env:PIPELINE1_API_URL = "http://localhost:8001"
uvicorn src.main_pipeline2:app --reload --port 8002
```
Check: `curl http://localhost:8002/health`.

> For a **realistic cross-database sync** (P2 on its own `fortress_p2` replica,
> like prod) see `docs/setup/local-dev.md §4`.

---

## 7. DB schema check + logging into Postgres from a SQL client

### Automated schema check (script)
Verifies every migration ran and all key columns/indexes exist. Exits non-zero
on any gap.

```bash
# DATABASE_URL from §2
python scripts/verify_schema.py
```

### Connect with a SQL GUI (SQL Workbench/J, DBeaver, pgAdmin, TablePlus)

Use the **plain JDBC/psql** connection (not the `+asyncpg` form):

| Field | Value |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `fortress_dev` |
| Username | `fortress` |
| Password | `fortress_dev` |
| SSL | not required (local) |

- **SQL Workbench/J** — needs the PostgreSQL JDBC driver; URL:
  `jdbc:postgresql://localhost:5432/fortress_dev`
- **DBeaver / pgAdmin / TablePlus** — pick "PostgreSQL", fill the table above.

Handy inspection queries once connected:
```sql
-- what tables exist
\dt                                        -- (psql)  or browse the tree in a GUI
SELECT version_num FROM alembic_version;   -- current migration revision
\d compliance_rules                        -- column/index layout of the rules table
SELECT regulator, count(*) FROM compliance_rules GROUP BY 1 ORDER BY 2 DESC;
SELECT severity,  count(*) FROM compliance_rules GROUP BY 1;   -- Critical/High/Medium/Low
```

### Or query without any GUI (through the container)
```bash
docker exec -it fortress-postgres psql -U fortress -d fortress_dev
# then run SQL at the psql prompt, \q to quit
```

---

## 8. (Optional) Dashboard

The SPA uses Cognito in prod; locally use the built-in dev-auth bypass.

```bash
cd dashboard
npm install            # one-time

# The dashboard talks to the P2 API. Start P2 (§6) with the dev token accepted:
#   macOS/Linux:  export DEV_MODE=true PIPELINE2_API_TOKEN=e2e-bypass  (before uvicorn)
#   Windows PS :  $env:DEV_MODE="true"; $env:PIPELINE2_API_TOKEN="e2e-bypass"

# Then run the UI with the matching frontend bypass:
#   macOS/Linux:  VITE_E2E_AUTH_BYPASS=1 npm run dev
#   Windows PS :  $env:VITE_E2E_AUTH_BYPASS="1"; npm run dev
```
Open **http://localhost:5173** (must be `localhost` — the bypass is gated to it).

---

## 9. Quick reference

```bash
# infra
docker compose up -d postgres redis      # start DB + cache
docker compose down                      # stop (keep data)

# schema
python -m alembic upgrade head           # migrate
python scripts/verify_schema.py          # schema check

# scrape + ingest (P1)
python -m scripts.run_scrape --connector fincen_part1010
python -m scripts.run_scrape             # full cycle

# sync (P2 pulls from P1)
python -m scripts.run_sync               # needs FORTRESS_API_URL/CUSTOMER_ID/FORTRESS_API_KEY + P1 running

# run the services
uvicorn src.main_pipeline1:app --reload --port 8001   # P1  (PIPELINE_MODE=pipeline1)
uvicorn src.main_pipeline2:app --reload --port 8002   # P2  (PIPELINE_MODE=pipeline2)

# tests
pytest tests/ -v
```

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `could not connect ... localhost:5432` | `docker compose up -d postgres` and wait for **healthy** (`docker compose ps`). |
| Scraper/agents want AWS creds | Set `LLM_MOCK_MODE=true` (§2). SSM/Secrets lookups are non-fatal locally. |
| `alembic ... DATABASE_URL` error | You didn't export `DATABASE_URL` in **this** terminal (§2). Each new terminal needs it. |
| Windows: `VAR=val cmd` "not recognized" | That inline syntax is bash-only. Use `$env:VAR="val"` on its own line first (§2). |
| Windows: venv won't activate | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, reopen shell, re-activate. |
| P2 sync pulls nothing | Confirm P1 API is running (§5) and `FORTRESS_API_URL=http://localhost:8001`. |
| SQL client auth fails | Use the **plain** form (host/port/db/user/pass in §7), not the `postgresql+asyncpg://` string. |

Deeper local-dev notes (hot-reload, VS Code debugger, realistic cross-DB sync,
enum-case gotchas): **`docs/setup/local-dev.md`**.
