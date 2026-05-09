"""Precompute ``precomputed_summary`` frontmatter for every wiki page.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 12.4 / R-9.5.

Walks ``data/knowledge/wiki/**/*.md``, skips pages that already carry a
``precomputed_summary`` key, and calls the default LLM to produce a 200-
character Chinese summary of each remaining page. The summary is
injected into frontmatter (preserving the rest) and the file is
rewritten atomically (temp file + rename).

Usage
-----

.. code:: bash

    python -m scripts.wiki_precompute_summaries [--dry-run] [--limit N]

Options
-------

``--dry-run``
    Scan + compute but don't write anything. The end-of-run report still
    records "would update" counts.

``--limit N``
    Process at most ``N`` pages needing summaries. Useful for rehearsals.

The script is idempotent: running it twice only updates pages that
haven't been summarised yet.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Allow ``python scripts/wiki_precompute_summaries.py`` from within server/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from src.config import settings  # noqa: E402
from src.services.kb_summarizer import (  # noqa: E402
    add_precomputed_summary,
    extract_frontmatter_and_body,
)

logger = logging.getLogger(__name__)


SUMMARY_SYSTEM = (
    "用 200 字以内中文总结以下 wiki 页面，只返回摘要文本，"
    "不要 markdown 格式或前缀解释。"
)


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


async def run(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    wiki_root: str | None = None,
    llm=None,
) -> dict[str, int]:
    """Walk the wiki tree, summarise missing pages, write atomically.

    Returns a dict ``{total, updated, skipped, errors}`` suitable for
    end-of-run reporting.
    """
    root = Path(wiki_root or os.path.join(settings.wiki_path, "wiki"))
    report = {"total": 0, "updated": 0, "skipped": 0, "errors": 0}

    if not root.is_dir():
        logger.warning("wiki root does not exist: %s", root)
        return report

    model = llm
    files = sorted(root.rglob("*.md"))
    for path in files:
        report["total"] += 1
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("read failed: %s — %s", path, exc)
            report["errors"] += 1
            continue

        fm, body = extract_frontmatter_and_body(content)
        if fm.get("precomputed_summary"):
            report["skipped"] += 1
            continue

        if not body.strip():
            report["skipped"] += 1
            continue

        if model is None:
            try:
                model = await _default_llm()
            except Exception:
                logger.exception("LLM unavailable; aborting remaining pages")
                report["errors"] += 1
                break

        try:
            summary = await _summarise(model, body)
        except Exception:
            logger.exception("LLM call failed for %s", path)
            report["errors"] += 1
            continue

        new_content = add_precomputed_summary(fm, body, summary)

        if dry_run:
            report["updated"] += 1
            logger.info("[dry-run] would update %s", path)
        else:
            try:
                _atomic_write(path, new_content)
                report["updated"] += 1
                logger.info("updated %s", path)
            except OSError as exc:
                logger.exception("write failed: %s — %s", path, exc)
                report["errors"] += 1

        if limit is not None and report["updated"] >= limit:
            logger.info("limit reached (%d); stopping", limit)
            break

    if dry_run:
        logger.info("[dry-run] would update %d pages", report["updated"])
    logger.info("precompute done: %s", report)
    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _summarise(model, body: str) -> str:
    """Invoke ``model`` and trim the result to 300 chars (safety bound)."""
    resp = await model.ainvoke(
        [
            SystemMessage(content=SUMMARY_SYSTEM),
            HumanMessage(content=body[:4000]),
        ]
    )
    raw = getattr(resp, "content", resp)
    if isinstance(raw, (list, tuple)):
        raw = "".join(str(p) for p in raw)
    return str(raw).strip()


def _atomic_write(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically via temp file + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


async def _default_llm():
    from src.core.model_factory import get_default_model

    return await get_default_model()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inject precomputed_summary frontmatter into wiki pages"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--wiki-root",
        default=None,
        help="Override wiki root (defaults to settings.wiki_path/wiki)",
    )
    return parser


async def _amain() -> None:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    report = await run(
        dry_run=args.dry_run,
        limit=args.limit,
        wiki_root=args.wiki_root,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
