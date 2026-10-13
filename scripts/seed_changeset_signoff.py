"""Bulk seed sign-off for pre-existing regulatory_change_records.

Used at pilot go-live to clear the inherited unsigned backlog. NOT for
production banks with real ops — it falsifies the audit trail. The runbook
in docs/examination_readiness_fix_plan.md Fix 4 covers when this is
appropriate (pilot / clean-slate) vs. when to build the bulk-acknowledge UI.

Behavior:
  - Selects regulatory_change_records WHERE is_active=TRUE AND
    status='detected' AND regulator <> 'OFAC'.
  - Sets status='signed_off', signed_off_at=NOW(), reviewer_name='bulk_seed',
    reviewer_role='admin'.
  - Inserts a change_record_comments row per record with comment_type='system'
    and an explicit "Initial seed sign-off" note so the audit trail records
    that these were not human-reviewed.
  - All inside a single transaction.

Connection uses the same env vars as the running ECS task
(AURORA_HOST, AURORA_PORT, DB_NAME, DB_PASSWORD).

Run via ECS one-shot task:
  aws ecs run-task --cluster fortress-pipeline2-<bank> ... \\
    --overrides '{"containerOverrides":[{"name":"pipeline2",
      "command":["python","-m","scripts.seed_changeset_signoff"]}]}'
"""
import os
import asyncio
import asyncpg


SEED_REASON = (
    "Initial seed sign-off; pre-existing records cleared at go-live. "
    "Not a human-reviewed acknowledgement — covered by docs/"
    "examination_readiness_fix_plan.md Fix 4."
)


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
            SELECT id, change_ref
              FROM regulatory_change_records
             WHERE is_active = TRUE
               AND status = 'detected'
               AND regulator <> 'OFAC'
        """)
        if not rows:
            print("seed_changeset_signoff: nothing to do (0 unsigned non-OFAC records)")
            await conn.close()
            return 0

        record_ids = [r["id"] for r in rows]
        print(f"seed_changeset_signoff: clearing {len(record_ids)} records")

        await conn.execute("""
            UPDATE regulatory_change_records
               SET status = 'signed_off',
                   signed_off_at = NOW(),
                   status_updated_at = NOW(),
                   reviewer_name = 'bulk_seed',
                   reviewer_role = 'admin'
             WHERE id = ANY($1::uuid[])
        """, record_ids)

        # comment_type must be one of (comment, status_transition, sign_off,
        # reassignment) per ck_comment_type. Use 'sign_off' to mark the audit
        # row as a sign-off action; the bulk_seed author + comment_text make
        # the non-human-reviewed nature explicit.
        await conn.executemany("""
            INSERT INTO change_record_comments
                (change_record_id, comment_type, author, author_role,
                 comment_text, status_from, status_to)
            VALUES ($1, 'sign_off', 'bulk_seed', 'admin', $2, 'detected', 'signed_off')
        """, [(rid, SEED_REASON) for rid in record_ids])

    remaining = await conn.fetchval("""
        SELECT COUNT(*) FROM regulatory_change_records
         WHERE is_active = TRUE
           AND status = 'detected'
           AND regulator <> 'OFAC'
    """)
    print(f"seed_changeset_signoff: done; remaining unsigned non-OFAC = {remaining}")
    await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
