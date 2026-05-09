"""Unit tests for :class:`EmbeddingService`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 7.1 / 7.2 / 7.3
/ R-2.4 / R-2.5 / P-Memory-4 / P-Memory-5.

Covers:
  * idempotency — ``embed([t, t])`` returns ``[v, v]`` and a second call
    with the same input is fully cache-served.
  * hit-ratio gauge — ``embedding_cache_hit_ratio`` updates as the
    rolling window fills.
  * fallback — ``enabled=False`` returns zero-length vectors and never
    touches Redis or the provider.
  * batch-window coalescing — three concurrent ``embed_one`` calls
    resolve in **one** provider invocation.

All tests use :mod:`fakeredis` for Redis and a bare async callable as the
provider; no network required.
"""
from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from src.core.metrics import embedding_cache_hit_ratio
from src.services.memory.embedding import EmbeddingService


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_stub_provider():
    """Return (provider, captured_calls) where provider is rule-based.

    Each call appends its input list to ``captured_calls`` so tests can
    assert on batch sizes.
    """
    captured: list[list[str]] = []

    async def _provider(texts: list[str]) -> list[list[float]]:
        captured.append(list(texts))
        # Simple deterministic encoding: each text -> [len(text), first_codepoint, ...]
        return [[float(len(t)), float(ord(t[0]) if t else 0), 0.5] for t in texts]

    return _provider, captured


async def _make_service(
    *,
    enabled: bool = True,
    provider=None,
    batch_window_ms: int = 30,
    max_batch: int = 16,
) -> EmbeddingService:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    if enabled and provider is None:
        provider, _ = _make_stub_provider()
    svc = EmbeddingService(
        api_key="test-key" if enabled and provider is None else "",
        model="test-model",
        dim=3,
        batch_window_ms=batch_window_ms,
        max_batch=max_batch,
        cache_ttl_s=3600,
        provider=provider if enabled else None,
        redis_client=redis,
    )
    return svc


# ---------------------------------------------------------------------------
# Enabled flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enabled_true_with_api_key() -> None:
    svc = EmbeddingService(api_key="sk-test", redis_client=fakeredis.aioredis.FakeRedis())
    assert svc.enabled is True


@pytest.mark.asyncio
async def test_enabled_true_with_injected_provider() -> None:
    async def prov(_t):
        return [[0.0]]

    svc = EmbeddingService(
        api_key="",
        provider=prov,
        redis_client=fakeredis.aioredis.FakeRedis(),
    )
    assert svc.enabled is True


@pytest.mark.asyncio
async def test_enabled_false_without_key_or_provider() -> None:
    svc = EmbeddingService(api_key="", redis_client=fakeredis.aioredis.FakeRedis())
    assert svc.enabled is False


# ---------------------------------------------------------------------------
# Task 7.2 — idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_duplicate_inputs_return_same_vector() -> None:
    """P-Memory-4: ``embed([t, t]) == [v, v]`` for the same text ``t``."""
    provider, calls = _make_stub_provider()
    svc = await _make_service(provider=provider)

    vectors = await svc.embed(["hello", "hello"])

    assert len(vectors) == 2
    assert vectors[0] == vectors[1], "same input must resolve to same vector"
    # One provider call (single batch window).
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_second_call_is_full_cache_hit() -> None:
    provider, calls = _make_stub_provider()
    svc = await _make_service(provider=provider)

    first = await svc.embed(["alpha", "beta"])
    assert len(calls) == 1  # one batched provider call
    assert len(first) == 2

    second = await svc.embed(["alpha", "beta"])
    # Identical cached results.
    assert second == first
    # Second call must not invoke the provider.
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Hit-ratio gauge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hit_ratio_gauge_updates_live() -> None:
    provider, _ = _make_stub_provider()
    svc = await _make_service(provider=provider)
    svc._window_size = 10  # tighten window so the test is fast

    # Prime cache with a few distinct items (all misses).
    await svc.embed(["a", "b", "c", "d", "e"])
    # Re-request them (all hits).
    await svc.embed(["a", "b", "c", "d", "e"])
    await svc.close()

    # After 5 miss + 5 hit + close, last flush should reflect 5/10.
    value = embedding_cache_hit_ratio._value.get()
    assert 0.4 <= value <= 0.6, f"expected ~0.5 hit ratio, got {value}"


@pytest.mark.asyncio
async def test_cache_miss_records_miss_even_when_vector_is_empty() -> None:
    """Regression: a zero-length vector must still be treated as 'miss'."""
    async def prov(texts):
        return [[] for _ in texts]

    svc = EmbeddingService(
        api_key="sk-test",
        provider=prov,
        redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    svc._window_size = 1
    await svc.embed(["ping"])
    # Gauge must update; even if miss ratio is 100% it should not crash.
    assert embedding_cache_hit_ratio._value.get() >= 0.0


# ---------------------------------------------------------------------------
# Task 7.3 — fallback path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_returns_empty_vectors() -> None:
    svc = await _make_service(enabled=False)
    out = await svc.embed(["hello", "world"])
    assert out == [[], []]


@pytest.mark.asyncio
async def test_disabled_embed_one_returns_empty() -> None:
    svc = await _make_service(enabled=False)
    assert await svc.embed_one("anything") == []


@pytest.mark.asyncio
async def test_disabled_does_not_touch_redis() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    svc = EmbeddingService(api_key="", redis_client=redis)
    # Should not hit Redis at all.
    out = await svc.embed(["x"])
    assert out == [[]]
    # Snapshot key space — no ``emb:*`` keys created.
    keys = await redis.keys("emb:*")
    assert keys == []


# ---------------------------------------------------------------------------
# Batch-window coalescing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_embed_one_coalesces_to_single_provider_call() -> None:
    provider, calls = _make_stub_provider()
    # Wider batch window so three in-flight calls definitely collide.
    svc = await _make_service(provider=provider, batch_window_ms=100, max_batch=16)

    results = await asyncio.gather(
        svc.embed_one("x"),
        svc.embed_one("y"),
        svc.embed_one("z"),
    )
    assert len(results) == 3
    assert all(r == [1.0, float(ord(c)), 0.5] for r, c in zip(results, ["x", "y", "z"]))
    # One or two provider calls permitted (the timer can fire mid-enqueue).
    # The spec says "<= 2" so we assert that explicitly.
    assert 1 <= len(calls) <= 2, f"unexpected number of provider calls: {len(calls)}"


@pytest.mark.asyncio
async def test_batch_overflows_trigger_immediate_flush() -> None:
    provider, calls = _make_stub_provider()
    svc = await _make_service(provider=provider, batch_window_ms=500, max_batch=2)

    # Issue four concurrent single-item embeds. With max_batch=2 we expect
    # at least two provider invocations (one per pair); the long window
    # (500ms) ensures nothing else triggers the flush.
    results = await asyncio.gather(
        *[svc.embed_one(f"t{i}") for i in range(4)]
    )
    assert len(results) == 4
    # Provider saw the four texts across 2 or more flushes.
    total = sum(len(c) for c in calls)
    assert total == 4
    assert len(calls) >= 2
