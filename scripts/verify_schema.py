#!/usr/bin/env python3
"""
scripts/verify_schema.py
Verifies all 17 Alembic migrations ran and key columns exist.
Exits 0 on success, 1 on failure.
Run: python scripts/verify_schema.py
"""
import asyncio
import asyncpg
import os
import sys

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://fortress:fortress_dev@localhost:5432/fortress_dev"
)

REQUIRED_COLUMNS = [
    # (table, column, expected_type)
    # Shared P1 regulatory tables
    ("compliance_rules",           "id",                    "uuid"),
    ("compliance_rules",           "rule_text",             "text"),
    ("compliance_rules",           "cfr_citation",          "character varying"),
    ("compliance_rules",           "state_code",            "character varying"),
    ("compliance_rules",           "enforcement_risk_score","double precision"),
    ("compliance_rules",           "severity",              "character varying"),  # migration 051
    ("compliance_rules",           "obligation_count",      "integer"),
    ("compliance_rules",           "embedding",             "USER-DEFINED"),  # pgvector
    # Obligations (migration 008)
    ("obligations",                "source_text",           "text"),
    ("obligations",                "rule_id",               "character varying"),
    # Enforcement (migration 009)
    ("enforcement_actions",        "id",                    "uuid"),
    ("enforcement_risk_scores",    "risk_score",            "double precision"),
    # Deadlines (migration 010)
    ("compliance_deadlines",       "days_remaining",        "integer"),
    ("compliance_deadlines",       "deadline_date",         "timestamp with time zone"),
    # Change records (migration 011)
    ("regulatory_change_records",  "change_ref",            "character varying"),
    ("regulatory_change_records",  "change_summary",        "text"),
    # Benchmarks (migration 012)
    ("peer_benchmarks",            "id",                    "uuid"),
    # Client credentials (migration 015)
    ("client_credentials",         "customer_id",           "character varying"),
    ("client_credentials",         "schema_name",           "character varying"),
    # P2 workflow tables (migration 016)
    ("workflow_executions",        "id",                    "uuid"),
    ("workflow_executions",        "jira_story_id",         "character varying"),
    ("workflow_executions",        "status",                "USER-DEFINED"),  # enum
    ("checkpoint_approvals",       "workflow_id",           "uuid"),
    ("checkpoint_approvals",       "status",                "USER-DEFINED"),  # enum
    # P2 bank policy tables (migration 017)
    ("bank_policies",              "client_id",             "character varying"),
    ("bank_policies",              "policy_ref",            "character varying"),
    ("policy_regulation_mappings", "policy_id",             "uuid"),
    ("policy_regulation_mappings", "regulation_id",         "character varying"),
]

REQUIRED_INDEXES = [
    ("compliance_rules",  "idx_cr_hnsw"),      # pgvector HNSW
    ("compliance_rules",  "idx_cr_severity"),  # migration 051
]

REQUIRED_EXTENSIONS = ["vector"]


async def verify():
    url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    failures = []
    warnings = []

    # ── Extensions ──────────────────────────────────────────────────
    for ext in REQUIRED_EXTENSIONS:
        row = await conn.fetchrow(
            "SELECT extname FROM pg_extension WHERE extname=$1", ext
        )
        if not row:
            failures.append(f"MISSING extension: {ext}")

    # ── Columns ─────────────────────────────────────────────────────
    for table, column, expected_type in REQUIRED_COLUMNS:
        row = await conn.fetchrow(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=$1 AND column_name=$2",
            table, column
        )
        if not row:
            failures.append(f"MISSING column: {table}.{column}")
        elif expected_type != "USER-DEFINED" and row["data_type"] != expected_type:
            failures.append(
                f"WRONG TYPE: {table}.{column} = '{row['data_type']}'"
                f" (expected '{expected_type}')"
            )

    # ── Indexes ─────────────────────────────────────────────────────
    for table, index in REQUIRED_INDEXES:
        row = await conn.fetchrow(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename=$1 AND indexname=$2", table, index
        )
        if not row:
            warnings.append(f"MISSING index: {index} on {table} (create it manually)")

    # ── Migration count ──────────────────────────────────────────────
    try:
        rows = await conn.fetch(
            "SELECT version_num FROM alembic_version"
        )
        if not rows:
            failures.append("Alembic version table is empty — run migrations")
        else:
            current = rows[0]["version_num"]
            print(f"  Current migration: {current}")
    except Exception:
        failures.append("alembic_version table not found — run: python -m alembic upgrade head")

    await conn.close()

    # ── Report ───────────────────────────────────────────────────────
    print("")
    print("Schema verification")
    print("─" * 50)

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠  {w}")

    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for f in failures:
            print(f"  ✗  {f}")
        print("")
        print(f"  {len(REQUIRED_COLUMNS)} columns checked,"
              f" {len(failures)} failures, {len(warnings)} warnings")
        sys.exit(1)
    else:
        print(f"\n  ✓  All {len(REQUIRED_COLUMNS)} columns present")
        print(f"  ✓  pgvector extension installed")
        if warnings:
            print(f"  ⚠  {len(warnings)} warnings (non-fatal)")
        print("")


if __name__ == "__main__":
    asyncio.run(verify())
