"""Reflection worker task.

Spec: .kiro/specs/agent-runtime-optimization-evolution,
tasks 21.1 (failure clustering) and 21.2 (candidate generation).

R-3.1: WHEN ``ReflectionWorker`` runs THE system SHALL pull trajectories
with ``outcome in ('error','timeout')`` plus sessions with ``count >= 3``
failures in 24h, and LLM-cluster them into named groups with example
trajectory ids.

R-3.2: the three candidate kinds (``skill`` / ``prompt_patch`` /
``tool_config``) all flow through the same reflector → evaluator →
promoter state machine.

R-3.3: skill artefacts land only in
``data/skills/.candidate/<name>/`` and prompt_patch candidates go to
``sub_agent_prompt_versions`` with ``status='proposed'``. Neither
touches the main ``data/skills/`` directory.

The real pipeline lives in
:mod:`src.services.evolution.reflection_logic` so tests can drive it
with injected DB / LLM fakes. This Celery wrapper is intentionally thin:

1. Bridges sync Celery into ``asyncio.run`` (Celery workers have no
   event loop of their own).
2. Retries with exponential backoff on unexpected exceptions (max 3
   retries at 60s / 120s / 240s).
3. Returns the full cycle result so the Celery payload reflects both
   the clusters produced and the candidates persisted.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.services.evolution.reflection_logic import (
    CANDIDATE_GEN_PROMPT,
    CLUSTER_FAILURES_PROMPT,
    run_reflection_full_cycle,
)
from src.workers.app import celery

logger = logging.getLogger(__name__)

__all__ = [
    "CANDIDATE_GEN_PROMPT",
    "CLUSTER_FAILURES_PROMPT",
    "run_reflection_cycle",
]


@celery.task(
    name="evolution.reflection",
    bind=True,
    acks_late=True,
    max_retries=3,
)
def run_reflection_cycle(
    self,
    *,
    window_hours: int = 24,
    max_trajectories: int = 500,
    persist: bool = True,
) -> dict[str, Any]:
    """Run one reflection cycle: cluster failures → generate candidates.

    Args:
        window_hours: trajectory lookback window. Defaults to 24h (R-3.1).
        max_trajectories: hard cap on trajectories fed to the LLM in a
            single cycle. Defaults to 500 (matches design.md § Reflector).
        persist: when True (default), candidate proposals are written
            to the DB and (for skill kind) to
            ``data/skills/.candidate/<name>/SKILL.md``. Tests or dry
            runs can set this to False to surface proposals without
            side effects.

    Returns:
        Dictionary form of
        :class:`src.services.evolution.reflection_logic.ReflectionCycleResult`.
    """
    try:
        result = asyncio.run(
            run_reflection_full_cycle(
                window_hours=window_hours,
                max_trajectories=max_trajectories,
                persist=persist,
            )
        )
    except Exception as exc:  # pragma: no cover - retry path
        countdown = 60 * (2 ** self.request.retries)
        logger.warning(
            "run_reflection_cycle failed (attempt %d), retrying in %ds: %s",
            self.request.retries + 1,
            countdown,
            exc,
        )
        raise self.retry(exc=exc, countdown=countdown, max_retries=3)

    return result.to_dict()
