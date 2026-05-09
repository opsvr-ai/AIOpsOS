"""Pydantic request/response shapes for the runtime feature flag admin API.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 5.2 / R-7.1.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RuntimeFlagOut(BaseModel):
    """Read shape returned by GET /runtime-flags endpoints."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    enabled: bool
    rollout_percent: int = Field(ge=0, le=100)
    data: dict = Field(default_factory=dict)
    updated_at: datetime


class RuntimeFlagUpsert(BaseModel):
    """Write shape accepted by PUT /runtime-flags/{key}.

    ``data`` is optional; missing / null leaves the stored JSON alone (on
    upsert-insert branches it defaults to ``{}``).
    """

    enabled: bool
    rollout_percent: int = Field(ge=0, le=100)
    data: dict | None = None


__all__ = ["RuntimeFlagOut", "RuntimeFlagUpsert"]
