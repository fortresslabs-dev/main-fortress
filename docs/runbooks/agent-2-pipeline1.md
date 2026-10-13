# Agent 2 — Pipeline 1 code + ingestion

**Persona:** Python/FastAPI engineer focused on P1 scraper ingestion, OpenSearch
indexing, Bedrock embeddings, and the sync API. Knows the 18 regulatory connectors.

**Trigger phrases:**
- "scraper not picking up new rules", "OpenSearch index empty", "embeddings not updating"
- "sync API returning wrong data", "obligation extractor failing", "change detection broken"
- "migrate from Weaviate to OpenSearch"

## P1 code flow — what happens when EventBridge fires

```
EventBridge cron(0 2 * * ? *)
  → ECS RunTask (EcsTaskDefScraperP1, 2 vCPU, 8 GB, Dockerfile.pipeline1)
  → command: ["python3", "-m", "scripts.run_scrape"]    # one-shot, no uvicorn
  → ScraperScheduler._run_scrape_cycle()
      → ConnectorManager.run_all()          # 17 federal + 5 state connectors
          → each connector: scrape → parse → ComplianceDocument
          → ObligationExtractor.extract()   # Bedrock claude-sonnet-4
          → EnforcementRiskScorer.score()
      → for each rule: upsert to Aurora
          → Bedrock Titan Embed v2 → embedding vector(1024)
          → UPDATE compliance_rules SET embedding = $1 WHERE rule_id = $2
          → OpenSearch index: PUT /fortress-compliance-docs/_doc/{rule_id}
      → DeadlineTracker.seed_and_refresh()
      → ChangeManager.detect_and_record()   # SHA-256 hash compare
      → SyncVersion.increment()
  → task exits (one-shot — EventBridge fires next cycle tomorrow)
```

The long-running API service (`EcsTaskDefP1`) serves the sync API and does **not**
run scrapes. The legacy in-process scheduler is gated behind
`SCRAPER_IN_PROCESS=true` and is off in production to avoid double-scraping.

Manually trigger a one-off scrape (any time):
```bash
aws ecs run-task \
  --cluster fortress-pipeline1-production \
  --task-definition $(aws cloudformation list-exports \
      --query "Exports[?Name=='FortressP1-production-EcsTaskDefScraperArn'].Value" \
      --output text) \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<private-subnet>],securityGroups=[<sg-ecs>],assignPublicIp=DISABLED}"
```

## Test the ingestion flow locally

```bash
# 1. Start services
docker compose up postgres redis -d

# 2. Run migrations
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
python -m alembic upgrade head

# 3. Test one connector (OCC BSA)
PIPELINE_MODE=pipeline1 \
DATABASE_URL="postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev" \
python -c "
import asyncio
from src.connectors.regulatory.usa.occ_bsa import OccBsaConnector
from src.services.database import init_db, get_db

async def test():
    await init_db()
    conn = OccBsaConnector()
    results = await conn.fetch()
    print(f'Fetched {len(results)} rules')
    for r in results[:3]:
        print(f'  {r.rule_id}: {r.rule_text[:80]}')

asyncio.run(test())
"

# 4. Verify Aurora has pgvector extension
psql -h localhost -U fortress -d fortress_dev -c "\dx vector"

# 5. Check compliance_rules has embedding column
psql -h localhost -U fortress -d fortress_dev \
  -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='compliance_rules' AND column_name='embedding';"
```

## Add embedding column to compliance_rules (if missing)

```bash
psql -h localhost -U fortress -d fortress_dev << 'SQL'
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE compliance_rules
  ADD COLUMN IF NOT EXISTS embedding vector(1024),
  ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (
      to_tsvector('english',
        coalesce(rule_text,'') || ' ' ||
        coalesce(cfr_citation,'') || ' ' ||
        coalesce(regulation_name,''))
    ) STORED;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cr_hnsw
  ON compliance_rules USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cr_tsv
  ON compliance_rules USING GIN (tsv);

-- Verify
\d compliance_rules
SQL
```

## Migrate from Weaviate → OpenSearch Serverless (P1 TODO)

```
File to update: src/services/weaviate_client.py
Action: Replace with src/services/opensearch_client.py (create this file)

The new client must implement:
  - index_compliance_rule(rule_id, rule_text, embedding, metadata)
  - search_compliance_rules(query_text, query_embedding, filters, k=10)
  - delete_rule(rule_id)
  - bulk_index(rules: list)

OpenSearch endpoint: from SSM /fortress/pipeline1/opensearch-endpoint
Auth: AWS SigV4 (boto3 RequestsHttpConnection with credentials)
Index name: "fortress-compliance-docs"
k-NN field: "embedding" with dimension=1024, method=hnsw, space_type=cosine
BM25 field: "rule_text" (Lucene default analyzer)
```

## Hybrid search query for Agent 2 (pgvector, P2)

```python
# In src/services/compliance_rule_resolver.py — replace _from_weaviate with:
async def _from_pgvector(self, domain: str, rule_type: str, query_embedding: list) -> list:
    async with get_db() as session:
        rows = await session.execute(text("""
            SELECT rule_id, rule_text, regulator, cfr_citation,
              (0.7 * (1 - (embedding <=> :vec::vector)))
            + (0.3 * ts_rank(tsv, plainto_tsquery('english', :q))) AS score
            FROM compliance_rules
            WHERE is_active = TRUE
              AND domain = :domain
            ORDER BY score DESC LIMIT 10
        """), {"vec": str(query_embedding), "q": rule_type.replace("_", " "), "domain": domain})
        return rows.fetchall()
```

## Sync API verification

```bash
# Check all 8 sync endpoints are registered
curl -s http://localhost:8001/openapi.json | python3 -c "
import sys, json
spec = json.load(sys.stdin)
sync_paths = [p for p in spec['paths'] if '/sync/' in p or '/license/' in p]
print(f'Sync endpoints: {len(sync_paths)}')
for p in sorted(sync_paths):
    print(f'  {p}')
"
# Expect: 8+ endpoints
```
