# Agent 3 — Issue monitor + auto-fix

**Persona:** SRE / on-call engineer. Watches CloudWatch, ECS task health, migration
chain integrity, and import errors. Fixes common issues autonomously.

**Trigger phrases:**
- "something is broken", "check for issues", "health check", "what's failing"
- "P1 didn't scrape last night", "P2 ECS task keeps restarting"

## Full system health check (run this first)

```bash
#!/usr/bin/env bash
# Run: chmod +x infra/scripts/health-check.sh && ./infra/scripts/health-check.sh

echo "=== P1 ECS Service Health ==="
aws ecs describe-services \
  --cluster fortress-pipeline1-production \
  --services fortress-pipeline1-api-production \
  --query "services[0].{Running:runningCount,Desired:desiredCount,Pending:pendingCount,Status:status}"

echo ""
echo "=== P1 Last Scrape (ECS task history) ==="
aws ecs list-tasks \
  --cluster fortress-pipeline1-production \
  --desired-status STOPPED \
  --query "taskArns[:3]" --output text

echo ""
echo "=== P1 Sync API Health ==="
curl -sf https://api.fortresslabs.dev/health && echo "OK" || echo "FAIL"

echo ""
echo "=== P2 ECS Service Health (per bank) ==="
for BANK in firstnational; do
  aws ecs describe-services \
    --cluster fortress-pipeline2-${BANK} \
    --services fortress-pipeline2-${BANK} \
    --query "services[0].{Bank:'${BANK}',Running:runningCount,Desired:desiredCount}"
done

echo ""
echo "=== CloudWatch Alarms in ALARM state ==="
aws cloudwatch describe-alarms \
  --alarm-name-prefix fortress \
  --state-value ALARM \
  --query "MetricAlarms[*].{Name:AlarmName,Reason:StateReason}"

echo ""
echo "=== Recent CloudWatch errors (P1) ==="
aws logs filter-log-events \
  --log-group-name /fortress/pipeline1 \
  --filter-pattern "ERROR" \
  --start-time $(date -d '1 hour ago' +%s000) \
  --query "events[-10:].message" --output text

echo ""
echo "=== Migration chain integrity ==="
python -m alembic history --verbose 2>&1 | grep "Rev:" | wc -l
echo "Expected: 17 migrations"
```

## Common failures and auto-fixes

| Symptom | Diagnosis command | Fix |
|---|---|---|
| P1 health returns 503 | `aws ecs describe-services --cluster fortress-pipeline1-production ...` | Force new deployment |
| `ModuleNotFoundError` in ECS logs | `aws logs tail /fortress/pipeline1 --since 30m` | Rebuild and push image |
| `alembic.util.exc.CommandError: Can't locate revision` | `python -m alembic history` | Run `alembic stamp head` or fix `down_revision` chain |
| `asyncpg.exceptions.UndefinedTableError` | Check if 016/017 migrations ran | `python -m alembic upgrade head` |
| Redis `WRONGTYPE` error | Data type mismatch in cache key | `redis-cli FLUSHDB` (dev only) |
| `weaviate.WeaviateConnectionError` | Expected — Weaviate removed from prod | Verify `WEAVIATE_URL` not set or set to non-existent host; compliance_rule_resolver falls through to fallback |
| OpenSearch 403 | IAM role missing `aoss:APIAccessAll` | Run data access policy command in Agent 1 |
| P2 ECS 0/2 tasks running | Check stopped task reason | Usually missing secret or bad DATABASE_URL |

## Import error check (run before every deploy)

```bash
# Verify all entry points compile cleanly
python -m py_compile src/main_pipeline1.py && echo "P1 main: OK"
python -m py_compile src/main_pipeline2.py && echo "P2 main: OK"
python -m py_compile src/utils/config.py   && echo "config: OK"

# Check for circular imports
python -c "import src.main_pipeline1; print('P1 import: OK')"
python -c "import src.main_pipeline2; print('P2 import: OK')"

# Check config picks up PIPELINE_MODE correctly
PIPELINE_MODE=pipeline1 python -c "
from src.utils.config import PIPELINE_MODE, settings
print(f'Mode: {PIPELINE_MODE}')
print(f'Settings mode: {settings.PIPELINE_MODE}')
assert PIPELINE_MODE == 'pipeline1', 'PIPELINE_MODE not pipeline1'
print('P1 config: OK')
"

PIPELINE_MODE=pipeline2 python -c "
from src.utils.config import PIPELINE_MODE, settings
print(f'Mode: {PIPELINE_MODE}')
assert PIPELINE_MODE == 'pipeline2', 'PIPELINE_MODE not pipeline2'
print('P2 config: OK')
"
```

## Schema regression test

```bash
# Verify all 17 migrations in chain + key columns exist
python << 'PYEOF'
import asyncio, asyncpg, os

async def verify_schema():
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)

    checks = [
        # Table: compliance_rules — P1 enrichment (migration 013)
        ("compliance_rules", "cfr_citation", "character varying"),
        ("compliance_rules", "state_code", "character varying"),
        ("compliance_rules", "enforcement_risk_score", "double precision"),
        ("compliance_rules", "obligation_count", "integer"),
        # pgvector — must exist
        ("compliance_rules", "embedding", "USER-DEFINED"),
        # P2 workflow tables (migration 016)
        ("workflow_executions", "id", "uuid"),
        ("checkpoint_approvals", "workflow_id", "uuid"),
        # P2 policy tables (migration 017)
        ("bank_policies", "client_id", "character varying"),
        ("policy_regulation_mappings", "policy_id", "uuid"),
        ("regulatory_change_records", "change_ref", "character varying"),
        # P1 tables
        ("obligations", "source_text", "text"),
        ("compliance_deadlines", "days_remaining", "integer"),
        ("client_credentials", "customer_id", "character varying"),
    ]

    failures = []
    for table, column, dtype in checks:
        row = await conn.fetchrow(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name=$1 AND column_name=$2", table, column
        )
        if not row:
            failures.append(f"MISSING: {table}.{column}")
        elif dtype != "USER-DEFINED" and row["data_type"] != dtype:
            failures.append(f"WRONG TYPE: {table}.{column} = {row['data_type']} (expected {dtype})")

    await conn.close()

    if failures:
        print("SCHEMA FAILURES:")
        for f in failures:
            print(f"  {f}")
        raise SystemExit(1)
    else:
        print(f"Schema OK: {len(checks)} checks passed")

asyncio.run(verify_schema())
PYEOF
```

## CloudWatch alarms — daily scraper email alerts (jamie@fortresslabs.dev)

Two alarms feed the existing SNS topic `fortress-p1-alarms-production`
(subscription: jamie@fortresslabs.dev, confirmed). Both alarms cover the
daily scrape cycle from different angles — a single connector failing AND
the whole scraper not running.

| Alarm | Metric | Window | Triggers when |
|---|---|---|---|
| `fortress-scraper-connector-failure-production` | `Fortress/Scraper/ConnectorFailure` (Sum) | 24h | ≥ 1 failure recorded by `scrape_monitor` MCP |
| `fortress-scraper-no-run-production` | `Fortress/Scraper/ConnectorSuccess` (Sum) | 25h | < 1 successes (treats missing data as breaching, so a stuck EventBridge / failed ECS task also alerts) |

Recreate both (idempotent — `put-metric-alarm` upserts):
```bash
aws cloudwatch put-metric-alarm \
  --region us-east-1 \
  --alarm-name fortress-scraper-connector-failure-production \
  --alarm-description "Fires when any P1 scraper connector reports a failure in the last 24h. Emitted by src/mcp_servers/scrape_monitor/server.py" \
  --namespace Fortress/Scraper --metric-name ConnectorFailure \
  --statistic Sum --period 86400 --evaluation-periods 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions arn:aws:sns:us-east-1:520385696621:fortress-p1-alarms-production \
  --ok-actions    arn:aws:sns:us-east-1:520385696621:fortress-p1-alarms-production

aws cloudwatch put-metric-alarm \
  --region us-east-1 \
  --alarm-name fortress-scraper-no-run-production \
  --alarm-description "Fires when no P1 scraper success has been recorded in 25h — means the daily scrape did not run (EventBridge / ECS task issue)." \
  --namespace Fortress/Scraper --metric-name ConnectorSuccess \
  --statistic Sum --period 90000 --evaluation-periods 1 \
  --threshold 1 --comparison-operator LessThanThreshold \
  --treat-missing-data breaching \
  --alarm-actions arn:aws:sns:us-east-1:520385696621:fortress-p1-alarms-production \
  --ok-actions    arn:aws:sns:us-east-1:520385696621:fortress-p1-alarms-production
```

Verify state:
```bash
aws cloudwatch describe-alarms --region us-east-1 \
  --alarm-names fortress-scraper-connector-failure-production \
                fortress-scraper-no-run-production \
  --query 'MetricAlarms[*].{Name:AlarmName,State:StateValue,Missing:TreatMissingData}' \
  --output table

# Confirm email subscription is still active
aws sns list-subscriptions-by-topic \
  --topic-arn arn:aws:sns:us-east-1:520385696621:fortress-p1-alarms-production \
  --region us-east-1 \
  --query 'Subscriptions[?Protocol==`email`].{Endpoint:Endpoint,Status:SubscriptionArn}'
```

Both alarms depend on `scrape_monitor` MCP being called from
`scraper_scheduler.py` for every connector result — if metrics never
appear in `Fortress/Scraper/*`, the wiring is broken, not the alarm.
