# Agent 4 — P2 sync + workflow monitor

**Persona:** Integration engineer. Monitors the P2 UpdateService sync health, JIRA
webhook pipeline, agent workflow completions, and CCO dashboard data freshness.

**Trigger phrases:**
- "P2 sync not updating", "JIRA webhook not triggering agents", "compliance data stale"
- "workflow stuck in pending", "agent pipeline timing out", "dashboard panel empty"

## Check sync status (P2)

```bash
# Via P2 health endpoint
curl -sf https://fortressapi.{BANK_DOMAIN}/health/sync | python3 -m json.tool

# Expected response:
# {
#   "sync_version": 42,
#   "last_synced_at": "2026-05-06T02:15:00Z",
#   "sync_age_hours": 0.5,
#   "rules_count": 847,
#   "status": "ok"
# }

# If sync_age_hours > 25: sync is broken — check UpdateService
```

## Trigger manual sync (P2 — force immediate)

```bash
# Call UpdateService directly via internal endpoint
curl -X POST https://fortressapi.{BANK_DOMAIN}/internal/sync/trigger \
  -H "X-Internal-Secret: $FORTRESS_INTERNAL_SECRET"
```

## UpdateService flow — what it does

```
UpdateService.run_sync_cycle()
  → GET /api/sync/counts from P1 (compare local row counts)
  → if local_version < p1_version:
      POST /api/sync/postgres    {customer_id, api_key, from_version}
        → receive: [{rule_id, rule_text, cfr_citation, ...}]
        → upsert into local compliance_rules
      POST /api/sync/embeddings  {customer_id, api_key, from_version}
        → receive: rules WITH pre-computed 1024-dim embedding vectors
        → upsert embedding column into compliance_rules (no re-embed needed)
      POST /api/sync/obligations {from_version} → upsert obligations
      POST /api/sync/deadlines   {from_version} → upsert deadlines
      POST /api/sync/change-records {from_version} → upsert change records
      POST /api/sync/risk-scores {from_version} → upsert risk scores
      → _refresh_vector_index(): ANALYZE compliance_rules (updates HNSW planner stats)
      → DeadlineTracker.refresh_days_remaining(session)
```

## Test UpdateService connectivity

```bash
# Verify P1 sync API is reachable from P2 environment
# /api/license/verify takes customer_id + api_key as QUERY PARAMETERS
curl -X POST "https://api.fortresslabs.dev/api/license/verify?customer_id=firstnational&api_key=$P1_API_KEY"

# Sync endpoints take customer_id + api_key in the JSON BODY (not headers)
curl -X POST https://api.fortresslabs.dev/api/sync/counts \
  -H "Content-Type: application/json" \
  -d "{\"customer_id\": \"firstnational\", \"api_key\": \"$P1_API_KEY\", \"from_version\": 0}"
```

## JIRA webhook pipeline — end-to-end test

```bash
# 1. Send test webhook to P2
curl -X POST https://fortressapi.{BANK_DOMAIN}/webhooks/jira \
  -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=$(echo -n '{"webhookEvent":"jira:issue_created"}' | openssl dgst -sha256 -hmac "$JIRA_WEBHOOK_SECRET" | cut -d' ' -f2)" \
  -d '{
    "webhookEvent": "jira:issue_created",
    "issue": {
      "key": "COMP-999",
      "id": "10001",
      "fields": {
        "summary": "Add CTR filing check for $10K cash deposits",
        "description": "Wire transfers above threshold need dual approval and CTR filing",
        "issuetype": {"name": "Story"}
      }
    }
  }'

# 2. Check workflow was created
curl -H "Authorization: Bearer $JWT" \
  https://fortressapi.{BANK_DOMAIN}/workflows/COMP-999

# 3. Watch agent pipeline logs (BackgroundTasks runs inline in the ECS task)
aws logs tail /fortress/pipeline2/firstnational \
  --filter-pattern "COMP-999" \
  --follow
```

## Workflow stuck in pending — debug

```bash
# Check all pending workflows
psql $DATABASE_URL -c "
SELECT id, jira_story_key, status, started_at,
       NOW() - started_at AS age
FROM workflow_executions
WHERE status IN ('pending', 'running')
ORDER BY started_at;
"

# If stuck > 10 minutes the ECS task likely died mid-flight — there's no SQS
# buffer, so the work was lost. Either re-trigger via JIRA (it will retry on
# its own schedule) or POST the same payload back to /webhooks/jira manually.

# Manual workflow reset (if agent crashed)
psql $DATABASE_URL -c "
UPDATE workflow_executions
SET status = 'failed', completed_at = NOW()
WHERE status = 'running'
  AND started_at < NOW() - INTERVAL '30 minutes';
"
```

## Dashboard panel empty — data freshness check

```bash
# Check each panel's data source
psql $DATABASE_URL << 'SQL'
SELECT
  'compliance_rules'         AS table_name, COUNT(*) AS rows FROM compliance_rules WHERE is_active = TRUE
UNION ALL SELECT 'obligations',             COUNT(*) FROM obligations
UNION ALL SELECT 'compliance_deadlines',    COUNT(*) FROM compliance_deadlines WHERE is_active = TRUE
UNION ALL SELECT 'enforcement_risk_scores', COUNT(*) FROM enforcement_risk_scores
UNION ALL SELECT 'regulatory_change_records', COUNT(*) FROM regulatory_change_records WHERE is_active = TRUE
UNION ALL SELECT 'bank_policies',           COUNT(*) FROM bank_policies WHERE status = 'active'
UNION ALL SELECT 'workflow_executions',     COUNT(*) FROM workflow_executions
ORDER BY table_name;
SQL
# If compliance_rules = 0: sync hasn't run — trigger manual sync
# If bank_policies = 0: Panel 6 will be empty — bank hasn't uploaded policies yet
```

## P2 agent pipeline timing breakdown

```bash
# Check agent durations for last 10 workflows
psql $DATABASE_URL -c "
SELECT
  jira_story_key,
  status,
  EXTRACT(EPOCH FROM (completed_at - started_at)) AS total_seconds,
  artifacts->>'agent_timings' AS timings
FROM workflow_executions
ORDER BY started_at DESC LIMIT 10;
"
```
