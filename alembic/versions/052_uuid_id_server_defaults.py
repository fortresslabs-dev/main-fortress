"""Add server-side DEFAULT gen_random_uuid() to UUID id columns written by raw SQL.

Revision ID: 052
Revises: 051
Create Date: 2026-10-10

These tables declare ``id = Column(UUID, primary_key=True, default=uuid.uuid4)``
— a *Python-side* ORM default. It only fires when a row is created through the
ORM. The P2 `UpdateService` ancillary-table sync inserts via raw ``text()``
statements (``INSERT INTO obligations (...) VALUES (...)``) that omit ``id``,
so on a fresh replica every ancillary insert hit::

    null value in column "id" ... violates not-null constraint

and `_sync_ancillary_tables` swallowed it per-table — leaving the replica with
``compliance_rules`` (ORM path, fine) but zero obligations / enforcement /
deadlines / change-records / SDN entries. Every bank's *first* P2 initial-sync
would have the same empty dashboard.

Giving the columns a DB-level default makes the raw inserts self-sufficient and
is harmless to the ORM path (SQLAlchemy still sends its own uuid4 when it has
one; the default only applies when no value is supplied).

Idempotent ALTERs; safe to re-run. ``gen_random_uuid()`` is in core PG13+
(pgcrypto on the pgvector:pg15 image — verified present).
"""
from alembic import op


revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


_TABLES = (
    "obligations",
    "enforcement_actions",
    "enforcement_risk_scores",
    "compliance_deadlines",
    "peer_benchmarks",
    "ofac_sdn_entries",
    "regulatory_change_records",
)


def upgrade() -> None:
    for t in _TABLES:
        op.execute(f"ALTER TABLE {t} ALTER COLUMN id SET DEFAULT gen_random_uuid();")


def downgrade() -> None:
    for t in _TABLES:
        op.execute(f"ALTER TABLE {t} ALTER COLUMN id DROP DEFAULT;")
