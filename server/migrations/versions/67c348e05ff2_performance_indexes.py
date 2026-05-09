"""add performance indexes for common query patterns

Revision ID: 67c348e05ff2
Revises: fde7ca1780f1
Create Date: 2026-05-03 12:00:00.000000

Idempotency note (deploy fix):
    Migration ``012_spaces`` already creates ``ix_sessions_space_id`` as
    part of its scoped-table fan-out loop. On a fresh DB, chain
    ``001 → 012_spaces → … → 67c348e05ff2`` would therefore attempt to
    create the same index twice and fail with
    ``relation "ix_sessions_space_id" already exists``. Both upgrade
    and downgrade are now guarded on current DB state, so this migration
    is safe on fresh installs, on installs that already went through
    012_spaces, and on installs where the indexes were created out-of-band
    (e.g. by ``Base.metadata.create_all`` during lifespan startup before
    alembic stamped the chain).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '67c348e05ff2'
down_revision: str | None = 'fde7ca1780f1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (index_name, table, columns)
_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ('ix_sessions_user_id', 'sessions', ['user_id']),
    ('ix_sessions_space_id', 'sessions', ['space_id']),
    ('ix_messages_session_created', 'messages', ['session_id', 'created_at']),
    ('ix_alerts_status', 'alerts', ['status']),
    ('ix_alerts_severity', 'alerts', ['severity']),
    ('ix_model_providers_lookup', 'model_providers', ['model_type', 'is_active', 'is_default']),
)


def _existing_indexes(insp, table: str) -> set[str]:
    try:
        return {ix["name"] for ix in insp.get_indexes(table)}
    except Exception:
        # Table missing entirely (e.g. ``model_providers`` on very old
        # chains before revision 009) — return empty so the caller skips.
        return set()


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing_tables = set(insp.get_table_names())
    for name, table, columns in _INDEXES:
        if table not in existing_tables:
            continue
        if name in _existing_indexes(insp, table):
            continue
        op.create_index(name, table, columns)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing_tables = set(insp.get_table_names())
    # Reverse order mirrors the old downgrade's drop sequence, but with
    # guards so a partially-rolled-back chain doesn't error.
    for name, table, _columns in reversed(_INDEXES):
        if table not in existing_tables:
            continue
        if name not in _existing_indexes(insp, table):
            continue
        op.drop_index(name, table_name=table)
