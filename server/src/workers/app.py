"""Celery application singleton.

Owns the Celery app, serialization/timezone defaults, task routing
to the three logical queues (`memory`, `wiki`, `evolution`), and
triggers autodiscovery of task modules under ``src.workers.tasks``.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 3.1
(Phase B — Celery worker app).
"""

from __future__ import annotations

from celery import Celery

from src.config import settings

__all__ = ["celery"]


celery = Celery(
    "aiopsos",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

# Serialization + timezone defaults — JSON only, UTC everywhere.
celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Execution semantics
    task_track_started=True,
    task_time_limit=600,        # 10 min hard kill
    task_soft_time_limit=540,   # 9 min soft (raises SoftTimeLimitExceeded)
    # Throughput / reliability
    worker_prefetch_multiplier=1,   # small tasks, want even distribution
    task_acks_late=True,            # retry on worker crash
    # Queue routing — default queue plus per-module routes.
    task_default_queue="celery",
    task_routes={
        "src.workers.tasks.memory_consolidation.*": {"queue": "memory"},
        "src.workers.tasks.wiki_compile.*": {"queue": "wiki"},
        "src.workers.tasks.reflection.*": {"queue": "evolution"},
        "src.workers.tasks.evaluator.*": {"queue": "evolution"},
    },
)

# Autodiscover all @celery.task-decorated functions in src.workers.tasks.*
# when a worker boots (also safe when the app is imported by the API process).
celery.autodiscover_tasks(["src.workers.tasks"])
