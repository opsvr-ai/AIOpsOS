"""Add spaces, space_members, space_invitations, space_join_requests tables.
Add space_id to scoped resources. Add category to notifications. Add default_space_id to users."""

revision = "012_spaces"
down_revision = "011_session_review_flags"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


def upgrade():
    # ── New tables ──────────────────────────────────────────────

    op.create_table(
        "spaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False, server_default="private"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_spaces_visibility", "spaces", ["visibility"])
    op.create_index("ix_spaces_created_by", "spaces", ["created_by"])

    op.create_table(
        "space_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("space_id", UUID(as_uuid=True), sa.ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_space_member", "space_members", ["space_id", "user_id"])
    op.create_index("ix_space_members_user_id", "space_members", ["user_id"])

    op.create_table(
        "space_invitations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("space_id", UUID(as_uuid=True), sa.ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("inviter_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invitee_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_space_invitation", "space_invitations", ["space_id", "invitee_id"])
    op.create_index("ix_space_invitations_invitee", "space_invitations", ["invitee_id", "status"])

    op.create_table(
        "space_join_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("space_id", UUID(as_uuid=True), sa.ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message", sa.String(200), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_space_join_request", "space_join_requests", ["space_id", "user_id"])
    op.create_index("ix_join_requests_status", "space_join_requests", ["space_id", "status"])

    # ── Ensure tables missed in earlier migrations exist ──────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS datasources (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(256) NOT NULL,
            description VARCHAR(1024),
            source_type VARCHAR(32) NOT NULL,
            is_enabled BOOLEAN DEFAULT TRUE,
            config JSONB DEFAULT '{}'::jsonb,
            normalization_rules JSONB DEFAULT '{}'::jsonb,
            last_ingested_at TIMESTAMPTZ,
            total_ingested INTEGER DEFAULT 0,
            status VARCHAR(16) DEFAULT 'active',
            error_message VARCHAR(1024),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS cron_jobs (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            prompt TEXT NOT NULL,
            schedule VARCHAR NOT NULL,
            timezone_str VARCHAR DEFAULT 'Asia/Shanghai',
            skills JSONB DEFAULT '[]'::jsonb,
            enabled_toolsets JSONB DEFAULT '[]'::jsonb,
            delivery JSONB,
            enabled BOOLEAN DEFAULT TRUE,
            last_run TIMESTAMPTZ,
            next_run TIMESTAMPTZ,
            last_output TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            alert_id UUID REFERENCES alerts(id) ON DELETE SET NULL,
            title VARCHAR(256) NOT NULL,
            message VARCHAR(1024),
            severity VARCHAR(16) NOT NULL DEFAULT 'info',
            is_read BOOLEAN DEFAULT FALSE,
            read_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
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

    # ── Add space_id to scoped tables ───────────────────────────

    insp = sa.inspect(op.get_bind())
    existing_tables = set(insp.get_table_names())

    for table in (
        "sessions", "agent_memories", "agents", "tools",
        "knowledge_documents", "alerts", "datasources",
        "scenarios", "schedules", "cron_jobs", "notification_channels",
    ):
        if table not in existing_tables:
            continue
        columns = {c["name"] for c in insp.get_columns(table)}
        if "space_id" in columns:
            continue
        op.add_column(table, sa.Column(
            "space_id", UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True,
        ))
        op.create_index(f"ix_{table}_space_id", table, ["space_id"])

    # ── Add category to notifications ───────────────────────────

    if "category" not in {c["name"] for c in insp.get_columns("notifications")}:
        op.add_column("notifications", sa.Column(
            "category", sa.String(32), nullable=False, server_default="alert",
        ))
    op.create_index("ix_notifications_category", "notifications", ["category"],
                    if_not_exists=True)

    # ── Add default_space_id to users ───────────────────────────

    if "default_space_id" not in {c["name"] for c in insp.get_columns("users")}:
        op.add_column("users", sa.Column(
            "default_space_id", UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True,
        ))


def downgrade():
    op.drop_column("users", "default_space_id")
    op.drop_index("ix_notifications_category")
    op.drop_column("notifications", "category")

    for table in (
        "sessions", "agent_memories", "agents", "tools",
        "knowledge_documents", "alerts", "datasources",
        "scenarios", "schedules", "cron_jobs", "notification_channels",
    ):
        op.drop_index(f"ix_{table}_space_id")
        op.drop_column(table, "space_id")

    op.drop_table("space_join_requests")
    op.drop_table("space_invitations")
    op.drop_table("space_members")
    op.drop_table("spaces")
