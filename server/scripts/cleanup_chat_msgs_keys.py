"""One-shot cleanup: retire the legacy ``chat:msgs:*`` Redis cache keys.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 25.3
/ Phase M DoD.

Background
----------

Before Phase M, :func:`/chat/stream` cached its 30-row history slice in
Redis under ``chat:msgs:{session_id}:recent`` with a 600 s TTL. Task
25.3 retired that key: the MemoryTier HOT block (``session:{sid}:
hot_mem``) and per-turn ``session:{sid}:msgs_recent`` list now cover
the same need, and the handler reads the tail directly from Postgres.

This script exists to **remove the residual keys** left in Redis by
older deployments. It is safe to run once against a production Redis
and then delete the file from the tree. The script is **idempotent**
— if no ``chat:msgs:*`` keys remain it simply reports zero deletions.

Usage
-----

.. code:: bash

    # From the ``server`` directory:
    python -m scripts.cleanup_chat_msgs_keys             # delete
    python -m scripts.cleanup_chat_msgs_keys --dry-run   # count only

Options
-------

``--dry-run``
    Print the count of matching keys without deleting anything. Makes
    the operation a pure read against ``SCAN``.

``--pattern PATTERN``
    Override the scan pattern. Defaults to ``chat:msgs:*``. Keep the
    default unless you are certain the legacy key shape differs.

``--batch-size N``
    Delete keys in batches of this size. Defaults to 100 — small
    enough that each ``UNLINK`` pipelines against a single Redis
    round-trip without blocking the server. Has no effect in dry-run.

``--scan-count N``
    ``COUNT`` hint forwarded to ``SCAN``. Defaults to 500. Only affects
    the number of keys Redis returns per cursor iteration, not the
    total scanned.

Operational notes
-----------------

* ``SCAN`` + ``UNLINK`` are preferred over ``KEYS`` + ``DEL`` so the
  script is safe against a non-trivially sized keyspace without
  blocking the server-side event loop.
* The script drops its own connection on exit via :func:`close_redis`
  so cron-style invocations don't leak pool members.
* Exit code is always ``0`` on a successful run; non-zero only on
  connection / Redis errors.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow ``python -m scripts.cleanup_chat_msgs_keys`` from ``server/``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.redis import close_redis, get_redis  # noqa: E402

logger = logging.getLogger(__name__)


DEFAULT_PATTERN = "chat:msgs:*"
DEFAULT_BATCH_SIZE = 100
DEFAULT_SCAN_COUNT = 500


async def run(
    *,
    pattern: str = DEFAULT_PATTERN,
    dry_run: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    scan_count: int = DEFAULT_SCAN_COUNT,
) -> dict[str, int]:
    """Scan Redis for ``pattern`` and ``UNLINK`` matches in batches.

    Returns a small report dict with ``scanned`` / ``deleted`` counts
    so callers (tests, runbooks) can assert on the outcome.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if scan_count < 1:
        raise ValueError("scan_count must be >= 1")

    redis = await get_redis()

    scanned = 0
    deleted = 0
    pending: list[str] = []

    async for key in redis.scan_iter(match=pattern, count=scan_count):
        scanned += 1
        # Normalise bytes → str; decode_responses=True is the default in
        # the project's Redis helper but be defensive.
        k = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
        pending.append(k)
        if len(pending) >= batch_size:
            deleted += await _flush(redis, pending, dry_run=dry_run)
            pending.clear()

    if pending:
        deleted += await _flush(redis, pending, dry_run=dry_run)
        pending.clear()

    return {"scanned": scanned, "deleted": deleted}


async def _flush(redis, keys: list[str], *, dry_run: bool) -> int:
    """Remove ``keys`` in one call, or just count them on dry-run."""
    if not keys:
        return 0
    if dry_run:
        return len(keys)
    try:
        # ``UNLINK`` is preferred over ``DEL`` — the server frees memory
        # lazily in a background thread, so a big batch won't block
        # other clients.
        removed = await redis.unlink(*keys)
        return int(removed or 0)
    except Exception:
        logger.exception("UNLINK failed for batch of %d keys", len(keys))
        # Fall back to DEL on Redis versions without UNLINK.
        try:
            removed = await redis.delete(*keys)
            return int(removed or 0)
        except Exception:
            logger.exception("DEL fallback also failed")
            return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cleanup_chat_msgs_keys",
        description=(
            "Retire legacy chat:msgs:* Redis keys. Safe to run once and "
            "delete the script afterwards."
        ),
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"SCAN MATCH pattern (default: {DEFAULT_PATTERN!r})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count matching keys without deleting them.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Keys per UNLINK batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--scan-count",
        type=int,
        default=DEFAULT_SCAN_COUNT,
        help=f"SCAN COUNT hint (default: {DEFAULT_SCAN_COUNT}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser


async def _amain(args: argparse.Namespace) -> int:
    try:
        report = await run(
            pattern=args.pattern,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            scan_count=args.scan_count,
        )
    except Exception:
        logger.exception("cleanup failed")
        return 2
    finally:
        await close_redis()

    action = "would delete" if args.dry_run else "deleted"
    logger.info(
        "cleanup done: scanned=%d %s=%d pattern=%s",
        report["scanned"],
        action,
        report["deleted"],
        args.pattern,
    )
    # Also print a machine-readable line for cron / CI capture.
    print(
        f"scanned={report['scanned']} "
        f"{'would_delete' if args.dry_run else 'deleted'}={report['deleted']} "
        f"pattern={args.pattern}"
    )
    return 0


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
