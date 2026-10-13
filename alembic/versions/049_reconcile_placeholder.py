"""Reconciliation placeholder for revision 049.

Revision ID: 049
Revises: 048
Create Date: 2026-10-09

The deployed database was already stamped at alembic ``050`` while this
working copy's ``alembic/versions/`` only contained scripts through ``048``.
The original ``049`` and ``050`` scripts are not present in this checkout and
could not be recovered (this tree is not a git repository).

Investigation (see CLAUDE.md migration table) confirmed that migration ``048``
(``rulegen_schema``) already accounts for **every** column, constraint, and
index on ``public.compliance_rules`` observed in the live DB — so revisions
``049`` and ``050`` did **not** touch ``compliance_rules``; they altered other
objects we cannot reconstruct here.

This no-op placeholder exists only to keep the alembic revision graph
contiguous (048 → 049 → 050 → 051) so that ``051`` (which adds
``compliance_rules.severity``) can be applied to the live DB, which is already
at ``050`` and therefore skips this script.

⚠️ On a FRESH database this placeholder does NOT reproduce whatever the real
``049`` did to non-``compliance_rules`` objects. Bootstrap fresh environments
from the canonical repository, not this working copy.
"""

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Intentional no-op — see module docstring.
    pass


def downgrade() -> None:
    pass
