"""is_public to visibility

Revision ID: 8481abf828c9
Revises: 6325af2d08a1
Create Date: 2026-05-02 20:37:47.898102

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8481abf828c9'
down_revision: str | None = '6325af2d08a1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            space_id UUID REFERENCES spaces(id) ON DELETE SET NULL,
            title VARCHAR(500) NOT NULL DEFAULT 'Untitled Report',
            description TEXT,
            html_content TEXT NOT NULL,
            theme VARCHAR(50) NOT NULL DEFAULT 'ink',
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            is_public BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.add_column('reports', sa.Column('visibility', sa.String(length=16), server_default='space', nullable=False))
    op.execute("UPDATE reports SET visibility = 'public' WHERE is_public = true")
    op.execute("UPDATE reports SET visibility = 'space' WHERE is_public = false")
    op.drop_column('reports', 'is_public')


def downgrade() -> None:
    op.add_column('reports', sa.Column('is_public', sa.BOOLEAN(), autoincrement=False, nullable=False, server_default=sa.text('false')))
    op.execute("UPDATE reports SET is_public = true WHERE visibility = 'public'")
    op.execute("UPDATE reports SET is_public = false WHERE visibility != 'public'")
    op.drop_column('reports', 'visibility')
