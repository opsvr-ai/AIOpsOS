"""add_space_id_to_agents

Revision ID: 61432a83b688
Revises: 7268beefe5b5
Create Date: 2026-04-30 23:43:33.728218

Idempotency note (deploy fix):
    Migration ``012_spaces`` already adds ``space_id`` to ``agents`` via a
    loop over scoped tables. On a fresh DB the chain ``001 → 012_spaces →
    … → 61432a83b688`` would therefore attempt to add the column twice and
    fail with ``column "space_id" of relation "agents" already exists``.
    Both :func:`upgrade` and :func:`downgrade` below guard on current DB
    state so this migration is safe on fresh installs, on installs that
    already went through 012_spaces, and on partially-applied chains.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '61432a83b688'
down_revision: str | None = '7268beefe5b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "agents"
_COLUMN = "space_id"
_FK_NAME = "fk_agents_space_id"


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def _space_fk(insp, table: str) -> dict | None:
    """Return the FK dict for ``space_id → spaces.id`` on *table*, or None."""
    for fk in insp.get_foreign_keys(table):
        if (
            _COLUMN in (fk.get("constrained_columns") or [])
            and fk.get("referred_table") == "spaces"
        ):
            return fk
    return None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not _has_column(insp, _TABLE, _COLUMN):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.UUID(), nullable=True))
    # Refresh inspector state — ``get_columns`` / ``get_foreign_keys`` on the
    # same inspector instance can be cached per-table in some drivers.
    insp = sa.inspect(op.get_bind())
    if _space_fk(insp, _TABLE) is None:
        op.create_foreign_key(
            _FK_NAME, _TABLE, "spaces",
            [_COLUMN], ["id"], ondelete="SET NULL",
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    fk = _space_fk(insp, _TABLE)
    if fk is not None:
        op.drop_constraint(fk["name"] or _FK_NAME, _TABLE, type_="foreignkey")
    insp = sa.inspect(op.get_bind())
    if _has_column(insp, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
