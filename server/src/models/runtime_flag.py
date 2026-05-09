"""SQLAlchemy model for the ``runtime_feature_flags`` table.

Matches migration ``202605041800_add_trajectory_and_evolution_tables.py``.
Backs ``FeatureFlagService`` (see design.md § Feature Flag Service).

``key`` is a short string PK rather than a surrogate UUID because lookups
are by the flag name itself (``router_llm_enabled`` etc.) and flags are
hand-curated.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class RuntimeFeatureFlag(Base):
    __tablename__ = "runtime_feature_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    rollout_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    data: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=dict, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
