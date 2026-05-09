"""add space_id to agent_memories

Revision ID: c2a0e4a72cbc
Revises: 716efe2f0c86
Create Date: 2026-05-01 20:04:00.044479

Idempotency note (deploy fix):
    Migration ``012_spaces`` already adds ``space_id`` to ``agent_memories``
    via a loop over scoped tables. On a fresh DB the chain would therefore
    attempt to add the column twice and fail with
    ``column "space_id" of relation "agent_memories" already exists``.
    Both :func:`upgrade` and :func:`downgrade` guard on current DB state so
    this migration is safe on fresh installs, on installs that already went
    through 012_spaces, and on partially-applied chains.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c2a0e4a72cbc'
down_revision: str | None = '716efe2f0c86'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "agent_memories"
_COLUMN = "space_id"
_FK_NAME = "fk_agent_memories_space_id"


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def _space_fk(insp, table: str) -> dict | None:
    for fk in insp.get_foreign_keys(table):
        if (
            _COLUMN in (fk.get("constrained_columns") or [])
            and fk.get("referred_table") == "spaces"
        ):
            return fk
    return None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    # ``agent_memories`` itself may not exist yet on very old chains — if
    # the table is missing, this migration has nothing to do; a later
    # migration (knowledge / memory tables) will own the column.
    if _TABLE not in set(insp.get_table_names()):
        return
    if not _has_column(insp, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.UUID(), nullable=True))
    insp = sa.inspect(op.get_bind())
    if _space_fk(insp, _TABLE) is None:
        op.create_foreign_key(
            _FK_NAME, _TABLE, "spaces",
            [_COLUMN], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if _TABLE not in set(insp.get_table_names()):
        return
    fk = _space_fk(insp, _TABLE)
    if fk is not None:
        op.drop_constraint(fk["name"] or _FK_NAME, _TABLE, type_="foreignkey")
    insp = sa.inspect(op.get_bind())
    if _has_column(insp, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
