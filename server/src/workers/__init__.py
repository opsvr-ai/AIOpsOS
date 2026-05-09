"""Async worker package — Celery app + task modules.

See `src.workers.app` for the Celery application singleton and
`src.workers.tasks.*` for individual task implementations. An
in-process worker for `service_type=allinone` lives in
`src.workers.embedded`.
"""
