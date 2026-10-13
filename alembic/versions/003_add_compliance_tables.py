"""Add compliance tables for USA banking regulations

Revision ID: 003_add_compliance_tables
Revises: 002_add_test_tables
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '003_add_compliance_tables'
down_revision = '002_add_test_tables'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Regulations master table
    op.create_table(
        'regulations',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('regulation_id', sa.String(100), unique=True, nullable=False),
        sa.Column('regulation_name', sa.String(500), nullable=False),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('subcategory', sa.String(50), nullable=True),
        sa.Column('regulator', sa.String(50), nullable=False),
        sa.Column('geography', sa.String(20), server_default='usa'),
        sa.Column('effective_date', sa.Date, nullable=True),
        sa.Column('compliance_deadline', sa.Date, nullable=True),
        sa.Column('exam_frequency', sa.String(20), nullable=True),
        sa.Column('source_url', sa.Text, nullable=True),
        sa.Column('last_updated', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('NOW()'), nullable=False),
    )

    # 2. Compliance requirements
    op.create_table(
        'compliance_requirements',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('requirement_id', sa.String(100), unique=True, nullable=False),
        sa.Column('regulation_id', sa.String(100), nullable=False),
        sa.Column('requirement_text', sa.Text, nullable=False),
        sa.Column('rule_type', sa.String(50), nullable=True),
        sa.Column('threshold_amount', sa.Numeric(15, 2), nullable=True),
        sa.Column('threshold_currency', sa.String(3), nullable=True),
        sa.Column('test_requirement', sa.Text, nullable=True),
        sa.Column('parameters', postgresql.JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('idx_compliance_req_regulation', 'compliance_requirements', ['regulation_id'])

    # 3. Compliance KPIs
    op.create_table(
        'compliance_kpis',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('kpi_id', sa.String(100), unique=True, nullable=False),
        sa.Column('regulation_id', sa.String(100), nullable=False),
        sa.Column('kpi_name', sa.String(200), nullable=False),
        sa.Column('kpi_formula', sa.Text, nullable=True),
        sa.Column('target_value', sa.Numeric(10, 2), nullable=True),
        sa.Column('target_unit', sa.String(20), nullable=True),
        sa.Column('monitoring_frequency', sa.String(20), nullable=True),
        sa.Column('alert_threshold', sa.Numeric(10, 2), nullable=True),
        sa.Column('criticality', sa.String(20), nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('idx_compliance_kpi_regulation', 'compliance_kpis', ['regulation_id'])

    # 4. OFAC SDN entries (special high-volume table)
    op.create_table(
        'ofac_sdn_entries',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('sdn_id', sa.String(20), unique=True, nullable=False),
        sa.Column('uid', sa.String(20), nullable=True),
        sa.Column('entity_type', sa.String(50), nullable=True),
        sa.Column('full_name', sa.String(350), nullable=True),
        sa.Column('sdn_type', sa.String(50), nullable=True),
        sa.Column('programs', sa.Text, nullable=True),
        sa.Column('title', sa.String(200), nullable=True),
        sa.Column('remarks', sa.Text, nullable=True),
        sa.Column('effective_date', sa.Date, nullable=True),
        sa.Column('last_updated', sa.DateTime, server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('idx_sdn_full_name', 'ofac_sdn_entries', ['full_name'])
    op.create_index('idx_sdn_entity_type', 'ofac_sdn_entries', ['entity_type'])


def downgrade():
    op.drop_index('idx_sdn_entity_type', 'ofac_sdn_entries')
    op.drop_index('idx_sdn_full_name', 'ofac_sdn_entries')
    op.drop_table('ofac_sdn_entries')
    op.drop_index('idx_compliance_kpi_regulation', 'compliance_kpis')
    op.drop_table('compliance_kpis')
    op.drop_index('idx_compliance_req_regulation', 'compliance_requirements')
    op.drop_table('compliance_requirements')
    op.drop_table('regulations')
