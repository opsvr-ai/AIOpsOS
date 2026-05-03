"""add delivery_status to messages and notifications table

Revision ID: 5eb219b0b7f0
Revises: 5c38a4fcb917
Create Date: 2026-05-03 19:29:52.501577

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5eb219b0b7f0'
down_revision: Union[str, None] = '5c38a4fcb917'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column(
        'delivery_status', sa.String(length=16),
        nullable=False, server_default='delivered',
    ))

    op.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            alert_id UUID REFERENCES alerts(id) ON DELETE SET NULL,
            title VARCHAR(256) NOT NULL,
            message VARCHAR(1024),
            severity VARCHAR(16) NOT NULL DEFAULT 'info',
            category VARCHAR(32) NOT NULL DEFAULT 'alert',
            is_read BOOLEAN DEFAULT FALSE,
            read_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.drop_column('messages', 'delivery_status')
    op.execute("DROP TABLE IF EXISTS notifications")
