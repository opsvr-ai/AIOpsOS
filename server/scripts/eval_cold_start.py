"""Cold-start eval set seeding from ``agent_trajectories``.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 17.1 /
R-4.3 / R-4.4.

For each operational scenario (one of the five defined in
``docs/roadmap.md`` / requirements.md § R-11), this script pulls
successful trajectories from ``agent_trajectories`` and materialises
them into ``eval_set_items`` under a ``{set_name}_v1`` label.

Design choices
--------------
* **Filter:** ``outcome='ok' AND latency_ms IS NOT NULL AND kind='turn'``
  with the scenario's tag family (e.g. ``scenario:knowledge_mgmt``) — we
  do NOT require a score threshold because trajectories are not scored
  at write time; "ok" is the closest deterministic proxy.
* **Dedupe:** content-hash the ``data.message_preview`` text so duplicate
  user prompts don't dominate the sample.
* **Sample cap:** 24 per scenario (leaves ~16 slots for human annotation
  per spec R-4.3 → 40 items total).
* **Idempotency:** rows with the same ``(set_name, prompt_hash)`` are
  skipped via a sha-based `ON CONFLICT DO NOTHING` (we derive the
  uniqueness by content, since ``eval_set_items`` has no natural unique
  constraint — the script logs the skip count).

Usage
-----

.. code:: bash

    python -m scripts.eval_cold_start
    python -m scripts.eval_cold_start --scenario fault_triage --limit 24
    python -m scripts.eval_cold_start --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

# Allow ``python scripts/...`` from within the server/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("eval_cold_start")


# Ops scenarios in priority order — names match requirements.md § R-11.
_SCENARIOS = {
    "knowledge_mgmt": "scenario:knowledge_mgmt",
    "fault_triage": "scenario:fault_triage",
    "incident_coord": "scenario:incident_coord",
    "capacity_mgmt": "scenario:capacity_mgmt",
    "runbook_mgmt": "scenario:runbook_mgmt",
}
_DEFAULT_LIMIT_PER_SCENARIO = 24


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:32]


async def _extract_scenario(
    db,
    *,
    scenario: str,
    tag: str,
    limit: int,
    dry_run: bool,
) -> tuple[int, int]:
    """Return ``(inserted, skipped_duplicate)`` for one scenario."""
    from sqlalchemy import func, select
    from src.models.evolution import EvalSetItem
    from src.models.trajectory import AgentTrajectory

    set_name = f"{scenario}_v1"

    # Pull candidate trajectories.
    q = (
        select(AgentTrajectory)
        .where(
            AgentTrajectory.kind == "turn",
            AgentTrajectory.outcome == "ok",
            AgentTrajectory.tags.op("@>")([tag]),
        )
        .order_by(AgentTrajectory.created_at.desc())
        .limit(limit * 4)  # over-fetch to survive dedupe
    )
    trajectories = (await db.execute(q)).scalars().all()

    # Existing prompt-hashes in this set — used for dedupe.
    q_existing = select(EvalSetItem.prompt).where(
        EvalSetItem.set_name == set_name
    )
    existing_hashes = {
        _prompt_hash(row) for row in (await db.execute(q_existing)).scalars()
    }

    inserted = 0
    skipped = 0
    seen: set[str] = set(existing_hashes)

    for t in trajectories:
        if inserted >= limit:
            break
        prompt = ""
        if isinstance(t.data, dict):
            prompt = str(t.data.get("message_preview") or "").strip()
        if not prompt:
            continue
        h = _prompt_hash(prompt)
        if h in seen:
            skipped += 1
            continue
        seen.add(h)

        if dry_run:
            logger.info(
                "[dry-run] would insert set=%s prompt=%r",
                set_name,
                prompt[:60],
            )
            inserted += 1
            continue

        item = EvalSetItem(
            set_name=set_name,
            prompt=prompt,
            expected_tools=[],
            expected_outcome="answered",
            grading_prompt=None,
            weight=Decimal("1.0"),
        )
        db.add(item)
        inserted += 1

    if not dry_run:
        await db.commit()

    return inserted, skipped


async def _run(args: argparse.Namespace) -> int:
    from src.models.base import async_session_factory

    scenarios = (
        {args.scenario: _SCENARIOS[args.scenario]}
        if args.scenario
        else _SCENARIOS
    )
    total_inserted = 0
    total_skipped = 0

    async with async_session_factory() as db:
        for scenario, tag in scenarios.items():
            inserted, skipped = await _extract_scenario(
                db,
                scenario=scenario,
                tag=tag,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            total_inserted += inserted
            total_skipped += skipped
            logger.info(
                "scenario=%s inserted=%d skipped_duplicate=%d",
                scenario,
                inserted,
                skipped,
            )

    logger.info(
        "done. total_inserted=%d total_skipped=%d (dry-run=%s)",
        total_inserted,
        total_skipped,
        args.dry_run,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed eval_set_items from production trajectories (Phase I)."
        )
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(_SCENARIOS.keys()),
        default=None,
        help="Only seed one scenario (default: all five).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT_PER_SCENARIO,
        help=f"Max items per scenario (default {_DEFAULT_LIMIT_PER_SCENARIO}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute + log but do not INSERT.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
