"""PBT: P-Memory-3 HOT cache version consistency.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 10.6 / R-2.7.

**Validates: Requirements 2.7**

Property: after a successful :func:`run_consolidation`, the
``session:{sid}:hot_mem`` hash in Redis has a ``version`` field that
matches the DB-side ``sessions.hot_memory_version`` column (both are
monotone-increasing integers).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from src.services.memory.consolidation_logic import MIN_CONTENT_LENGTH, run_consolidation

from tests.workers._fake_db import FakeDB

pytestmark = [pytest.mark.property]


@dataclass
class _FakeResponse:
    content: str


class _TrivialLLM:
    async def ainvoke(self, messages):
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
                {"new_personal": items, "new_team": [], "supersedes": [], "ignored": []}
            )
        )


class _NoEmbed:
    enabled = False

    async def embed(self, texts):
        return [[] for _ in texts]


def _no_pii(text):
    return False, []


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(n_turns=st.integers(min_value=1, max_value=5))
def test_hot_cache_version_matches_db_after_consolidation(n_turns: int) -> None:
    async def _run() -> None:
        db = FakeDB()
        user_id = uuid.uuid4()
        session_id = uuid.uuid4()
        db.add_session(session_id=session_id, user_id=user_id)

        base = datetime.now(UTC)
        for i in range(n_turns):
            db.add_message(
                session_id=session_id,
                role="user",
                content=f"user says something useful about ops {i}",
                created_at=base + timedelta(seconds=i),
            )

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        result = await run_consolidation(
            str(session_id),
            llm=_TrivialLLM(),
            redis_client=redis,
            db_factory=db.factory(),
            embedding=_NoEmbed(),
            pii_sanitiser=_no_pii,
        )
        assert result.status == "ok"

        db_version = db.sessions[session_id].hot_memory_version
        raw = await redis.hgetall(f"session:{session_id}:hot_mem")
        assert raw, "HOT cache hash was not written to Redis"
        redis_version = int(raw.get("version", "0") or "0")
        assert redis_version == db_version, (
            f"hot_memory_version desync: redis={redis_version} db={db_version}"
        )
        # Version must increase from the pre-run value (0).
        assert db_version >= 1

    asyncio.run(_run())
