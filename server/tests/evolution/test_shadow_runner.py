"""Unit tests for task 23.2 — :class:`ShadowRunner`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.2
(Phase L — Promoter).

**Validates: Requirements 3.7**

Covers :mod:`src.services.evolution.shadow_runner`:

* :meth:`ShadowRunner.replay` persists exactly one
  :class:`ShadowComparisonStat` row per invocation (R-3.7 persistence
  contract; one stat per shadow sample).
* :meth:`ShadowRunner.schedule` returns an already-scheduled
  :class:`asyncio.Task` and does not block the caller — the task is
  still pending immediately after ``schedule`` returns because the
  fake runner introduces a deliberate delay.
* :meth:`ShadowRunner.replay` returns ``None`` (R-3.7: the candidate
  response SHALL NOT leak to the caller) and does not mutate the
  baseline response it receives.
* When the candidate runner raises, :meth:`ShadowRunner.replay`
  swallows the exception and still writes a stat with
  ``candidate_response=None`` and a populated ``error_message``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.services.evolution.shadow_runner import (
    SHADOW_EVAL_SET_NAME,
    CandidateRunResult,
    LiveRequest,
    ShadowRunner,
)


# ---------------------------------------------------------------------------
# Fake DB — records INSERTs into ``skill_evaluations``.
# ---------------------------------------------------------------------------


@dataclass
class _InsertRecord:
    sql: str
    params: dict[str, Any]


class _Result:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []
        self.rowcount = len(self._rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


@dataclass
class _FakeDB:
    inserts: list[_InsertRecord] = field(default_factory=list)
    commits: int = 0

    def factory(self):
        db = self

        @asynccontextmanager
        async def _factory():
            yield _FakeSession(db)

        return _factory


class _FakeSession:
    def __init__(self, db: _FakeDB) -> None:
        self._db = db

    async def execute(
        self, stmt: Any, params: dict[str, Any] | None = None
    ) -> _Result:
        sql = " ".join(str(stmt).split()).lower()
        params = params or {}
        if sql.startswith("insert into skill_evaluations"):
            self._db.inserts.append(_InsertRecord(sql=sql, params=dict(params)))
        return _Result([])

    async def commit(self) -> None:
        self._db.commits += 1

    async def rollback(self) -> None:  # pragma: no cover - unused
        return None


# ---------------------------------------------------------------------------
# Fake candidate runners
# ---------------------------------------------------------------------------


def _live_request(
    *, message: str = "what's on fire?", session: str = "sess-1"
) -> LiveRequest:
    return LiveRequest(
        message=message,
        session_id=session,
        user_id="user-1",
        space_id="space-1",
    )


def _ok_runner(response: str, *, latency_ms: int = 120, tools: tuple[str, ...] = ()):
    async def _run(
        candidate_id: uuid.UUID, live_request: LiveRequest
    ) -> CandidateRunResult:
        return CandidateRunResult(
            response=response, latency_ms=latency_ms, tools_used=tools
        )

    return _run


def _slow_runner(response: str, *, sleep_for_s: float):
    async def _run(
        candidate_id: uuid.UUID, live_request: LiveRequest
    ) -> CandidateRunResult:
        await asyncio.sleep(sleep_for_s)
        return CandidateRunResult(response=response, latency_ms=1)

    return _run


def _failing_runner(exc: Exception):
    async def _run(
        candidate_id: uuid.UUID, live_request: LiveRequest
    ) -> CandidateRunResult:
        raise exc

    return _run


# ---------------------------------------------------------------------------
# Test 1 — ``replay`` writes a stat for every call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_writes_one_stat_per_call() -> None:
    """Every ``replay`` call lands exactly one row in ``skill_evaluations``.

    Row shape invariants:

    * ``eval_set_name`` is :data:`SHADOW_EVAL_SET_NAME`.
    * ``candidate_id`` matches the argument passed to ``replay``.
    * ``passed`` is ``True`` when candidate matches baseline and no
      error was recorded.
    * ``details`` JSON contains the ``shadow_stat`` blob with the
      baseline + candidate response and a ``response_match=True`` flag.
    """
    db = _FakeDB()
    runner = ShadowRunner(
        candidate_runner=_ok_runner("same-answer"),
        db_factory=db.factory(),
    )
    cid = uuid.uuid4()

    returned = await runner.replay(
        cid,
        _live_request(),
        baseline_response="same-answer",
        baseline_tools=(),
        baseline_latency_ms=100,
    )

    assert returned is None, "replay MUST NOT return the candidate response"
    assert len(db.inserts) == 1
    assert db.commits == 1
    record = db.inserts[0]
    assert record.params["candidate_id"] == cid
    assert record.params["eval_set_name"] == SHADOW_EVAL_SET_NAME
    assert record.params["passed"] is True

    details = json.loads(record.params["details"])
    stat = details["shadow_stat"]
    assert stat["baseline_response"] == "same-answer"
    assert stat["candidate_response"] == "same-answer"
    assert stat["response_match"] is True
    assert stat["error_message"] is None
    assert "timestamp" in stat
    # live_request identity context is persisted for correlation
    lr = details["live_request"]
    assert lr["session_id"] == "sess-1"
    assert lr["user_id"] == "user-1"
    assert lr["space_id"] == "space-1"


@pytest.mark.asyncio
async def test_replay_records_mismatch_and_latency_delta() -> None:
    """Different candidate text → ``response_match=False``; latency delta reflects delta."""
    db = _FakeDB()
    runner = ShadowRunner(
        candidate_runner=_ok_runner("candidate-answer", latency_ms=200),
        db_factory=db.factory(),
    )

    await runner.replay(
        uuid.uuid4(),
        _live_request(),
        baseline_response="baseline-answer",
        baseline_latency_ms=150,
    )

    assert len(db.inserts) == 1
    details = json.loads(db.inserts[0].params["details"])
    stat = details["shadow_stat"]
    assert stat["response_match"] is False
    assert stat["latency_delta_ms"] == 50
    # "passed" on the skill_evaluations row mirrors response_match
    assert db.inserts[0].params["passed"] is False


@pytest.mark.asyncio
async def test_replay_captures_tools_delta() -> None:
    """``tools_delta`` lists candidate-only (+) and baseline-only (-) tools."""
    db = _FakeDB()
    runner = ShadowRunner(
        candidate_runner=_ok_runner(
            "x", tools=("tool_a", "tool_c")
        ),
        db_factory=db.factory(),
    )

    await runner.replay(
        uuid.uuid4(),
        _live_request(),
        baseline_response="x",
        baseline_tools=("tool_a", "tool_b"),
    )

    stat = json.loads(db.inserts[0].params["details"])["shadow_stat"]
    # candidate-only tools come first (+), baseline-only second (-)
    assert stat["tools_delta"] == ["+tool_c", "-tool_b"]


# ---------------------------------------------------------------------------
# Test 2 — ``schedule`` returns immediately and doesn't block
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_returns_without_blocking() -> None:
    """``schedule`` returns an unfinished Task while the runner is still awaiting.

    The slow runner sleeps for 0.2s, so when we inspect the task
    immediately after ``schedule`` returns it must be pending (i.e.
    ``done() is False``). Then we await the runner's helper to let
    the task complete and verify the stat was persisted.
    """
    db = _FakeDB()
    runner = ShadowRunner(
        candidate_runner=_slow_runner("x", sleep_for_s=0.2),
        db_factory=db.factory(),
    )

    task = runner.schedule(
        uuid.uuid4(), _live_request(), baseline_response="x"
    )

    # Hot path observation: the scheduled task is not yet done.
    assert not task.done(), "schedule() must not block until replay finishes"
    assert runner.pending_count == 1

    await runner.wait_pending(timeout=5.0)

    assert task.done(), "task should have completed after wait_pending"
    assert runner.pending_count == 0
    assert len(db.inserts) == 1


@pytest.mark.asyncio
async def test_schedule_multiple_tasks_are_independent() -> None:
    """Scheduling N replays produces N independent tasks + N stat rows."""
    db = _FakeDB()
    runner = ShadowRunner(
        candidate_runner=_ok_runner("x"),
        db_factory=db.factory(),
    )

    tasks = [
        runner.schedule(uuid.uuid4(), _live_request(), baseline_response="x")
        for _ in range(5)
    ]
    await runner.wait_pending(timeout=5.0)
    assert all(t.done() for t in tasks)
    assert len(db.inserts) == 5


# ---------------------------------------------------------------------------
# Test 3 — user-visible baseline response is never mutated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_does_not_mutate_baseline_response() -> None:
    """``replay`` MUST NOT mutate or replace the baseline response text (R-3.7).

    We assert two things:

    1. Even when the candidate returns a different string, the
       persisted ``baseline_response`` field is byte-identical to the
       value the caller handed in.
    2. The variable the caller holds after ``replay`` returns is the
       same object (``is``), not a reinterned / lower-cased variant.
    """
    db = _FakeDB()
    runner = ShadowRunner(
        candidate_runner=_ok_runner("CANDIDATE — different text"),
        db_factory=db.factory(),
    )

    baseline = "Hello, USER! \u4f60\u597d\u3002"  # mixed ascii + CJK
    original_id = id(baseline)

    await runner.replay(uuid.uuid4(), _live_request(), baseline_response=baseline)

    assert id(baseline) == original_id, "caller's object identity preserved"
    stat = json.loads(db.inserts[0].params["details"])["shadow_stat"]
    assert stat["baseline_response"] == baseline
    # Candidate-only fields stay on the candidate side — never leak
    # into baseline.
    assert stat["candidate_response"] == "CANDIDATE — different text"
    assert stat["candidate_response"] != stat["baseline_response"]


# ---------------------------------------------------------------------------
# Test 4 — candidate errors are swallowed but persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_exception_is_swallowed_and_recorded() -> None:
    """When the candidate runner raises, ``replay`` must:

    1. Not re-raise the exception back to the caller.
    2. Persist a stat row with ``candidate_response=None``.
    3. Include a non-empty ``error_message`` identifying the failure.
    4. Mark the row ``passed=False`` (a failed candidate never
       "matches" the baseline, even if baseline_response was empty).
    """
    db = _FakeDB()
    runner = ShadowRunner(
        candidate_runner=_failing_runner(RuntimeError("boom")),
        db_factory=db.factory(),
    )

    # Must not raise.
    result = await runner.replay(
        uuid.uuid4(), _live_request(), baseline_response="hi"
    )
    assert result is None

    assert len(db.inserts) == 1
    record = db.inserts[0]
    assert record.params["passed"] is False

    stat = json.loads(record.params["details"])["shadow_stat"]
    assert stat["candidate_response"] is None
    assert stat["response_match"] is False
    assert stat["error_message"] is not None
    assert "RuntimeError" in stat["error_message"]
    assert "boom" in stat["error_message"]
    # Baseline survives untouched even when the candidate exploded.
    assert stat["baseline_response"] == "hi"


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed() -> None:
    """``asyncio.CancelledError`` is a task-lifecycle signal, not a
    candidate failure — it must propagate so the scheduler can unwind
    cleanly. No stat row is written in this case.
    """
    db = _FakeDB()

    async def _cancelling_runner(
        candidate_id: uuid.UUID, live_request: LiveRequest
    ) -> CandidateRunResult:
        raise asyncio.CancelledError()

    runner = ShadowRunner(
        candidate_runner=_cancelling_runner, db_factory=db.factory()
    )

    with pytest.raises(asyncio.CancelledError):
        await runner.replay(
            uuid.uuid4(), _live_request(), baseline_response="x"
        )
    assert db.inserts == []


# ---------------------------------------------------------------------------
# Test 5 — persistence errors don't propagate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_write_failure_is_swallowed() -> None:
    """A failing DB write MUST NOT bubble up — shadow stats are
    best-effort. The caller (gateway post-turn hook) has already
    returned a response to the user and can't do anything with a
    raised exception anyway.
    """

    class _BrokenSession:
        async def execute(self, *args: Any, **kwargs: Any) -> _Result:
            raise RuntimeError("db down")

        async def commit(self) -> None:  # pragma: no cover - not reached
            return None

    @asynccontextmanager
    async def _broken_factory():
        yield _BrokenSession()

    runner = ShadowRunner(
        candidate_runner=_ok_runner("x"),
        db_factory=_broken_factory,
    )

    # Must not raise.
    await runner.replay(uuid.uuid4(), _live_request(), baseline_response="x")
