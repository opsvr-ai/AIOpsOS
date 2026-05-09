"""SQLAlchemy models for the evolution pipeline.

Matches the DDL created in migration
``202605041800_add_trajectory_and_evolution_tables.py``.

Five tables live here:
  * skill_candidates          — proposed / shadow / ab / active candidates
  * skill_evaluations         — offline eval-set score rows per candidate
  * eval_set_items            — offline evaluation samples
  * skill_versions            — promoted-skill history (rollback chain)
  * sub_agent_prompt_versions — per-subagent system-prompt versions

Loose string columns (``kind``, ``status``) keep the state machine
adjustable without a schema migration — constraints live in the service
layer (see design.md § Evolution).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class SkillCandidate(Base, TimestampMixin):
    """A proposed skill / prompt_patch / tool_config candidate.

    ``kind`` is kept as a loose string (no DB enum constraint) so the
    state machine and candidate taxonomies can evolve without a
    migration. Valid values today: ``skill``, ``prompt_patch``,
    ``tool_config``.
    """

    __tablename__ = "skill_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    proposal_source: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_trajectory_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="proposed", server_default=text("'proposed'")
    )
    skill_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, default=list, server_default=text("'[]'::jsonb")
    )
    tool_names: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, default=list, server_default=text("'[]'::jsonb")
    )
    baseline_skill_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # kind ∈ {skill, prompt_patch, tool_config}
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="skill", server_default=text("'skill'")
    )
    target_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # TimestampMixin provides created_at / updated_at.

    evaluations: Mapped[list["SkillEvaluation"]] = relationship(
        "SkillEvaluation",
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
    prompt_versions: Mapped[list["SubAgentPromptVersion"]] = relationship(
        "SubAgentPromptVersion",
        back_populates="candidate",
    )


class SkillEvaluation(Base):
    """One evaluation run of a candidate against a named eval set."""

    __tablename__ = "skill_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    eval_set_name: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    candidate_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    n_samples: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    candidate: Mapped[SkillCandidate] = relationship(
        "SkillCandidate", back_populates="evaluations"
    )


class EvalSetItem(Base):
    """One offline evaluation sample belonging to a named set (e.g. ``fault_triage_v1``)."""

    __tablename__ = "eval_set_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    set_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    expected_tools: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, default=list, server_default=text("'[]'::jsonb")
    )
    expected_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    grading_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2), nullable=True, server_default=text("1.0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SkillVersion(Base, TimestampMixin):
    """Promoted-skill history. Each row represents one ``active`` → ``retired`` span.

    ``candidate_id`` is nullable so we can seed the table with versions
    synthesized from pre-evolution skills that never had a candidate row.
    """

    __tablename__ = "skill_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_candidates.id"),
        nullable=True,
    )
    skill_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    was_successor: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, default=False, server_default=text("false")
    )

    # TimestampMixin provides created_at / updated_at.

    candidate: Mapped[SkillCandidate | None] = relationship("SkillCandidate")


class SubAgentPromptVersion(Base):
    """Per-subagent ``system_prompt`` version chain with ``proposed/shadow/ab/active/retired`` status.

    ``parent_version_id`` self-FK forms the rollback chain: rolling back
    from ``active`` version N walks to N.parent_version_id. Candidate FK
    is ``SET NULL`` so candidate pruning doesn't cascade and lose history.
    """

    __tablename__ = "sub_agent_prompt_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sub_agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skill_candidates.id", ondelete="SET NULL"),
        nullable=True,
    )
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="proposed", server_default=text("'proposed'")
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sub_agent_prompt_versions.id"),
        nullable=True,
    )
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    candidate: Mapped[SkillCandidate | None] = relationship(
        "SkillCandidate", back_populates="prompt_versions"
    )
    parent_version: Mapped["SubAgentPromptVersion | None"] = relationship(
        "SubAgentPromptVersion",
        remote_side="SubAgentPromptVersion.id",
    )
