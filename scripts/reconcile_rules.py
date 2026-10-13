#!/usr/bin/env python3
"""
scripts/reconcile_rules.py
Detect and fix invalid/stale rules across Aurora + OpenSearch.

Usage:
  python3 scripts/reconcile_rules.py --check         # report only
  python3 scripts/reconcile_rules.py --fix-embeddings # backfill missing embeddings
  python3 scripts/reconcile_rules.py --fix-opensearch # remove tombstoned from OpenSearch
  python3 scripts/reconcile_rules.py --fix-all        # run all fixes
"""
import argparse
import asyncio
import boto3
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


async def get_aurora_conn():
    import asyncpg
    sm = boto3.client("secretsmanager",
                      region_name=os.environ.get("AWS_REGION", "us-east-1"))
    secret = json.loads(sm.get_secret_value(
        SecretId="fortress/p1/db-password")["SecretString"])
    ssm = boto3.client("ssm",
                       region_name=os.environ.get("AWS_REGION", "us-east-1"))
    host = ssm.get_parameter(
        Name="/fortress/pipeline1/AURORA_HOST")["Parameter"]["Value"]
    return await asyncpg.connect(
        host=host, port=5432, user="fortress_p1",
        password=secret["password"], database="fortress_p1", ssl="require"
    )


async def report(conn):
    print("\n=== Rule validity report ===\n")

    # 1. Active vs tombstoned
    counts = await conn.fetch("""
        SELECT is_active, COUNT(*) AS n FROM compliance_rules GROUP BY is_active
    """)
    for r in counts:
        label = "Active" if r["is_active"] else "Tombstoned (is_active=FALSE)"
        print(f"  {label:<40s} {r['n']:>8,}")

    # 2. Missing embeddings
    missing = await conn.fetchval("""
        SELECT COUNT(*) FROM compliance_rules
        WHERE is_active = TRUE AND embedding IS NULL
    """)
    print(f"  {'Missing embedding (active)':<40s} {missing:>8,}")

    # 3. Per-regulator breakdown of missing embeddings
    print("\n  Missing embeddings by regulator:")
    rows = await conn.fetch("""
        SELECT regulator, COUNT(*) AS missing
        FROM compliance_rules
        WHERE is_active = TRUE AND embedding IS NULL
        GROUP BY regulator ORDER BY missing DESC
    """)
    for r in rows:
        print(f"    {r['regulator']:<30s} {r['missing']:>6,} missing")

    # 4. Sync version age
    ver = await conn.fetchrow("""
        SELECT current_version, updated_at FROM sync_versions LIMIT 1
    """)
    if ver:
        print(f"\n  Current sync_version: {ver['current_version']}")
        print(f"  Last updated:         {ver['updated_at']}")

    # 5. Deleted_rules table
    deleted = await conn.fetchval("SELECT COUNT(*) FROM deleted_rules")
    print(f"\n  Deleted rules (audit trail): {deleted:,}")

    # 6. Rules changed in last cycle
    latest_ver = await conn.fetchval(
        "SELECT MAX(sync_version) FROM compliance_rules")
    recent = await conn.fetchval("""
        SELECT COUNT(*) FROM compliance_rules
        WHERE sync_version = $1 AND is_active = TRUE
    """, latest_ver)
    print(f"  Rules changed in last scrape: {recent:,} (version {latest_ver})")


async def fix_embeddings(conn, limit=500):
    print("\n=== Backfilling missing embeddings ===\n")
    bedrock = boto3.client("bedrock-runtime",
                           region_name=os.environ.get("AWS_REGION", "us-east-1"))
    rows = await conn.fetch(f"""
        SELECT rule_id, rule_text, cfr_citation, regulation_name
        FROM compliance_rules
        WHERE is_active = TRUE AND embedding IS NULL
        ORDER BY created_at LIMIT {limit}
    """)
    print(f"  Rules to embed: {len(rows)}")
    ok = failed = 0
    for r in rows:
        text = " ".join(filter(None, [
            r["rule_text"] or "", r["cfr_citation"] or "",
            r["regulation_name"] or ""
        ]))[:8000]
        try:
            resp = bedrock.invoke_model(
                modelId="amazon.titan-embed-text-v2:0",
                body=json.dumps({"inputText": text, "dimensions": 1024,
                                 "normalize": True})
            )
            emb = json.loads(resp["body"].read())["embedding"]
            await conn.execute("""
                UPDATE compliance_rules SET embedding = $1::vector, updated_at = NOW()
                WHERE rule_id = $2
            """, str(emb), r["rule_id"])
            ok += 1
            if ok % 50 == 0:
                print(f"  Embedded {ok}/{len(rows)}")
        except Exception as e:
            failed += 1
            print(f"  FAILED: {r['rule_id']} — {e}")
    print(f"\n  Done: {ok} embedded, {failed} failed")


def fix_opensearch(conn_sync, tombstoned_ids):
    print("\n=== Removing tombstoned rules from OpenSearch ===\n")
    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
        region = os.environ.get("AWS_REGION", "us-east-1")
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, "aoss")
        endpoint = os.environ.get("OPENSEARCH_ENDPOINT", "").replace("https://", "")
        if not endpoint:
            print("  OPENSEARCH_ENDPOINT not set — skipping")
            return
        client = OpenSearch(hosts=[{"host": endpoint, "port": 443}],
                            http_auth=auth, use_ssl=True, verify_certs=True,
                            connection_class=RequestsHttpConnection)
        deleted = 0
        for rid in tombstoned_ids:
            r = client.delete(index="fortress-compliance-docs", id=rid, ignore=[404])
            if r.get("result") == "deleted":
                deleted += 1
        print(f"  Tombstoned: {len(tombstoned_ids)} | Deleted from OpenSearch: {deleted}")
    except ImportError:
        print("  opensearch-py not installed — pip install opensearch-py")


async def main():
    parser = argparse.ArgumentParser(description="Rule reconciliation tool")
    parser.add_argument("--check", action="store_true", help="Report only")
    parser.add_argument("--fix-embeddings", action="store_true")
    parser.add_argument("--fix-opensearch", action="store_true")
    parser.add_argument("--fix-all", action="store_true")
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        return

    conn = await get_aurora_conn()
    try:
        await report(conn)

        if args.fix_embeddings or args.fix_all:
            await fix_embeddings(conn)

        if args.fix_opensearch or args.fix_all:
            tombstoned = [r["rule_id"] for r in await conn.fetch(
                "SELECT rule_id FROM compliance_rules WHERE is_active = FALSE"
            )]
            fix_opensearch(conn, tombstoned)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
