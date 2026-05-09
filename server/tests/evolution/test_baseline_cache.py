"""Unit tests for task 22.3 — baseline run fixtures cache.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 22.3
(Phase K — Evaluator).

**Validates: cost control; design.md § Risks**

Covers :mod:`src.services.evolution.baseline_cache`:

* :func:`compute_active_version_hash` is deterministic and shifts when
  either the active prompt version or the baseline config changes.
* Miss → :func:`get_cached_baseline_run` returns ``None``.
* Set → Get round-trips every field of :class:`GradingRun`.
* Cache key format is exactly
  ``eval:baseline:{set_name}:{item_id}:{active_version_hash}``.
* TTL is passed to Redis as ``ex=86400``.
* Broken Redis on ``get`` returns ``None`` without raising.
* Broken Redis on ``set`` does not raise.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.services.evolution.baseline_cache import (
    BASELINE_CACHE_KEY_PREFIX,
    BASELINE_CACHE_TTL_SECONDS,
    cache_key_for,
    compute_active_version_hash,
    get_cached_baseline_run,
    set_cached_baseline_run,
)
from src.services.evolution.grading import GradingRun


# ---------------------------------------------------------------------------
# Test helpers — in-memory Redis + broken Redis doubles
# ---------------------------------------------------------------------------


class _InMemoryRedis:
    """Tiny dict-backed Redis double with the narrow surface we use.

    Records every ``set`` call's TTL so tests can assert ``ex=86400``
    was passed through.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ops: list[tuple[str, str]] = []
        self.last_ttl: int | None = None

    async def get(self, key: str) -> str | None:
        self.ops.append(("get", key))
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.ops.append(("set", key))
        self.store[key] = value
        self.last_ttl = ex


class _BrokenGetRedis:
    """Redis double whose ``get`` always raises."""

    async def get(self, key: str) -> str | None:
        raise RuntimeError("redis down on get")

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        # shouldn't be called in get-path tests; included for completeness.
        raise RuntimeError("redis down on set")


class _BrokenSetRedis:
    """Redis double whose ``set`` always raises."""

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        raise RuntimeError("redis down on set")


def _run(coro):
    return asyncio.run(coro)


def _make_run(**overrides: Any) -> GradingRun:
    defaults: dict[str, Any] = {
        "output": "kafka lag is 0",
        "tools_used": ["grep_kb", "query_prom"],
        "outcome": "answered",
        "latency_ms": 1234,
        "tokens_in": 500,
        "tokens_out": 120,
    }
    defaults.update(overrides)
    return GradingRun(**defaults)


# ---------------------------------------------------------------------------
# compute_active_version_hash
# ---------------------------------------------------------------------------


def test_compute_active_version_hash_is_deterministic():
    """Same inputs → same hash. Basic invariant of a hash function,
    but worth pinning because the cache key derives from it."""
    h1 = compute_active_version_hash(
        "v-abc", {"model": "m1", "temperature": 0.0, "seed": 42}
    )
    h2 = compute_active_version_hash(
        "v-abc", {"model": "m1", "temperature": 0.0, "seed": 42}
    )
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest


def test_compute_active_version_hash_is_insensitive_to_dict_ordering():
    """Dict key order must not perturb the hash — callers may assemble
    ``baseline_config`` at different sites with different insertion
    orders and still expect a cache hit."""
    h1 = compute_active_version_hash(
        "v-abc", {"model": "m1", "temperature": 0.0, "seed": 42}
    )
    h2 = compute_active_version_hash(
        "v-abc", {"seed": 42, "temperature": 0.0, "model": "m1"}
    )
    assert h1 == h2


def test_compute_active_version_hash_shifts_when_active_version_changes():
    base_config = {"model": "m1", "temperature": 0.0, "seed": 42}
    h1 = compute_active_version_hash("v-abc", base_config)
    h2 = compute_active_version_hash("v-def", base_config)
    assert h1 != h2


def test_compute_active_version_hash_shifts_when_baseline_config_changes():
    """Changing any baseline knob (model / temperature / seed / tool
    set) must invalidate prior cache entries automatically — that's
    how the 24h TTL still tracks drift."""
    h_base = compute_active_version_hash(
        "v-abc", {"model": "m1", "temperature": 0.0, "seed": 42}
    )
    assert h_base != compute_active_version_hash(
        "v-abc", {"model": "m2", "temperature": 0.0, "seed": 42}
    )
    assert h_base != compute_active_version_hash(
        "v-abc", {"model": "m1", "temperature": 0.2, "seed": 42}
    )
    assert h_base != compute_active_version_hash(
        "v-abc", {"model": "m1", "temperature": 0.0, "seed": 7}
    )


# ---------------------------------------------------------------------------
# Cache key format
# ---------------------------------------------------------------------------


def test_cache_key_for_follows_documented_format():
    """``eval:baseline:{set_name}:{item_id}:{active_version_hash}``
    exactly, no extra separators, no reordering."""
    key = cache_key_for("fault_triage_v1", "item-42", "abc123")
    assert key == "eval:baseline:fault_triage_v1:item-42:abc123"
    assert key.startswith(BASELINE_CACHE_KEY_PREFIX)


def test_cache_key_for_uses_stringified_item_id():
    """Item ids are often UUIDs / ints; the key composer must not
    raise on non-string ids."""
    key = cache_key_for("s", 99, "h")
    assert key == "eval:baseline:s:99:h"


# ---------------------------------------------------------------------------
# Miss path
# ---------------------------------------------------------------------------


def test_get_cached_baseline_run_returns_none_on_miss():
    redis = _InMemoryRedis()
    result = _run(
        get_cached_baseline_run(
            "fault_triage_v1", "item-1", "hash-x", redis=redis
        )
    )
    assert result is None
    # We did try the lookup (cache layer was exercised, not short-circuited).
    assert ("get", "eval:baseline:fault_triage_v1:item-1:hash-x") in redis.ops


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_set_then_get_roundtrips_every_grading_run_field():
    """Every field on :class:`GradingRun` must survive a set→get cycle.

    The evaluator passes the cached run straight to the grader, so
    dropping any field (``tools_used``, ``latency_ms``, etc.) would
    silently corrupt downstream scoring / analytics."""
    redis = _InMemoryRedis()
    original = _make_run()

    _run(
        set_cached_baseline_run(
            "set-A", "item-1", "hash-xyz", original, redis=redis
        )
    )
    loaded = _run(
        get_cached_baseline_run(
            "set-A", "item-1", "hash-xyz", redis=redis
        )
    )

    assert loaded is not None
    assert isinstance(loaded, GradingRun)
    assert loaded.output == original.output
    assert loaded.tools_used == original.tools_used
    assert loaded.outcome == original.outcome
    assert loaded.latency_ms == original.latency_ms
    assert loaded.tokens_in == original.tokens_in
    assert loaded.tokens_out == original.tokens_out


def test_set_then_get_roundtrip_with_empty_optional_fields():
    """Defaults / Nones on GradingRun must also round-trip cleanly."""
    redis = _InMemoryRedis()
    original = GradingRun(output="ok", tools_used=[], outcome="answered")

    _run(
        set_cached_baseline_run(
            "s", "i", "h", original, redis=redis
        )
    )
    loaded = _run(get_cached_baseline_run("s", "i", "h", redis=redis))

    assert loaded is not None
    assert loaded.output == "ok"
    assert loaded.tools_used == []
    assert loaded.outcome == "answered"
    assert loaded.latency_ms is None
    assert loaded.tokens_in is None
    assert loaded.tokens_out is None


def test_set_uses_documented_key_format_in_redis():
    """What's actually persisted under the hood must be the documented
    key, not a variant — covers against subtle refactors renaming the
    prefix separator."""
    redis = _InMemoryRedis()
    run = _make_run()

    _run(
        set_cached_baseline_run(
            "fault_triage_v1", "i-9", "vhash", run, redis=redis
        )
    )

    assert "eval:baseline:fault_triage_v1:i-9:vhash" in redis.store


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


def test_set_passes_ttl_of_24h_to_redis_ex():
    """TTL must be 86_400 seconds on the ``ex=`` kwarg."""
    redis = _InMemoryRedis()
    _run(
        set_cached_baseline_run(
            "s", "i", "h", _make_run(), redis=redis
        )
    )
    assert redis.last_ttl == 86_400
    assert BASELINE_CACHE_TTL_SECONDS == 86_400


def test_set_accepts_custom_ttl_override():
    """Custom TTL (e.g. for short-lived test fixtures) still passes
    through as ``ex=``. Belt-and-suspenders for the 24h default."""
    redis = _InMemoryRedis()
    _run(
        set_cached_baseline_run(
            "s", "i", "h", _make_run(), redis=redis, ttl=60
        )
    )
    assert redis.last_ttl == 60


# ---------------------------------------------------------------------------
# Silent failure on broken Redis
# ---------------------------------------------------------------------------


def test_get_with_broken_redis_returns_none_silently():
    """Redis down on ``get`` must not raise — the evaluator worker will
    recompute the baseline run as if it were a cache miss."""
    result = _run(
        get_cached_baseline_run(
            "s", "i", "h", redis=_BrokenGetRedis()
        )
    )
    assert result is None


def test_set_with_broken_redis_does_not_raise():
    """Redis down on ``set`` must not propagate — the evaluator still
    produced a valid run and needs to continue the batch."""
    # No exception ⇒ test passes.
    _run(
        set_cached_baseline_run(
            "s", "i", "h", _make_run(), redis=_BrokenSetRedis()
        )
    )


def test_get_with_malformed_cache_entry_returns_none():
    """A non-JSON cache entry (corruption, version mismatch) must be
    treated as a miss, not crash the evaluator."""
    redis = _InMemoryRedis()
    key = cache_key_for("s", "i", "h")
    redis.store[key] = "not-json-at-all"
    result = _run(
        get_cached_baseline_run("s", "i", "h", redis=redis)
    )
    assert result is None


def test_get_with_json_but_wrong_shape_returns_none():
    """A JSON cache entry that isn't a dict (e.g. bare string / list)
    must also degrade to miss."""
    redis = _InMemoryRedis()
    key = cache_key_for("s", "i", "h")
    redis.store[key] = json.dumps(["not", "a", "dict"])
    result = _run(
        get_cached_baseline_run("s", "i", "h", redis=redis)
    )
    assert result is None


# ---------------------------------------------------------------------------
# Distinct keys don't collide
# ---------------------------------------------------------------------------


def test_different_set_names_do_not_collide_in_cache():
    redis = _InMemoryRedis()
    run_a = _make_run(output="out-a")
    run_b = _make_run(output="out-b")

    _run(set_cached_baseline_run("set-a", "i", "h", run_a, redis=redis))
    _run(set_cached_baseline_run("set-b", "i", "h", run_b, redis=redis))

    loaded_a = _run(get_cached_baseline_run("set-a", "i", "h", redis=redis))
    loaded_b = _run(get_cached_baseline_run("set-b", "i", "h", redis=redis))

    assert loaded_a is not None and loaded_a.output == "out-a"
    assert loaded_b is not None and loaded_b.output == "out-b"


def test_different_active_version_hashes_do_not_collide_in_cache():
    """Central to design.md § Risks: a changed active prompt version
    must force a baseline re-run, never reuse a stale entry."""
    redis = _InMemoryRedis()
    run_v1 = _make_run(output="v1-out")
    run_v2 = _make_run(output="v2-out")

    _run(set_cached_baseline_run("s", "i", "h-v1", run_v1, redis=redis))
    _run(set_cached_baseline_run("s", "i", "h-v2", run_v2, redis=redis))

    loaded_v1 = _run(get_cached_baseline_run("s", "i", "h-v1", redis=redis))
    loaded_v2 = _run(get_cached_baseline_run("s", "i", "h-v2", redis=redis))

    assert loaded_v1 is not None and loaded_v1.output == "v1-out"
    assert loaded_v2 is not None and loaded_v2.output == "v2-out"
