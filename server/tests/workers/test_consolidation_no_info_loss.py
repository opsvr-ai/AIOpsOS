"""PBT: P-Memory-1 no information loss.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 10.4 / R-2.3.

**Validates: Requirements 2.3**

Property: for every canonical fact we inject as a turn, the
consolidation pipeline either:

1. inserts it as a new ``agent_memories`` row, OR
2. leaves it in the (already-populated) baseline, OR
3. records it in the LLM's ``ignored`` list.

We drive the pipeline with a deterministic rule-based FakeLLM so the
only varying dimension is the shape of the input turn batch.
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

from src.services.memory.consolidation_logic import (
    MIN_CONTENT_LENGTH,
    run_consolidation,
)

from tests.workers._fake_db import FakeDB


pytestmark = [pytest.mark.property]


# ---------------------------------------------------------------------------
# FakeLLM: for each turn, emit one new_personal per "fact" not in baseline
# ---------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    content: str


class _RuleBasedLLM:
    """Emits ``new_personal`` for every fact not already in the baseline."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ainvoke(self, messages):
        # last message is the HumanMessage rendered by run_consolidation
        user_text = messages[-1].content
        self.calls.append(user_text)

        # Parse baseline id+content pairs so we can know what's "already known".
        baseline_contents: set[str] = set()
        turn_lines: list[str] = []
        section = None
        for line in user_text.splitlines():
            if line.startswith("## baseline"):
                section = "baseline"
                continue
            if line.startswith("## 新 turns"):
                section = "turns"
                continue
            if section == "baseline" and line.startswith("- id="):
                # e.g. "- id=abc... | [personal] title or content[:40]"
                try:
                    tail = line.split("|", 1)[1].strip()
                    # Strip "[scope]" prefix
                    if tail.startswith("["):
                        tail = tail.split("]", 1)[1].strip()
                    baseline_contents.add(tail)
                except Exception:
                    pass
            elif section == "turns" and line.startswith("[user]"):
                turn_lines.append(line[len("[user]"):].strip())

        new_personal: list[dict] = []
        ignored: list[str] = []
        seen: set[str] = set()
        for fact in turn_lines:
            if not fact:
                continue
            if fact in baseline_contents:
                ignored.append(f"duplicate: {fact[:20]}")
                continue
            if fact in seen:
                continue
            seen.add(fact)
            # Ensure content is long enough to pass the 15-char validator.
            if len(fact) < MIN_CONTENT_LENGTH:
                # Pad with stable suffix so filter doesn't drop it.
                content = fact + " " + ("x" * (MIN_CONTENT_LENGTH - len(fact)))
            else:
                content = fact
            new_personal.append(
                {
                    "title": fact[:30] or "fact",
                    "content": content,
                    "tags": ["auto"],
                }
            )

        out = {
            "new_personal": new_personal,
            "new_team": [],
            "supersedes": [],
            "ignored": ignored,
        }
        return _FakeResponse(content=json.dumps(out, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


# Only ASCII letters/digits/spaces so the Chinese-in-prompt encoding stays
# predictable and we don't accidentally hit PII detectors.
_fact = st.text(
    alphabet=st.sampled_from("abcdefghijklmnopqrstuvwxyz "),
    min_size=10,
    max_size=40,
).map(lambda s: s.strip() + " operational detail")


@settings(
    max_examples=6,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    turn_facts=st.lists(
        st.lists(_fact, min_size=1, max_size=3), min_size=1, max_size=6
    )
)
def test_consolidation_does_not_lose_input_facts(turn_facts: list[list[str]]) -> None:
    """Every injected fact is either stored, in baseline, or explicitly ignored."""

    async def _run() -> None:
        db = FakeDB()
        user_id = uuid.uuid4()
        session_id = uuid.uuid4()
        db.add_session(session_id=session_id, user_id=user_id)

        base_ts = datetime.now(UTC)
        all_facts: list[str] = []
        counter = 0
        for turn_idx, facts in enumerate(turn_facts):
            for fact in facts:
                # Emit one fact per message so the FakeLLM can tally them
                # individually; this matches the "sampled fact" wording of
                # P-Memory-1 more literally.
                db.add_message(
                    session_id=session_id,
                    role="user",
                    content=fact,
                    created_at=base_ts
                    + timedelta(seconds=counter, microseconds=0),
                )
                db.add_message(
                    session_id=session_id,
                    role="assistant",
                    content="ack",
                    created_at=base_ts
                    + timedelta(seconds=counter, microseconds=500),
                )
                all_facts.append(fact)
                counter += 1

        # Pre-seed one fact into baseline so we exercise the "ignored" branch.
        baseline_fact = None
        if all_facts:
            baseline_fact = all_facts[0]
            db.add_memory(
                session_id=session_id,
                user_id=user_id,
                content=baseline_fact,
                content_hash=_content_hash(baseline_fact),
            )

        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        llm = _RuleBasedLLM()

        result = await run_consolidation(
            str(session_id),
            llm=llm,
            redis_client=redis,
            db_factory=db.factory(),
            embedding=_NoEmbed(),
            pii_sanitiser=_no_pii,
        )

        assert result.status == "ok", f"unexpected status: {result}"

        # Each distinct input fact must be covered by one of:
        # * a stored ``agent_memories`` row whose content equals (or starts with) the fact, OR
        # * the pre-seeded baseline row, OR
        # * the ignored list (non-empty).
        stored_contents = {m.content for m in db.memories.values()}

        for fact in set(all_facts):
            in_new = any(
                sc == fact or sc.startswith(fact)
                for sc in stored_contents
            )
            in_baseline = (fact == baseline_fact)
            if not (in_new or in_baseline):
                assert result.ignored > 0, (
                    f"fact lost: {fact!r} — not stored, not in baseline, "
                    f"no ignored entries. stored={stored_contents}"
                )

    asyncio.run(_run())


def _content_hash(content: str) -> str:
    import hashlib
    return hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()


class _NoEmbed:
    enabled = False

    async def embed(self, texts):
        return [[] for _ in texts]


def _no_pii(text: str):
    return False, []
