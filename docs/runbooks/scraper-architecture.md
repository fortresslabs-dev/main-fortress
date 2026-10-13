# Scraper architecture — storage, S3-first, S3-diff, rule validity

## Scraper storage — where data goes per cycle

Each scrape cycle writes to four destinations in this order:

```
17 connectors fetch raw content
        │
        ├─ 1. Aurora (compliance_rules, ofac_sdn_entries)     ← structured rules
        │        hash compare → only changed rules written
        │        sync_version bumped
        │
        ├─ 2. Bedrock Titan Embed v2                           ← embeddings
        │        called ONLY for new/changed rules (delta)
        │        stores vector(1024) in Aurora embedding column
        │
        ├─ 3. OpenSearch Serverless                            ← BM25 + k-NN
        │        indexes changed rules only
        │        both text (BM25) and embedding (HNSW) updated
        │
        └─ 4. S3 raw chunks + MCP scrape monitor log          ← audit trail
                 s3://fortress-p1-data/scraper-chunks/{type}/{date}/
                 s3://fortress-p1-data/scrape-logs/{date}/run_{ts}.json
```

**One env var required for S3 to work — must be in ECS task definition:**
```yaml
# In infra/cloudformation/pipeline1-compute.yml EcsTaskDefP1 Environment:
- Name: S3_BUCKET
  Value: !Sub
    - "${Bucket}"
    - Bucket:
        Fn::ImportValue: !Sub FortressP1-${Environment}-S3Bucket
- Name: S3_CHUNK_PREFIX
  Value: scraper-chunks
```

Without `S3_BUCKET`, chunk_exporter falls back to `/app/chunks/` (ECS ephemeral — data lost on task exit).

**Verify S3 data is arriving after a scrape:**
```bash
aws s3 ls s3://fortress-p1-data-{ACCOUNT_ID}-production/scraper-chunks/ --recursive | head -20
aws s3 ls s3://fortress-p1-data-{ACCOUNT_ID}-production/scrape-logs/ --recursive | head -10
```

## MCP scrape monitor — `src/mcp_servers/scrape_monitor/server.py`

MCP server (migration 018 adds `scrape_runs` table to Aurora).
Logs every connector result to S3 + Aurora + CloudWatch on each scrape cycle.

**Four tools:**

| Tool | When to call | What it does |
|---|---|---|
| `log_scrape_run` | After each connector completes | Writes result to S3, Aurora, CloudWatch metric |
| `log_connector_failure` | On connector crash | Writes FAILURE_ prefixed S3 log, emits error metric |
| `get_scrape_health` | Agent 1 health check | Success rate per connector over last N days |
| `get_failed_connectors` | Daily alert / debug | Lists connectors with failures in last N days |

**CloudWatch metrics emitted:**
- `Fortress/Scraper/ConnectorFailure` — dimension: Connector name
- `Fortress/Scraper/ConnectorSuccess` — dimension: Connector name

**Debug a failing connector:**
```bash
# Via Claude CLI
claude -p "Use get_failed_connectors tool with days=1 and report which connectors failed"

# Via AWS CLI — read the S3 failure log directly
aws s3 cp s3://fortress-p1-data-ACCOUNT-production/scrape-logs/2026/05/09/FAILURE_occ_bsa_20260509_020315.json - | python3 -m json.tool

# Check CloudWatch for failure metric
aws cloudwatch get-metric-statistics \
  --namespace Fortress/Scraper \
  --metric-name ConnectorFailure \
  --dimensions Name=Connector,Value=occ_bsa \
  --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 86400 \
  --statistics Sum \
  --output table
```

## S3-first scraping architecture (recommended next evolution)

**Current approach (direct to DB):**
- Connector → parse → Aurora → Bedrock (all rules) → OpenSearch → S3 chunks
- Problem: Bedrock Titan v2 called for ALL ~200K rules every daily cycle even when 95% haven't changed

**S3-first approach (proposed — 85-90% cost reduction on typical days):**

```
Stage 1 — Raw dump (EventBridge 01:00 UTC, lightweight task):
  Connectors fetch raw HTML/PDF → dump to S3 raw-scrapes/{regulator}/{date}/
  Write manifest.json: {filename → SHA-256 hash}
  Fast, cheap, no LLM, no Aurora writes

Stage 2 — Diff + process (triggered when Stage 1 completes):
  Read today's manifest + yesterday's manifest from S3
  Diff: find new, changed, deleted files by SHA-256 comparison
  Download ONLY changed files from S3 (not regulator sites again)
  Extract rules from changed content only
  Bedrock Titan v2 → embed only new/changed rules
  Aurora upsert → only changed rules
  OpenSearch index → only changed rules
  MCP scrape monitor → log result
```

**Benefits over current approach:**

| | Current | S3-first |
|---|---|---|
| Bedrock calls/day | ~200K rules | ~10K-20K (only changed) |
| Raw data preserved | No (ECS ephemeral) | Yes (S3 forever) |
| Replay old data | Impossible | Yes — reprocess any date |
| Failure recovery | Restart from scratch | Re-run Stage 2 only |
| Scraper audit trail | None | Immutable S3 per cycle |

**S3 bucket structure with S3-first:**
```
s3://fortress-p1-data-{AccountId}/
  raw-scrapes/
    occ_bsa/2026/05/09/bsa_aml_manual.pdf        ← original file
    occ_bsa/2026/05/09/bsa_aml_manual.parsed.json ← extracted text
    manifest/2026/05/09/manifest.json              ← {file → hash} map
  scraper-chunks/
    compliance_rules/2026/05/09/                   ← changed rules only
  scrape-logs/
    2026/05/09/run_{timestamp}.json                ← MCP monitor output
  cloudtrail/
    ...
```

**Implementation plan (after pilot launch):**
1. Create `src/services/s3_raw_dumper.py` — Stage 1 raw dump + manifest writer
2. Create `src/services/s3_diff_engine.py` — compares manifests, returns changed file list
3. Create `src/main_pipeline1_stage2.py` — Stage 2 entry point (reads diff, processes only changes)
4. Update `pipeline1-compute.yml` — add second ECS task definition for Stage 2
5. Update EventBridge — Stage 1 task completion triggers Stage 2

**This replaces the current single-stage scraper loop entirely.**

### S3-diff core logic

```python
# scripts/s3_diff_engine.py — core diff logic
def diff_manifests(today: dict, yesterday: dict) -> dict:
    result = {}
    for name, meta in today.get("connectors", {}).items():
        if name not in yesterday.get("connectors", {}):
            result[name] = "new"
        elif meta["sha256"] != yesterday["connectors"][name]["sha256"]:
            result[name] = "changed"
        else:
            result[name] = "unchanged"  # skip entirely
    for name in yesterday.get("connectors", {}):
        if name not in today.get("connectors", {}):
            result[name] = "deleted"
    return result
```

**Expected savings:** 85-90% fewer Bedrock calls on a typical day. Only OCC BSA won't change on most days — SDN list changes daily but most OCC/FDIC guidance is stable for months.

## Rule validity — five categories of invalid/stale rules

Run the audit first, then fix by category:

```bash
# Full audit report — run against P1 Aurora
python3 scripts/reconcile_rules.py --check

# Fix all issues
python3 scripts/reconcile_rules.py --fix-all

# Fix specific categories
python3 scripts/reconcile_rules.py --fix-embeddings   # Category 2
python3 scripts/reconcile_rules.py --fix-opensearch   # Category 1 + 3
```

### Category 1 — Rule removed from regulatory source

Scraper sets `is_active=FALSE` and writes to `deleted_rules` table (Aurora correct).
**OpenSearch still has the document indexed** — vector search returns it as valid.

```sql
-- Find tombstoned rules
SELECT rule_id, regulator, updated_at FROM compliance_rules
WHERE is_active = FALSE ORDER BY updated_at DESC LIMIT 20;

-- Audit trail
SELECT rule_id, deleted_at_version, deleted_at FROM deleted_rules ORDER BY deleted_at DESC;
```

Fix: `python3 scripts/reconcile_rules.py --fix-opensearch` — deletes each tombstoned rule_id from OpenSearch using `opensearch_client.delete_rule()`.

### Category 2 — Active rule with no embedding

Rule is in Aurora with `embedding IS NULL`. BM25 keyword search finds it; k-NN vector search never returns it. Caused by Bedrock Titan v2 call failing or embedding step not yet wired.

```sql
SELECT regulator, COUNT(*) AS missing
FROM compliance_rules WHERE is_active=TRUE AND embedding IS NULL
GROUP BY regulator ORDER BY missing DESC;
```

Fix: `python3 scripts/reconcile_rules.py --fix-embeddings` — calls Bedrock Titan Embed v2 for each NULL rule, writes `vector(1024)` to Aurora.

### Category 3 — OpenSearch document with no Aurora match

In OpenSearch but `is_active=FALSE` in Aurora, or Aurora record deleted. Search returns it; no corresponding DB record to join against. Detection requires scanning OpenSearch and cross-referencing Aurora.

```bash
python3 -c "
from src.services.opensearch_client import _get_client
c = _get_client()
resp = c.search(index='fortress-compliance-docs',
    body={'query':{'match_all':{}}, '_source':['rule_id'], 'size':1000})
print(f'Total OpenSearch docs: {resp[\"hits\"][\"total\"][\"value\"]}')
"
```

### Category 4 — Rule text changed but embedding not regenerated

Regulation amended → `rule_text` updated in Aurora → but Bedrock re-embedding failed → `embedding` column still reflects old text. Vector search returns this rule for queries matching the old meaning.

**Fix — add tracking column:**
```sql
ALTER TABLE compliance_rules ADD COLUMN IF NOT EXISTS embed_updated_at TIMESTAMPTZ;

-- Find stale: rule updated after embedding was generated
SELECT rule_id, updated_at, embed_updated_at,
       updated_at - embed_updated_at AS staleness
FROM compliance_rules
WHERE is_active=TRUE AND embed_updated_at IS NOT NULL
  AND updated_at > embed_updated_at + INTERVAL '1 minute'
ORDER BY staleness DESC LIMIT 20;
```

### Category 5 — P2 bank running behind P1 version

P2 UpdateService syncs daily. If sync failed, bank has stale or removed rules.

```bash
# Check P2 vs P1 version gap (customer_id + api_key in BODY, not headers)
curl -X POST https://api.fortresslabs.dev/api/sync/counts \
  -H "Content-Type: application/json" \
  -d "{\"customer_id\":\"firstnational\",\"api_key\":\"$P1_API_KEY\",\"from_version\":0}" \
  | python3 -m json.tool

# Force P2 re-sync now
curl -X POST https://fortressapi.firstnationalbank.com/internal/sync/trigger \
  -H "X-Internal-Secret: $FORTRESS_INTERNAL_SECRET"

# Check if tombstoned P1 rules still active in P2
psql $P2_DATABASE_URL -c "
SELECT r.rule_id, r.regulator FROM compliance_rules r
WHERE r.is_active=TRUE AND r.rule_id IN (SELECT rule_id FROM deleted_rules);
"
```
