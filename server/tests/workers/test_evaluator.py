"""Unit tests for task 22.1 — evaluator worker.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 22.1
(Phase K — Evaluator).

**Validates: Requirements 3.5, 3.6, 4.5**

Covers :mod:`src.workers.tasks.evaluator`:

* Happy path — candidate ≥ baseline → ``passed=True`` and candidate
  row moved to ``shadow``. The async core returns a full per-item
  breakdown (R-4.5) and inserts a ``skill_evaluations`` row (R-3.5).
* Regression — candidate below baseline by more than the ε window
  (R-3.6) → ``passed=False``, candidate moved to ``rejected``.
* PII in candidate output → ``passed=False`` with ``reason="pii_detected"``
  regardless of the score comparison.
* The INSERT into ``skill_evaluations`` carries the computed scores.
* Baseline scores hit cache on the second invocation for the same
  ``(set, item, active_version)`` (task 22.3).

The tests drive :func:`run_evaluation` directly rather than going
through Celery — the Celery wrapper is a three-line shim over
``asyncio.run`` and re-testing it would only exercise the broker
plumbing.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.services.evolution.grading import GradingResult, GradingRun
from src.workers.tasks.evaluator import (
    BASELINE_CACHE_KEY_PREFIX,
    EVAL_SCORE_EPSILON,
    run_evaluation,
)


# ---------------------------------------------------------------------------
# Fake DB — models just enough of eval_set_items + skill_evaluations
# for the evaluator async core.
# ---------------------------------------------------------------------------


@dataclass
class _ItemRow:
    id: uuid.UUID
    set_name: str
    prompt: str
    expected_tools: list[str]
    expected_outcome: str
    grading_prompt: str | None
    weight: float
    created_at: int = 0


@dataclass
class _EvalRow:
    candidate_id: uuid.UUID
    eval_set_name: str
    baseline_score: Any
    candidate_score: Any
    n_samples: int
    passed: bool
    details: Any


class _Row:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Result:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    def first(self) -> _Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[_Row]:
        return list(self._rows)


@dataclass
class _FakeDB:
    """Small in-memory fixture backing the evaluator tests.

    We only emit two SQL shapes in the evaluator core:

    1. ``SELECT ... FROM eval_set_items WHERE set_name = :name``
    2. ``INSERT INTO skill_evaluations (...) VALUES (...)``

    So the dispatch matrix is tiny.
    """

    items: list[_ItemRow] = field(default_factory=list)
    evaluations: list[_EvalRow] = field(default_factory=list)
    insert_sqls: list[str] = field(default_factory=list)
    insert_params: list[dict] = field(default_factory=list)

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
            return self._insert_eval(sql, params)
        if sql.startswith("select") and "from eval_set_items" in sql:
            return self._select_items(params)
        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:  # pragma: no cover
        return None

    # -- dispatch -------------------------------------------------------

    def _select_items(self, params: dict) -> _Result:
        name = str(params["name"])
        matches = [i for i in self._db.items if i.set_name == name]
        matches.sort(key=lambda r: (r.created_at, str(r.id)))
        return _Result(
            [
                _Row(
                    id=r.id,
                    set_name=r.set_name,
                    prompt=r.prompt,
                    expected_tools=r.expected_tools,
                    expected_outcome=r.expected_outcome,
                    grading_prompt=r.grading_prompt,
                    weight=r.weight,
                )
                for r in matches
            ]
        )

    def _insert_eval(self, sql: str, params: dict) -> _Result:
        self._db.insert_sqls.append(sql)
        self._db.insert_params.append(dict(params))
        row = _EvalRow(
            candidate_id=params["candidate_id"],
            eval_set_name=params["eval_set_name"],
            baseline_score=params["baseline_score"],
            candidate_score=params["candidate_score"],
            n_samples=params["n_samples"],
            passed=params["passed"],
            details=params["details"],
        )
        self._db.evaluations.append(row)
        return _Result([])


# ---------------------------------------------------------------------------
# Fake candidate store — tracks status transitions in memory.
# ---------------------------------------------------------------------------


@dataclass
class _FakeCandidateRow:
    id: uuid.UUID
    kind: str
    name: str
    status: str
    table: str = "skill_candidates"


class _FakeCandidateStore:
    """Minimal stand-in for :class:`SkillCandidateStore`.

    Only ``get`` and ``update_status`` are called by the evaluator.
    ``update_status`` is strict about state-machine transitions so we
    can assert the evaluator calls it with the right pair.
    """

    def __init__(self) -> None:
        self._rows: dict[uuid.UUID, _FakeCandidateRow] = {}
        self.transitions: list[tuple[uuid.UUID, str, str]] = []

    def seed(
        self,
        *,
        candidate_id: uuid.UUID | None = None,
        status: str = "proposed",
        kind: str = "skill",
        name: str = "test-cand",
    ) -> _FakeCandidateRow:
        cid = candidate_id or uuid.uuid4()
        row = _FakeCandidateRow(
            id=cid,
            kind=kind,
            name=name,
            status=status,
        )
        self._rows[cid] = row
        return row

    async def get(self, candidate_id: uuid.UUID) -> _FakeCandidateRow | None:
        return self._rows.get(candidate_id)

    async def update_status(
        self, candidate_id: uuid.UUID, new_status: str
    ) -> None:
        row = self._rows.get(candidate_id)
        if row is None:
            raise LookupError(f"candidate {candidate_id} not found")
        old = row.status
        row.status = new_status
        self.transitions.append((candidate_id, old, new_status))


# ---------------------------------------------------------------------------
# Fake grader — scripted scores keyed by (item_id, side).
# ---------------------------------------------------------------------------


class _ScriptedGrader:
    """Replacement for :func:`src.services.evolution.grading.grade`.

    Accepts a dict ``{(item_id, side): score}`` mapping and returns
    deterministic :class:`GradingResult` objects. ``side`` is either
    ``"baseline"`` or ``"candidate"`` — inferred from the
    :class:`GradingRun.outcome` field the runner tags.
    """

    def __init__(self, scores: dict[tuple[str, str], float]):
        self._scores = scores
        self.calls: list[tuple[str, str]] = []

    async def __call__(
        self,
        run: GradingRun,
        item: Any,
        *,
        llm: Any = None,
        redis: Any = None,
        active_version: str = "default",
    ) -> GradingResult:
        item_id = str(getattr(item, "id", None) or "unknown")
        # The runner tags the outcome string with the side so the
        # grader can discriminate without inspecting internal test
        # state. Default to "candidate" so a misconfigured test fails
        # the comparison rather than silently passing.
        side = "baseline" if run.outcome.endswith("baseline") else "candidate"
        self.calls.append((item_id, side))
        score = self._scores.get((item_id, side), 0.0)
        return GradingResult(
            score=score,
            per_rubric={"overall": score},
            rationale=f"scripted:{side}:{score}",
        )


# ---------------------------------------------------------------------------
# In-memory Redis (only get/set used)
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls: list[str] = []
        self.set_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_calls.append(key)
        self.store[key] = value
        self.last_ttl = ex


# ---------------------------------------------------------------------------
# Runner factories — deterministic, side-tagged GradingRuns
# ---------------------------------------------------------------------------


def _make_runner(
    *,
    side: str,
    outputs: dict[str, str] | None = None,
    tools: dict[str, list[str]] | None = None,
):
    """Build a runner callable that returns a :class:`GradingRun`.

    ``side`` is encoded in the ``outcome`` field (e.g.
    ``"answered:baseline"``) so the scripted grader above can tell
    which half of the comparison this run represents without any
    cross-wiring to the test.
    """
    outputs = outputs or {}
    tools = tools or {}

    async def _runner(item: Any) -> GradingRun:
        item_id = str(getattr(item, "id", None) or "unknown")
        return GradingRun(
            output=outputs.get(item_id, f"{side} output for {item_id}"),
            tools_used=tools.get(item_id, []),
            outcome=f"answered:{side}",
        )

    return _runner


def _seed_items(db: _FakeDB, set_name: str, count: int = 2) -> list[uuid.UUID]:
    """Seed ``count`` items into *db*. Returns their ids in order."""
    out = []
    for i in range(count):
        item_id = uuid.uuid4()
        db.items.append(
            _ItemRow(
                id=item_id,
                set_name=set_name,
                prompt=f"prompt {i}",
                expected_tools=[],
                expected_outcome="answered",
                grading_prompt="rubric text",
                weight=1.0,
                created_at=i,
            )
        )
        out.append(item_id)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _patch_grade(monkeypatch: pytest.MonkeyPatch, grader: _ScriptedGrader) -> None:
    """Swap :func:`evaluator.grade` for the scripted grader."""
    import src.workers.tasks.evaluator as evaluator_mod

    monkeypatch.setattr(evaluator_mod, "grade", grader)


def test_happy_path_candidate_ge_baseline_passes_and_moves_to_shadow(
    monkeypatch: pytest.MonkeyPatch,
):
    """Candidate scores that match baseline → passed=True, status=shadow.

    Validates R-3.5 (row inserted) + R-3.6 (pass gate via ε window) +
    R-4.5 (per-item breakdown returned).
    """
    db = _FakeDB()
    set_name = "happy_path_v1"
    item_a, item_b = _seed_items(db, set_name, count=2)

    store = _FakeCandidateStore()
    cand = store.seed(kind="skill", name="kb_lookup_v2")

    grader = _ScriptedGrader(
        {
            (str(item_a), "baseline"): 0.60,
            (str(item_a), "candidate"): 0.70,
            (str(item_b), "baseline"): 0.80,
            (str(item_b), "candidate"): 0.85,
        }
    )
    _patch_grade(monkeypatch, grader)

    out = _run(
        run_evaluation(
            cand.id,
            set_name,
            db_factory=db.factory(),
            redis=_FakeRedis(),
            baseline_runner=_make_runner(side="baseline"),
            candidate_runner=_make_runner(side="candidate"),
            candidate_store=store,
            active_version="v1",
        )
    )

    assert out["passed"] is True, out
    assert out["reason"] is None
    assert out["n_samples"] == 2
    # Weighted mean (weights=1) = simple mean. Candidate wins on both
    # items, so candidate_mean > baseline_mean.
    assert out["candidate_score"] == pytest.approx((0.70 + 0.85) / 2)
    assert out["baseline_score"] == pytest.approx((0.60 + 0.80) / 2)
    # R-4.5 — per-item breakdown returned.
    assert len(out["per_item"]) == 2
    for detail in out["per_item"]:
        assert set(detail.keys()) >= {
            "item_id",
            "weight",
            "baseline_score",
            "candidate_score",
            "baseline_per_rubric",
            "candidate_per_rubric",
            "regression",
            "pii_kinds",
        }
    # R-3.5 — skill_evaluations row inserted.
    assert len(db.evaluations) == 1
    saved = db.evaluations[0]
    assert saved.candidate_id == cand.id
    assert saved.eval_set_name == set_name
    assert saved.n_samples == 2
    assert saved.passed is True
    # State machine moved to shadow.
    assert store.transitions == [(cand.id, "proposed", "shadow")]
    assert store._rows[cand.id].status == "shadow"
    assert out["status_transition"] == "shadow"


def test_regression_beyond_epsilon_fails_and_moves_to_rejected(
    monkeypatch: pytest.MonkeyPatch,
):
    """Candidate mean below baseline mean - ε → passed=False, rejected.

    R-3.6: the promotion gate requires ``candidate >= baseline - 0.02``.
    Here candidate lags by 0.10 across two items, so the evaluator
    must reject.
    """
    db = _FakeDB()
    set_name = "regression_v1"
    item_a, item_b = _seed_items(db, set_name, count=2)

    store = _FakeCandidateStore()
    cand = store.seed()

    grader = _ScriptedGrader(
        {
            (str(item_a), "baseline"): 0.90,
            (str(item_a), "candidate"): 0.80,
            (str(item_b), "baseline"): 0.90,
            (str(item_b), "candidate"): 0.80,
        }
    )
    _patch_grade(monkeypatch, grader)

    out = _run(
        run_evaluation(
            cand.id,
            set_name,
            db_factory=db.factory(),
            redis=_FakeRedis(),
            baseline_runner=_make_runner(side="baseline"),
            candidate_runner=_make_runner(side="candidate"),
            candidate_store=store,
            active_version="v1",
        )
    )

    assert out["passed"] is False
    assert out["reason"] == "score_regression"
    # Delta (0.10) > EVAL_SCORE_EPSILON (0.02) → fail.
    assert out["baseline_score"] - out["candidate_score"] > EVAL_SCORE_EPSILON
    # Both items contribute a per-item regression (delta > 0.05).
    assert len(out["regressions"]) == 2
    assert set(out["regressions"]) == {str(item_a), str(item_b)}
    # Candidate moved to rejected.
    assert store.transitions == [(cand.id, "proposed", "rejected")]
    assert store._rows[cand.id].status == "rejected"
    # R-3.5 — row still inserted, with passed=False.
    assert len(db.evaluations) == 1
    assert db.evaluations[0].passed is False


def test_pii_in_candidate_output_forces_fail_with_pii_detected_reason(
    monkeypatch: pytest.MonkeyPatch,
):
    """Even if the candidate would otherwise pass, PII in output fails it.

    PII detected in the candidate run's output marks the item as
    tripped AND forces ``passed=False`` with ``reason='pii_detected'``.
    Candidate is moved to rejected.
    """
    db = _FakeDB()
    set_name = "pii_v1"
    item_a = _seed_items(db, set_name, count=1)[0]

    store = _FakeCandidateStore()
    cand = store.seed()

    grader = _ScriptedGrader(
        {
            (str(item_a), "baseline"): 0.40,
            # Candidate scores higher — would pass on merits alone.
            (str(item_a), "candidate"): 0.95,
        }
    )
    _patch_grade(monkeypatch, grader)

    # Candidate output contains an email which the PII scanner flags.
    candidate_outputs = {str(item_a): "here's our user: alice@example.com"}

    out = _run(
        run_evaluation(
            cand.id,
            set_name,
            db_factory=db.factory(),
            redis=_FakeRedis(),
            baseline_runner=_make_runner(side="baseline"),
            candidate_runner=_make_runner(
                side="candidate", outputs=candidate_outputs
            ),
            candidate_store=store,
            active_version="v1",
        )
    )

    assert out["passed"] is False
    assert out["reason"] == "pii_detected"
    assert out["pii_detected"] == [str(item_a)]
    # Per-item detail carries the PII kinds.
    per_item = out["per_item"][0]
    assert "email" in per_item["pii_kinds"]
    # Candidate moved to rejected because PII is a hard stop.
    assert store.transitions == [(cand.id, "proposed", "rejected")]


def test_insert_skill_evaluations_sql_carries_computed_scores(
    monkeypatch: pytest.MonkeyPatch,
):
    """The INSERT INTO skill_evaluations parameters contain the exact
    baseline / candidate scores the aggregate produced.

    R-3.5 requires the row to record
    ``(baseline_score, candidate_score, n_samples, passed, details)``
    — so the params bound to the INSERT must match what the aggregator
    computed.
    """
    db = _FakeDB()
    set_name = "insert_v1"
    item_a, item_b = _seed_items(db, set_name, count=2)

    store = _FakeCandidateStore()
    cand = store.seed()

    grader = _ScriptedGrader(
        {
            (str(item_a), "baseline"): 0.50,
            (str(item_a), "candidate"): 0.75,
            (str(item_b), "baseline"): 0.60,
            (str(item_b), "candidate"): 0.65,
        }
    )
    _patch_grade(monkeypatch, grader)

    out = _run(
        run_evaluation(
            cand.id,
            set_name,
            db_factory=db.factory(),
            redis=_FakeRedis(),
            baseline_runner=_make_runner(side="baseline"),
            candidate_runner=_make_runner(side="candidate"),
            candidate_store=store,
            active_version="v1",
        )
    )

    # The INSERT fired exactly once with the computed scores.
    assert len(db.insert_params) == 1
    params = db.insert_params[0]
    assert params["candidate_id"] == cand.id
    assert params["eval_set_name"] == set_name
    assert params["n_samples"] == 2
    assert params["passed"] is True
    # Scores serialized as Decimal; compare as floats.
    assert float(params["baseline_score"]) == pytest.approx(out["baseline_score"])
    assert float(params["candidate_score"]) == pytest.approx(out["candidate_score"])
    # Details is JSON text carrying per_item + regressions + pii.
    details = json.loads(params["details"])
    assert set(details["per_item"][0].keys()) >= {
        "item_id",
        "baseline_score",
        "candidate_score",
    }


def test_baseline_scores_cached_on_miss_then_hit(
    monkeypatch: pytest.MonkeyPatch,
):
    """Re-evaluating the same set against a new candidate reuses baseline.

    Task 22.3: baseline runs are cached under
    ``eval:baseline:{set}:{item}:{active_version}`` for 24h. A second
    evaluation of the same set on the same active version hits the
    cache — the *baseline* runner is invoked zero times on pass 2, and
    the *baseline* side of the grader is not called either.
    """
    db = _FakeDB()
    set_name = "cache_v1"
    item_a = _seed_items(db, set_name, count=1)[0]

    store = _FakeCandidateStore()
    cand_1 = store.seed(name="cand-1")
    cand_2 = store.seed(name="cand-2")

    redis = _FakeRedis()

    # Count baseline-runner invocations across both passes.
    baseline_calls = {"n": 0}

    async def _counting_baseline_runner(item: Any) -> GradingRun:
        baseline_calls["n"] += 1
        return GradingRun(
            output="baseline answer",
            tools_used=[],
            outcome="answered:baseline",
        )

    grader = _ScriptedGrader(
        {
            (str(item_a), "baseline"): 0.55,
            (str(item_a), "candidate"): 0.60,
        }
    )
    _patch_grade(monkeypatch, grader)

    # Pass 1 — miss. Baseline runner fires; grader sees both sides.
    out1 = _run(
        run_evaluation(
            cand_1.id,
            set_name,
            db_factory=db.factory(),
            redis=redis,
            baseline_runner=_counting_baseline_runner,
            candidate_runner=_make_runner(side="candidate"),
            candidate_store=store,
            active_version="v1",
        )
    )
    assert baseline_calls["n"] == 1
    assert out1["per_item"][0]["baseline_cache_hit"] is False
    # One SET on the baseline cache key.
    baseline_sets = [k for k in redis.set_calls if k.startswith(BASELINE_CACHE_KEY_PREFIX)]
    assert len(baseline_sets) == 1

    # Reset grader calls so we can isolate pass 2 behaviour.
    grader.calls.clear()

    # Pass 2 — hit. Same set_name + item_a + active_version ⇒ cache
    # hit; baseline runner must NOT fire; grader must NOT be called
    # for the baseline side.
    out2 = _run(
        run_evaluation(
            cand_2.id,
            set_name,
            db_factory=db.factory(),
            redis=redis,
            baseline_runner=_counting_baseline_runner,
            candidate_runner=_make_runner(side="candidate"),
            candidate_store=store,
            active_version="v1",
        )
    )
    assert baseline_calls["n"] == 1  # still 1 — no additional call
    assert out2["per_item"][0]["baseline_cache_hit"] is True
    # Grader only called once on pass 2 — for the candidate side.
    sides = [side for _, side in grader.calls]
    assert sides == ["candidate"], sides
    # Baseline score identical across passes (served from cache).
    assert out2["baseline_score"] == pytest.approx(out1["baseline_score"])
