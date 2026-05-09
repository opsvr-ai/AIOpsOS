"""PBT: P-Memory-2 supersede monotonicity.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 10.5 / R-2.3.

**Validates: Requirements 2.3**

Property: after :func:`run_consolidation` completes, every baseline row
referenced in the LLM's ``supersedes`` list is marked
``is_archived=True``; the newly-inserted replacement memories remain
``is_archived=False``.
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


class _SupersedeLLM:
    """Emits one ``new_personal`` per turn + a supersedes list targeting
    a caller-chosen subset of the baseline ids."""

    def __init__(self, *, supersede_ids: list[uuid.UUID]) -> None:
        self._target_ids = supersede_ids

    async def ainvoke(self, messages):
        text = messages[-1].content
        turn_lines = []
        section = None
        for line in text.splitlines():
            if line.startswith("## 新 turns"):
                section = "turns"
                continue
            if line.startswith("## baseline"):
                section = "baseline"
                continue
            if section == "turns" and line.startswith("[user]"):
                turn_lines.append(line[len("[user]"):].strip())

        new_personal = []
        for t in turn_lines:
            content = (t + " detail") if len(t) < MIN_CONTENT_LENGTH else t
            new_personal.append({"title": t[:30] or "t", "content": content, "tags": ["x"]})
        return _FakeResponse(
            content=json.dumps(
                {
                    "new_personal": new_personal,
                    "new_team": [],
                    "supersedes": [str(i) for i in self._target_ids],
                    "ignored": [],
                }
            )
        )


class _NoEmbed:
    enabled = False

    async def embed(self, texts):
        return [[] for _ in texts]


def _no_pii(text: str):
    return False, []


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    n_baseline=st.integers(min_value=2, max_value=6),
    n_superseded=st.integers(min_value=1, max_value=4),
    n_turns=st.integers(min_value=1, max_value=3),
)
def test_supersede_list_archives_only_referenced_rows(
    n_baseline: int, n_superseded: int, n_turns: int
) -> None:
    async def _run() -> None:
        n_sup = min(n_superseded, n_baseline)
        db = FakeDB()
        user_id = uuid.uuid4()
        session_id = uuid.uuid4()
        db.add_session(session_id=session_id, user_id=user_id)

        base_ts = datetime.now(UTC)
        baseline_rows = []
        for i in range(n_baseline):
            content = f"baseline item {i} for user {user_id.hex[:8]}"
            row = db.add_memory(
                session_id=session_id,
                user_id=user_id,
                content=content,
                content_hash=_chash(content),
            )
            baseline_rows.append(row)

        for i in range(n_turns):
            db.add_message(
                session_id=session_id,
                role="user",
                content=f"new content turn {i} some details here",
                created_at=base_ts + timedelta(seconds=i),
            )

        supersede_ids = [r.id for r in baseline_rows[:n_sup]]

        result = await run_consolidation(
            str(session_id),
            llm=_SupersedeLLM(supersede_ids=supersede_ids),
            redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
            db_factory=db.factory(),
            embedding=_NoEmbed(),
            pii_sanitiser=_no_pii,
        )

        assert result.status == "ok"
        assert result.archived == n_sup

        # Every archived row must have been in the supersede list; every
        # non-archived row must not be.
        for m in db.memories.values():
            if m.user_id != user_id:
                continue
            if m.is_archived:
                assert m.id in supersede_ids, (
                    f"unexpected archive on {m.id}"
                )
            else:
                # Either a surviving baseline row or a newly-inserted one.
                assert m.id not in supersede_ids

    asyncio.run(_run())


def _chash(content: str) -> str:
    import hashlib
    return hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()
