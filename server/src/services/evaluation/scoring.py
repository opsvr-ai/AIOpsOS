"""Scoring aggregator for offline eval-set runs.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — tasks 17.4
/ 17.5 / 22.2 / R-4.5.

This module is intentionally pure: it takes per-item scored records
(produced by the upcoming LLM-as-judge grader in task 22.2) and
aggregates them into the per-set / per-rubric / weighted-mean shapes
the CLI (task 17.4) and ``skill_evaluations`` (task 22.1) both need.

No I/O, no randomness, no side effects — that makes it trivially
unit-testable (task 17.5) and safe to call from both sync (CLI) and
async (worker) contexts.

Shape of a scored item
----------------------

```python
@dataclass(slots=True)
class ItemScore:
    item_id: str           # eval_set_items.id or synthetic JSONL index
    weight: float          # defaults to 1.0
    overall: float         # LLM-as-judge 0..1 aggregate
    rubric: dict[str, float] | None  # per-criterion breakdown (0..1 each)
    passed: bool            # True if overall ≥ pass_threshold
    tags: list[str]        # passed through from EvalSetItem metadata
```

Aggregation rules
-----------------

* ``weighted_mean``: ``Σ(w_i × overall_i) / Σ(w_i)``. Missing weights
  are treated as 1.0. Empty input → 0.0 (callers decide whether to
  treat that as a test-run failure).
* ``per_rubric_mean``: for every criterion that appears in at least
  one ``rubric`` dict, compute ``Σ(w_i × rubric_i[c]) / Σ(w_i)`` over
  items that have it. Items without ``c`` are simply excluded from
  that criterion's denominator.
* ``pass_rate``: fraction of items whose ``passed`` is True, weighted
  by ``weight``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean
from typing import Iterable


__all__ = [
    "ItemScore",
    "AggregateScore",
    "aggregate",
    "weighted_mean",
    "per_rubric_mean",
    "pass_rate",
]


@dataclass(slots=True)
class ItemScore:
    """One graded eval item."""

    item_id: str
    weight: float = 1.0
    overall: float = 0.0
    rubric: dict[str, float] | None = None
    passed: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AggregateScore:
    """Aggregated summary across a run."""

    n_samples: int
    weighted_mean: float
    pass_rate: float
    per_rubric: dict[str, float]
    total_weight: float
    # Per-tag breakdown is handy for per-scenario slicing even when the
    # caller supplied a single ``set_name``. Empty when no tags seen.
    per_tag: dict[str, float]


# ---------------------------------------------------------------------------
# Primitive aggregators
# ---------------------------------------------------------------------------


def weighted_mean(items: Iterable[ItemScore]) -> float:
    """Return the weight-adjusted mean of ``overall``.

    Empty input → ``0.0`` (consistent with the "no items" degenerate
    case). Zero or negative weights are treated as ``1.0`` so one
    accidentally zeroed weight can't silently drop a sample from the
    denominator.
    """
    total_w = 0.0
    weighted_sum = 0.0
    for it in items:
        w = it.weight if it.weight and it.weight > 0 else 1.0
        total_w += w
        weighted_sum += w * float(it.overall)
    if total_w == 0.0:
        return 0.0
    return weighted_sum / total_w


def per_rubric_mean(items: Iterable[ItemScore]) -> dict[str, float]:
    """Return a per-criterion weighted mean across the run.

    Criteria that don't appear on every item only average over the
    subset that does have them — handy when an optional rubric is
    introduced partway through a set.
    """
    acc: dict[str, tuple[float, float]] = {}
    for it in items:
        if not it.rubric:
            continue
        w = it.weight if it.weight and it.weight > 0 else 1.0
        for criterion, score in it.rubric.items():
            total_w, weighted_sum = acc.get(criterion, (0.0, 0.0))
            acc[criterion] = (total_w + w, weighted_sum + w * float(score))
    out: dict[str, float] = {}
    for c, (total_w, weighted_sum) in acc.items():
        out[c] = weighted_sum / total_w if total_w else 0.0
    return out


def pass_rate(items: Iterable[ItemScore]) -> float:
    """Weighted pass-rate in ``[0, 1]``."""
    total_w = 0.0
    passed_w = 0.0
    for it in items:
        w = it.weight if it.weight and it.weight > 0 else 1.0
        total_w += w
        if it.passed:
            passed_w += w
    if total_w == 0.0:
        return 0.0
    return passed_w / total_w


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------


def aggregate(items: list[ItemScore]) -> AggregateScore:
    """Compute the full :class:`AggregateScore` in a single pass.

    The implementation calls the individual primitives — keeping the
    result identical to calling them piecewise — but guarantees the
    same input list is iterated exactly once per aggregation shape.
    """
    n = len(items)
    total_w = sum(
        (it.weight if it.weight and it.weight > 0 else 1.0) for it in items
    )
    wmean = weighted_mean(items)
    rubric = per_rubric_mean(items)
    prate = pass_rate(items)

    # Per-tag weighted-mean — handy for per-scenario slicing in a single
    # eval set that happens to carry scenario tags (``scenario:<name>``).
    per_tag: dict[str, tuple[float, float]] = {}
    for it in items:
        w = it.weight if it.weight and it.weight > 0 else 1.0
        for tag in it.tags or []:
            total_w_tag, weighted_sum_tag = per_tag.get(tag, (0.0, 0.0))
            per_tag[tag] = (
                total_w_tag + w,
                weighted_sum_tag + w * float(it.overall),
            )
    per_tag_out = {
        tag: (w_sum / w_total if w_total else 0.0)
        for tag, (w_total, w_sum) in per_tag.items()
    }

    return AggregateScore(
        n_samples=n,
        weighted_mean=wmean,
        pass_rate=prate,
        per_rubric=rubric,
        total_weight=total_w,
        per_tag=per_tag_out,
    )


# Explicitly unused import suppressed — ``fmean`` was briefly used
# during prototyping; keep the import so grep-friendly edits remain
# cheap.
_ = fmean
