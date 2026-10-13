# P1 DB Dump, Restore, and Local P2 Sync

How a developer dumps the **Pipeline 1** database, restores it on another
machine (or into a P2 replica), then runs the **P2 sync from local P1** and
starts **P2 + the dashboard**. Works on Windows and macOS (Docker Desktop).

---

## 0. What needs dumping (and what doesn't)

| Store | Dump it? | Why |
|---|---|---|
| **Postgres** (`fortress_dev`) | ✅ **Yes** | Holds all durable P1 data — `compliance_rules`, `ofac_sdn_vectors`/`entries`, `enforcement_actions`, `regulatory_change_records`, `obligations`, `client_credentials`, etc. |
| **Redis** | ❌ **No** | **Cache only.** `init_redis()` soft-fails and every call site tolerates a miss; locally it holds **0 durable keys** and repopulates from Postgres. Nothing to back up. |

Reference snapshot of a populated local P1 (`fortress_dev`, ~722 MB):

| Table | ~Rows |
|---|---|
| `regulatory_change_records` | 53,000 |
| `ofac_sdn_vectors` | 51,500 |
| `ofac_sdn_entries` | 19,800 |
| `enforcement_actions` | 2,150 |
| `compliance_rules` | 476 |
| `obligations` | 366 |

> The DB uses the **pgvector** extension (`embedding vector(1024)` + HNSW
> indexes). Always restore into the `pgvector/pgvector:pg15` image (the repo's
> `docker compose` postgres) so the extension and index types exist.

---

## 1. Prerequisites

- Docker Desktop running, with the local Postgres container up:
  ```bash
  docker compose up -d postgres redis
  docker compose ps        # fortress-postgres should be "healthy"
  ```
- Connection facts (local): host `localhost`, port `5432`, db `fortress_dev`,
  user `fortress`, password `fortress_dev`.
- You do **not** need `pg_dump`/`pg_restore` installed on the host — run them
  **inside** the `fortress-postgres` container via `docker exec`.

---

## 2. Dump the P1 Postgres database

Dumps are written to `dumps/` (git-ignored — they are large and may contain
data, so they are never committed/pushed).

### Custom / compressed format (recommended — use with `pg_restore`)
**macOS / Linux**
```bash
mkdir -p dumps
docker exec fortress-postgres pg_dump -U fortress -Fc --no-owner --no-privileges \
  fortress_dev > dumps/fortress_dev_p1_$(date +%Y%m%d).dump
ls -lh dumps/
```
**Windows (PowerShell)**
```powershell
mkdir dumps -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd"
docker exec fortress-postgres pg_dump -U fortress -Fc --no-owner --no-privileges `
  fortress_dev > "dumps/fortress_dev_p1_$stamp.dump"
dir dumps
```
Expect ~250 MB for the reference dataset above.

### Verify the dump is valid (lists the archive table of contents)
```bash
docker exec -i fortress-postgres pg_restore -l < dumps/fortress_dev_p1_<stamp>.dump | head
```

### Plain-SQL alternative (human-readable / grep-able, not compressed)
```bash
docker exec fortress-postgres pg_dump -U fortress --no-owner --no-privileges \
  fortress_dev | gzip > dumps/fortress_dev_p1_$(date +%Y%m%d).sql.gz
```

---

## 3. Restore the dump

Copy the `*.dump` file to the target machine's `dumps/` folder, bring up a fresh
Postgres (`docker compose up -d postgres`), then:

**Restore into a clean `fortress_dev`:**
```bash
# drop + recreate so the restore is clean
docker exec fortress-postgres psql -U fortress -d postgres \
  -c "DROP DATABASE IF EXISTS fortress_dev;" -c "CREATE DATABASE fortress_dev OWNER fortress;"

# restore (custom format). --no-owner keeps it portable across roles.
docker exec -i fortress-postgres pg_restore -U fortress -d fortress_dev \
  --no-owner --no-privileges < dumps/fortress_dev_p1_<stamp>.dump
```
> `pg_restore` may print a few non-fatal warnings about the `vector` extension
> or HNSW index ordering — harmless. Add `--clean --if-exists` instead of the
> DROP/CREATE step if you prefer restoring over an existing DB in place.

**Confirm the data landed:**
```bash
docker exec fortress-postgres psql -U fortress -d fortress_dev \
  -c "SELECT count(*) FROM compliance_rules;" \
  -c "SELECT version_num FROM alembic_version;"
```

A restored DB already carries the full schema, so you do **not** need to run
`alembic upgrade head` again. (Only run migrations on a DB you built from
scratch without a dump.)

---

## 4. Developer setup — dump P1, then sync P2 from local

The dump is how you move the P1 rule corpus to your machine. **P2 does not read
the dump directly** — P2's `UpdateService` pulls rules from the **P1 API over
HTTP**. So the flow is: restore P1 → run the P1 API → point P2 at it → sync.

### 4a. Stand up P1 from the dump and start its API
```bash
docker compose up -d postgres redis
# (restore per §3 if this is a fresh machine)

# env for this terminal — macOS/Linux
export DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev"
export REDIS_URL="redis://:fortress_dev@localhost:6379"
export LLM_MOCK_MODE=true PIPELINE_MODE=pipeline1

uvicorn src.main_pipeline1:app --reload --port 8001     # P1 API on :8001
# check: curl localhost:8001/health/sync
```
Windows PowerShell equivalent — set each with `$env:VAR="..."` on its own line,
then run the same `uvicorn` command.

### 4b. Give P2 its own database (realistic cross-DB sync)
This mirrors prod (P1 and P2 are separate databases). Clone the schema from the
restored P1, then empty the rule tables so the sync actually transfers rows:
```bash
docker exec fortress-postgres psql -U fortress -d fortress_dev -c "CREATE DATABASE fortress_p2 OWNER fortress;"
docker exec fortress-postgres sh -c "pg_dump -U fortress -s fortress_dev | psql -U fortress -d fortress_p2 -q"
docker exec fortress-postgres psql -U fortress -d fortress_p2 -c "DELETE FROM alembic_version; INSERT INTO alembic_version VALUES ('051');"
docker exec fortress-postgres psql -U fortress -d fortress_p2 -c "TRUNCATE compliance_rules; TRUNCATE local_sync_status;"
```
> Simpler option: skip this and let P2 share `fortress_dev`. The sync code still
> runs, but it's a no-op (source == target). Use the separate `fortress_p2` DB
> when you want to *see* rows move.

### 4c. Register a dev client in P1 so sync auth passes
`UpdateService` authenticates to P1 with an API key; P1 stores `sha256(key)`.
```bash
docker exec fortress-postgres psql -U fortress -d fortress_dev -c "
INSERT INTO client_credentials (id,customer_id,bank_name,api_key_hash,is_active,tier,created_at)
VALUES (gen_random_uuid(),'devbank','Dev Bank',
        encode(digest('devkey-123456','sha256'),'hex'), true,'standard',now())
ON CONFLICT (customer_id) DO UPDATE SET api_key_hash=EXCLUDED.api_key_hash, is_active=true;"
```
(Needs the `pgcrypto` extension for `digest`; it's present in the image. If not:
`CREATE EXTENSION IF NOT EXISTS pgcrypto;` first.)

### 4d. Run the sync (P2 pulls from local P1)
One-shot entry point `scripts/run_sync.py`. It reads `FORTRESS_API_URL`,
`CUSTOMER_ID`, `FORTRESS_API_KEY`:
```bash
# macOS/Linux — P1 API from 4a must be running
PIPELINE_MODE=pipeline2 \
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_p2" \
REDIS_URL="redis://:fortress_dev@localhost:6379" \
FORTRESS_API_URL=http://localhost:8001 \
CUSTOMER_ID=devbank FORTRESS_API_KEY=devkey-123456 \
LLM_MOCK_MODE=true \
python -m scripts.run_sync
```
```powershell
# Windows PowerShell
$env:PIPELINE_MODE="pipeline2"
$env:DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_p2"
$env:REDIS_URL="redis://:fortress_dev@localhost:6379"
$env:FORTRESS_API_URL="http://localhost:8001"
$env:CUSTOMER_ID="devbank"; $env:FORTRESS_API_KEY="devkey-123456"
$env:LLM_MOCK_MODE="true"
python -m scripts.run_sync
```
Verify rows arrived in `fortress_p2`:
```bash
docker exec fortress-postgres psql -U fortress -d fortress_p2 -c "SELECT count(*) FROM compliance_rules;"
```

---

## 5. Start Pipeline 2 (API)

Point P2 at the same DB you synced into (`fortress_p2` here) and at local P1.

**macOS / Linux**
```bash
PIPELINE_MODE=pipeline2 CLIENT_NAME=devbank \
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_p2" \
REDIS_URL="redis://:fortress_dev@localhost:6379" \
PIPELINE1_API_URL=http://localhost:8001 \
FORTRESS_API_URL=http://localhost:8001 CUSTOMER_ID=devbank FORTRESS_API_KEY=devkey-123456 \
SYNC_INTERVAL_HOURS=0 LLM_MOCK_MODE=true \
DEV_MODE=true PIPELINE2_API_TOKEN=e2e-bypass \
uvicorn src.main_pipeline2:app --reload --port 8002
```
**Windows (PowerShell)** — set each `$env:VAR="..."` then run the `uvicorn` line.

- `SYNC_INTERVAL_HOURS=0` disables the 24h background sync loop (you drive it
  manually via §4d or `POST /internal/sync/trigger`).
- `DEV_MODE=true` + `PIPELINE2_API_TOKEN=e2e-bypass` let the dashboard talk to
  P2 without Cognito (see §6).
- Check: `curl http://localhost:8002/health`.

To (re)sync from the running P2 instead of the one-shot script:
```bash
curl -X POST "http://localhost:8002/internal/sync/trigger?force_initial=true"
curl http://localhost:8002/internal/sync/counts
```

---

## 6. Start the Dashboard

The SPA uses Cognito in prod; locally use the dev-auth bypass. P2 must be
running with `DEV_MODE=true` + `PIPELINE2_API_TOKEN=e2e-bypass` (set in §5).

```bash
cd dashboard
npm install            # one-time

# macOS/Linux
VITE_E2E_AUTH_BYPASS=1 npm run dev        # → http://localhost:5173

# Windows PowerShell
$env:VITE_E2E_AUTH_BYPASS="1"; npm run dev
```
Open **http://localhost:5173** — must be `localhost` (the bypass is gated to it).
No login screen; you're a dev CCO. Vite proxies `/api` → `:8002`.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `pg_restore: error: could not find extension "vector"` | Restore into the `pgvector/pgvector:pg15` image (the repo's compose postgres), not stock `postgres:15`. |
| Sync transfers 0 rows | P1 API not running / wrong `FORTRESS_API_URL`; or P2 points at the same DB as P1 (source == target) — use a separate `fortress_p2` (§4b). |
| `401` from sync | `client_credentials` row missing or key mismatch — re-run §4c; key must equal `FORTRESS_API_KEY` (`devkey-123456`). |
| Dashboard shows no data | P2 missing `DEV_MODE=true` + `PIPELINE2_API_TOKEN=e2e-bypass`, or Vite missing `VITE_E2E_AUTH_BYPASS=1`. |
| Dump/restore very slow | `ofac_sdn_vectors` HNSW index rebuild dominates; expected for the full dataset. |

See also: `README.local.md` (general local run) and `docs/setup/local-dev.md`
(hot-reload, VS Code debugger, deeper sync notes).
