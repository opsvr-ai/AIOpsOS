"""PBT: P-Sleep-3 single-session consolidation concurrency.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 10.7 / R-2.14.

**Validates: Requirements 2.14**

Property: when ten consolidation runs are launched for the same session
concurrently, the Redis SETNX lock admits exactly one; the others
return ``{"status": "skipped", "reason": "locked"}``.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from src.services.memory.consolidation_logic import MIN_CONTENT_LENGTH, run_consolidation

from tests.workers._fake_db import FakeDB


@dataclass
class _FakeResponse:
    content: str


class _SlowLLM:
    """Sleeps briefly so the other workers have time to observe the lock."""

    def __init__(self, delay_s: float = 0.1) -> None:
        self._delay_s = delay_s

    async def ainvoke(self, messages):
        await asyncio.sleep(self._delay_s)
        text = messages[-1].content
        lines = [
            line[len("[user]"):].strip()
            for line in text.splitlines()
            if line.startswith("[user]")
        ]
        items = []
        for t in lines:
            c = t if len(t) >= MIN_CONTENT_LENGTH else (t + " detail data")
            items.append({"title": t[:30] or "x", "content": c, "tags": ["t"]})
        return _FakeResponse(
            content=json.dumps(
                {
                    "new_personal": items,
                    "new_team": [],
                    "supersedes": [],
                    "ignored": [],
                }
            )
        )


class _NoEmbed:
    enabled = False

    async def embed(self, texts):
        return [[] for _ in texts]


def _no_pii(text):
    return False, []


@pytest.mark.asyncio
async def test_concurrent_consolidations_lock_admits_exactly_one() -> None:
    db = FakeDB()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    db.add_session(session_id=session_id, user_id=user_id)
    base = datetime.now(UTC)
    for i in range(3):
        db.add_message(
            session_id=session_id,
            role="user",
            content=f"user content turn number {i}",
            created_at=base + timedelta(seconds=i),
        )

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _run_one():
        return await run_consolidation(
            str(session_id),
            llm=_SlowLLM(delay_s=0.1),
            redis_client=redis,
            db_factory=db.factory(),
            embedding=_NoEmbed(),
            pii_sanitiser=_no_pii,
        )

    results = await asyncio.gather(*[_run_one() for _ in range(10)])

    ok = [r for r in results if r.status == "ok"]
    skipped = [r for r in results if r.status == "skipped"]
    assert len(ok) == 1, f"expected exactly 1 OK, got {len(ok)}"
    assert len(skipped) == 9, f"expected 9 skipped, got {len(skipped)}"
    for s in skipped:
        assert s.reason == "locked", f"unexpected reason {s.reason}"


@pytest.mark.asyncio
async def test_sequential_runs_after_first_do_not_block() -> None:
    """After the first run releases the lock, subsequent runs succeed."""
    db = FakeDB()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    db.add_session(session_id=session_id, user_id=user_id)
    base = datetime.now(UTC)
    db.add_message(
        session_id=session_id,
        role="user",
        content="initial content we want to consolidate",
        created_at=base,
    )

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    llm = _SlowLLM(delay_s=0.0)

    first = await run_consolidation(
        str(session_id),
        llm=llm,
        redis_client=redis,
        db_factory=db.factory(),
        embedding=_NoEmbed(),
        pii_sanitiser=_no_pii,
    )
    assert first.status == "ok"

    # Add another pending message so the second run is not a no-op.
    db.add_message(
        session_id=session_id,
        role="user",
        content="later content to drive second consolidation",
        created_at=base + timedelta(minutes=1),
    )

    second = await run_consolidation(
        str(session_id),
        llm=llm,
        redis_client=redis,
        db_factory=db.factory(),
        embedding=_NoEmbed(),
        pii_sanitiser=_no_pii,
    )
    assert second.status == "ok"
