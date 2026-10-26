# Runbook — Backfill missing `compliance_rules` embeddings (production)

**When to use:** semantic/RAG retrieval is missing a slice of rules — e.g. a
block of rules (the FinCEN 31 CFR Part 1010 set) never surfaces in Agent 2's
hybrid search, or `compliance_rules.embedding IS NULL` for active rules. Symptom
seen in the field: generate-test obligation coverage lands on RAG-neighbour
rules instead of the clicked rule, because the clicked rule had no vector and
fell out of the top-k.

> Vectors are **Titan Embed v2** (`amazon.titan-embed-text-v2:0`, 1024-dim,
> normalized) stored in `compliance_rules.embedding vector(1024)` in **P1's**
> Aurora. P2 receives them by sync — see §4. There is no separate vector DB.

---

## 0. Background — how embeddings normally get written

The embedding phase is **part of the P1 scrape cycle**:
`scraper_scheduler._run_scrape_cycle()` → `_embed_and_store_rules()`
(`src/services/scraper_scheduler.py`). It selects every active rule
`WHERE embedding IS NULL AND rule_text IS NOT NULL`, embeds
`rule_text + cfr_citation + category` via Titan v2, and writes the vector back.
It is **idempotent and additive** — it only touches NULL-embedding rows, so it
is always safe to re-run.

Prod schedule: EventBridge fires `EcsTaskDefScraperP1` daily at **02:00 UTC**
(`Command: python3 -m scripts.run_scrape`), which runs the full cycle including
this embed phase. So a gap means either the embed phase errored
(`embedding_phase_failed` in logs) or rules were loaded by a path that bypassed
it (e.g. a direct ingest of the Part 1010 block).

---

## 1. Detect the gap (P1 Aurora)

```sql
-- How many active rules lack a vector, and which slice?
SELECT count(*) FROM compliance_rules WHERE embedding IS NULL AND is_active;

SELECT category, count(*)
FROM compliance_rules
WHERE embedding IS NULL AND is_active
GROUP BY 1 ORDER BY 2 DESC;

-- Spot-check a known rule
SELECT rule_id, (embedding IS NOT NULL) AS has_vec
FROM compliance_rules WHERE rule_id = 'FINCEN-1010-660-R3';
```

Also check the P1 logs for a failed embed phase:
```bash
aws logs tail /ecs/fortress-pipeline1-production --since 48h \
  | grep -E 'embedding_phase_failed|embedding_start|embedding_complete'
```

---

## 2. Prerequisites

- **Bedrock Titan v2 access** on the **P1 ECS task role** — `bedrock:InvokeModel`
  on `amazon.titan-embed-text-v2:0` in the deploy region (`us-east-1`). Verify:
  ```bash
  aws bedrock list-foundation-models --region us-east-1 \
    --query "modelSummaries[?modelId=='amazon.titan-embed-text-v2:0'].modelId"
  ```
- P1 can reach its Aurora (it always can from within the task).
- Confirm the cluster / service / task-def names from the stack outputs before
  running (names below follow `CLAUDE.md`; verify against
  `infra/cloudformation/pipeline1-compute.yml` for your environment):
  - cluster `fortress-pipeline1-production`
  - scraper task family `fortress-pipeline1-scraper-production`
  - API service `fortress-pipeline1-api-production`

---

## 3. Backfill on P1 — choose one

### Option A — surgical: run the backfill script (recommended)
No scrape, no network crawl — just heal the NULL rows. `scripts/backfill_embeddings.py`
is idempotent/additive (only `embedding IS NULL` rows) and covers **both**
`compliance_rules` and `obligations` (the latter needs migration 054). It uses
the app DB layer, so it runs unchanged on P1, on a bank's P2, or locally.

```bash
# locally / in any container with DB + Bedrock access:
python -m scripts.backfill_embeddings --target both          # or: --target rules
python -m scripts.backfill_embeddings --target both --dry-run # count only

# on prod P1 via ECS Exec into the running API task:
TASK=$(aws ecs list-tasks --cluster fortress-pipeline1-production \
  --service-name fortress-pipeline1-api-production \
  --desired-status RUNNING --query 'taskArns[0]' --output text)
aws ecs execute-command --cluster fortress-pipeline1-production \
  --task "$TASK" --container pipeline1 --interactive \
  --command 'python -m scripts.backfill_embeddings --target both'
```
Logs `backfill_start … missing=N` then `backfill_complete … embedded=N failed=M`,
and exits non-zero if any embed call failed.

> ECS Exec needs `enableExecuteCommand` on the service + SSM perms on the task
> role. If Exec isn't available, use Option B.

### Option B — run the scraper task (full cycle; heals NULLs as a side effect)
Fire the same task EventBridge uses. Heavier (it also crawls connectors) but
needs no Exec and is the most "stock" path.

```bash
aws ecs run-task --cluster fortress-pipeline1-production \
  --task-definition fortress-pipeline1-scraper-production \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<private-subnets>],securityGroups=[<sg>],assignPublicIp=DISABLED}" \
  --count 1
# then watch:
aws logs tail /ecs/fortress-pipeline1-production --follow \
  | grep -E 'embedding_start|embedding_complete|embedding_failed'
```

### Verify on P1
```sql
SELECT count(*) FROM compliance_rules WHERE embedding IS NULL AND is_active;  -- expect 0
```

---

## 4. Propagate to each bank's P2

P2 pulls pre-computed vectors from P1 via **`POST /api/sync/embeddings`**
(`update_service._sync_embeddings`), then runs `pgvector ANALYZE`
(`_refresh_vector_index`). This happens automatically on P2's next scheduled
sync; to apply immediately, trigger a sync for the bank.

Per bank P2 (run inside that bank's P2 task, or hit its internal endpoint):
```bash
# one-shot sync entrypoint
python -m scripts.run_sync
# — or, against a running P2 API with internal auth —
curl -X POST "https://fortressapi.<bank>.com/internal/sync/trigger" \
  -H "X-Internal-Secret: $INTERNAL_SYNC_SECRET"
```

### Verify on P2
```sql
SELECT count(*) FROM compliance_rules WHERE embedding IS NULL AND is_active;  -- expect 0
```
Retrieval smoke test (run from a P2 task that has Titan access): embed a themed
query and confirm the previously-missing rules now rank — e.g. a correspondent-
account query should return `FINCEN-1010-6xx` rules in the top results.

---

## 5. Safety / rollback

- **Idempotent:** only rows with `embedding IS NULL` are touched; re-running is a
  no-op for already-embedded rows. No deletes, no row churn.
- **Additive:** writes the `embedding` column (+ `updated_at`) only. Existing
  rule text, obligations, and sync version are untouched.
- **Cost:** one Titan v2 call per rule (~$0.00002/1k tokens); a few hundred rules
  is cents.
- **Dimension must stay 1024** (`"dimensions":1024,"normalize":true`) to match
  the `vector(1024)` column and the resolver's query embeddings. Do not change.
- No rollback needed; if an embedding is ever suspect, set it back to NULL and
  re-run this runbook.

---

## 6. Prevent recurrence

- Alarm on `embedding_phase_failed` in the P1 scraper logs, and on a nightly
  `SELECT count(*) WHERE embedding IS NULL AND is_active > 0` check.
- Any new bulk-ingest path for rules (outside the normal scrape) must run the
  embed phase afterwards, or leave the rows for the nightly cycle to heal.

---

## Appendix — local reproduction (dev)

The same gap was fixed locally on 2026-10-15 for the 175-rule Part 1010 block in
`fortress_p2` and `fortress_dev`. Locally, `scraper_scheduler` is stripped from
the P2 image, so a standalone script (Titan v2 + asyncpg, mirroring
`_embed_and_store_rules`) was run **inside the P2 container** (the only local
container with AWS creds — see `docker-compose.override.yml`), targeting each DB
on the compose Postgres. Result: 175/175 embedded per DB, 0 NULL remaining. See
`docs/setup/local-dev.md` for running services locally.
