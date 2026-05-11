"""add trajectory and evolution tables

Revision ID: 202605041800
Revises: 202605041750
Create Date: 2026-05-04 18:00:00.000000

Creates the data-model foundation for the Agent Runtime Optimization &
Evolution feature (spec: .kiro/specs/agent-runtime-optimization-evolution).

Tables:
  * agent_trajectories        — per-turn/tool/subagent/router-decision trace
  * skill_candidates          — proposed / shadow / ab / active skill or
                                prompt-patch / tool-config candidates
  * skill_evaluations         — offline eval-set scores for a candidate
  * eval_set_items            — per-scenario evaluation samples
  * runtime_feature_flags     — rollout flags with per-user stable hashing
  * skill_versions            — promoted skill history (rollback chain)
  * sub_agent_prompt_versions — sub-agent system-prompt versions with status
  * kafka_topic_schemas       — local JSON-schema registry for Kafka topics

DDL is taken verbatim from design.md § Data Models. Both upgrade() and
downgrade() are exactly reversible.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "202605041800"
down_revision: str | None = "202605041750"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. agent_trajectories — per-event trace table
    # ------------------------------------------------------------------
    op.create_table(
        "agent_trajectories",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "parent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_trajectories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column(
            "data",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tags",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "trajectory_session_idx",
        "agent_trajectories",
        ["session_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "trajectory_outcome_idx",
        "agent_trajectories",
        ["outcome", sa.text("created_at DESC")],
        postgresql_where=sa.text("outcome != 'ok'"),
    )
    op.create_index(
        "trajectory_kind_idx",
        "agent_trajectories",
        ["kind", sa.text("created_at DESC")],
    )
    op.create_index(
        "trajectory_tags_idx",
        "agent_trajectories",
        ["tags"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------
    # 2. skill_candidates — candidate entries (skill / prompt_patch / tool_config)
    # ------------------------------------------------------------------
    op.create_table(
        "skill_candidates",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("proposal_source", sa.String(length=32), nullable=False),
        sa.Column(
            "origin_trajectory_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'proposed'"),
        ),
        sa.Column("skill_prompt", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            JSONB(),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tool_names",
            JSONB(),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("baseline_skill_id", sa.String(length=128), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "kind",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'skill'"),
        ),
        sa.Column("target_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "skill_candidate_status_idx",
        "skill_candidates",
        ["status", sa.text("updated_at DESC")],
    )
    op.create_index(
        "skill_candidate_name_status_idx",
        "skill_candidates",
        ["name", "status"],
        unique=True,
        postgresql_where=sa.text("status IN ('shadow', 'ab', 'active')"),
    )

    # ------------------------------------------------------------------
    # 3. skill_evaluations — per-candidate eval-set result rows
    # ------------------------------------------------------------------
    op.create_table(
        "skill_evaluations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("eval_set_name", sa.String(length=64), nullable=False),
        sa.Column("baseline_score", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("candidate_score", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("n_samples", sa.Integer(), nullable=True),
        sa.Column(
            "details",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "skill_eval_candidate_idx",
        "skill_evaluations",
        ["candidate_id", sa.text("created_at DESC")],
    )

    # ------------------------------------------------------------------
    # 4. eval_set_items — offline evaluation samples
    # ------------------------------------------------------------------
    op.create_table(
        "eval_set_items",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("set_name", sa.String(length=64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "expected_tools",
            JSONB(),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("expected_outcome", sa.String(length=16), nullable=True),
        sa.Column("grading_prompt", sa.Text(), nullable=True),
        sa.Column(
            "weight",
            sa.Numeric(precision=4, scale=2),
            nullable=True,
            server_default=sa.text("1.0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("eval_item_set_idx", "eval_set_items", ["set_name"])

    # ------------------------------------------------------------------
    # 5. runtime_feature_flags — rollout flags with stable hashing
    # ------------------------------------------------------------------
    op.create_table(
        "runtime_feature_flags",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "rollout_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "data",
            JSONB(),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ------------------------------------------------------------------
    # 6. skill_versions — promoted skill history
    # ------------------------------------------------------------------
    op.create_table(
        "skill_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill_candidates.id"),
            nullable=True,
        ),
        sa.Column("skill_prompt", sa.Text(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "was_successor",
            sa.Boolean(),
            nullable=True,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "sv_name_act_idx",
        "skill_versions",
        ["skill_name", sa.text("activated_at DESC")],
    )

    # ------------------------------------------------------------------
    # 7. sub_agent_prompt_versions — per-subagent prompt version chain
    # ------------------------------------------------------------------
    op.create_table(
        "sub_agent_prompt_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("sub_agent_name", sa.String(length=64), nullable=False),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("skill_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'proposed'"),
        ),
        sa.Column(
            "parent_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sub_agent_prompt_versions.id"),
            nullable=True,
        ),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "sapv_active_idx",
        "sub_agent_prompt_versions",
        ["sub_agent_name"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "sapv_status_idx",
        "sub_agent_prompt_versions",
        ["sub_agent_name", "status", sa.text("created_at DESC")],
    )

    # ------------------------------------------------------------------
    # 8. kafka_topic_schemas — local JSON-schema registry
    # ------------------------------------------------------------------
    op.create_table(
        "kafka_topic_schemas",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("topic", sa.String(length=256), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("schema", JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("topic", "version", name="uq_kafka_topic_schemas_topic_version"),
    )


def downgrade() -> None:
    # Reverse order of upgrade(). Indexes attached to a table are dropped
    # automatically together with the table, but we drop them explicitly
    # for clarity and to guarantee identical on-disk state after
    # up → down → up.

    # 8. kafka_topic_schemas
    op.drop_table("kafka_topic_schemas")

    # 7. sub_agent_prompt_versions
    op.drop_index("sapv_status_idx", table_name="sub_agent_prompt_versions")
    op.drop_index("sapv_active_idx", table_name="sub_agent_prompt_versions")
    op.drop_table("sub_agent_prompt_versions")

    # 6. skill_versions
    op.drop_index("sv_name_act_idx", table_name="skill_versions")
    op.drop_table("skill_versions")

    # 5. runtime_feature_flags
    op.drop_table("runtime_feature_flags")

    # 4. eval_set_items
    op.drop_index("eval_item_set_idx", table_name="eval_set_items")
    op.drop_table("eval_set_items")

    # 3. skill_evaluations
    op.drop_index("skill_eval_candidate_idx", table_name="skill_evaluations")
    op.drop_table("skill_evaluations")

    # 2. skill_candidates
    op.drop_index("skill_candidate_name_status_idx", table_name="skill_candidates")
    op.drop_index("skill_candidate_status_idx", table_name="skill_candidates")
    op.drop_table("skill_candidates")

    # 1. agent_trajectories
    op.drop_index("trajectory_tags_idx", table_name="agent_trajectories")
    op.drop_index("trajectory_kind_idx", table_name="agent_trajectories")
    op.drop_index("trajectory_outcome_idx", table_name="agent_trajectories")
    op.drop_index("trajectory_session_idx", table_name="agent_trajectories")
    op.drop_table("agent_trajectories")
