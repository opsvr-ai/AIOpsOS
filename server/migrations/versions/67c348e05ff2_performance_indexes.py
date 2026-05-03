"""add performance indexes for common query patterns

Revision ID: 67c348e05ff2
Revises: fde7ca1780f1
Create Date: 2026-05-03 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '67c348e05ff2'
down_revision: str | None = 'fde7ca1780f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # sessions: filtered by user_id on every list query
    op.create_index('ix_sessions_user_id', 'sessions', ['user_id'])
    # sessions: filtered by space_id for space-scoped queries + dashboard
    op.create_index('ix_sessions_space_id', 'sessions', ['space_id'])
    # messages: most common access pattern is session_id + created_at ordering
    op.create_index('ix_messages_session_created', 'messages', ['session_id', 'created_at'])
    # alerts: filtered/grouped by status in list + dashboard
    op.create_index('ix_alerts_status', 'alerts', ['status'])
    # alerts: dashboard GROUP BY severity
    op.create_index('ix_alerts_severity', 'alerts', ['severity'])
    # model_providers: get_default_model() query pattern
    op.create_index('ix_model_providers_lookup', 'model_providers', ['model_type', 'is_active', 'is_default'])


def downgrade() -> None:
    op.drop_index('ix_model_providers_lookup', table_name='model_providers')
    op.drop_index('ix_alerts_severity', table_name='alerts')
    op.drop_index('ix_alerts_status', table_name='alerts')
    op.drop_index('ix_messages_session_created', table_name='messages')
    op.drop_index('ix_sessions_space_id', table_name='sessions')
    op.drop_index('ix_sessions_user_id', table_name='sessions')
