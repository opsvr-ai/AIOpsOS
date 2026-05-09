"""SME annotation CLI for offline eval sets.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 17.2 /
R-4.2 / R-4.3.

Human-in-the-loop CLI that lets an SME add annotated items to
``eval_set_items`` — either one at a time from interactive prompts or
in bulk from a JSONL file. Output matches the schema expected by the
cold-start seed (``set_name`` / ``prompt`` / ``expected_tools`` /
``expected_outcome`` / ``grading_prompt`` / ``weight`` / ``tags``).

Usage
-----

Interactive (one-shot):
    python -m scripts.eval_annotate --set fault_triage_v1

Bulk from JSONL (preferred for the 40% human-authored seed):
    python -m scripts.eval_annotate --set fault_triage_v1 \\
        --from-jsonl data/eval_sets/v1/fault_triage_v1.jsonl

Expected JSONL line schema::

    {
        "prompt": "...",
        "expected_tools": ["grep_kb", "search_logs"],
        "expected_outcome": "answered" | "delegated" | "refused",
        "grading_rubric": "...optional LLM-judge rubric...",
        "weight": 1.0,
        "tags": ["scenario:fault_triage", "positive"]
    }

Idempotent: re-running on the same JSONL skips items whose
``sha256(prompt)`` already exists for that ``set_name``.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("eval_annotate")


_VALID_OUTCOMES = {"answered", "delegated", "refused"}


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:32]


def _validate_record(rec: dict) -> str | None:
    """Return an error string, or None if record is valid."""
    if not isinstance(rec.get("prompt"), str) or not rec["prompt"].strip():
        return "prompt must be a non-empty string"
    outcome = rec.get("expected_outcome")
    if outcome is not None and outcome not in _VALID_OUTCOMES:
        return f"expected_outcome must be one of {sorted(_VALID_OUTCOMES)}"
    tools = rec.get("expected_tools", [])
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        return "expected_tools must be list[str]"
    tags = rec.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        return "tags must be list[str]"
    try:
        weight = float(rec.get("weight", 1.0))
    except (TypeError, ValueError):
        return "weight must be numeric"
    if weight <= 0 or weight > 10:
        return "weight must be in (0, 10]"
    return None


async def _insert_items(
    *,
    set_name: str,
    records: list[dict],
    dry_run: bool,
) -> tuple[int, int, int]:
    """Return ``(inserted, skipped_duplicate, rejected_invalid)``."""
    from sqlalchemy import select
    from src.models.base import async_session_factory
    from src.models.evolution import EvalSetItem

    inserted = 0
    skipped = 0
    invalid = 0

    async with async_session_factory() as db:
        q_existing = select(EvalSetItem.prompt).where(
            EvalSetItem.set_name == set_name
        )
        existing = {
            _prompt_hash(row)
            for row in (await db.execute(q_existing)).scalars()
        }

        for rec in records:
            err = _validate_record(rec)
            if err:
                logger.warning("skip invalid record: %s — %r", err, rec)
                invalid += 1
                continue

            h = _prompt_hash(rec["prompt"])
            if h in existing:
                skipped += 1
                continue
            existing.add(h)

            if dry_run:
                logger.info(
                    "[dry-run] would insert set=%s prompt=%r",
                    set_name,
                    rec["prompt"][:60],
                )
                inserted += 1
                continue

            item = EvalSetItem(
                set_name=set_name,
                prompt=rec["prompt"].strip(),
                expected_tools=list(rec.get("expected_tools", [])),
                expected_outcome=rec.get("expected_outcome"),
                grading_prompt=rec.get("grading_rubric"),
                weight=Decimal(str(rec.get("weight", 1.0))),
            )
            db.add(item)
            inserted += 1

        if not dry_run:
            await db.commit()

    return inserted, skipped, invalid


def _prompt_interactive(set_name: str) -> dict:
    """Walk the SME through one annotation with stdin prompts."""
    print(f"\nAdding to eval set: {set_name}")
    prompt = input("User prompt (required):\n> ").strip()
    tools_raw = input(
        "Expected tools, comma-sep (e.g. grep_kb,search_logs) [optional]:\n> "
    ).strip()
    outcome = input(
        "Expected outcome [answered|delegated|refused, optional]:\n> "
    ).strip() or None
    rubric = input(
        "Grading rubric for LLM-as-judge [optional, multi-line — finish with a blank line]:\n"
    )
    # Capture multi-line rubric.
    extra_lines = []
    while True:
        line = input()
        if line.strip() == "":
            break
        extra_lines.append(line)
    if extra_lines:
        rubric = (rubric + "\n" + "\n".join(extra_lines)).strip()
    weight_raw = input("Weight [default 1.0]:\n> ").strip()
    weight = float(weight_raw) if weight_raw else 1.0
    tags_raw = input(
        "Tags, comma-sep (e.g. scenario:fault_triage,positive) [optional]:\n> "
    ).strip()

    return {
        "prompt": prompt,
        "expected_tools": [
            t.strip() for t in tools_raw.split(",") if t.strip()
        ],
        "expected_outcome": outcome,
        "grading_rubric": rubric or None,
        "weight": weight,
        "tags": [t.strip() for t in tags_raw.split(",") if t.strip()],
    }


def _load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                logger.error("%s:%d invalid JSON: %s", path, lineno, exc)
    return records


async def _run(args: argparse.Namespace) -> int:
    if args.from_jsonl:
        records = _load_jsonl(Path(args.from_jsonl))
        if not records:
            logger.error("no records loaded from %s", args.from_jsonl)
            return 1
    else:
        records = [_prompt_interactive(args.set)]

    inserted, skipped, invalid = await _insert_items(
        set_name=args.set,
        records=records,
        dry_run=args.dry_run,
    )
    logger.info(
        "done. set=%s inserted=%d skipped_duplicate=%d rejected_invalid=%d (dry-run=%s)",
        args.set,
        inserted,
        skipped,
        invalid,
        args.dry_run,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SME annotation CLI for eval_set_items (Phase I)."
    )
    parser.add_argument(
        "--set",
        required=True,
        help="set_name to append to (e.g. fault_triage_v1).",
    )
    parser.add_argument(
        "--from-jsonl",
        default=None,
        help="Path to a JSONL file of annotated items (bulk mode).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate + log but do not INSERT.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
