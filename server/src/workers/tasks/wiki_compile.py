"""Wiki compiler worker task.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 12.1 /
R-2.12 / R-2.13.

This Celery task is deliberately thin — it just runs the async
pipeline from :mod:`src.services.kb.compile_logic` and handles retry
semantics. All orchestration + LLM + DB logic lives in ``compile_logic``
so it can be unit-tested without a broker.
"""
from __future__ import annotations

import asyncio
import logging

from src.services.kb.compile_logic import compile_wiki_async
from src.workers.app import celery

logger = logging.getLogger(__name__)

__all__ = ["compile_wiki"]


@celery.task(name="wiki.compile", bind=True, acks_late=True, max_retries=3)
def compile_wiki(self, raw_path: str) -> dict:
    """Compile a raw knowledge file into a wiki page.

    Idempotent on ``sha256(raw_path)``. On transient failures, retries up
    to 3 times with exponential backoff (30s, 60s, 120s).
    """
    try:
        from src.models.base import async_session_factory

        result = asyncio.run(
            compile_wiki_async(raw_path, db_factory=async_session_factory)
        )
        return result.to_dict()
    except Exception as exc:
        countdown = 30 * (2 ** self.request.retries)
        logger.exception(
            "wiki.compile failed (retry=%d, countdown=%ds)",
            self.request.retries,
            countdown,
        )
        raise self.retry(exc=exc, countdown=countdown, max_retries=3)
