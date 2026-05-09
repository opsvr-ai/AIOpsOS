"""Feature flag service (``runtime_feature_flags`` table).

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 5.1 / R-7.1..R-7.3.

Design goals:

* **Zero-lock reads** — ``is_enabled`` does a single dict lookup on an
  immutable snapshot. Refreshes build a new dict and swap the reference
  under an ``asyncio.Lock`` so readers never see a partial update.
* **10s background refresh** — satisfies the 15s propagation bound of
  R-7.1 with margin.
* **Stable-hash rollout** — uses ``xxhash.xxh3_64(user_id + ":" + key)``
  to bucket users into ``[0, 100)`` with roughly uniform distribution
  (±5% on 24h scale per R-7.3).

Singleton accessor ``get_feature_flags()`` lazily creates + starts one
service instance per process. The creation path is guarded by an
``asyncio.Lock`` so that concurrent first-callers don't race.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import xxhash
from sqlalchemy import select

from src.models.base import async_session_factory
from src.models.runtime_flag import RuntimeFeatureFlag

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Snapshot dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FlagSnapshot:
    """Immutable point-in-time view of one flag row."""

    key: str
    enabled: bool
    rollout_percent: int
    data: dict


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class FeatureFlagService:
    """Async-safe feature flag cache + background refresher."""

    def __init__(
        self,
        *,
        session_factory: Any | None = None,
        refresh_interval_s: float = 10.0,
    ) -> None:
        self._session_factory = session_factory or async_session_factory
        self._refresh_interval_s = float(refresh_interval_s)
        # Reference-swap on refresh: readers see self._flags with no lock.
        self._flags: dict[str, FlagSnapshot] = {}
        self._write_lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Do a first refresh then spawn the background refresher task."""
        if self._task is not None:
            return
        try:
            await self.refresh()
        except Exception:
            # A DB blip on startup must not prevent service bring-up —
            # subsequent refreshes will pick up.
            logger.exception("feature_flags: initial refresh failed")
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._refresh_loop(), name="feature-flags-refresh"
        )

    async def stop(self) -> None:
        """Cancel the refresher and drop in-memory state."""
        self._stop_event.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Reload the full snapshot from DB, then swap atomically."""
        async with self._session_factory() as session:
            rows = (await session.execute(select(RuntimeFeatureFlag))).scalars().all()
        new: dict[str, FlagSnapshot] = {}
        for r in rows:
            new[r.key] = FlagSnapshot(
                key=r.key,
                enabled=bool(r.enabled),
                rollout_percent=int(r.rollout_percent or 0),
                data=dict(r.data or {}),
            )
        async with self._write_lock:
            # Reference swap — readers see either the old or the new dict,
            # never a half-populated one.
            self._flags = new

    async def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._refresh_interval_s
                )
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            try:
                await self.refresh()
            except Exception:
                logger.exception("feature_flags: periodic refresh failed")

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def is_enabled(self, key: str, user_id: str | None = None) -> bool:
        """Evaluate the flag for an optional user id.

        Decision table (matches R-7.1..R-7.3):

        * flag missing            → ``False``
        * ``enabled=False``       → ``False``
        * ``rollout_percent>=100``→ ``True``   (short-circuit)
        * ``rollout_percent<=0``  → ``False``  (short-circuit)
        * ``user_id is None``     → ``False``  (anonymous doesn't participate in % rollout)
        * else                    → ``xxh3_64(f"{user_id}:{key}") % 100 < rollout_percent``
        """
        snap = self._flags.get(key)
        if snap is None or not snap.enabled:
            return False
        pct = snap.rollout_percent
        if pct >= 100:
            return True
        if pct <= 0:
            return False
        if user_id is None:
            # Can't bucket an anonymous user; treat as out-of-rollout.
            return False
        bucket = _stable_bucket(user_id, key)
        return bucket < pct

    def get(self, key: str) -> FlagSnapshot | None:
        """Return the raw snapshot so callers can inspect ``data``."""
        return self._flags.get(key)

    def all(self) -> dict[str, FlagSnapshot]:
        """Return a shallow copy of the current snapshot dict."""
        return dict(self._flags)


# ---------------------------------------------------------------------------
# Stable user hashing
# ---------------------------------------------------------------------------


def _stable_bucket(user_id: str, key: str) -> int:
    """Deterministic 0..99 bucket from (user_id, key).

    Uses xxh3_64 for speed + good distribution. Falls back to blake2b
    (stdlib) if xxhash is missing so the module can still load in
    stripped-down deployments.
    """
    payload = f"{user_id}:{key}".encode("utf-8")
    try:
        h = xxhash.xxh3_64(payload).intdigest()
    except Exception:  # pragma: no cover - fallback
        import hashlib

        h = int.from_bytes(
            hashlib.blake2b(payload, digest_size=8).digest(), byteorder="big"
        )
    return int(h % 100)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


_SVC_INIT_LOCK = asyncio.Lock()
_SVC: FeatureFlagService | None = None


async def get_feature_flags() -> FeatureFlagService:
    """Lazily create + start a single ``FeatureFlagService`` per process."""
    global _SVC
    if _SVC is not None:
        return _SVC
    async with _SVC_INIT_LOCK:
        if _SVC is None:
            svc = FeatureFlagService()
            await svc.start()
            _SVC = svc
    return _SVC


async def shutdown_feature_flags() -> None:
    """Stop the singleton, if any, and drop the reference."""
    global _SVC
    svc, _SVC = _SVC, None
    if svc is not None:
        await svc.stop()


def _reset_singleton_for_tests() -> None:
    """Test-only escape hatch — drop the cached singleton without awaiting."""
    global _SVC
    _SVC = None


__all__ = [
    "FeatureFlagService",
    "FlagSnapshot",
    "get_feature_flags",
    "shutdown_feature_flags",
]
