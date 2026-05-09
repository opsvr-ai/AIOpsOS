"""Backfill ``content_hash`` + ``embedding`` for existing ``agent_memories``.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 9.1 / R-9.2.

Usage
-----

.. code:: bash

    python -m scripts.backfill_memory_hash_and_embedding [options]

Options
-------

``--dry-run``
    Compute hashes + embeddings but skip UPDATEs. Useful for rehearsals.

``--batch-size N``
    Rows to process per DB round-trip. Defaults to 500.

``--limit N``
    Stop after N rows. Unlimited when omitted.

``--no-resume``
    Start from scratch — ignore the Redis cursor
    ``backfill:memory:cursor`` and set it back to zero UUID.

Checkpoint
----------

We stash the last processed ``id`` under the Redis key
``backfill:memory:cursor``. Rerunning the script picks up from there,
making the job idempotent and interruption-tolerant (SIGINT persists
the cursor before exiting 0).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import signal
import sys
import uuid
from pathlib import Path
from typing import Any

# Allow ``python scripts/...`` from within the server/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from src.core.redis import get_redis  # noqa: E402
from src.models.base import async_session_factory  # noqa: E402
from src.services.memory.embedding import EmbeddingService, get_embedding_service  # noqa: E402


logger = logging.getLogger(__name__)


CURSOR_KEY = "backfill:memory:cursor"
ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class _Report:
    processed: int = 0
    embedded: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "embedded": self.embedded,
            "skipped": self.skipped,
            "failed": self.failed,
        }


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


async def run(
    *,
    dry_run: bool,
    batch_size: int,
    limit: int | None,
    resume: bool,
    embedding_service: EmbeddingService | None = None,
    db_factory: Any | None = None,
    redis_client: Any | None = None,
) -> _Report:
    """Run the backfill end-to-end; returns a report dict."""
    report = _Report()
    stop_requested = False

    def _sigint(_sig, _frame):
        nonlocal stop_requested
        stop_requested = True
        logger.info("backfill: SIGINT received; persisting cursor and exiting")

    try:
        signal.signal(signal.SIGINT, _sigint)
    except ValueError:
        # Called outside the main thread (tests).
        pass

    db_factory = db_factory or async_session_factory
    redis = redis_client if redis_client is not None else await get_redis()
    svc = embedding_service or get_embedding_service()

    cursor = ZERO_UUID
    if resume:
        try:
            raw = await redis.get(CURSOR_KEY)
        except Exception:
            raw = None
        if raw:
            try:
                cursor = uuid.UUID(
                    raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
                )
            except Exception:
                cursor = ZERO_UUID
    else:
        try:
            await redis.set(CURSOR_KEY, str(ZERO_UUID))
        except Exception:
            logger.warning("backfill: failed to reset cursor", exc_info=True)

    total_budget = limit if limit is not None else None

    while not stop_requested:
        remaining = (
            min(batch_size, total_budget - report.processed)
            if total_budget is not None
            else batch_size
        )
        if remaining <= 0:
            break

        rows = await _fetch_batch(db_factory, cursor, remaining)
        if not rows:
            break

        cursor = await _process_batch(
            svc=svc,
            rows=rows,
            report=report,
            dry_run=dry_run,
            db_factory=db_factory,
        )

        try:
            await redis.set(CURSOR_KEY, str(cursor))
        except Exception:
            logger.warning("backfill: cursor write failed", exc_info=True)

        logger.info(
            "backfill batch done: cursor=%s processed=%d embedded=%d skipped=%d failed=%d",
            cursor,
            report.processed,
            report.embedded,
            report.skipped,
            report.failed,
        )

    return report


async def _fetch_batch(
    db_factory: Any, cursor: uuid.UUID, batch_size: int
) -> list[dict]:
    """Return rows ordered by id > cursor with id,content,content_hash,embedding."""
    async with db_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT id, content, content_hash,
                       (embedding IS NOT NULL) AS has_embedding
                FROM agent_memories
                WHERE id > CAST(:cursor AS uuid)
                ORDER BY id
                LIMIT :lim
                """
            ),
            {"cursor": str(cursor), "lim": int(batch_size)},
        )
        return [dict(r._mapping) for r in result.fetchall()]


async def _process_batch(
    *,
    svc: EmbeddingService,
    rows: list[dict],
    report: _Report,
    dry_run: bool,
    db_factory: Any,
) -> uuid.UUID:
    """Compute hash + (optional) embedding for each row; UPDATE in batches."""
    # 1) Figure out which rows need an embedding.
    need_embed_idx: list[int] = []
    texts_to_embed: list[str] = []
    for i, r in enumerate(rows):
        if not r.get("has_embedding") and svc.enabled:
            need_embed_idx.append(i)
            texts_to_embed.append(r["content"] or "")

    # 2) Compute embeddings in sub-batches of 16 (matches EmbeddingService default).
    embeddings: dict[int, list[float]] = {}
    if texts_to_embed:
        try:
            for start in range(0, len(texts_to_embed), 16):
                chunk = texts_to_embed[start : start + 16]
                idxs = need_embed_idx[start : start + 16]
                vectors = await svc.embed(chunk)
                for idx, vec in zip(idxs, vectors, strict=True):
                    if vec:
                        embeddings[idx] = vec
        except Exception:
            logger.exception("backfill: embedding step failed; rows will get hash only")

    # 3) UPDATE each row.
    last_id = rows[-1]["id"]
    for i, r in enumerate(rows):
        content = r["content"] or ""
        new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        vec = embeddings.get(i)
        if vec:
            report.embedded += 1
        needs_hash = not r.get("content_hash")
        needs_embed = vec is not None

        if not needs_hash and not needs_embed:
            report.skipped += 1
            report.processed += 1
            continue

        if dry_run:
            report.processed += 1
            continue

        try:
            await _update_row(db_factory, r["id"], new_hash if needs_hash else None, vec)
            report.processed += 1
        except Exception:
            logger.exception("backfill: UPDATE failed for %s", r["id"])
            report.failed += 1
            report.processed += 1

    return last_id


async def _update_row(
    db_factory: Any,
    row_id: uuid.UUID,
    new_hash: str | None,
    vec: list[float] | None,
) -> None:
    """Apply a row-level UPDATE with only the requested columns."""
    if new_hash is None and vec is None:
        return

    set_parts: list[str] = []
    params: dict[str, Any] = {"id": str(row_id)}
    if new_hash is not None:
        set_parts.append("content_hash = :hash")
        params["hash"] = new_hash
    if vec is not None:
        set_parts.append("embedding = CAST(:vec AS vector)")
        params["vec"] = "[" + ",".join(f"{float(x):.7f}" for x in vec) + "]"

    stmt = text(
        f"UPDATE agent_memories SET {', '.join(set_parts)} WHERE id = CAST(:id AS uuid)"
    )
    async with db_factory() as session:
        await session.execute(stmt, params)
        await session.commit()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill content_hash + embedding for agent_memories"
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute but don't UPDATE")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Ignore the Redis cursor and start from the zero UUID",
    )
    parser.set_defaults(resume=True)
    return parser


async def _amain() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    report = await run(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        limit=args.limit,
        resume=args.resume,
    )
    print(json.dumps(report.as_dict(), indent=2))


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
