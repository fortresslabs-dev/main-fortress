"""Server-side defaults for screening_events + screening_cases (drift fix).

Revision ID: 032
Revises: 031
Create Date: 2026-06-09

Migration 028 created the screening tables with `DEFAULT gen_random_uuid()`
on `id` and `DEFAULT now()` on `created_at`, but on environments where the
tables were bootstrapped via SQLAlchemy `create_all()` before 028 was
authored (dev, demo, test) those defaults never landed. The raw-SQL inserts
in `OFACScreener._write_audit` (`src/services/validators.py:603` and `:695`)
omit both columns, so:

  * `screening_events`  — id is NOT NULL with no default → INSERT silently
    violates the PK constraint, caught by the try/except at validators.py:367
    and degraded to a `screening_audit_failed` warning. Audit row never
    persists.

  * `screening_cases`   — same id gap on the REVIEW/HOLD path, plus a
    matching gap on `status` (NOT NULL with no default; 028 sets
    `DEFAULT 'open'`). The case INSERT at validators.py:695 omits both,
    so any REVIEW/HOLD screen tries to upsert a case row and hits a
    NOT NULL violation on status. Masked in the F1 spec because the
    PASS case doesn't open a case row.

  * `screening_events.created_at` — nullable with no default, so inserts
    that did succeed (in env where 028 ran cleanly but defaults were later
    dropped, or in any pre-fix state) store NULL. Breaks the 24h idempotency
    lookup at validators.py:191 and the `ORDER BY created_at DESC` index.

All operations are metadata-only or short row updates — safe on populated
tables. Statements are idempotent so this migration can re-run if mid-flight
state ever needs reconciling.
"""
from alembic import op


revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE screening_events "
        "ALTER COLUMN id SET DEFAULT gen_random_uuid()"
    )
    op.execute(
        "ALTER TABLE screening_cases "
        "ALTER COLUMN id SET DEFAULT gen_random_uuid()"
    )
    op.execute(
        "ALTER TABLE screening_cases "
        "ALTER COLUMN status SET DEFAULT 'open'"
    )

    # created_at: backfill any NULLs the broken state may have produced,
    # then attach the default and the NOT NULL constraint.
    op.execute(
        "UPDATE screening_events SET created_at = now() "
        "WHERE created_at IS NULL"
    )
    op.execute(
        "ALTER TABLE screening_events "
        "ALTER COLUMN created_at SET DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE screening_events "
        "ALTER COLUMN created_at SET NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE screening_events "
        "ALTER COLUMN created_at DROP NOT NULL"
    )
    op.execute(
        "ALTER TABLE screening_events "
        "ALTER COLUMN created_at DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE screening_cases "
        "ALTER COLUMN status DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE screening_cases "
        "ALTER COLUMN id DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE screening_events "
        "ALTER COLUMN id DROP DEFAULT"
    )
