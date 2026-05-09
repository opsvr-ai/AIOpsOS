"""Pydantic request/response shapes for the evolution admin API.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.4
(Phase L — Admin API for evolution). Covers R-3.8 / R-3.9 / R-8.4.

These shapes back the routes in :mod:`src.api.control.evolution`.
Kept here rather than inside the router module so the same schemas
can be reused by the CLI (task 23.5, ``scripts/evo_ctl.py``) and any
downstream tooling that needs the same payload contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Prompt versions
# ---------------------------------------------------------------------------


class PromptVersionSummaryOut(BaseModel):
    """Summary view of one ``sub_agent_prompt_versions`` row.

    Omits the full ``system_prompt`` body — list responses only carry
    the metadata. Clients that need the body call the diff endpoint
    or the single-version GET endpoint.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sub_agent_name: str
    status: str
    manifest_sha256: str | None = None
    candidate_id: uuid.UUID | None = None
    parent_version_id: uuid.UUID | None = None
    activated_at: datetime | None = None
    retired_at: datetime | None = None
    created_at: datetime


class PromptVersionDiffOut(BaseModel):
    """Unified-diff between a requested prompt version and the active one."""

    sub_agent_name: str
    requested_version_id: uuid.UUID
    requested_status: str
    active_version_id: uuid.UUID | None = None
    active_status: str | None = None
    # Unified-diff text body. Empty string when both sides are byte-equal.
    diff: str
    # Line counts for convenience in UIs that render a summary ahead of
    # the diff body.
    added: int = 0
    removed: int = 0


class RollbackResultOut(BaseModel):
    """Serialised :class:`~src.services.evolution.promoter.RollbackResult`."""

    ok: bool
    kind: str
    name: str
    retired_version_id: uuid.UUID | None = None
    restored_version_id: uuid.UUID | None = None
    reason: str
    event_published: bool = False


class ActivateResultOut(BaseModel):
    """Outcome of an admin-override activation request."""

    candidate_id: uuid.UUID
    from_status: str
    to_status: str
    action: Literal["promoted", "activated", "noop", "rejected"]
    reason: str


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


class CandidateOut(BaseModel):
    """Summary view of one candidate row.

    Kind-specific fields (``target_ref`` for prompt/tool-config,
    ``data`` for any carried blob) are exposed via ``extra``. The
    caller doesn't need to know which table the row physically lives
    in — the ``table`` field is there for diagnostics only.
    """

    id: uuid.UUID
    kind: str
    name: str
    status: str
    table: str
    target_ref: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class PromoteResultOut(BaseModel):
    """Outcome of a candidate promote (one-edge) operation."""

    candidate_id: uuid.UUID
    from_status: str
    to_status: str
    action: Literal["advanced", "activated", "noop", "rejected"]
    reason: str


class RejectResultOut(BaseModel):
    """Outcome of a candidate reject operation."""

    candidate_id: uuid.UUID
    from_status: str
    to_status: Literal["rejected"]
    reason: str


__all__ = [
    "ActivateResultOut",
    "CandidateOut",
    "PromoteResultOut",
    "PromptVersionDiffOut",
    "PromptVersionSummaryOut",
    "RejectResultOut",
    "RollbackResultOut",
]
