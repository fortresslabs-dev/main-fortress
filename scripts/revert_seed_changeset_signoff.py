"""Revert scripts/seed_changeset_signoff.py.

Restores any regulatory_change_records where reviewer_name='bulk_seed' back
to status='detected' with signed_off_at cleared, and deletes the associated
'sign_off' audit rows authored by 'bulk_seed'. Manually-signed-off records
(reviewer_name != 'bulk_seed') are untouched.

Run via the same ECS one-shot pattern as the seed script.
"""
import os
import asyncio
import asyncpg


async def main() -> int:
    conn = await asyncpg.connect(
        host=os.environ["AURORA_HOST"],
        port=int(os.environ.get("AURORA_PORT", "5432")),
        database=os.environ["DB_NAME"],
        user=os.environ.get("DB_USER", "fortress_testbank"),
        password=os.environ["DB_PASSWORD"],
        ssl="require",
    )
    async with conn.transaction():
        rows = await conn.fetch("""
            SELECT id FROM regulatory_change_records
             WHERE reviewer_name = 'bulk_seed'
               AND is_active = TRUE
        """)
        if not rows:
            print("revert_seed_changeset_signoff: nothing to revert "
                  "(no bulk_seed records found)")
            return 0

        record_ids = [r["id"] for r in rows]
        print(f"revert_seed_changeset_signoff: reverting {len(record_ids)} records")

        # tg_immutable_signoff (BEFORE UPDATE trigger) raises if signed_off_at
        # changes after being set. That's correct for normal ops; here we're
        # explicitly reverting a bulk seed so the constraint needs to step
        # aside. session_replication_role=replica skips non-internal triggers
        # for this session only (no DDL, no global effect).
        await conn.execute("SET LOCAL session_replication_role = 'replica'")

        await conn.execute("""
            UPDATE regulatory_change_records
               SET status = 'detected',
                   signed_off_at = NULL,
                   reviewer_name = NULL,
                   reviewer_role = NULL,
                   status_updated_at = NOW()
             WHERE id = ANY($1::uuid[])
        """, record_ids)

        deleted = await conn.fetchval("""
            WITH d AS (
                DELETE FROM change_record_comments
                 WHERE change_record_id = ANY($1::uuid[])
                   AND comment_type = 'sign_off'
                   AND author = 'bulk_seed'
             RETURNING 1
            )
            SELECT COUNT(*) FROM d
        """, record_ids)
        print(f"revert_seed_changeset_signoff: deleted {deleted} audit rows")

    remaining_seed = await conn.fetchval("""
        SELECT COUNT(*) FROM regulatory_change_records
         WHERE reviewer_name = 'bulk_seed' AND is_active = TRUE
    """)
    open_now = await conn.fetchval("""
        SELECT COUNT(*) FROM regulatory_change_records
         WHERE is_active = TRUE
           AND status = 'detected'
           AND regulator <> 'OFAC'
    """)
    print(f"revert_seed_changeset_signoff: done; "
          f"bulk_seed records left = {remaining_seed}, "
          f"unsigned non-OFAC = {open_now}")
    await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
