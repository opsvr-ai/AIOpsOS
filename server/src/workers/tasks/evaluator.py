"""Evaluator worker task — task 22.1.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 22.1
(Phase K — Evaluator). Covers:

* **R-3.5** — WHEN ``Evaluator.evaluate(candidate, eval_set)`` completes
  the system SHALL record a ``skill_evaluations`` row with
  ``baseline_score`` / ``candidate_score`` / ``n_samples`` / ``passed``
  / ``details``.
* **R-3.6** — Promotion transitions ``shadow → ab`` / ``ab → active``
  require ``candidate_score >= baseline_score - ε`` (ε=0.02). This
  worker is what produces the pair of scores those gates read.
* **R-4.5** — the ``EvaluationRunner`` CLI outputs per-item +
  per-rubric + weighted-mean scores. The worker returns the same
  three-level breakdown in its result dict so the CLI just renders
  whatever this function computed.

The worker is split into two layers so the async core is unit-testable
without a real Celery broker:

* :func:`run_evaluation` — async coroutine that does all the work.
  Every external dependency (DB factory, LLM, Redis, baseline /
  candidate run callables) is injectable. Returns a ``dict`` describing
  the outcome.
* :func:`evaluate` — ``@celery.task`` Sync shim that calls
  ``asyncio.run(run_evaluation(...))``. Keeps Celery out of the core
  so nothing tests needs a broker.

Design choices
--------------

* **Injectable runners.** The "real" agent executor needs a full
  DeepAgents plumbing (tools, sub-agents, memory) that a worker
  process has no business rebuilding. The evaluator therefore accepts
  ``baseline_runner`` and ``candidate_runner`` callables with a narrow
  contract: ``(item) -> GradingRun``. Defaults return an empty
  :class:`GradingRun` so the worker is unit-testable without touching
  the executor, and the CLI / integration test supplies the real
  runners when needed.
* **Deterministic runs.** Callers are responsible for passing runners
  that pin ``temperature=0`` + a fixed seed. This worker doesn't try
  to enforce it on the agent side — different executor backends expose
  different seed knobs. The grader *does* enforce determinism on its
  own LLM, which is what the R-3.6 epsilon-check ultimately depends
  on.
* **Parallel baseline vs candidate per item.** Inside a single item,
  baseline and candidate runs are dispatched concurrently with
  :func:`asyncio.gather`. Between items, we go sequentially — the
  Evaluator worker is budget-bound, not latency-bound, and parallel
  items would make the regression log harder to read.
* **PII scan on outputs.** Per R-8.1 sensibilities + the task spec,
  any candidate run whose output contains PII (email / IP / token /
  credit / phone per :func:`contains_pii`) auto-fails with reason
  ``"pii_detected"``. The per-item detail keeps the category list so
  an operator can audit which pattern triggered.
* **Weighted mean via item weights.** Each eval item has a ``weight``
  column (default 1.0). The weighted mean is ``sum(score * weight) /
  sum(weight)``. Matches the pattern used by
  :mod:`src.services.evaluation.scoring` (task 17.5) so the worker
  and the CLI produce identical aggregates.
* **Baseline cache.** Baseline runs are cached under
  ``eval:baseline:{set_name}:{item_id}:{active_version}`` with 24h TTL
  (task 22.3). A cache hit skips the baseline runner AND the baseline
  grading step, re-using both the :class:`GradingRun` and its
  :class:`GradingResult`. Candidate runs are never cached — they're
  the thing under test.
* **State-machine transitions via SkillCandidateStore.** On a pass we
  move ``proposed → shadow``; on a fail we move ``proposed → rejected``.
  Idempotent per :meth:`SkillCandidateStore.update_status` so a replay
  doesn't flip a candidate twice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from src.services.evolution.grading import (
    GradingResult,
    GradingRun,
    grade,
)
from src.services.pii import contains_pii
from src.workers.app import celery

logger = logging.getLogger(__name__)

__all__ = [
    "BASELINE_CACHE_KEY_PREFIX",
    "BASELINE_CACHE_TTL_SECONDS",
    "EVAL_REGRESSION_DELTA",
    "EVAL_SCORE_EPSILON",
    "evaluate",
    "run_evaluation",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


EVAL_SCORE_EPSILON: float = 0.02
"""Pass threshold (R-3.6). ``candidate_score >= baseline_score - ε``."""


EVAL_REGRESSION_DELTA: float = 0.05
"""Per-item regression threshold. When ``baseline.score -
candidate.score > EVAL_REGRESSION_DELTA`` the item is logged as a
regression. The aggregate pass/fail still uses the weighted means and
:data:`EVAL_SCORE_EPSILON`.
"""


BASELINE_CACHE_TTL_SECONDS: int = 86_400
"""24h TTL on baseline run cache (task 22.3)."""


BASELINE_CACHE_KEY_PREFIX: str = "eval:baseline:"
"""Redis key prefix: ``eval:baseline:{set_name}:{item_id}:{active_version}``.
"""


# ---------------------------------------------------------------------------
# Per-item result dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ItemResult:
    """One item's contribution to the aggregate evaluation.

    ``pii_kinds`` is the non-empty list of PII categories found in the
    *candidate* run's output (e.g. ``["email", "ip"]``). An empty list
    means the run was clean. Baseline PII is tracked but doesn't cause
    a fail — only candidate PII does, because that's what might reach
    a user if this candidate were promoted.
    """

    item_id: str
    weight: float
    baseline_score: float
    candidate_score: float
    baseline_per_rubric: dict[str, float] = field(default_factory=dict)
    candidate_per_rubric: dict[str, float] = field(default_factory=dict)
    regression: bool = False
    pii_kinds: list[str] = field(default_factory=list)
    baseline_cache_hit: bool = False


# ---------------------------------------------------------------------------
# Public async core
# ---------------------------------------------------------------------------


async def run_evaluation(
    candidate_id: str | uuid.UUID,
    eval_set_name: str,
    *,
    db_factory: Any | None = None,
    llm: Any | None = None,
    redis: Any | None = None,
    baseline_runner: Callable[[Any], Awaitable[GradingRun]] | None = None,
    candidate_runner: Callable[[Any], Awaitable[GradingRun]] | None = None,
    candidate_store: Any | None = None,
    active_version: str = "default",
) -> dict[str, Any]:
    """Evaluate *candidate_id* against *eval_set_name*.

    Parameters
    ----------
    candidate_id, eval_set_name :
        Row identifiers. ``candidate_id`` is looked up via
        :class:`SkillCandidateStore.get`. ``eval_set_name`` selects
        items via ``SELECT * FROM eval_set_items WHERE set_name = :n``.
    db_factory :
        Async context manager yielding a session with ``.execute`` /
        ``.commit``. Defaults to :func:`async_session_factory`.
    llm :
        Grading LLM. Passed through to :func:`grade`. ``None`` triggers
        the grader's lazy default model resolution.
    redis :
        Redis client. Used for baseline run caching AND passed to
        :func:`grade` so the grading LLM cache shares the same backend.
    baseline_runner, candidate_runner :
        Callables ``(item) -> Awaitable[GradingRun]``. Default to a
        no-op runner producing an empty :class:`GradingRun` so the
        worker is unit-testable. In production these wrap a real
        DeepAgents executor.
    candidate_store :
        A :class:`SkillCandidateStore`-like object used to:

        * look up the candidate row (``.get(id)``)
        * advance its status (``.update_status(id, new_status)``)

        Defaults to a freshly-constructed
        :class:`SkillCandidateStore`.
    active_version :
        Identifier included in the grading cache key and the baseline
        cache key. When the active prompt / tool version changes,
        baselines invalidate automatically.

    Returns
    -------
    dict
        Keys:

        * ``candidate_id`` / ``eval_set_name``
        * ``baseline_score`` / ``candidate_score`` — weighted means
        * ``n_samples``
        * ``passed`` — bool (R-3.6)
        * ``reason`` — only set when ``passed=False``
        * ``per_item`` — list of item-level breakdowns (R-4.5)
        * ``pii_detected`` — list of item ids whose candidate output
          tripped the PII scan
        * ``regressions`` — list of item ids where baseline beat
          candidate by more than :data:`EVAL_REGRESSION_DELTA`
        * ``status_transition`` — which state the candidate was moved
          to (``shadow`` / ``rejected``), or ``None`` if store was
          absent or transition failed
    """
    cid = _coerce_uuid(candidate_id)
    factory = db_factory if db_factory is not None else _default_db_factory()
    store = candidate_store if candidate_store is not None else _default_candidate_store(
        db_factory=factory
    )
    b_run = baseline_runner or _noop_runner
    c_run = candidate_runner or _noop_runner

    # 1. Load candidate + items.
    candidate_row = await store.get(cid)
    if candidate_row is None:
        return {
            "candidate_id": str(cid),
            "eval_set_name": eval_set_name,
            "baseline_score": 0.0,
            "candidate_score": 0.0,
            "n_samples": 0,
            "passed": False,
            "reason": "candidate_not_found",
            "per_item": [],
            "pii_detected": [],
            "regressions": [],
            "status_transition": None,
        }

    items = await _load_eval_items(factory, eval_set_name)
    if not items:
        # Nothing to evaluate — treat as failure without a reason the
        # Evaluator can retry around. Promoter should read ``reason``
        # and escalate.
        return {
            "candidate_id": str(cid),
            "eval_set_name": eval_set_name,
            "baseline_score": 0.0,
            "candidate_score": 0.0,
            "n_samples": 0,
            "passed": False,
            "reason": "eval_set_empty",
            "per_item": [],
            "pii_detected": [],
            "regressions": [],
            "status_transition": None,
        }

    # 2. Run + grade each item.
    per_item: list[_ItemResult] = []
    for item in items:
        result = await _evaluate_item(
            item=item,
            eval_set_name=eval_set_name,
            baseline_runner=b_run,
            candidate_runner=c_run,
            llm=llm,
            redis=redis,
            active_version=active_version,
        )
        per_item.append(result)

    # 3. Aggregate.
    baseline_mean = _weighted_mean([r.baseline_score for r in per_item],
                                   [r.weight for r in per_item])
    candidate_mean = _weighted_mean([r.candidate_score for r in per_item],
                                    [r.weight for r in per_item])

    pii_items = [r.item_id for r in per_item if r.pii_kinds]
    regression_items = [r.item_id for r in per_item if r.regression]

    pii_blocked = bool(pii_items)
    score_ok = candidate_mean >= baseline_mean - EVAL_SCORE_EPSILON
    passed = score_ok and not pii_blocked

    reason: str | None = None
    if not passed:
        if pii_blocked:
            reason = "pii_detected"
        else:
            reason = "score_regression"

    # 4. Persist skill_evaluations row (R-3.5).
    details_payload = {
        "per_item": [_item_to_dict(r) for r in per_item],
        "regressions": regression_items,
        "pii_detected": pii_items,
        "active_version": active_version,
        "epsilon": EVAL_SCORE_EPSILON,
    }
    if reason is not None:
        details_payload["reason"] = reason

    try:
        await _insert_skill_evaluation(
            factory,
            candidate_id=cid,
            eval_set_name=eval_set_name,
            baseline_score=baseline_mean,
            candidate_score=candidate_mean,
            n_samples=len(per_item),
            passed=passed,
            details=details_payload,
        )
    except Exception:
        # A DB write failure here isn't fatal to the evaluation — the
        # candidate status transition is still the operator-visible
        # signal. We log + continue so the Promoter can at least see
        # the in-memory result via the Celery return value.
        logger.exception(
            "evaluator: failed to insert skill_evaluations row for candidate=%s",
            cid,
        )

    # 5. Advance candidate state machine.
    new_status = "shadow" if passed else "rejected"
    status_transition: str | None = None
    try:
        # Only move from proposed; idempotent on repeat invocations —
        # update_status is a no-op when current == new.
        await store.update_status(cid, new_status)
        status_transition = new_status
    except Exception:
        logger.warning(
            "evaluator: could not transition candidate=%s to %s",
            cid, new_status, exc_info=True,
        )

    # 6. Regression logging (R-4.5 / task spec).
    for r in per_item:
        if r.regression:
            logger.info(
                "evaluator: per-item regression candidate=%s eval_set=%s "
                "item=%s baseline=%.4f candidate=%.4f delta=%.4f",
                cid, eval_set_name, r.item_id,
                r.baseline_score, r.candidate_score,
                r.baseline_score - r.candidate_score,
            )

    return {
        "candidate_id": str(cid),
        "eval_set_name": eval_set_name,
        "baseline_score": baseline_mean,
        "candidate_score": candidate_mean,
        "n_samples": len(per_item),
        "passed": passed,
        "reason": reason,
        "per_item": [_item_to_dict(r) for r in per_item],
        "pii_detected": pii_items,
        "regressions": regression_items,
        "status_transition": status_transition,
    }


# ---------------------------------------------------------------------------
# Celery wrapper
# ---------------------------------------------------------------------------


@celery.task(name="evolution.evaluate", bind=True, acks_late=True, max_retries=3)
def evaluate(self, candidate_id: str, eval_set_name: str) -> dict[str, Any]:
    """Run :func:`run_evaluation` in a Celery worker.

    Thin wrapper — the real logic lives in :func:`run_evaluation` so
    tests can drive it with fakes. The wrapper:

    1. Bridges sync Celery into ``asyncio.run`` (Celery workers have
       no event loop of their own).
    2. Retries with exponential backoff on unexpected exceptions.
    """
    try:
        return asyncio.run(run_evaluation(candidate_id, eval_set_name))
    except Exception as exc:  # pragma: no cover - retry path
        countdown = 60 * (2 ** self.request.retries)
        logger.warning(
            "evaluate[candidate=%s] failed (attempt %d), retrying in %ds: %s",
            candidate_id,
            self.request.retries + 1,
            countdown,
            exc,
        )
        raise self.retry(exc=exc, countdown=countdown, max_retries=3)


# ---------------------------------------------------------------------------
# Internal — per-item evaluation
# ---------------------------------------------------------------------------


async def _evaluate_item(
    *,
    item: Any,
    eval_set_name: str,
    baseline_runner: Callable[[Any], Awaitable[GradingRun]],
    candidate_runner: Callable[[Any], Awaitable[GradingRun]],
    llm: Any | None,
    redis: Any | None,
    active_version: str,
) -> _ItemResult:
    """Drive baseline + candidate runners for *item*; grade + compare.

    Cache strategy: the baseline run itself + its :class:`GradingResult`
    are cached under :data:`BASELINE_CACHE_KEY_PREFIX` so repeated
    evaluations of the same item under the same active version don't
    burn tokens twice. Candidate runs never hit cache — they're the
    thing under test.

    Concurrency: baseline + candidate runs execute concurrently via
    :func:`asyncio.gather` unless the baseline is a cache hit, in
    which case we fire only the candidate run. Grading is sequential
    after the runs so a failure in one side doesn't leave the other
    half-graded.
    """
    item_id = _extract_item_id(item)
    weight = _extract_weight(item)

    # --- baseline path (cache-first) ---
    baseline_cache_key = _baseline_cache_key(
        eval_set_name=eval_set_name,
        item_id=item_id,
        active_version=active_version,
    )
    cached = await _baseline_cache_get(redis, baseline_cache_key)

    if cached is not None:
        baseline_run, baseline_result = cached
        # Still run candidate fresh.
        candidate_run = await _safe_run(candidate_runner, item)
        candidate_result = await grade(
            candidate_run,
            item,
            llm=llm,
            redis=redis,
            active_version=active_version,
        )
        cache_hit = True
    else:
        # Concurrent runs — both are independent.
        baseline_run, candidate_run = await asyncio.gather(
            _safe_run(baseline_runner, item),
            _safe_run(candidate_runner, item),
        )
        baseline_result, candidate_result = await asyncio.gather(
            grade(baseline_run, item, llm=llm, redis=redis,
                  active_version=active_version),
            grade(candidate_run, item, llm=llm, redis=redis,
                  active_version=active_version),
        )
        # Write-through to baseline cache.
        await _baseline_cache_set(
            redis, baseline_cache_key, baseline_run, baseline_result
        )
        cache_hit = False

    # --- PII scan on candidate output ---
    pii_kinds: list[str] = []
    if candidate_run.output:
        found, kinds = contains_pii(candidate_run.output)
        if found:
            pii_kinds = kinds

    # --- regression check ---
    delta = baseline_result.score - candidate_result.score
    regression = delta > EVAL_REGRESSION_DELTA

    return _ItemResult(
        item_id=item_id,
        weight=weight,
        baseline_score=baseline_result.score,
        candidate_score=candidate_result.score,
        baseline_per_rubric=dict(baseline_result.per_rubric),
        candidate_per_rubric=dict(candidate_result.per_rubric),
        regression=regression,
        pii_kinds=pii_kinds,
        baseline_cache_hit=cache_hit,
    )


async def _safe_run(
    runner: Callable[[Any], Awaitable[GradingRun]], item: Any
) -> GradingRun:
    """Invoke *runner* with *item*; return an empty run if it raises.

    We never want an executor glitch on one item to sink the whole
    evaluation. The grader handles an empty :class:`GradingRun` as a
    zero-score run with a diagnostic rationale, which is the right
    behaviour for "runner crashed".
    """
    try:
        result = await runner(item)
    except Exception:
        logger.exception("evaluator: runner raised for item=%s", _extract_item_id(item))
        return GradingRun(output="", tools_used=[], outcome="error")
    if not isinstance(result, GradingRun):
        # Defensive — a runner that returns the wrong type shouldn't
        # crash grading.
        logger.warning(
            "evaluator: runner returned non-GradingRun for item=%s (%r)",
            _extract_item_id(item), type(result).__name__,
        )
        return GradingRun(output="", tools_used=[], outcome="error")
    return result


# ---------------------------------------------------------------------------
# Baseline cache helpers
# ---------------------------------------------------------------------------


def _baseline_cache_key(
    *, eval_set_name: str, item_id: str, active_version: str
) -> str:
    return f"{BASELINE_CACHE_KEY_PREFIX}{eval_set_name}:{item_id}:{active_version}"


async def _baseline_cache_get(
    redis: Any | None, key: str
) -> tuple[GradingRun, GradingResult] | None:
    """Return a cached ``(run, result)`` pair or ``None`` on miss / error."""
    if redis is None:
        return None
    try:
        raw = await redis.get(key)
    except Exception:
        logger.debug("evaluator: baseline cache get failed", exc_info=True)
        return None
    if raw is None:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("evaluator: baseline cache entry not JSON for %s", key)
        return None
    try:
        run = GradingRun(
            output=str(payload["run"].get("output", "")),
            tools_used=list(payload["run"].get("tools_used") or []),
            outcome=str(payload["run"].get("outcome", "answered")),
        )
        result = GradingResult(
            score=float(payload["result"].get("score", 0.0)),
            per_rubric={
                str(k): float(v)
                for k, v in (payload["result"].get("per_rubric") or {}).items()
            },
            rationale=str(payload["result"].get("rationale") or ""),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("evaluator: baseline cache entry malformed for %s", key)
        return None
    return run, result


async def _baseline_cache_set(
    redis: Any | None,
    key: str,
    run: GradingRun,
    result: GradingResult,
) -> None:
    if redis is None:
        return
    payload = {
        "run": {
            "output": run.output,
            "tools_used": list(run.tools_used or []),
            "outcome": run.outcome,
        },
        "result": {
            "score": result.score,
            "per_rubric": result.per_rubric,
            "rationale": result.rationale,
        },
    }
    try:
        await redis.set(
            key, json.dumps(payload, ensure_ascii=False),
            ex=BASELINE_CACHE_TTL_SECONDS,
        )
    except Exception:
        logger.debug("evaluator: baseline cache set failed", exc_info=True)


# ---------------------------------------------------------------------------
# DB access helpers
# ---------------------------------------------------------------------------


async def _load_eval_items(factory: Any, eval_set_name: str) -> list[Any]:
    """Load every :class:`EvalSetItem` row for *eval_set_name*.

    Returns rows as SQLAlchemy Row objects (or whatever the DB fake
    yields from ``.fetchall()``). Ordering is by ``created_at ASC`` so
    successive evaluations of the same set are reproducible.
    """
    async with factory() as session:
        result = await session.execute(
            text(
                """
                SELECT id, set_name, prompt, expected_tools, expected_outcome,
                       grading_prompt, weight
                FROM eval_set_items
                WHERE set_name = :name
                ORDER BY created_at ASC, id ASC
                """
            ),
            {"name": eval_set_name},
        )
        return list(result.fetchall())


async def _insert_skill_evaluation(
    factory: Any,
    *,
    candidate_id: uuid.UUID,
    eval_set_name: str,
    baseline_score: float,
    candidate_score: float,
    n_samples: int,
    passed: bool,
    details: dict[str, Any],
) -> None:
    """Persist one row in ``skill_evaluations`` (R-3.5)."""
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO skill_evaluations (
                    candidate_id, eval_set_name, baseline_score,
                    candidate_score, n_samples, passed, details
                ) VALUES (
                    :candidate_id, :eval_set_name, :baseline_score,
                    :candidate_score, :n_samples, :passed,
                    CAST(:details AS jsonb)
                )
                """
            ),
            {
                "candidate_id": candidate_id,
                "eval_set_name": eval_set_name,
                "baseline_score": _to_decimal(baseline_score),
                "candidate_score": _to_decimal(candidate_score),
                "n_samples": n_samples,
                "passed": passed,
                "details": json.dumps(details, ensure_ascii=False, default=str),
            },
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Item-attribute helpers (duck-typed so the fake DB rows work)
# ---------------------------------------------------------------------------


def _extract_item_id(item: Any) -> str:
    """Return a string id for *item*. Never raises."""
    for accessor in (_getattr, _getitem):
        value = accessor(item, "id")
        if value is not None:
            return str(value)
    return "unknown"


def _extract_weight(item: Any) -> float:
    """Extract the weight column. Defaults to 1.0 for missing / invalid."""
    for accessor in (_getattr, _getitem):
        value = accessor(item, "weight")
        if value is None:
            continue
        try:
            f = float(value)
        except (TypeError, ValueError):
            continue
        if f <= 0.0:
            return 1.0
        return f
    return 1.0


def _getattr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _getitem(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return None


# ---------------------------------------------------------------------------
# Math + defaults
# ---------------------------------------------------------------------------


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total_w = 0.0
    total = 0.0
    for v, w in zip(values, weights, strict=False):
        if w <= 0:
            continue
        total_w += w
        total += v * w
    if total_w == 0:
        return 0.0
    return total / total_w


def _item_to_dict(result: _ItemResult) -> dict[str, Any]:
    return {
        "item_id": result.item_id,
        "weight": result.weight,
        "baseline_score": result.baseline_score,
        "candidate_score": result.candidate_score,
        "baseline_per_rubric": dict(result.baseline_per_rubric),
        "candidate_per_rubric": dict(result.candidate_per_rubric),
        "regression": result.regression,
        "pii_kinds": list(result.pii_kinds),
        "baseline_cache_hit": result.baseline_cache_hit,
    }


def _coerce_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _to_decimal(value: float) -> Decimal:
    """Convert to Decimal at 4 decimal places for ``Numeric(6,4)``."""
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except Exception:
        return Decimal("0.0000")


async def _noop_runner(item: Any) -> GradingRun:
    """Default runner — returns an empty :class:`GradingRun`.

    Used when a caller hasn't injected a real executor. The grader
    treats an empty run as "agent produced nothing"; rubrics generally
    score that at 0.0, which is a reasonable stand-in for "no runner
    configured".
    """
    return GradingRun(output="", tools_used=[], outcome="answered")


def _default_db_factory() -> Any:
    from src.models.base import async_session_factory

    return async_session_factory


def _default_candidate_store(*, db_factory: Any | None) -> Any:
    from src.services.evolution.candidate_store import SkillCandidateStore

    return SkillCandidateStore(db_factory=db_factory)
