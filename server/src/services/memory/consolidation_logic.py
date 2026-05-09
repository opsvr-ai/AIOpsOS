"""Async consolidation pipeline — the real body of ``memory.consolidate``.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 10.1 /
R-2.2 / R-2.3 / R-2.7 / R-2.11 / R-2.14 / R-8.1.

The Celery wrapper in :mod:`src.workers.tasks.memory_consolidation` is
intentionally thin. All the real logic lives here so tests can drive
the pipeline with injected Redis / DB / LLM fakes.

High-level flow::

    lock:consolidate:{sid}                     (Redis, 5-min TTL)
    ↓
    load session row
    ↓
    SELECT messages since last_consolidation_at  (up to 100)
    ↓
    SELECT agent_memories (baseline, 50 latest personal)
    ↓
    DIFF_EXTRACTION_PROMPT → {new_personal, new_team, supersedes, ignored}
    ↓
    validate + filter (min 15 chars, non-empty title)
    ↓
    PII check on team → downgrade to personal if leaked
    ↓
    dedupe by content_hash
    ↓
    embed (batch) — SKIPPED when degraded=True
    ↓
    INSERT ON CONFLICT (content_hash) DO NOTHING
    ↓
    UPDATE is_archived=TRUE for supersedes list
    ↓
    rebuild HOT block, HSET session:{sid}:hot_mem, TTL 600
    ↓
    UPDATE sessions.last_consolidation_at / hot_memory_version
    ↓
    release lock
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from src.core.metrics import (
    consolidation_degraded_total,
    consolidation_failed_total,
)
from src.services.pii import contains_pii

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Diff-extraction prompt (design.md § 差分 Prompt)
# ---------------------------------------------------------------------------

DIFF_EXTRACTION_PROMPT = """你是增量记忆更新器。给定 baseline 记忆 + 一批新 turns。
输出严格 JSON，格式：
{
  "new_personal": [{"title": "", "content": "", "tags": []}, ...],
  "new_team":     [{"title": "", "content": "", "tags": []}, ...],
  "supersedes":   ["<baseline.id>", ...],
  "ignored":      ["reason 1", ...]
}

规则：
1. 不要重复 baseline 里已有的信息；若新 turn 和 baseline 里某条只是表达不同但含义相同，放入 supersedes。
2. 闲聊 / 工具 help / 自我介绍 等归入 ignored。
3. team 记忆必须剔除用户名 / IP / Token / 邮箱等个人身份信息。
4. 每条 title ≤ 30 字，content ≥ 15 字，tags 2-5 个。
5. 只输出 JSON，不要添加任何解释文字。
"""

MIN_CONTENT_LENGTH = 15
TEAM_SCOPE = "team"
PERSONAL_SCOPE = "personal"

# Redis lock TTL (5 minutes).
_LOCK_TTL = 300
# Session HOT cache TTL (10 minutes).
_HOT_TTL = 600


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ConsolidationResult:
    """Return value of :func:`run_consolidation`."""

    status: str                                   # ok | skipped | noop | error
    session_id: str
    added: int = 0
    archived: int = 0
    ignored: int = 0
    degraded: bool = False
    token_cost_estimate: int = 0
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "status": self.status,
            "session_id": self.session_id,
            "added": self.added,
            "archived": self.archived,
            "ignored": self.ignored,
            "degraded": self.degraded,
            "token_cost_estimate": self.token_cost_estimate,
        }
        if self.reason is not None:
            out["reason"] = self.reason
        return out


# ---------------------------------------------------------------------------
# Lock abstraction
# ---------------------------------------------------------------------------


class _RedisLockCtx:
    """Async context-manager around ``redis.set(nx=True, ex=ttl)``.

    We don't use ``redis.lock()`` because fakeredis' implementation
    sometimes struggles with blocking+timeout combinations. A SETNX
    scheme is plenty for the "at most one consolidation per session"
    guarantee and is easy to test.
    """

    def __init__(self, redis: Any, key: str, *, ttl: int = _LOCK_TTL) -> None:
        self._redis = redis
        self._key = key
        self._ttl = ttl
        self._token = uuid.uuid4().hex
        self._acquired = False

    async def acquire(self) -> bool:
        try:
            ok = await self._redis.set(self._key, self._token, nx=True, ex=self._ttl)
        except Exception:
            logger.debug("consolidation lock: set failed", exc_info=True)
            return False
        self._acquired = bool(ok)
        return self._acquired

    async def release(self) -> None:
        if not self._acquired:
            return
        # Best-effort compare-and-delete so we don't clobber a lock held by
        # another worker if our TTL expired mid-execution.
        try:
            current = await self._redis.get(self._key)
            if current is None:
                return
            decoded = (
                current.decode() if isinstance(current, (bytes, bytearray)) else str(current)
            )
            if decoded == self._token:
                await self._redis.delete(self._key)
        except Exception:
            logger.debug("consolidation lock: release failed", exc_info=True)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


async def run_consolidation(
    session_id: str,
    *,
    embedding: Any | None = None,
    llm: Any | None = None,
    redis_client: Any | None = None,
    db_factory: Any | None = None,
    pii_sanitiser: Any | None = None,
    degraded: bool = False,
) -> ConsolidationResult:
    """Run the consolidation pipeline for one session.

    All external dependencies are injectable so the logic is unit-testable
    without real Redis / PG / LLM.

    Args:
        session_id: UUID string of the session to consolidate.
        embedding: ``EmbeddingService`` (lazy default via
            :func:`get_embedding_service`).
        llm: LangChain-style chat model with ``.ainvoke(messages)``.
        redis_client: async ``redis.Redis`` / ``fakeredis.aioredis.FakeRedis``.
        db_factory: ``async_session_factory``-compatible context manager.
        pii_sanitiser: callable ``(text) -> (found: bool, kinds: list[str])``.
        degraded: when True, skip the embedding step and mark the result
            as degraded (increments ``consolidation_degraded_total``).

    Returns:
        :class:`ConsolidationResult` describing the run.
    """
    sid = str(session_id)
    redis = await _resolve_redis(redis_client)
    factory = db_factory or _default_db_factory()
    pii_check = pii_sanitiser or contains_pii

    lock = _RedisLockCtx(redis, f"lock:consolidate:{sid}")
    if not await lock.acquire():
        return ConsolidationResult(status="skipped", session_id=sid, reason="locked")

    try:
        # 1. load session row
        session_row = await _load_session(factory, sid)
        if session_row is None:
            return ConsolidationResult(
                status="skipped", session_id=sid, reason="session_missing"
            )

        # 2. pending turns since last consolidation
        pending = await _load_pending_messages(
            factory, sid, session_row.get("last_consolidation_at")
        )
        if not pending:
            return ConsolidationResult(status="noop", session_id=sid)

        user_id = str(session_row["user_id"])
        space_id = (
            str(session_row["space_id"]) if session_row.get("space_id") else None
        )

        # 3. baseline
        baseline = await _load_baseline_memories(factory, user_id)

        # 4. LLM extraction
        model = llm if llm is not None else await _default_llm()
        extracted = await _run_llm_extraction(model, pending, baseline)
        if extracted is None:
            # Invalid LLM output — we still succeed (no-op) so the session
            # doesn't get stuck, but record the reason.
            return ConsolidationResult(
                status="ok", session_id=sid, reason="empty_extraction"
            )

        new_personal = _filter_items(extracted.get("new_personal", []))
        new_team_raw = _filter_items(extracted.get("new_team", []))
        ignored = list(extracted.get("ignored", []) or [])
        supersedes_ids = _coerce_uuid_list(extracted.get("supersedes", []))

        # 5. PII scan — downgrade leaky team items
        downgraded_personal: list[dict] = []
        new_team: list[dict] = []
        for item in new_team_raw:
            flagged, _ = pii_check(item.get("content", ""))
            if flagged:
                downgraded_personal.append(item)
            else:
                new_team.append(item)

        all_new = (
            [(PERSONAL_SCOPE, i) for i in new_personal]
            + [(PERSONAL_SCOPE, i) for i in downgraded_personal]
            + [(TEAM_SCOPE, i) for i in new_team]
        )

        # 6. dedupe by content_hash vs baseline + intra-batch
        baseline_hashes = {
            b["content_hash"] for b in baseline if b.get("content_hash")
        }
        seen_hashes = set(baseline_hashes)
        deduped: list[tuple[str, dict, str]] = []
        for scope, item in all_new:
            content = item["content"]
            chash = _content_hash(content)
            if chash in seen_hashes:
                continue
            seen_hashes.add(chash)
            deduped.append((scope, item, chash))

        # 7. embed (skipped under degraded)
        embeddings: list[list[float] | None]
        if degraded or not deduped:
            embeddings = [None] * len(deduped)
            if degraded:
                try:
                    consolidation_degraded_total.inc()
                except Exception:
                    logger.debug("metric inc failed", exc_info=True)
        else:
            embed_svc = embedding if embedding is not None else await _default_embedding()
            if embed_svc is not None and getattr(embed_svc, "enabled", False):
                try:
                    vectors = await embed_svc.embed([i[1]["content"] for i in deduped])
                    embeddings = [v if v else None for v in vectors]
                except Exception:
                    logger.exception("consolidation: embed failed, degrading")
                    embeddings = [None] * len(deduped)
                    try:
                        consolidation_degraded_total.inc()
                    except Exception:
                        logger.debug("metric inc failed", exc_info=True)
                    degraded = True
            else:
                embeddings = [None] * len(deduped)

        # 8. INSERT ON CONFLICT DO NOTHING + archive supersedes + bump session
        added, archived, new_version = await _commit_memories(
            factory,
            session_id=sid,
            user_id=user_id,
            space_id=space_id,
            entries=deduped,
            embeddings=embeddings,
            supersedes_ids=supersedes_ids,
        )

        # 9. rebuild HOT block + persist to Redis
        try:
            await _rebuild_hot_cache(
                redis,
                factory,
                session_id=sid,
                user_id=user_id,
                space_id=space_id,
                version=new_version,
            )
        except Exception:
            logger.exception("consolidation: hot cache rebuild failed (non-fatal)")

        token_cost = _estimate_tokens(pending, baseline, new_personal + new_team)
        return ConsolidationResult(
            status="ok",
            session_id=sid,
            added=added,
            archived=archived,
            ignored=len(ignored),
            degraded=degraded,
            token_cost_estimate=token_cost,
        )

    except Exception:
        try:
            consolidation_failed_total.inc()
        except Exception:
            logger.debug("metric inc failed", exc_info=True)
        logger.exception("consolidation: pipeline failure for %s", sid)
        raise
    finally:
        await lock.release()


# ---------------------------------------------------------------------------
# Helpers: resolvers for injectable deps
# ---------------------------------------------------------------------------


async def _resolve_redis(redis_client: Any | None) -> Any:
    if redis_client is not None:
        return redis_client
    from src.core.redis import get_redis

    return await get_redis()


def _default_db_factory() -> Any:
    from src.models.base import async_session_factory

    return async_session_factory


async def _default_embedding() -> Any | None:
    try:
        from src.services.memory.embedding import get_embedding_service

        return get_embedding_service()
    except Exception:
        logger.debug("default embedding unavailable", exc_info=True)
        return None


async def _default_llm() -> Any:
    from src.core.model_factory import get_default_model

    return await get_default_model()


# ---------------------------------------------------------------------------
# Helpers: DB reads
# ---------------------------------------------------------------------------


async def _load_session(factory: Any, session_id: str) -> dict | None:
    async with factory() as session:
        row = await session.execute(
            text(
                """
                SELECT id, user_id, space_id, last_consolidation_at,
                       hot_memory_version, consolidation_count
                FROM sessions
                WHERE id = CAST(:sid AS uuid)
                """
            ),
            {"sid": session_id},
        )
        fetched = row.first()
    if fetched is None:
        return None
    return {
        "id": fetched.id,
        "user_id": fetched.user_id,
        "space_id": fetched.space_id,
        "last_consolidation_at": fetched.last_consolidation_at,
        "hot_memory_version": fetched.hot_memory_version,
        "consolidation_count": fetched.consolidation_count,
    }


async def _load_pending_messages(
    factory: Any, session_id: str, last_at: datetime | None
) -> list[dict]:
    """Return messages created after ``last_consolidation_at`` (up to 100)."""
    async with factory() as session:
        if last_at is None:
            rows = await session.execute(
                text(
                    """
                    SELECT id, role, content, created_at
                    FROM messages
                    WHERE session_id = CAST(:sid AS uuid)
                    ORDER BY created_at ASC
                    LIMIT 100
                    """
                ),
                {"sid": session_id},
            )
        else:
            rows = await session.execute(
                text(
                    """
                    SELECT id, role, content, created_at
                    FROM messages
                    WHERE session_id = CAST(:sid AS uuid)
                      AND created_at > :last_at
                    ORDER BY created_at ASC
                    LIMIT 100
                    """
                ),
                {"sid": session_id, "last_at": last_at},
            )
        items = rows.fetchall()
    return [
        {
            "id": r.id,
            "role": r.role,
            "content": r.content or "",
            "created_at": r.created_at,
        }
        for r in items
    ]


async def _load_baseline_memories(factory: Any, user_id: str) -> list[dict]:
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, title, content, scope, tags, content_hash
                FROM agent_memories
                WHERE user_id = CAST(:uid AS uuid)
                  AND is_archived = false
                ORDER BY created_at DESC
                LIMIT 50
                """
            ),
            {"uid": user_id},
        )
        items = rows.fetchall()
    return [
        {
            "id": r.id,
            "title": r.title or "",
            "content": r.content or "",
            "scope": r.scope or PERSONAL_SCOPE,
            "tags": list(r.tags or []),
            "content_hash": r.content_hash,
        }
        for r in items
    ]


# ---------------------------------------------------------------------------
# Helpers: LLM call
# ---------------------------------------------------------------------------


async def _run_llm_extraction(
    llm: Any, pending: list[dict], baseline: list[dict]
) -> dict | None:
    """Invoke *llm* with the diff prompt; return parsed JSON or None."""
    user_block = _render_diff_context(pending, baseline)
    try:
        resp = await llm.ainvoke(
            [
                SystemMessage(content=DIFF_EXTRACTION_PROMPT),
                HumanMessage(content=user_block),
            ]
        )
    except Exception:
        logger.exception("consolidation: LLM call failed")
        raise

    raw = getattr(resp, "content", resp)
    if isinstance(raw, (list, tuple)):
        # Some models stream content as a list of parts.
        raw = "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
        )
    raw = str(raw).strip()
    if raw.startswith("```"):
        # strip ``` fencing
        head, _, rest = raw.partition("\n")
        if rest.rstrip().endswith("```"):
            raw = rest.rstrip()[: -3].rstrip()
        else:
            raw = rest

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("consolidation: LLM output not valid JSON: %s", raw[:200])
        return None


def _render_diff_context(pending: list[dict], baseline: list[dict]) -> str:
    """Format the user-side message fed to the diff LLM."""
    baseline_lines = [
        f"- id={b['id']} | [{b['scope']}] {b.get('title') or b['content'][:40]}"
        for b in baseline[:50]
    ]
    turn_lines = [
        f"[{m['role']}] {m['content'][:500]}"
        for m in pending[:80]
    ]
    return (
        "## baseline 记忆（最多 50 条）\n"
        + ("\n".join(baseline_lines) if baseline_lines else "(空)")
        + "\n\n## 新 turns\n"
        + ("\n---\n".join(turn_lines) if turn_lines else "(空)")
        + "\n\n请按 SYSTEM 指示输出 JSON。"
    )


# ---------------------------------------------------------------------------
# Helpers: item validation + hashing
# ---------------------------------------------------------------------------


def _filter_items(raw: Any) -> list[dict]:
    """Drop items without non-empty title / content ≥ 15 chars."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title or not content or len(content) < MIN_CONTENT_LENGTH:
            continue
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        out.append({"title": title, "content": content, "tags": list(tags)})
    return out


def _coerce_uuid_list(raw: Any) -> list[uuid.UUID]:
    if not isinstance(raw, list):
        return []
    out: list[uuid.UUID] = []
    for item in raw:
        try:
            out.append(uuid.UUID(str(item)))
        except (TypeError, ValueError):
            continue
    return out


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()


def _estimate_tokens(
    pending: list[dict], baseline: list[dict], items: list[dict]
) -> int:
    """Cheap character-count heuristic for logging / budget tracking."""
    chars = sum(len(m.get("content", "")) for m in pending)
    chars += sum(len(b.get("content", "")) for b in baseline)
    chars += sum(len(i.get("content", "")) for i in items)
    return max(200, chars // 4)


# ---------------------------------------------------------------------------
# Helpers: DB writes + HOT cache
# ---------------------------------------------------------------------------


async def _commit_memories(
    factory: Any,
    *,
    session_id: str,
    user_id: str,
    space_id: str | None,
    entries: list[tuple[str, dict, str]],
    embeddings: list[list[float] | None],
    supersedes_ids: list[uuid.UUID],
) -> tuple[int, int, int]:
    """Run all writes in a single transaction.

    Returns (added, archived, new_hot_version).
    """
    added = 0
    archived = 0
    async with factory() as session:
        # Insert new memories one-by-one with ON CONFLICT (content_hash) DO NOTHING.
        # SQLAlchemy core batch insert won't express the conflict target
        # cleanly across dialects, so we use raw SQL.
        for (scope, item, chash), vec in zip(entries, embeddings, strict=True):
            params: dict[str, Any] = {
                "id": uuid.uuid4(),
                "sid": session_id,
                "uid": user_id,
                "space_id": space_id,
                "title": item["title"][:512],
                "content": item["content"],
                "scope": scope,
                "tags": json.dumps(item.get("tags") or []),
                "chash": chash,
            }
            if vec:
                params["embedding"] = _vec_to_literal(vec)
                stmt = text(
                    """
                    INSERT INTO agent_memories
                        (id, session_id, user_id, memory_type, content, embedding,
                         metadata, scope, title, tags, space_id, content_hash,
                         is_archived, pinned)
                    VALUES (
                        :id, CAST(:sid AS uuid), CAST(:uid AS uuid), 'fact',
                        :content, CAST(:embedding AS vector),
                        '{}'::jsonb, :scope, :title, CAST(:tags AS jsonb),
                        CASE WHEN :space_id IS NULL THEN NULL
                             ELSE CAST(:space_id AS uuid) END,
                        :chash, false, false
                    )
                    ON CONFLICT (content_hash) DO NOTHING
                    RETURNING id
                    """
                )
            else:
                stmt = text(
                    """
                    INSERT INTO agent_memories
                        (id, session_id, user_id, memory_type, content, embedding,
                         metadata, scope, title, tags, space_id, content_hash,
                         is_archived, pinned)
                    VALUES (
                        :id, CAST(:sid AS uuid), CAST(:uid AS uuid), 'fact',
                        :content, NULL,
                        '{}'::jsonb, :scope, :title, CAST(:tags AS jsonb),
                        CASE WHEN :space_id IS NULL THEN NULL
                             ELSE CAST(:space_id AS uuid) END,
                        :chash, false, false
                    )
                    ON CONFLICT (content_hash) DO NOTHING
                    RETURNING id
                    """
                )
            try:
                result = await session.execute(stmt, params)
                if result.scalar() is not None:
                    added += 1
            except Exception:
                logger.exception(
                    "consolidation: insert failed for hash %s", chash[:10]
                )

        # Archive superseded.
        if supersedes_ids:
            try:
                res = await session.execute(
                    text(
                        """
                        UPDATE agent_memories
                        SET is_archived = true
                        WHERE id = ANY(CAST(:ids AS uuid[]))
                          AND user_id = CAST(:uid AS uuid)
                        """
                    ),
                    {
                        "ids": [str(i) for i in supersedes_ids],
                        "uid": user_id,
                    },
                )
                archived = res.rowcount or 0
            except Exception:
                logger.exception("consolidation: archive failed")

        # Bump session counters.
        new_version_row = await session.execute(
            text(
                """
                UPDATE sessions
                SET last_consolidation_at = now(),
                    consolidation_count = consolidation_count + 1,
                    hot_memory_version = hot_memory_version + 1,
                    memory_status = 'consolidated'
                WHERE id = CAST(:sid AS uuid)
                RETURNING hot_memory_version
                """
            ),
            {"sid": session_id},
        )
        new_version = new_version_row.scalar() or 0
        await session.commit()

    return added, archived, int(new_version)


def _vec_to_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.7f}" for x in vec) + "]"


async def _rebuild_hot_cache(
    redis: Any,
    factory: Any,
    *,
    session_id: str,
    user_id: str,
    space_id: str | None,
    version: int,
) -> None:
    """Materialise a new HOT block and persist to Redis.

    We intentionally import :class:`MemoryTier` lazily to avoid a circular
    import: ``memory.tier`` may itself import from ``memory_service``.
    """
    from src.services.memory.tier import HotContext, MemoryTier

    tier = MemoryTier(db_factory=factory, redis_client=redis)
    ctx = HotContext(
        session_id=session_id,
        user_id=user_id,
        space_id=space_id,
    )
    block = await tier._build_hot_from_db(ctx)
    block.version = version  # override: use the freshly bumped number
    key = f"session:{session_id}:hot_mem"
    try:
        await redis.delete(key)
        await redis.hset(key, mapping=block.to_redis_hash())
        await redis.expire(key, _HOT_TTL)
    except Exception:
        logger.debug("hot cache HSET failed", exc_info=True)


__all__ = [
    "DIFF_EXTRACTION_PROMPT",
    "ConsolidationResult",
    "run_consolidation",
]
