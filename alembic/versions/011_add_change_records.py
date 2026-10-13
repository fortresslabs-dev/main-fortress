"""Add regulatory_change_records and change_record_comments tables (REQ-013)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "regulatory_change_records",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("change_ref", sa.String(50), nullable=False, unique=True),
        sa.Column("regulation_id", sa.String(100), nullable=True),
        sa.Column("rule_id", sa.String(100), nullable=False),
        sa.Column("regulator", sa.String(50), nullable=False),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("previous_hash", sa.String(64), nullable=True),
        sa.Column("new_hash", sa.String(64), nullable=True),
        sa.Column("change_summary", sa.Text, nullable=True),
        sa.Column("semantic_diff", sa.Text, nullable=True),
        sa.Column("impact_rating", sa.String(10), nullable=True),
        sa.Column("change_type", sa.String(20), nullable=True),
        sa.Column("assigned_to", sa.String(200), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="'detected'"),
        sa.Column("status_updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("reviewer_name", sa.String(200), nullable=True),
        sa.Column("reviewer_role", sa.String(200), nullable=True),
        sa.Column("signed_off_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "impact_rating IN ('High','Medium','Low','None')",
            name="ck_impact_rating",
        ),
        sa.CheckConstraint(
            "change_type IN ('added','modified','removed','clarified')",
            name="ck_change_record_type",
        ),
        sa.CheckConstraint(
            "status IN ('detected','in_review','impact_assessed','remediation_planned','signed_off','closed')",
            name="ck_change_record_status",
        ),
    )
    op.create_index("idx_change_status", "regulatory_change_records", ["status", "detected_at"],
                    postgresql_where=sa.text("is_active = TRUE"))
    op.create_index("idx_change_regulator", "regulatory_change_records", ["regulator", "status"])
    op.create_index("idx_change_unsigned", "regulatory_change_records", ["detected_at"],
                    postgresql_where=sa.text("status NOT IN ('signed_off','closed') AND is_active = TRUE"))

    # Immutability trigger: signed_off_at cannot be changed once set
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_signoff_overwrite() RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.signed_off_at IS NOT NULL
               AND NEW.signed_off_at IS DISTINCT FROM OLD.signed_off_at THEN
                RAISE EXCEPTION 'signed_off_at is immutable once set';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER tg_immutable_signoff
            BEFORE UPDATE ON regulatory_change_records
            FOR EACH ROW EXECUTE FUNCTION prevent_signoff_overwrite();
    """)

    op.create_table(
        "change_record_comments",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("change_record_id", UUID, sa.ForeignKey("regulatory_change_records.id"), nullable=False),
        sa.Column("comment_type", sa.String(20), nullable=False, server_default="'comment'"),
        sa.Column("author", sa.String(200), nullable=False),
        sa.Column("author_role", sa.String(200), nullable=True),
        sa.Column("comment_text", sa.Text, nullable=True),
        sa.Column("status_from", sa.String(30), nullable=True),
        sa.Column("status_to", sa.String(30), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.CheckConstraint(
            "comment_type IN ('comment','status_transition','sign_off','reassignment')",
            name="ck_comment_type",
        ),
    )
    op.create_index("idx_change_comments_record", "change_record_comments",
                    ["change_record_id", "created_at"])


def downgrade():
    op.drop_index("idx_change_comments_record", "change_record_comments")
    op.drop_table("change_record_comments")
    op.execute("DROP TRIGGER IF EXISTS tg_immutable_signoff ON regulatory_change_records")
    op.execute("DROP FUNCTION IF EXISTS prevent_signoff_overwrite()")
    op.drop_index("idx_change_unsigned", "regulatory_change_records")
    op.drop_index("idx_change_regulator", "regulatory_change_records")
    op.drop_index("idx_change_status", "regulatory_change_records")
    op.drop_table("regulatory_change_records")
