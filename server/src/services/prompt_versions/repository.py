"""CRUD repository for ``sub_agent_prompt_versions``.

Spec: .kiro/specs/agent-runtime-optimization-evolution,
task 18.1 / R-3.15 (backing), R-3.20 (default fallback).

Scope: read-mostly accessors used by
:class:`~src.services.evolution.prompt_registry.SubAgentPromptRegistry`
to materialise the in-memory snapshot plus a few helpers the Promoter /
Reloader need (``get_by_id``, ``get_previous_active``, ``get_by_candidate``).

What this layer explicitly **does not** do:

* No status-machine enforcement. State transitions live in
  :mod:`src.services.evolution.promoter` (task 23).
* No caching. The registry caches; the repo hits the DB every call.
* No writes. Inserts / updates are the Promoter's responsibility.
  The one exception is ``bump_status`` which exists only for tests and
  admin-console overrides.

Return shape is an immutable :class:`PromptVersionRow` dataclass so
SQLAlchemy detached-instance hazards can't leak into the registry's
lock-free read path.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.base import async_session_factory
from src.models.evolution import SubAgentPromptVersion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plain, picklable row view
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromptVersionRow:
    """Immutable snapshot of one ``sub_agent_prompt_versions`` row.

    Decoupled from the ORM object so the registry can hand these out to
    callers without inheriting SQLAlchemy's identity-map rules or
    session lifetime. Safe to share across coroutines / threads.
    """

    id: uuid.UUID
    sub_agent_name: str
    candidate_id: uuid.UUID | None
    system_prompt: str
    rationale: str | None
    status: str
    parent_version_id: uuid.UUID | None
    manifest_sha256: str | None
    activated_at: datetime | None
    retired_at: datetime | None
    created_at: datetime

    @classmethod
    def from_orm(cls, row: SubAgentPromptVersion) -> PromptVersionRow:
        return cls(
            id=row.id,
            sub_agent_name=row.sub_agent_name,
            candidate_id=row.candidate_id,
            system_prompt=row.system_prompt,
            rationale=row.rationale,
            status=row.status,
            parent_version_id=row.parent_version_id,
            manifest_sha256=row.manifest_sha256,
            activated_at=row.activated_at,
            retired_at=row.retired_at,
            created_at=row.created_at,
        )


# Valid ``status`` values we index against. Kept as a plain tuple so a
# typo here won't silently match an unexpected row from the DB. The full
# state machine lives in design.md § Evolution.
_LIVE_STATUSES: tuple[str, ...] = ("proposed", "shadow", "ab", "active")


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class SubAgentPromptVersionRepository:
    """Thin async CRUD wrapper around ``sub_agent_prompt_versions``.

    Construct once per process (or once per test). The default
    session factory is the module-level ``async_session_factory`` so a
    caller doesn't need to wire anything up in production code.
    """

    def __init__(self, session_factory: async_sessionmaker | None = None) -> None:
        self._session_factory = session_factory or async_session_factory

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_live(self) -> list[PromptVersionRow]:
        """All rows with a live status (proposed/shadow/ab/active).

        Used by :meth:`SubAgentPromptRegistry.load` on startup. Rows are
        ordered by ``(sub_agent_name, created_at DESC)`` so the caller
        sees the newest version of each sub-agent first — handy when
        two rows share a bucket (shouldn't happen for ``active`` thanks
        to the partial UNIQUE index, but better to sort defensively).
        """
        stmt = (
            select(SubAgentPromptVersion)
            .where(SubAgentPromptVersion.status.in_(_LIVE_STATUSES))
            .order_by(
                SubAgentPromptVersion.sub_agent_name,
                desc(SubAgentPromptVersion.created_at),
            )
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [PromptVersionRow.from_orm(r) for r in rows]

    async def get_by_id(self, version_id: uuid.UUID | str) -> PromptVersionRow | None:
        """Fetch a single row by primary key, status-agnostic.

        Promoter rollback and ``apply_promotion`` both need to look up
        specific versions — including already-retired ones — so this
        method intentionally does **not** filter by status.
        """
        vid = _coerce_uuid(version_id)
        if vid is None:
            return None
        stmt = select(SubAgentPromptVersion).where(SubAgentPromptVersion.id == vid)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalars().first()
        return PromptVersionRow.from_orm(row) if row is not None else None

    async def get_active(self, sub_agent_name: str) -> PromptVersionRow | None:
        """Return the single ``active`` version for ``sub_agent_name``.

        The partial UNIQUE index ``sapv_active_idx`` guarantees at most
        one row matches; callers can treat the return value as
        canonical.
        """
        stmt = (
            select(SubAgentPromptVersion)
            .where(
                and_(
                    SubAgentPromptVersion.sub_agent_name == sub_agent_name,
                    SubAgentPromptVersion.status == "active",
                )
            )
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalars().first()
        return PromptVersionRow.from_orm(row) if row is not None else None

    async def get_previous_active(
        self,
        sub_agent_name: str,
        *,
        before_id: uuid.UUID | str | None,
    ) -> PromptVersionRow | None:
        """Return the prior ``active`` version for rollback semantics.

        "Prior" is defined as the most recent row for ``sub_agent_name``
        whose status was ``active`` at some point (i.e. ``activated_at``
        is set) **and** whose ``id`` is not ``before_id``. We order by
        ``activated_at DESC`` so the closest predecessor wins; rows
        that never activated (rejected / proposed) are skipped.
        """
        bid = _coerce_uuid(before_id) if before_id is not None else None
        conds = [
            SubAgentPromptVersion.sub_agent_name == sub_agent_name,
            SubAgentPromptVersion.activated_at.is_not(None),
        ]
        if bid is not None:
            conds.append(SubAgentPromptVersion.id != bid)

        stmt = (
            select(SubAgentPromptVersion)
            .where(and_(*conds))
            .order_by(desc(SubAgentPromptVersion.activated_at))
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalars().first()
        return PromptVersionRow.from_orm(row) if row is not None else None

    async def list_by_sub_agent(
        self, sub_agent_name: str
    ) -> list[PromptVersionRow]:
        """Every row for ``sub_agent_name`` regardless of status.

        Powers the admin prompt-versions list endpoint (task 23.4).
        Ordered newest-first by ``created_at`` so the UI shows the
        most recent activity at the top. Retired / rejected rows are
        included — the admin view needs the full audit trail.
        """
        stmt = (
            select(SubAgentPromptVersion)
            .where(SubAgentPromptVersion.sub_agent_name == sub_agent_name)
            .order_by(desc(SubAgentPromptVersion.created_at))
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [PromptVersionRow.from_orm(r) for r in rows]

    async def get_by_candidate(
        self, candidate_id: uuid.UUID | str
    ) -> list[PromptVersionRow]:
        """All prompt_versions linked to a given ``skill_candidate`` row.

        A candidate can spawn multiple versions over its lifecycle
        (shadow → ab → active, each as a separate row in some
        implementations), so the return type is ``list[…]``. Ordered
        oldest-first to make it easy to build a timeline view.
        """
        cid = _coerce_uuid(candidate_id)
        if cid is None:
            return []
        stmt = (
            select(SubAgentPromptVersion)
            .where(SubAgentPromptVersion.candidate_id == cid)
            .order_by(SubAgentPromptVersion.created_at.asc())
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [PromptVersionRow.from_orm(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    """Accept ``UUID | str | None`` and return a ``UUID`` or ``None``.

    Keeps repository callers ergonomic — Kafka events carry string ids,
    ORM code carries ``uuid.UUID``; both should just work. Malformed
    strings return ``None`` rather than raising so a single bad event
    can't crash a full ``apply_promotion`` call.
    """
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        logger.warning("prompt_versions.repository: invalid uuid %r", value)
        return None


__all__ = [
    "PromptVersionRow",
    "SubAgentPromptVersionRepository",
]
