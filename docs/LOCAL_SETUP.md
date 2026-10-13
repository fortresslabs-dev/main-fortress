# Fortress — Local Setup (DB restore + AWS connect)

Local-reproduction runbook for running Pipeline 1 / Pipeline 2 against a restored
production dump, with AWS (Secrets Manager / SSM / Bedrock) reachable from the Mac.

> **Status (verified 2026-10-04):** P1 dump restored into local Postgres; AWS
> profile `fortress-local` verified against STS, Secrets Manager, SSM and Bedrock
> (Claude Haiku 4.5 + Titan Embed v2). **P2 live:** `fortress_p2` populated from P1 +
> testbank seeded; P2 API on `:8002` and CCO dashboard on `:5173` serving real data (§4).

---

## 0. Prerequisites

- Docker Desktop running
- AWS CLI v2 (`which aws`) and `jq`
- Working dir: `fortress_server/`
- No local `psql`/`pg_restore` needed — we use the ones inside the pgvector container.

---

## 1. Local database — restore a production dump

The dump `fortress_p1_YYYYMMDD-HHMMSSZ.dump` is a **custom-format** pg_dump (starts
with `PGDMP`) → restore with `pg_restore`, not `psql`. It has `vector(1024)` columns,
so the `pgvector/pgvector:pg15` image (already in `docker-compose.yml`) is required.

```bash
cd fortress_server

# 1a. Start Postgres (pgvector) + Redis
make up                      # docker compose up -d postgres redis  → container "fortress-postgres"

# 1b. Fresh DB + vector extension (extension MUST exist before restore)
docker compose exec -T postgres psql -U fortress -d postgres  -c "DROP DATABASE IF EXISTS fortress_dev;"
docker compose exec -T postgres psql -U fortress -d postgres  -c "CREATE DATABASE fortress_dev OWNER fortress;"
docker compose exec -T postgres psql -U fortress -d fortress_dev -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 1c. Copy the dump into the container, then restore
docker cp ~/Downloads/jolly/fortress_p1_20261003-061959Z.dump fortress-postgres:/tmp/p1.dump
docker compose exec -T postgres pg_restore -U fortress -d fortress_dev \
  --no-owner --no-privileges -j4 /tmp/p1.dump
#   --no-owner --no-privileges : REQUIRED — the dump references the Aurora master
#                                role which does not exist locally.
#   -j4                        : parallel restore (~2-4 min for 245 MB).

# 1d. Verify
docker compose exec -T postgres psql -U fortress -d fortress_dev -c "\dt"
docker compose exec -T postgres psql -U fortress -d fortress_dev -c "
SELECT 'compliance_rules' t, count(*) FROM compliance_rules
UNION ALL SELECT 'ofac_sdn_entries', count(*) FROM ofac_sdn_entries
UNION ALL SELECT 'obligations', count(*) FROM obligations;"
```

**Last verified restore (P1 dump 2026-10-03):** 49 public tables + `rulegen` schema.

| table | rows |
|---|---|
| compliance_rules | 301 |
| obligations | 88 |
| ofac_sdn_entries | 19,779 |
| ofac_sdn_vectors | 51,499 |
| enforcement_actions | 2,150 |
| compliance_deadlines | 10 |
| regulatory_change_records | 53,043 |

Connection string (host tools / app): `postgresql://fortress:fortress_dev@localhost:5432/fortress_dev`

---

## 2. AWS connect from local

### How config resolves
`src/utils/config.py` only pulls from Secrets Manager / SSM when `SECRETS_SOURCE=aws`.
Locally leave it empty → everything falls back to `.env`. boto3 (Bedrock, S3, etc.)
uses the **default credential chain**, i.e. `~/.aws/credentials`.

### Credentials — stored in `~/.aws`, NEVER in the repo
Added as a **named profile** `fortress-local` so the pre-existing `[default]`
profile is untouched. The real secret lives only in `~/.aws/credentials`.

```ini
# ~/.aws/credentials
[fortress-local]
aws_access_key_id     = AKIA............ZERIZ        # IAM user "jamie" (acct 520385696621)
aws_secret_access_key = <REDACTED — see ~/.aws/credentials>
```
```ini
# ~/.aws/config
[profile fortress-local]
region = us-east-1
output = json
```
```bash
chmod 600 ~/.aws/credentials ~/.aws/config
export AWS_PROFILE=fortress-local          # or add AWS_PROFILE=fortress-local to .env
```

### Verify (all four passed on 2026-10-04)
```bash
export AWS_PROFILE=fortress-local

aws sts get-caller-identity
# → arn:aws:iam::520385696621:user/jamie

aws secretsmanager get-secret-value --secret-id fortress/pipeline1/app-config \
  --query 'SecretString' --output text | jq 'keys'
# → [AWS_REGION, BEDROCK_MODEL, DATABASE_URL, ENVIRONMENT, P1_OBLIGATION_EXTRACTION_MODEL,
#    PIPELINE_MODE, REDIS_URL, S3_BUCKET, S3_CHUNK_PREFIX, SECRETS_SOURCE]

aws ssm get-parameters-by-path --path /fortress/testbank --recursive --with-decryption \
  --query 'Parameters[].Name' --output text | tr '\t' '\n'
```

### Bedrock model access (console, one-time)
Console → Bedrock → **Model access** → enable Claude Sonnet 4.5 + Claude Haiku 4.5
in `us-east-1`. Smoke test (AWS CLI v2 needs `--cli-binary-format raw-in-base64-out`):

```bash
aws bedrock-runtime invoke-model \
  --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 --region us-east-1 \
  --cli-binary-format raw-in-base64-out --content-type application/json \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":16,"messages":[{"role":"user","content":"ping"}]}' \
  /tmp/bedrock.json && cat /tmp/bedrock.json          # → "Pong!"

aws bedrock-runtime invoke-model \
  --model-id amazon.titan-embed-text-v2:0 --region us-east-1 \
  --cli-binary-format raw-in-base64-out --content-type application/json \
  --body '{"inputText":"ping","dimensions":1024,"normalize":true}' \
  /tmp/titan.json                                      # → 1024-dim embedding
```

---

## 3. Two ways to run

| Mode | AWS needed | What you get |
|---|---|---|
| **A — data only** | none | Rules/OFAC/obligations served from the restored DB. Bedrock embed / enrichment steps warn-and-skip (best-effort). |
| **B — full** | `fortress-local` profile + Bedrock model access | Embeddings, obligation extraction, enrichment all run. |

S3 export + CloudWatch metrics are best-effort — they skip cleanly when `S3_BUCKET`
is unset / without the permission. SSM + Secrets Manager are **not** used locally
unless you set `SECRETS_SOURCE=aws`.

---

## 4. Pipeline 2 + dashboard (testbank) — WORKING

Host has **no Python deps / no venv**, so all P2 backend work runs through the
Docker `pipeline2` image. We use a **dedicated `fortress_p2` database** (P1 source
stays in `fortress_dev`), populated from the same P1 dump, then seed the bank layer.

The CCO dashboard (Vite SPA) talks to the **P2 API on `:8002`** (proxy in
`dashboard/vite.config.js`); it never hits Postgres directly.

### 4.1 Create + populate the P2 database from P1
```bash
docker compose exec -T postgres psql -U fortress -d postgres     -c "DROP DATABASE IF EXISTS fortress_p2;"
docker compose exec -T postgres psql -U fortress -d postgres     -c "CREATE DATABASE fortress_p2 OWNER fortress;"
docker compose exec -T postgres psql -U fortress -d fortress_p2   -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker compose exec -T postgres pg_restore -U fortress -d fortress_p2 --no-owner --no-privileges -j4 /tmp/p1.dump
```

### 4.2 Two schema-drift fixes (dump is alembic 050; this code is 048)
```bash
# (a) seed_testbank expects compliance_rules.regulation_name (dropped by 050) — re-add nullable
docker compose exec -T postgres psql -U fortress -d fortress_p2 -c \
  "ALTER TABLE compliance_rules ADD COLUMN IF NOT EXISTS regulation_name TEXT;"
# (b) dump's workflowstatus enum is UPPERCASE; the 048 code queries lowercase — add lowercase labels
for v in pending running waiting_approval completed failed; do
  docker compose exec -T postgres psql -U fortress -d fortress_p2 -c "ALTER TYPE workflowstatus ADD VALUE IF NOT EXISTS '$v';"
done
```

### 4.3 Seed the testbank bank-side tables (projected from the P1 data)
Uses `scripts/seed_testbank_local.py` — a no-SSL local wrapper around the real
`scripts/seed_testbank_dashboard.py` (the prod seed hardcodes `ssl="require"`).
```bash
docker compose run --rm --no-deps \
  -e SEED_DB_HOST=postgres -e SEED_DB_NAME=fortress_p2 \
  -e SEED_DB_USER=fortress -e SEED_DB_PASSWORD=fortress_dev \
  -v "$PWD":/app pipeline2 python -m scripts.seed_testbank_local
# → bank_profile=1 officers=1 controls=297 mappings=297 findings=50 ofac_validations=1 dispositions=50
```

### 4.4 Run the P2 API on :8002 (uvicorn directly — skip entrypoint's `alembic upgrade head`,
which would fail because the dump is at revision 050 and this code only knows 048)
```bash
docker compose run --rm --service-ports --no-deps --name fortress-p2-api \
  -e DATABASE_URL=postgresql+asyncpg://fortress:fortress_dev@postgres:5432/fortress_p2 \
  -e PIPELINE_MODE=pipeline2 -e CLIENT_NAME=testbank \
  -e DEV_MODE=true -e PIPELINE2_API_TOKEN=e2e-bypass \
  -e SECRETS_SOURCE= -e COGNITO_USER_POOL_ID= \
  pipeline2 uvicorn src.main_pipeline2:app --host 0.0.0.0 --port 8000
```
Auth note: `DEV_MODE=true` + `PIPELINE2_API_TOKEN=e2e-bypass` makes the API accept
`Authorization: Bearer e2e-bypass` (role via `X-E2E-Role`, default CCO).

### 4.5 Run the dashboard SPA on :5173
`dashboard/.env.local` sets `VITE_E2E_AUTH_BYPASS=1` (localhost-only Cognito bypass;
SPA then sends `Bearer e2e-bypass`) and `VITE_API_URL=http://localhost:8002`.
```bash
cd dashboard && npm install && npm run dev      # → http://localhost:5173/
```

### 4.6 Verified working (2026-10-04)
All `/api/v1/dashboard/*` endpoints return 200. Sample `overview`:
`total_rules=297 · pending_changes=53043 · regulators_count=14 · nearest_deadline="FFIEC Call Report — Q3" (27 days) · role=CCO`.
Seeded panels (findings, controls, OFAC validation, change dispositions) populate from
the P1 data; P1-native panels (coverage, risk, enforcement, deadlines, obligations,
changes) read the replicated reference tables.

> The UpdateService thread in P2 will log sync-retry warnings because no live P1 API
> is running on `:8001` — harmless; the data was loaded directly via the dump restore.

---

## 4.7 Live P1→P2 sync (the real UpdateService path)

Instead of loading P2 directly from the dump, exercise the actual sync: P1 serves
`/api/sync/*` from `fortress_dev`; P2's `UpdateService` pulls over HTTP into
`fortress_p2`. `_get_local_version()` reads `max(local_sync_status.sync_version)`
(empty → 0 → **initial** sync; populated → **incremental**).

```bash
# 1. Register a sync credential in the P1 source DB (sha256 of a raw key)
RAW=$(python3 -c "import secrets;print(secrets.token_hex(32))"); echo "$RAW" > /tmp/testbank_api_key.txt
HASH=$(python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$RAW")
docker compose exec -T postgres psql -U fortress -d fortress_dev -c \
 "INSERT INTO client_credentials (id,customer_id,bank_name,api_key_hash,is_active,tier,created_at)
  VALUES (gen_random_uuid(),'testbank','Test Bank, N.A.','$HASH',true,'standard',now())
  ON CONFLICT (customer_id) DO UPDATE SET api_key_hash=EXCLUDED.api_key_hash, is_active=true;"

# 2. Bring up P1 API on :8001 against fortress_dev (uvicorn direct; scraper off)
docker compose build pipeline1
docker compose run --rm --service-ports --no-deps --name fortress-p1-api \
  -e DATABASE_URL=postgresql+asyncpg://fortress:fortress_dev@postgres:5432/fortress_dev \
  -e PIPELINE_MODE=pipeline1 -e SECRETS_SOURCE= -e SCRAPER_IN_PROCESS=false \
  pipeline1 uvicorn src.main_pipeline1:app --host 0.0.0.0 --port 8000     # background it

# 3. Smoke-test auth (correct key → counts; wrong key → 401)
curl -s -X POST localhost:8001/api/sync/counts -H "X-Api-Key: $(cat /tmp/testbank_api_key.txt)" \
     -H "X-Customer-Id: testbank" -H "Content-Type: application/json" -d '{}'

# 4. Run the sync one-off into fortress_p2 (reach host P1 via host.docker.internal)
docker compose run --rm --no-deps \
  -e DATABASE_URL=postgresql+asyncpg://fortress:fortress_dev@postgres:5432/fortress_p2 \
  -e PIPELINE_MODE=pipeline2 -e SECRETS_SOURCE= \
  -e FORTRESS_API_URL=http://host.docker.internal:8001 \
  -e FORTRESS_API_KEY="$(cat /tmp/testbank_api_key.txt)" -e CUSTOMER_ID=testbank \
  -v "$PWD":/app pipeline2 python -c "
import asyncio, json
from src.services.database import init_db, close_db
from src.services.update_service import UpdateService
async def go():
    await init_db()
    try: print('SYNC_RESULT='+json.dumps(await UpdateService().sync_all(), default=str))
    finally: await close_db()
asyncio.run(go())"
```

### Schema-drift fixes the sync needs on fortress_p2 (dump 050 vs code 048)
All additive / local-only; each maps to a concrete failure seen during the run:
```sql
-- (a) raw-SQL inserts omit id and rely on a server default the dump lacks
DO $$ DECLARE r RECORD; BEGIN
  FOR r IN SELECT table_name FROM information_schema.columns
           WHERE table_schema='public' AND column_name='id'
             AND udt_name='uuid' AND column_default IS NULL LOOP
    EXECUTE format('ALTER TABLE public.%I ALTER COLUMN id SET DEFAULT gen_random_uuid()', r.table_name);
  END LOOP; END $$;
ALTER TABLE sync_metadata ALTER COLUMN id SET DEFAULT gen_random_uuid();   -- on fortress_dev too
-- (b) 050 typed applicability_* as text[]; the 048 insert binds jsonb
ALTER TABLE compliance_rules ALTER COLUMN applicability_charter   TYPE jsonb USING to_jsonb(applicability_charter);
ALTER TABLE compliance_rules ALTER COLUMN applicability_activities TYPE jsonb USING to_jsonb(applicability_activities);
ALTER TABLE compliance_rules ALTER COLUMN applicability_states    TYPE jsonb USING to_jsonb(applicability_states);
-- (c) drop 050-era authority/citation CHECK gates the 048 sync can't satisfy
ALTER TABLE compliance_rules DROP CONSTRAINT IF EXISTS ck_cr_law_requires_citation;
ALTER TABLE compliance_rules DROP CONSTRAINT IF EXISTS ck_law_needs_verification;
ALTER TABLE compliance_rules DROP CONSTRAINT IF EXISTS ck_cr_verified_implies_law;
ALTER TABLE compliance_rules DROP CONSTRAINT IF EXISTS ck_cr_verified_source_present;
ALTER TABLE compliance_rules DROP CONSTRAINT IF EXISTS ck_cr_authority_type_domain;
ALTER TABLE compliance_rules DROP CONSTRAINT IF EXISTS ck_cr_guidance_forbids_citation;
-- (d) compliance_deadlines NOT NULL cols the raw insert omits
ALTER TABLE compliance_deadlines ALTER COLUMN has_test_coverage SET DEFAULT false;
ALTER TABLE compliance_deadlines ALTER COLUMN test_coverage_count SET DEFAULT 0;
ALTER TABLE compliance_deadlines ALTER COLUMN obligation_count SET DEFAULT 0;
ALTER TABLE compliance_deadlines ALTER COLUMN uncovered_obligation_count SET DEFAULT 0;
ALTER TABLE compliance_deadlines ALTER COLUMN is_recurring SET DEFAULT false;
ALTER TABLE compliance_deadlines ALTER COLUMN is_active SET DEFAULT true;
```
Re-run a fresh initial after fixes with `TRUNCATE local_sync_status;` first.

### Verified (2026-10-05)
- Initial sync: `rules_added=301 enforcement_actions=2150 obligations=88 ofac_sdn_entries=19779 deadlines=8 change_records=1000 (per-run cap) status=SUCCESS version=76`.
- Re-run → `sync_type=incremental from_version=76 rules_added=0` (version handshake + idempotency proven).
- Dashboard `overview` after sync: `total_rules=314 · regulators_count=16 · pending_changes=53043`.
- Auth: correct key → 200 counts; wrong key → 401.
- `change_records` is capped per sync (~1000); run incremental repeatedly to pull all 53,043.

## 5. Security notes

- **This IAM key is `AdministratorAccess` and was shared in plaintext.** Rotate it
  once local repro is done:
  ```bash
  aws iam create-access-key --user-name jamie          # make the replacement first
  aws iam delete-access-key --user-name jamie --access-key-id AKIA............ZERIZ
  ```
  (2-key IAM limit — you're at 2/2: this Mac key + the VM key. Delete one before creating another.)
- Prefer scoping to a least-privilege user + MFA for laptop use.
- Never commit `~/.aws/*` or a populated `.env`. Keep the secret out of any repo file
  (this doc keeps it redacted on purpose).
