"""Add test scenarios, code, and audit tables

Revision ID: 002_add_test_tables
Revises: 
Create Date: 2024-12-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '002_add_test_tables'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Create test_scenarios table
    op.create_table(
        'test_scenarios',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('given_text', sa.Text, nullable=True),
        sa.Column('when_text', sa.Text, nullable=True),
        sa.Column('then_text', sa.Text, nullable=True),
        sa.Column('priority', sa.String(10), nullable=True),
        sa.Column('tags', postgresql.ARRAY(sa.String), nullable=True),
        sa.Column('approved', sa.Boolean, default=False),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('NOW()'), nullable=False)
    )
    
    # Create test_code table
    op.create_table(
        'test_code',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('code_text', sa.Text, nullable=False),
        sa.Column('language', sa.String(20), default='python'),
        sa.Column('framework', sa.String(20), default='pytest'),
        sa.Column('quality_score', sa.Integer, nullable=True),
        sa.Column('lines_of_code', sa.Integer, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('NOW()'), nullable=False)
    )
    
    # Create audit_logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('agent_type', sa.String(50), nullable=True),
        sa.Column('action', sa.String(100), nullable=False),
        sa.Column('user_id', sa.String(100), nullable=True),
        sa.Column('timestamp', sa.DateTime, server_default=sa.text('NOW()'), nullable=False),
        sa.Column('details', postgresql.JSONB, nullable=True)
    )
    
    # Create indexes
    op.create_index('idx_scenarios_workflow', 'test_scenarios', ['workflow_id'])
    op.create_index('idx_code_workflow', 'test_code', ['workflow_id'])
    op.create_index('idx_audit_workflow', 'audit_logs', ['workflow_id'])
    op.create_index('idx_audit_timestamp', 'audit_logs', ['timestamp'])


def downgrade():
    op.drop_index('idx_audit_timestamp', 'audit_logs')
    op.drop_index('idx_audit_workflow', 'audit_logs')
    op.drop_index('idx_code_workflow', 'test_code')
    op.drop_index('idx_scenarios_workflow', 'test_scenarios')
    op.drop_table('audit_logs')
    op.drop_table('test_code')
    op.drop_table('test_scenarios')
