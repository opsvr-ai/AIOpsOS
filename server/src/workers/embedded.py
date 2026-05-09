"""In-process Celery worker for ``service_type == 'allinone'`` deployments.

A developer running a single FastAPI process wants background tasks to
run without having to launch a separate ``celery -A src.workers.app
worker`` process. This module spawns a ``WorkController`` on a daemon
thread with ``pool=solo`` so it shares the Python process but does not
share the asyncio event loop.

Callers should invoke :func:`start_embedded_worker` from the FastAPI
``lifespan`` startup hook and :func:`stop_embedded_worker` on shutdown.
Both calls are idempotent.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 3.4.
"""

from __future__ import annotations

import logging
import threading

from celery.worker import WorkController

from src.workers.app import celery

__all__ = ["start_embedded_worker", "stop_embedded_worker"]

logger = logging.getLogger(__name__)

# Module-level guards — keep a single embedded worker per process.
_worker: WorkController | None = None
_thread: threading.Thread | None = None


def start_embedded_worker() -> WorkController | None:
    """Start an embedded Celery worker on a daemon thread.

    Idempotent: calling twice returns the existing worker. Swallows no
    exceptions — the caller should wrap in try/except to avoid blocking
    startup if the broker is unreachable.

    Returns the ``WorkController`` instance (or ``None`` if disabled).
    """
    global _worker, _thread

    if _worker is not None:
        logger.debug("Embedded worker already running; skipping start")
        return _worker

    worker = WorkController(
        app=celery,
        pool_cls="solo",
        queues=("memory", "wiki", "evolution"),
        concurrency=1,
        loglevel="INFO",
        # Avoid stealing signal handlers from the FastAPI main thread.
        without_mingle=True,
        without_gossip=True,
        without_heartbeat=True,
    )

    def _run() -> None:
        try:
            worker.start()
        except Exception:  # pragma: no cover — worker errors surface in logs
            logger.exception("Embedded Celery worker crashed")

    thread = threading.Thread(
        target=_run,
        name="celery-embedded-worker",
        daemon=True,
    )
    thread.start()

    _worker = worker
    _thread = thread
    logger.info(
        "Embedded Celery worker started (queues=memory,wiki,evolution, pool=solo)"
    )
    return worker


def stop_embedded_worker() -> None:
    """Stop the embedded worker and join its thread (5s timeout)."""
    global _worker, _thread

    worker = _worker
    thread = _thread
    if worker is None:
        return

    try:
        worker.stop()
    except Exception:
        logger.exception("Error stopping embedded Celery worker")

    if thread is not None:
        thread.join(timeout=5.0)
        if thread.is_alive():
            logger.warning("Embedded Celery worker thread did not exit within 5s")

    _worker = None
    _thread = None
    logger.info("Embedded Celery worker stopped")
