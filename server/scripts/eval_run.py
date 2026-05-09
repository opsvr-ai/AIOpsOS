"""EvaluationRunner CLI — load items, invoke grader, print scores.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 17.4 /
R-4.5.

Produces a JSON + table summary of per-item overall + per-rubric +
weighted-mean scores for a given ``set_name``. Phase K (task 22.1+)
will wire this into the Celery evaluator task; today this CLI is the
SME-facing handle for "how does the baseline look on fault_triage_v1?".

Design choices
--------------
* **Source of truth.** Items are loaded from ``eval_set_items`` via
  ``set_name``. If the DB is empty (fresh install), the CLI falls back
  to reading ``data/eval_sets/v1/{set_name}.jsonl`` to keep dev loops
  fast.
* **Grader.** For v1 the grader is pluggable via ``--grader``:
  - ``stub`` — deterministic 0.5 score per item, for pipeline smoke.
  - ``manual`` — prints each item and prompts the SME for a score.
  - ``llm`` — reserved for Phase K; returns NotImplemented right now.
* **Output.** Writes a JSON file (``--out PATH``) and prints a short
  table to stdout. Skill evaluations row is NOT persisted here — that's
  task 22.1's job.

Usage
-----

.. code:: bash

    python -m scripts.eval_run --set fault_triage_v1 --grader stub
    python -m scripts.eval_run --set fault_triage_v1 --grader manual --out run.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.evaluation.scoring import (  # noqa: E402
    AggregateScore,
    ItemScore,
    aggregate,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("eval_run")


_DEFAULT_JSONL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "eval_sets" / "v1"


# ---------------------------------------------------------------------------
# Item loading
# ---------------------------------------------------------------------------


async def _load_items_from_db(set_name: str) -> list[dict]:
    from sqlalchemy import select
    from src.models.base import async_session_factory
    from src.models.evolution import EvalSetItem

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(EvalSetItem).where(EvalSetItem.set_name == set_name)
            )
        ).scalars().all()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "item_id": str(r.id),
                    "prompt": r.prompt,
                    "expected_tools": list(r.expected_tools or []),
                    "expected_outcome": r.expected_outcome,
                    "grading_rubric": r.grading_prompt,
                    "weight": float(r.weight or Decimal("1.0")),
                    "tags": [],  # EvalSetItem has no tags column; tags come from JSONL seed
                }
            )
        return out


def _load_items_from_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.error("%s:%d invalid JSON: %s", path, lineno, exc)
                continue
            records.append(
                {
                    "item_id": f"{path.stem}:{lineno}",
                    "prompt": rec.get("prompt", ""),
                    "expected_tools": rec.get("expected_tools", []),
                    "expected_outcome": rec.get("expected_outcome"),
                    "grading_rubric": rec.get("grading_rubric"),
                    "weight": float(rec.get("weight", 1.0)),
                    "tags": list(rec.get("tags", [])),
                }
            )
    return records


async def _load_items(set_name: str) -> list[dict]:
    """Prefer DB rows; fall back to JSONL for dev loops."""
    try:
        rows = await _load_items_from_db(set_name)
        if rows:
            logger.info("loaded %d items from DB for set=%s", len(rows), set_name)
            return rows
    except Exception as exc:  # noqa: BLE001 — wide on purpose; fallback is safe
        logger.info("DB lookup failed (%s); falling back to JSONL", exc)

    jsonl_path = _DEFAULT_JSONL_DIR / f"{set_name}.jsonl"
    if jsonl_path.exists():
        rows = _load_items_from_jsonl(jsonl_path)
        logger.info("loaded %d items from %s", len(rows), jsonl_path)
        return rows

    logger.error("no items found in DB or %s", jsonl_path)
    return []


# ---------------------------------------------------------------------------
# Graders
# ---------------------------------------------------------------------------


def _grade_stub(item: dict) -> tuple[float, dict[str, float], bool]:
    """Deterministic 0.5 score — used to smoke-test the pipeline."""
    return 0.5, {"stub": 0.5}, False


def _grade_manual(item: dict) -> tuple[float, dict[str, float], bool]:
    """Interactive scoring — prints the item, reads a 0..1 score."""
    print("\n" + "=" * 60)
    print(f"[{item['item_id']}] prompt: {item['prompt']}")
    if item.get("expected_tools"):
        print(f"  expected_tools: {item['expected_tools']}")
    if item.get("expected_outcome"):
        print(f"  expected_outcome: {item['expected_outcome']}")
    if item.get("grading_rubric"):
        print(f"  rubric: {item['grading_rubric']}")

    while True:
        raw = input("Score (0..1): ").strip()
        try:
            score = float(raw)
        except ValueError:
            print("not a number; try again")
            continue
        if 0 <= score <= 1:
            break
        print("must be in [0, 1]")
    passed_raw = input("Passed? (y/N): ").strip().lower()
    passed = passed_raw in {"y", "yes", "1", "t"}
    return score, {"manual": score}, passed


def _grade_llm(item: dict) -> tuple[float, dict[str, float], bool]:  # noqa: ARG001
    raise NotImplementedError(
        "LLM grader lands in task 22.2 (Phase K). Use --grader stub or manual for now."
    )


_GRADERS = {
    "stub": _grade_stub,
    "manual": _grade_manual,
    "llm": _grade_llm,
}


# ---------------------------------------------------------------------------
# Run + output
# ---------------------------------------------------------------------------


def _run_items(items: list[dict], grader_name: str) -> list[ItemScore]:
    grader = _GRADERS[grader_name]
    scored: list[ItemScore] = []
    for it in items:
        overall, rubric, passed = grader(it)
        scored.append(
            ItemScore(
                item_id=it["item_id"],
                weight=float(it.get("weight", 1.0)),
                overall=overall,
                rubric=rubric,
                passed=passed or overall >= 0.7,
                tags=list(it.get("tags", [])),
            )
        )
    return scored


def _print_table(agg: AggregateScore, set_name: str) -> None:
    print("\n" + "=" * 60)
    print(f"Eval set: {set_name}")
    print(f"  samples: {agg.n_samples}")
    print(f"  weighted mean: {agg.weighted_mean:.4f}")
    print(f"  pass rate:     {agg.pass_rate:.4f}")
    print(f"  total weight:  {agg.total_weight:.2f}")
    if agg.per_rubric:
        print("  per-rubric:")
        for c, v in sorted(agg.per_rubric.items()):
            print(f"    {c:20s} {v:.4f}")
    if agg.per_tag:
        print("  per-tag:")
        for t, v in sorted(agg.per_tag.items()):
            print(f"    {t:30s} {v:.4f}")
    print("=" * 60)


def _write_json(out_path: Path, set_name: str, items: list[ItemScore], agg: AggregateScore) -> None:
    payload = {
        "set_name": set_name,
        "items": [
            {
                "item_id": s.item_id,
                "weight": s.weight,
                "overall": s.overall,
                "rubric": s.rubric,
                "passed": s.passed,
                "tags": s.tags,
            }
            for s in items
        ],
        "aggregate": {
            "n_samples": agg.n_samples,
            "weighted_mean": agg.weighted_mean,
            "pass_rate": agg.pass_rate,
            "total_weight": agg.total_weight,
            "per_rubric": agg.per_rubric,
            "per_tag": agg.per_tag,
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("wrote %s", out_path)


async def _run(args: argparse.Namespace) -> int:
    items = await _load_items(args.set)
    if not items:
        logger.error("no items; aborting")
        return 1

    scored = _run_items(items, args.grader)
    agg = aggregate(scored)
    _print_table(agg, args.set)

    if args.out:
        _write_json(Path(args.out), args.set, scored, agg)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluation runner CLI for offline eval sets (Phase I)."
    )
    parser.add_argument(
        "--set", required=True, help="set_name (e.g. fault_triage_v1)."
    )
    parser.add_argument(
        "--grader",
        choices=sorted(_GRADERS.keys()),
        default="stub",
        help="Which grader to use (default: stub).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write a JSON report to this path.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
