"""Memory consolidation worker task.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 10.1.

The real pipeline lives in :mod:`src.services.memory.consolidation_logic`
as an injectable async function so tests can drive it without a real
Celery worker. The Celery task here is a thin wrapper that:

1. Bridges sync Celery into ``asyncio.run`` (Celery workers don't have
   an event loop).
2. Retries with exponential backoff on any exception (max 3 retries).
3. Accepts an optional ``degraded`` kwarg forwarded from
   :class:`~src.services.sleep_scheduler.SleepScheduler` when the queue
   depth exceeds the backpressure threshold.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.services.memory.consolidation_logic import (
    DIFF_EXTRACTION_PROMPT,
    run_consolidation as run_consolidation_async,
)
from src.workers.app import celery

logger = logging.getLogger(__name__)

__all__ = ["run_consolidation", "DIFF_EXTRACTION_PROMPT"]


@celery.task(
    name="memory.consolidate",
    bind=True,
    acks_late=True,
    max_retries=3,
)
def run_consolidation(self, session_id: str, degraded: bool = False) -> dict[str, Any]:
    """Consolidate pending turns for a session.

    Blocks the worker slot for the duration of the LLM + DB calls. Safe
    to call repeatedly (distributed lock + ON CONFLICT DO NOTHING
    semantics make every retry idempotent).
    """
    try:
        result = asyncio.run(
            run_consolidation_async(session_id, degraded=degraded)
        )
    except Exception as exc:  # pragma: no cover - exercised via Celery retry path
        # Exponential backoff: 30s, 60s, 120s. Raise terminal failure after
        # ``max_retries`` so the Celery result reflects the real outcome.
        countdown = 30 * (2 ** self.request.retries)
        logger.warning(
            "run_consolidation[%s] failed (attempt %d), retrying in %ds: %s",
            session_id,
            self.request.retries + 1,
            countdown,
            exc,
        )
        raise self.retry(exc=exc, countdown=countdown, max_retries=3)

    return result.to_dict()
