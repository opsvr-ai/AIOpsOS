"""extend agent_memories and sessions for evolution/runtime features

Revision ID: 202605041810
Revises: 202605041800
Create Date: 2026-05-04 18:10:00.000000

Adds the columns + indexes required by the Agent Runtime Optimization &
Evolution feature (spec: .kiro/specs/agent-runtime-optimization-evolution).

agent_memories — additions:
  * content_hash    VARCHAR(64)          — sha256 hash for embedding cache lookup
  * is_archived     BOOLEAN NOT NULL     — set TRUE when superseded by newer memory
  * superseded_by   UUID                 — FK → agent_memories(id) ON DELETE SET NULL
  * pinned          BOOLEAN NOT NULL     — manually pinned to HOT block
  * last_used_at    TIMESTAMPTZ          — most-recent read timestamp (recency score)

Indexes on agent_memories:
  * agent_memories_active_idx
        (user_id, scope, created_at DESC) WHERE is_archived = FALSE
  * agent_memories_embed_idx
        USING hnsw (embedding vector_cosine_ops) WHERE embedding IS NOT NULL
        — requires the pgvector extension (already provided by the
          pgvector/pgvector:pg15 image per design.md § Infrastructure).

sessions — additions:
  * last_consolidation_at TIMESTAMPTZ
  * consolidation_count   INT NOT NULL DEFAULT 0
  * hot_memory_version    INT NOT NULL DEFAULT 0

Both upgrade() and downgrade() are exactly reversible.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "202605041810"
down_revision: str | None = "202605041800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. agent_memories — new columns
    # ------------------------------------------------------------------
    op.add_column(
        "agent_memories",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_memories",
        sa.Column(
            "is_archived",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "agent_memories",
        sa.Column("superseded_by", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_memories_superseded_by",
        source_table="agent_memories",
        referent_table="agent_memories",
        local_cols=["superseded_by"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "agent_memories",
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "agent_memories",
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # 2. agent_memories — new indexes
    # ------------------------------------------------------------------
    # Partial btree for the "active, per-user, recent" read path.
    op.create_index(
        "agent_memories_active_idx",
        "agent_memories",
        ["user_id", "scope", sa.text("created_at DESC")],
        postgresql_where=sa.text("is_archived = false"),
    )

    # HNSW vector ANN index. SQLAlchemy/alembic's kwargs-based rendering
    # for "USING hnsw (col vector_cosine_ops) WHERE ..." is awkward
    # (postgresql_ops + postgresql_using + postgresql_where have to align
    # across alembic versions). Use raw DDL for a stable, reviewable form.
    op.execute(
        sa.text(
            "CREATE INDEX agent_memories_embed_idx "
            "ON agent_memories "
            "USING hnsw (embedding vector_cosine_ops) "
            "WHERE embedding IS NOT NULL"
        )
    )

    # ------------------------------------------------------------------
    # 3. sessions — new columns
    # ------------------------------------------------------------------
    op.add_column(
        "sessions",
        sa.Column("last_consolidation_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "consolidation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "hot_memory_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    # Reverse order of upgrade(). Drop indexes before their columns; drop
    # the self-referential FK before the column it secures.

    # 3. sessions — columns
    op.drop_column("sessions", "hot_memory_version")
    op.drop_column("sessions", "consolidation_count")
    op.drop_column("sessions", "last_consolidation_at")

    # 2. agent_memories — indexes
    op.execute(sa.text("DROP INDEX IF EXISTS agent_memories_embed_idx"))
    op.drop_index("agent_memories_active_idx", table_name="agent_memories")

    # 1. agent_memories — columns (FK first)
    op.drop_column("agent_memories", "last_used_at")
    op.drop_column("agent_memories", "pinned")
    op.drop_constraint(
        "fk_agent_memories_superseded_by",
        "agent_memories",
        type_="foreignkey",
    )
    op.drop_column("agent_memories", "superseded_by")
    op.drop_column("agent_memories", "is_archived")
    op.drop_column("agent_memories", "content_hash")
