# Fortress — Developer Setup

Get Pipeline 1 (scraper + sync API), Pipeline 2 (bank-side API), and the CCO
dashboard running locally. Postgres and Redis run as Docker containers — you do
**not** need to install a database natively.

> Reproducing a production **data dump** locally (restore + sync) is a separate,
> more advanced flow — see [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md). This guide
> gets the stack running on a **fresh, empty database**.

---

## Architecture at a glance

```
┌─ Pipeline 1 (:8001) ── scraper + /api/sync/* ─┐
│                                               │  one PostgreSQL 15 (pgvector)
├─ Pipeline 2 (:8002) ── validators + dashboard │  + one Redis
│         ▲                                      │
└─ Dashboard SPA (:5173) ──► proxies to :8002 ──┘
```

| Component | Port | Served by |
|---|---|---|
| Pipeline 1 API | 8001 | `docker compose` service `pipeline1` |
| Pipeline 2 API | 8002 | `docker compose` service `pipeline2` |
| Dashboard (Vite dev server) | 5173 | `npm run dev` in `dashboard/` |
| PostgreSQL (pgvector:pg15) | 5432 | `docker compose` service `postgres` |
| Redis | 6379 | `docker compose` service `redis` |

---

## 1. Prerequisites

| Tool | Version | Needed for | Install (macOS) |
|---|---|---|---|
| **Docker Desktop** | current | Postgres, Redis, P1, P2 | https://www.docker.com/products/docker-desktop/ |
| **Node.js + npm** | 18 LTS or newer | the dashboard SPA | `brew install node` |
| Git | any | cloning | `brew install git` |
| AWS CLI v2 | optional | Bedrock embeddings/LLM, SSM (production) | `brew install awscli` |
| Python 3.11 | optional | running P1/P2 on the host instead of Docker | `brew install python@3.11` |

Docker Desktop bundles `docker compose`, `pg_restore`, and `psql` (inside the
Postgres container) — nothing else to install for the database.

Verify Docker is running: `docker info` should succeed.

---

## 2. Configure environment — `.env` from `.env.example`

The repo ships **`.env.example`** — a documented template with placeholders and
**no secrets**. Copy it to `.env` (which is gitignored) and adjust:

```bash
cd fortress_server
cp .env.example .env
```

For a **local run, leave `SECRETS_SOURCE` empty** (the app reads `.env`). The
defaults in `.env.example` already work locally; the keys that matter for the
dashboard to load are:

```bash
SECRETS_SOURCE=                 # empty = read .env (local). "aws" = pull from SSM/Secrets Manager (prod)
DEV_MODE=true                   # enables the dev-token auth path
PIPELINE2_API_TOKEN=e2e-bypass  # token the dashboard sends in dev
COGNITO_USER_POOL_ID=           # leave BLANK locally (forces the dev-token path, not Cognito)
# MUST be a JSON array — pydantic-settings JSON-decodes list env vars; a comma string errors:
ALLOWED_ORIGINS=["http://localhost:5173","http://localhost:3000"]
LLM_PROVIDER=bedrock
AWS_REGION=us-east-1
```

Fill real values only for the integrations you actually use:
- **JIRA** (`JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`) — only to use the dashboard's **"Create story"** action.
- **GitHub** (`GITHUB_TOKEN/ORG/REPO`) — only for the test-generation PR step (skips cleanly if unset).

> `.env` holds credentials — never commit it. `.env.example` is the only env
> file that is committed (it's explicitly un-ignored in `.gitignore`).

---

## 3. Start Postgres + Redis

```bash
make up
# ≡ docker compose up -d postgres redis   (image: pgvector/pgvector:pg15)
```

---

## 4. Run Pipeline 1 + Pipeline 2

### Option A — Docker (recommended, no host Python needed)

```bash
docker compose up -d           # builds images (first run is slow), runs migrations, serves P1+P2
```

Each service's entrypoint runs `alembic upgrade head` against the empty database
(creating the full schema) and then starts its API. Check them:

```bash
curl http://localhost:8001/health      # Pipeline 1
curl http://localhost:8002/health      # Pipeline 2
docker compose logs -f pipeline2       # follow logs
```

### Option B — on the host (if you prefer, needs Python 3.11)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-pipeline1.txt      # or requirements-pipeline2.txt
make migrate                                   # alembic upgrade head
make p1        # Pipeline 1 on :8001
make p2        # Pipeline 2 on :8002  (separate terminal)
```

---

## 5. Run the dashboard

The dashboard is a Vite React SPA that calls the Pipeline 2 API. It has its own
env file — copy its template and enable the localhost auth bypass:

```bash
cd dashboard
cp .env.example .env.local
```

Set in `dashboard/.env.local`:
```bash
VITE_API_URL=http://localhost:8002
VITE_E2E_AUTH_BYPASS=1          # localhost-only: skip Cognito, send Bearer e2e-bypass
VITE_BANK_ID=testbank
```

Then:
```bash
npm install
npm run dev                     # http://localhost:5173
```

Open **http://localhost:5173** — no login required in dev.

---

## 6. Verify

```bash
# APIs
curl http://localhost:8001/health && echo
curl http://localhost:8002/health && echo

# A dashboard endpoint (dev token + CORS origin)
curl -s -H "Authorization: Bearer e2e-bypass" -H "X-E2E-Role: CCO" \
     -H "Origin: http://localhost:5173" \
     http://localhost:8002/api/v1/dashboard/overview
```

On a fresh empty database these return `200` with **zero counts** — that's
expected. Load data next.

---

## 7. Put data in it (the DB starts empty)

Pick one:

- **Restore a provided dump** (fastest way to see a full dashboard) — follow
  [`docs/LOCAL_SETUP.md`](docs/LOCAL_SETUP.md). It also covers the
  `docker-compose.local-reproduction.yml` overlay and the P1→P2 sync.
- **Run a scrape** (needs AWS Bedrock access): `python -m scripts.run_scrape`
  populates `compliance_rules`, OFAC, obligations, etc. from the live sources.
- **Seed the bank-side demo tables** (bank profile, controls, findings) so the
  CCO panels render: see `scripts/seed_testbank_dashboard.py` /
  `scripts/seed_demo_p2.py`.

---

## 8. AWS / Bedrock (optional)

Only needed for embeddings, the LLM extraction/test-generation, or pointing at
production. Configure credentials once:

```bash
aws configure                  # or: aws sso login --profile <name>
export AWS_PROFILE=<name>
aws sts get-caller-identity    # confirm
```

When running P1/P2 **in Docker**, the container needs the credentials too — mount
your `~/.aws` into the container (the local-reproduction overlay does this and
sets `AWS_SHARED_CREDENTIALS_FILE`). Bedrock also requires one-time **model
access** enablement in the AWS console (Bedrock → Model access).

To run against **production** instead of local, set `SECRETS_SOURCE=aws` — config
is then injected from SSM `/fortress/<CLIENT_NAME>/*` + Secrets Manager.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Dashboard panels blank, "Could not load …" | **CORS.** `ALLOWED_ORIGINS` in `.env` must include `http://localhost:5173` **as a JSON array**, and the P2 API must be restarted after changing it. |
| `error parsing value for field "ALLOWED_ORIGINS"` on P2 start | It's a comma string — use a JSON array: `["http://localhost:5173"]`. |
| Dashboard shows a Cognito login screen | `VITE_E2E_AUTH_BYPASS=1` not set in `dashboard/.env.local`, or you're not on `localhost`. |
| `401 Unauthorized` from API | `DEV_MODE=true` + `PIPELINE2_API_TOKEN` must be set on the P2 API, and the SPA token must match. |
| `502 JIRA story creation failed` on "Create story" | JIRA isn't configured — set `JIRA_URL/JIRA_USERNAME/JIRA_API_TOKEN/JIRA_PROJECT_KEY` in `.env` and restart P2. |
| `port is already allocated` | Something is on 8001/8002/5173/5432 — stop it or change the mapping. |
| `alembic ... Can't locate revision` on startup | You restored a dump from a **newer** schema than this code. Don't auto-migrate — see `docs/LOCAL_SETUP.md`. |
| Host run: `ModuleNotFoundError` | Host Python deps missing — use Docker (Option A) or `pip install -r requirements-pipeline2.txt`. |

---

## 10. Stop / reset

```bash
make down                         # stop all services (keeps data volume)
docker compose down -v            # stop AND delete the postgres/redis data volumes (full reset)
```

Config lives in: `.env` (your secrets, gitignored), `.env.example` (template),
`dashboard/.env.local` (SPA), `docker-compose.yml` (base), and
`docker-compose.local-reproduction.yml` (prod-data overlay).
