"""Add spaces, space_members, space_invitations, space_join_requests tables.
Add space_id to scoped resources. Add category to notifications. Add default_space_id to users."""

revision = "012_spaces"
down_revision = "011_session_review_flags"

from alembic import op
import sqlalchemy as sa
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

    # ── Add space_id to scoped tables ───────────────────────────

    for table in (
        "sessions", "agent_memories", "agents", "tools",
        "knowledge_documents", "alerts", "datasources",
        "scenarios", "schedules", "cron_jobs", "notification_channels",
    ):
        op.add_column(table, sa.Column(
            "space_id", UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True,
        ))
        op.create_index(f"ix_{table}_space_id", table, ["space_id"])

    # ── Add category to notifications ───────────────────────────

    op.add_column("notifications", sa.Column(
        "category", sa.String(32), nullable=False, server_default="alert",
    ))
    op.create_index("ix_notifications_category", "notifications", ["category"])

    # ── Add default_space_id to users ───────────────────────────

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
