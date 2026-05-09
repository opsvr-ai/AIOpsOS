"""Unit tests for ``DatabaseMemoryProvider.sync_turn``.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 25.2 /
R-2.1 / R-9.3.

Task 25.2 removed the legacy in-request LLM extraction path entirely.
``sync_turn`` now unconditionally emits a lightweight hint to the
ConsolidationWorker via Redis (``session:{sid}:pending`` hash +
``sleep:queue`` ZSET) and returns without running any LLM calls. This
suite pins that contract at the function level so a future refactor
can't reintroduce the legacy buffer/flush path.
"""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from src.services.memory_provider import DatabaseMemoryProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client


def _patch_redis(monkeypatch, redis) -> None:
    import src.core.redis as r

    async def _fake_get_redis():
        return redis

    monkeypatch.setattr(r, "get_redis", _fake_get_redis)


# ---------------------------------------------------------------------------
# sync_turn behaviour — single canonical path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_turn_emits_hint_to_redis(fake_redis, monkeypatch) -> None:
    """A single ``sync_turn`` call writes ``pending`` hash + ``sleep:queue`` entry."""
    _patch_redis(monkeypatch, fake_redis)

    provider = DatabaseMemoryProvider()
    sid = "11111111-1111-1111-1111-111111111111"
    uid = "22222222-2222-2222-2222-222222222222"
    provider.initialize(sid, user_id=uid)

    await provider.sync_turn("who broke prod?", "i did, rolling back")

    # pending hash incremented + TTL set.
    pending = await fake_redis.hgetall(f"session:{sid}:pending")
    assert pending.get("turns") == "1"
    ttl = await fake_redis.ttl(f"session:{sid}:pending")
    assert 7000 <= ttl <= 7200

    # Session dispatched to the sleep queue.
    score = await fake_redis.zscore("sleep:queue", sid)
    assert score is not None
    # Score is a future timestamp — sanity check that it's within a few
    # seconds of "now + 5 min".
    import time as _t

    now = _t.time()
    assert now + 250 <= score <= now + 350


@pytest.mark.asyncio
async def test_sync_turn_multiple_calls_accumulate_pending(
    fake_redis, monkeypatch
) -> None:
    """Repeated ``sync_turn`` calls increment the ``turns`` counter."""
    _patch_redis(monkeypatch, fake_redis)

    provider = DatabaseMemoryProvider()
    sid = "33333333-3333-3333-3333-333333333333"
    provider.initialize(sid, user_id="44444444-4444-4444-4444-444444444444")

    for _ in range(3):
        await provider.sync_turn("u", "a")

    pending = await fake_redis.hgetall(f"session:{sid}:pending")
    assert pending.get("turns") == "3"


@pytest.mark.asyncio
async def test_sync_turn_redis_failure_is_swallowed(monkeypatch) -> None:
    """If Redis is unavailable, ``sync_turn`` returns silently.

    Request-path stability trumps emit guarantees — a broken Redis must
    not surface as a 500 on ``/chat``. The ConsolidationWorker will pick
    the session back up on the next successful turn.
    """
    import src.core.redis as r

    class _BrokenRedis:
        def pipeline(self):  # noqa: D401 - stub
            raise RuntimeError("redis unavailable")

    async def _fake_get_redis():
        return _BrokenRedis()

    monkeypatch.setattr(r, "get_redis", _fake_get_redis)

    provider = DatabaseMemoryProvider()
    provider.initialize(
        "55555555-5555-5555-5555-555555555555",
        user_id="66666666-6666-6666-6666-666666666666",
    )

    # Must not raise.
    await provider.sync_turn("u", "a")


# ---------------------------------------------------------------------------
# Legacy path is gone
# ---------------------------------------------------------------------------


def test_database_provider_has_no_turn_buffer_attribute() -> None:
    """``_turn_buffer`` was removed with the legacy path (task 25.2).

    Keeping this assertion locks the surface so a future change that
    accidentally reintroduces the in-request buffer fails loudly
    instead of re-growing an unbounded list on every chat turn.
    """
    provider = DatabaseMemoryProvider()
    assert not hasattr(provider, "_turn_buffer")


def test_flush_is_a_no_op_shim() -> None:
    """``flush`` is retained for BC but does nothing."""
    import inspect

    src = inspect.getsource(DatabaseMemoryProvider.flush)
    # The shim just returns ``None``; no LLM import, no buffer access.
    assert "get_default_model" not in src
    assert "_turn_buffer" not in src
