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
    op.add_column('reports', sa.Column('visibility', sa.String(length=16), server_default='space', nullable=False))
    op.execute("UPDATE reports SET visibility = 'public' WHERE is_public = true")
    op.execute("UPDATE reports SET visibility = 'space' WHERE is_public = false")
    op.drop_column('reports', 'is_public')


def downgrade() -> None:
    op.add_column('reports', sa.Column('is_public', sa.BOOLEAN(), autoincrement=False, nullable=False, server_default=sa.text('false')))
    op.execute("UPDATE reports SET is_public = true WHERE visibility = 'public'")
    op.execute("UPDATE reports SET is_public = false WHERE visibility != 'public'")
    op.drop_column('reports', 'visibility')
