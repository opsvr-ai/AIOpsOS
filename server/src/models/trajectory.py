"""SQLAlchemy model for the ``agent_trajectories`` table.

Matches the DDL created in migration ``202605041800_add_trajectory_and_evolution_tables.py``.
Backing for TrajectorySink (design.md § TrajectorySink) and
ReflectionWorker source-of-truth.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class AgentTrajectory(Base):
    """One row per turn / tool_call / subagent / router_decision / reflection event.

    ``parent_id`` forms a chain inside a session so a subagent or tool-call
    event can point back to the parent turn. FK is intentionally
    ``ondelete='SET NULL'`` to keep child rows around for offline analysis
    even if the parent is pruned.

    No ``relationship()`` back to ``Session`` — trajectory reads happen
    through dedicated repositories and carrying the ORM edge here would
    eagerly pull huge per-session histories through Session loads.
    """

    __tablename__ = "agent_trajectories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    space_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_trajectories.id", ondelete="SET NULL"),
        nullable=True,
    )

    # kind ∈ {turn, tool_call, subagent, router_decision, reflection}
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # outcome ∈ {ok, error, timeout, rejected}
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)

    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Self-referential parent / children. ``remote_side`` uses a string to
    # avoid ordering issues with the forward declaration of ``id``.
    parent: Mapped["AgentTrajectory | None"] = relationship(
        "AgentTrajectory",
        remote_side="AgentTrajectory.id",
        back_populates="children",
    )
    children: Mapped[list["AgentTrajectory"]] = relationship(
        "AgentTrajectory",
        back_populates="parent",
    )
