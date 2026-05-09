"""add safety column to tools table

Revision ID: 202605041830
Revises: 202605041820
Create Date: 2026-05-04 18:30:00.000000

Phase H / Task 16.1 of the Agent Runtime Optimization & Evolution spec.

Adds a ``safety`` classification column to ``tools`` so the forthcoming
``ToolDispatcher`` can partition tool-calls into parallel / sequential /
destructive lanes (see design.md § ToolDispatcher).

Column:
  * ``safety`` VARCHAR(16) NOT NULL DEFAULT 'sequential'
  * CHECK constraint ``safety IN ('parallel-safe','sequential','destructive')``

Built-in seed classification (matches design.md § ToolDispatcher table):

  parallel-safe:
      grep_kb, read_wiki, list_wiki, memory_retrieve, list_cron_jobs,
      get_config, list_datasources, query_cmdb_nodes, search_logs,
      count_logs, search_tickets, get_ticket_detail
  destructive:
      execute, write_wiki, write_raw, cron_create, sync_datasource

Every other row (read/write/edit/bash/task/ls/... and user-defined
tools) keeps the column default, ``sequential``.

The server-side default is dropped after backfill so application-level
inserts must supply the value (the ORM default keeps SQLAlchemy-created
rows consistent). Requirements: R-1.7.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202605041830"
down_revision: str | None = "202605041820"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep in sync with ToolManager._BUILTIN_SAFETY_SEED.
_PARALLEL_SAFE_NAMES: tuple[str, ...] = (
    "grep_kb",
    "read_wiki",
    "list_wiki",
    "memory_retrieve",
    "list_cron_jobs",
    "get_config",
    "list_datasources",
    "query_cmdb_nodes",
    "search_logs",
    "count_logs",
    "search_tickets",
    "get_ticket_detail",
)

_DESTRUCTIVE_NAMES: tuple[str, ...] = (
    "execute",
    "write_wiki",
    "write_raw",
    "cron_create",
    "sync_datasource",
)


def _backfill_safety(connection, names: Sequence[str], classification: str) -> None:
    """Set safety=<classification> for every row whose name matches.

    Uses a parameterised UPDATE — absent names are simply no-ops.
    """
    if not names:
        return
    connection.execute(
        sa.text(
            "UPDATE tools SET safety = :cls WHERE name = ANY(:names)"
        ),
        {"cls": classification, "names": list(names)},
    )


def upgrade() -> None:
    # 1. Add the column with a server_default so existing rows get 'sequential'.
    op.add_column(
        "tools",
        sa.Column(
            "safety",
            sa.String(length=16),
            nullable=False,
            server_default="sequential",
        ),
    )

    # 2. Backfill builtins per design.md § ToolDispatcher. Rows whose name
    #    isn't present are silently skipped (expected on a fresh install
    #    before the tool-registry seed runs).
    bind = op.get_bind()
    _backfill_safety(bind, _PARALLEL_SAFE_NAMES, "parallel-safe")
    _backfill_safety(bind, _DESTRUCTIVE_NAMES, "destructive")

    # 3. Drop the DDL default; the ORM default (mapped_column server_default)
    #    plus application code now owns the value for new rows.
    op.alter_column("tools", "safety", server_default=None)

    # 4. Enforce the finite domain via a check constraint.
    op.create_check_constraint(
        "ck_tools_safety",
        "tools",
        "safety IN ('parallel-safe','sequential','destructive')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tools_safety", "tools", type_="check")
    op.drop_column("tools", "safety")
