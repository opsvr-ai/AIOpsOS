"""Add missing columns to cmdb_sync_logs and scene_triggers

Revision ID: 202605121000
Revises: 202605111000
Create Date: 2026-05-12 10:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "202605121000"
down_revision = "202605111000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add raw_snapshot_path to cmdb_sync_logs if not exists
    conn = op.get_bind()
    
    # Check and add raw_snapshot_path to cmdb_sync_logs
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'cmdb_sync_logs' AND column_name = 'raw_snapshot_path'"
    ))
    if not result.fetchone():
        op.add_column('cmdb_sync_logs', sa.Column('raw_snapshot_path', sa.String(512), nullable=True))
    
    # Check and add space_id to cmdb_sync_logs
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'cmdb_sync_logs' AND column_name = 'space_id'"
    ))
    if not result.fetchone():
        op.add_column('cmdb_sync_logs', sa.Column('space_id', UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            'fk_cmdb_sync_logs_space_id', 'cmdb_sync_logs', 'spaces',
            ['space_id'], ['id'], ondelete='SET NULL'
        )
        op.create_index('ix_cmdb_sync_logs_space_id', 'cmdb_sync_logs', ['space_id'])
    
    # Check and add space_id to scene_triggers
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'scene_triggers' AND column_name = 'space_id'"
    ))
    if not result.fetchone():
        op.add_column('scene_triggers', sa.Column('space_id', UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            'fk_scene_triggers_space_id', 'scene_triggers', 'spaces',
            ['space_id'], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    # Remove space_id from scene_triggers
    op.drop_constraint('fk_scene_triggers_space_id', 'scene_triggers', type_='foreignkey')
    op.drop_column('scene_triggers', 'space_id')
    
    # Remove columns from cmdb_sync_logs
    op.drop_index('ix_cmdb_sync_logs_space_id', 'cmdb_sync_logs')
    op.drop_constraint('fk_cmdb_sync_logs_space_id', 'cmdb_sync_logs', type_='foreignkey')
    op.drop_column('cmdb_sync_logs', 'space_id')
    op.drop_column('cmdb_sync_logs', 'raw_snapshot_path')
