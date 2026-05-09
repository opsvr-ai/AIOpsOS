"""Baseline run fixture cache — task 22.3.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 22.3
(Phase K — Evaluator). Covers:

* **Cost control** — the evaluator compares a candidate run against a
  baseline run for every eval item it scores. Without caching, each
  candidate evaluation re-burns tokens replaying the *same* baseline
  against the *same* items. Worker task 22.1 resolves this by caching
  the baseline :class:`GradingRun` per ``(set_name, item_id,
  active_version_hash)`` for 24h.
* **design.md § Risks** — "eval caching relevant for 24h" + "baseline
  runs invalidated automatically when active prompt version changes"
  are the two invariants this cache is built around. The
  ``active_version_hash`` component of the key carries the current
  active sub-agent prompt version plus the baseline configuration
  (model id, temperature, seed) so a baseline becomes stale the
  moment any of those shifts.

This module ships **only** the cache helpers; the evaluator worker
(task 22.1) is the consumer that actually populates and reads from
the cache during ``evaluate(candidate_id, eval_set_name)``.

Key format::

    eval:baseline:{set_name}:{item_id}:{active_version_hash}

TTL: :data:`BASELINE_CACHE_TTL_SECONDS` = ``86_400`` (24h).

Design goals:

* Silent failure on broken Redis, consistent with
  :mod:`src.services.evolution.grading`. A cache is an optimisation,
  not a correctness surface; if Redis is unreachable, the evaluator
  recomputes the baseline run and still produces valid scores.
* :class:`GradingRun` is the canonical serialised shape — same
  dataclass the grading harness uses, so a cached baseline can be fed
  straight back into :func:`grade` without reshaping.
* Pure async. No Celery, no background loops. The evaluator owns
  concurrency.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from src.services.evolution.grading import GradingRun

logger = logging.getLogger(__name__)


BASELINE_CACHE_TTL_SECONDS: int = 86_400
"""24h TTL on baseline cache entries.

Matches design.md § Risks: "eval caching relevant for 24h". Long
enough to amortise the token cost of re-running the full eval set
across a day of candidate evaluations; short enough that drift in
upstream models / tool behaviour doesn't silently persist.
"""


BASELINE_CACHE_KEY_PREFIX: str = "eval:baseline:"
"""Redis key prefix for baseline run fixtures.

Full key format: ``eval:baseline:{set_name}:{item_id}:{active_version_hash}``.
See :func:`cache_key_for` for the assembler.
"""


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def compute_active_version_hash(
    active_version: str, baseline_config: dict[str, Any]
) -> str:
    """Derive a stable hash identifying "which baseline we'd produce now".

    The hash is ``sha256(active_version + json(baseline_config, sorted))``
    rendered as a hex digest. Any change to the active sub-agent prompt
    version, or to the baseline model / temperature / seed / tool set,
    shifts the hash and invalidates prior entries automatically — that's
    how the 24h cache still tracks changes to the underlying baseline.

    ``baseline_config`` is serialised with ``sort_keys=True`` so dict
    ordering differences across call sites don't cause spurious cache
    misses.
    """
    serialized = json.dumps(
        baseline_config or {},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    payload = (active_version or "") + serialized
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_key_for(
    set_name: str, item_id: Any, active_version_hash: str
) -> str:
    """Compose the documented ``eval:baseline:...`` key."""
    return (
        f"{BASELINE_CACHE_KEY_PREFIX}{set_name}"
        f":{item_id}:{active_version_hash}"
    )


# ---------------------------------------------------------------------------
# Redis client resolution
# ---------------------------------------------------------------------------


async def _get_redis_client(redis: Any | None) -> Any | None:
    """Return a usable Redis client or ``None``.

    Lazy-imports :func:`src.core.redis.get_redis` so tests can import
    this module without a live Redis. Any import / connection error
    degrades silently to ``None``.
    """
    if redis is not None:
        return redis
    try:
        from src.core.redis import get_redis

        return await get_redis()
    except Exception:
        logger.debug(
            "baseline_cache: no redis client available", exc_info=True
        )
        return None


# ---------------------------------------------------------------------------
# GradingRun ↔ JSON
# ---------------------------------------------------------------------------


def _serialize_run(run: GradingRun) -> str:
    """Project a :class:`GradingRun` onto the JSON shape we persist."""
    return json.dumps(
        {
            "output": run.output,
            "tools_used": list(run.tools_used or []),
            "outcome": run.outcome,
            "latency_ms": run.latency_ms,
            "tokens_in": run.tokens_in,
            "tokens_out": run.tokens_out,
        },
        ensure_ascii=False,
    )


def _deserialize_run(raw: str | bytes) -> GradingRun | None:
    """Best-effort rehydrate. Returns ``None`` on malformed payloads."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning("baseline_cache: cache entry not utf-8")
            return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("baseline_cache: cache entry not JSON")
        return None
    if not isinstance(payload, dict):
        return None
    try:
        tools_raw = payload.get("tools_used") or []
        tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else []
        return GradingRun(
            output=str(payload.get("output") or ""),
            tools_used=tools,
            outcome=str(payload.get("outcome") or "answered"),
            latency_ms=_maybe_int(payload.get("latency_ms")),
            tokens_in=_maybe_int(payload.get("tokens_in")),
            tokens_out=_maybe_int(payload.get("tokens_out")),
        )
    except Exception:
        logger.warning("baseline_cache: failed to rehydrate GradingRun")
        return None


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_cached_baseline_run(
    set_name: str,
    item_id: Any,
    active_version_hash: str,
    *,
    redis: Any | None = None,
) -> GradingRun | None:
    """Return a cached baseline :class:`GradingRun` or ``None``.

    Parameters
    ----------
    set_name :
        Eval set name (e.g. ``"fault_triage_v1"``).
    item_id :
        Eval item identifier. Stringified into the key, so any
        hashable value is accepted.
    active_version_hash :
        Output of :func:`compute_active_version_hash`. Callers should
        precompute this once per evaluator pass and reuse for every
        item.
    redis :
        Optional Redis client. If omitted, :func:`get_redis` is used
        lazily.

    Returns
    -------
    GradingRun | None
        ``None`` on cache miss, on a broken / unreachable Redis, or on
        a malformed cache entry. Never raises.
    """
    client = await _get_redis_client(redis)
    if client is None:
        return None
    key = cache_key_for(set_name, item_id, active_version_hash)
    try:
        raw = await client.get(key)
    except Exception:
        logger.debug(
            "baseline_cache: get failed for %s", key, exc_info=True
        )
        return None
    if raw is None:
        return None
    return _deserialize_run(raw)


async def set_cached_baseline_run(
    set_name: str,
    item_id: Any,
    active_version_hash: str,
    run: GradingRun,
    *,
    redis: Any | None = None,
    ttl: int = BASELINE_CACHE_TTL_SECONDS,
) -> None:
    """Persist a baseline :class:`GradingRun` for 24h.

    Errors are swallowed: caller must treat the cache write as
    best-effort, consistent with :mod:`src.services.evolution.grading`.
    """
    client = await _get_redis_client(redis)
    if client is None:
        return
    key = cache_key_for(set_name, item_id, active_version_hash)
    payload = _serialize_run(run)
    try:
        await client.set(key, payload, ex=ttl)
    except Exception:
        logger.debug(
            "baseline_cache: set failed for %s", key, exc_info=True
        )


__all__ = [
    "BASELINE_CACHE_KEY_PREFIX",
    "BASELINE_CACHE_TTL_SECONDS",
    "cache_key_for",
    "compute_active_version_hash",
    "get_cached_baseline_run",
    "set_cached_baseline_run",
]
