"""Add pg_trgm indexes for fast ILIKE search on tool name/description."""

revision = "008_tool_search_idx"
down_revision = "007_event_id_string"

from alembic import op


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tool_name_trgm "
        "ON tools USING gin (name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tool_description_trgm "
        "ON tools USING gin (description gin_trgm_ops)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_tool_description_trgm")
    op.execute("DROP INDEX IF EXISTS ix_tool_name_trgm")
