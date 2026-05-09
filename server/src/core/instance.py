"""Process-wide instance identity + empty consumer-group TTL cleanup.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 20.2 / R-3.17.

Purpose
=======

Every FastAPI execution-plane process needs a stable, unique id so the
per-instance Kafka consumer groups used by
:class:`~src.services.evolution.prompt_reloader.PromptReloader` don't
collide between replicas. The id lives in memory only — it is
regenerated on each process start so we never leak a dead replica's
identity forward.

We use UUIDv7 (``uuid_utils.uuid7``) so the id is both globally unique
*and* monotonically orderable by creation time. That makes observability
dashboards (``group_id=prompt-reloader-<uuid7>``) naturally sortable by
instance start time.

TTL cleanup
===========

Because every instance creates its own consumer group, over time the
Kafka broker accumulates groups from replicas that have exited. This
module runs a background task every hour that:

1. Lists consumer groups matching the ``prompt-reloader-*`` prefix.
2. For each group, checks ``describe_group`` — if it has **no members**
   **and** **no committed offsets**, it is considered abandoned.
3. Issues a synchronous ``delete_consumer_groups`` via kafka-python's
   admin client (aiokafka's admin does not expose group deletion yet).

Deletion is wrapped in best-effort error handling: a broker blip or
``GroupNotEmptyError`` are logged and skipped rather than raised. Our
own group is protected by skipping any id that matches
``instance_id()``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import uuid_utils

from src.config import settings

logger = logging.getLogger(__name__)


# Prefix used by PromptReloader — duplicated here (rather than imported)
# to avoid a circular import between this module and
# ``src.services.evolution.prompt_reloader``.
_GROUP_PREFIX = "prompt-reloader-"

# How often the cleanup loop wakes up, in seconds. Hourly per spec.
_DEFAULT_CLEANUP_INTERVAL_S = 3600.0


# ---------------------------------------------------------------------------
# Instance id (process-local, regenerated on each startup)
# ---------------------------------------------------------------------------


_INSTANCE_ID: str | None = None
_ID_LOCK = asyncio.Lock()


def _fresh_instance_id() -> str:
    """Generate a new UUIDv7 and return it as a lowercase hex string.

    ``uuid_utils.uuid7`` returns a ``uuid_utils.UUID`` which stringifies
    in the usual ``xxxxxxxx-xxxx-7xxx-xxxx-xxxxxxxxxxxx`` form. We keep
    the hyphens so the string is self-identifying as a UUID in logs.
    """
    return str(uuid_utils.uuid7())


def instance_id() -> str:
    """Return this process's instance id, generating one on first call.

    Thread-safe enough for our purposes: the first caller does a
    check-then-set under Python's GIL. For concurrent async callers
    racing during startup we prefer a tiny duplicate risk (two
    generations, one winning the set) over the cost of an asyncio.Lock
    on a read path that can be called by any request handler.
    """
    global _INSTANCE_ID
    if _INSTANCE_ID is None:
        _INSTANCE_ID = _fresh_instance_id()
        logger.info("instance_id: generated %s", _INSTANCE_ID)
    return _INSTANCE_ID


def reset_instance_id_for_tests() -> None:
    """Test-only: clear the cached id so the next ``instance_id()`` regenerates."""
    global _INSTANCE_ID
    _INSTANCE_ID = None


# ---------------------------------------------------------------------------
# Empty consumer-group TTL cleanup
# ---------------------------------------------------------------------------


class ConsumerGroupTTLReaper:
    """Background task that trims abandoned per-instance consumer groups.

    Construction is cheap; the reaper does not touch Kafka until
    :meth:`start` is called. The loop wakes up every
    ``interval_s`` seconds (default 1h) and scans Kafka for
    ``prompt-reloader-*`` groups that have:

    * no active members (empty members list), **and**
    * no committed offsets on any topic-partition.

    Such groups are safe to delete: nothing is consuming from them and
    no consumer will expect to resume from a remembered offset.

    We use the synchronous ``kafka-python`` admin client for the actual
    delete because aiokafka does not expose group deletion. All calls
    are wrapped in ``asyncio.to_thread`` so the event loop stays
    responsive.
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str | None = None,
        interval_s: float = _DEFAULT_CLEANUP_INTERVAL_S,
        admin_service: Any | None = None,
        sync_admin_factory: Any | None = None,
        group_prefix: str = _GROUP_PREFIX,
    ) -> None:
        self._bootstrap = bootstrap_servers or settings.kafka_bootstrap_servers
        self._interval_s = float(interval_s)
        self._group_prefix = group_prefix
        self._admin_service = admin_service  # async KafkaAdminService
        self._sync_admin_factory = sync_admin_factory  # kafka.admin.KafkaAdminClient

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._started = False
        # Counter for tests / observability
        self.cleanup_runs: int = 0
        self.cleanup_deleted: int = 0
        self.cleanup_errors: int = 0

    async def start(self) -> None:
        """Kick off the hourly cleanup loop.

        Safe to call multiple times; second call is a no-op. The first
        cycle waits the full interval before acting — we don't want a
        startup-time scan to race with freshly-created consumer groups.
        """
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(), name="consumer-group-ttl-reaper"
        )
        logger.info(
            "consumer-group-ttl-reaper: started (interval=%.0fs, prefix=%r)",
            self._interval_s,
            self._group_prefix,
        )

    async def stop(self) -> None:
        """Signal the loop to exit and wait up to 5s for clean shutdown."""
        if not self._started:
            return
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except Exception:  # pragma: no cover - cancel path
                    pass
            except Exception:
                logger.exception(
                    "consumer-group-ttl-reaper: stop wait raised"
                )
        self._started = False

    async def _run(self) -> None:
        """Main loop — sleep, then run one cleanup pass; repeat until stopped."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval_s
                )
                break  # stop_event set
            except asyncio.TimeoutError:
                pass
            try:
                await self.run_once()
            except Exception:
                # Never let a single cycle's failure kill the whole loop.
                self.cleanup_errors += 1
                logger.exception("consumer-group-ttl-reaper: pass failed")

    async def run_once(self) -> int:
        """Execute one cleanup pass and return the number of groups deleted.

        Exposed for tests and ``/api/control`` debug endpoints; the
        long-running loop simply invokes this method on a timer.
        """
        self.cleanup_runs += 1
        admin = self._admin_service
        owns_admin = admin is None
        if admin is None:
            # Lazy import to avoid circular deps at module load time.
            from src.services.kafka.admin import KafkaAdminService

            admin = KafkaAdminService(bootstrap_servers=self._bootstrap)
        if owns_admin:
            await admin.start()
        try:
            try:
                groups = await admin.list_consumer_groups()
            except Exception:
                logger.exception(
                    "consumer-group-ttl-reaper: list_consumer_groups failed"
                )
                self.cleanup_errors += 1
                return 0

            my_id = instance_id()
            my_group = f"{self._group_prefix}{my_id}"
            abandoned: list[str] = []
            for grp in groups:
                gid = grp.group_id
                if not gid.startswith(self._group_prefix):
                    continue
                if gid == my_group:
                    # Never delete our own group.
                    continue
                try:
                    detail = await admin.describe_group(gid)
                except Exception:
                    logger.debug(
                        "consumer-group-ttl-reaper: describe_group(%s) failed; skipping",
                        gid,
                    )
                    continue
                if detail.members:
                    # Still has live members — not abandoned.
                    continue
                # Any lane with a non-negative committed offset means
                # someone is expected to resume. Only truly empty groups
                # are safe to reap.
                has_commit = any(
                    lag.current_offset >= 0 for lag in detail.lags
                )
                if has_commit:
                    continue
                abandoned.append(gid)

            if not abandoned:
                return 0

            deleted = await self._delete_groups(abandoned)
            self.cleanup_deleted += deleted
            return deleted
        finally:
            if owns_admin:
                try:
                    await admin.close()
                except Exception:  # pragma: no cover
                    pass

    async def _delete_groups(self, group_ids: list[str]) -> int:
        """Delete the given consumer groups via kafka-python's admin.

        Runs in a worker thread so the blocking admin client does not
        stall the event loop. Returns the number of groups the broker
        reported as successfully deleted.
        """
        if not group_ids:
            return 0

        factory = self._sync_admin_factory or _default_sync_admin_factory

        def _do_delete() -> int:
            deleted = 0
            client = factory(self._bootstrap)
            try:
                # kafka-python returns a list of
                # (group_id, error_code) tuples; error_code 0 means OK.
                try:
                    response = client.delete_consumer_groups(group_ids)
                except Exception as exc:
                    logger.warning(
                        "consumer-group-ttl-reaper: delete raised %s",
                        exc,
                    )
                    return 0
                for entry in response or []:
                    try:
                        gid, err = entry[0], entry[1]
                    except (IndexError, TypeError):
                        continue
                    err_code = getattr(err, "errno", None) or (
                        err if isinstance(err, int) else None
                    )
                    if err_code in (0, None):
                        deleted += 1
                        logger.info(
                            "consumer-group-ttl-reaper: deleted %s", gid
                        )
                    else:
                        logger.info(
                            "consumer-group-ttl-reaper: skip %s (err=%r)",
                            gid,
                            err,
                        )
            finally:
                try:
                    client.close()
                except Exception:  # pragma: no cover
                    pass
            return deleted

        return await asyncio.to_thread(_do_delete)


def _default_sync_admin_factory(bootstrap_servers: str) -> Any:
    """Return a fresh ``kafka.admin.KafkaAdminClient`` bound to ``bootstrap_servers``.

    Isolated into a module-level function so tests can monkeypatch a
    mock factory without touching the reaper class.
    """
    # Lazy import — kafka-python is a peer dep, not a hard one.
    from kafka.admin import KafkaAdminClient

    return KafkaAdminClient(
        bootstrap_servers=bootstrap_servers,
        client_id="aiopsos-reaper",
    )


# ---------------------------------------------------------------------------
# Singleton accessor (mirrors other services in this repo)
# ---------------------------------------------------------------------------


_REAPER: ConsumerGroupTTLReaper | None = None


def get_consumer_group_reaper(
    *,
    bootstrap_servers: str | None = None,
    interval_s: float = _DEFAULT_CLEANUP_INTERVAL_S,
) -> ConsumerGroupTTLReaper:
    """Return (and lazily construct) the process-wide reaper singleton."""
    global _REAPER
    if _REAPER is None:
        _REAPER = ConsumerGroupTTLReaper(
            bootstrap_servers=bootstrap_servers,
            interval_s=interval_s,
        )
    return _REAPER


def _reset_reaper_for_tests() -> None:
    """Test-only: drop the singleton reference."""
    global _REAPER
    _REAPER = None


__all__ = [
    "ConsumerGroupTTLReaper",
    "get_consumer_group_reaper",
    "instance_id",
    "reset_instance_id_for_tests",
]
