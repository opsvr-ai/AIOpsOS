"""Three-tier memory read path — HOT (Redis) / WARM (pgvector) / COLD (wiki).

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 8.1-8.4
/ R-2.5 / R-2.6 / R-2.8 / R-2.13 / R-6.2.

Phase D scope:

* :class:`HotContext` — the inputs the gateway ships on every turn.
* :class:`MemoryItem` — a single warm-recall row.
* :class:`HotBlock` — the pre-assembled HOT cache entry, markdown-able.
* :class:`MemoryTier` — the three read paths plus a small rolling
  hit-ratio counter per tier that feeds ``memory_recall_hit_ratio``.

Write paths (Phase E) are intentionally out of scope: this module only
**reads** existing rows, pulls vectors through the batched
:class:`EmbeddingService`, and serves frontmatter-extracted summaries
from the wiki directory.

Hybrid scoring (R-2.8)::

    score = 0.5 * sim + 0.3 * recency + 0.2 * (1.0 if pinned else 0.0)
    recency = 1.0 / (1.0 + age_days / 7.0)

See ``_hybrid_score`` below for the canonical implementation; the PBT
in ``tests/memory/test_hybrid_scoring.py`` validates its monotonicity.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.config import settings
from src.core.metrics import memory_recall_hit_ratio
from src.models.base import async_session_factory

from .embedding import EmbeddingService, get_embedding_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HotContext:
    """Input bundle for HOT-tier lookups."""

    session_id: str
    user_id: str
    space_id: str | None = None
    user_profile_text: str = ""


@dataclass
class MemoryItem:
    """Single row returned by warm_recall."""

    id: uuid.UUID
    title: str
    content: str
    scope: str
    tags: list[str]
    pinned: bool
    score: float
    created_at: datetime
    last_used_at: datetime | None = None
    sim: float = 0.0

    def to_summary(self) -> str:
        """Short one-line markdown representation used in HOT blocks."""
        lbl = self.title or self.content[:60]
        tag = "个人" if self.scope == "personal" else "团队"
        pin = " 📌" if self.pinned else ""
        return f"- [{tag}]{pin} {lbl}"


@dataclass
class HotBlock:
    """Pre-assembled HOT cache payload.

    ``as_markdown()`` renders an injection-ready block for the system
    prompt; callers can also access individual fields directly.
    """

    version: int = 0
    user_profile: str = ""
    space_ctx: str = ""
    last_k_summary: str = ""
    top_recent_personal: list[MemoryItem] = field(default_factory=list)
    top_pinned_team: list[MemoryItem] = field(default_factory=list)

    def as_markdown(self) -> str:
        parts: list[str] = []
        if self.user_profile:
            parts.append(f"## 用户画像\n\n{self.user_profile.strip()}")
        if self.space_ctx:
            parts.append(f"## 空间上下文\n\n{self.space_ctx.strip()}")
        if self.last_k_summary:
            parts.append(f"## 最近对话摘要\n\n{self.last_k_summary.strip()}")
        if self.top_recent_personal:
            lines = [m.to_summary() for m in self.top_recent_personal]
            parts.append("## 个人记忆\n\n" + "\n".join(lines))
        if self.top_pinned_team:
            lines = [m.to_summary() for m in self.top_pinned_team]
            parts.append("## 团队记忆（置顶）\n\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def to_redis_hash(self) -> dict[str, str]:
        """Serialise for ``HSET session:{sid}:hot_mem``."""
        return {
            "version": str(self.version),
            "user_profile": self.user_profile,
            "space_ctx": self.space_ctx,
            "last_k_summary": self.last_k_summary,
            "top_recent_personal": json.dumps(
                [_memory_item_to_dict(m) for m in self.top_recent_personal]
            ),
            "top_pinned_team": json.dumps(
                [_memory_item_to_dict(m) for m in self.top_pinned_team]
            ),
        }

    @classmethod
    def from_redis_hash(cls, raw: dict) -> "HotBlock":
        """Inverse of :meth:`to_redis_hash`."""
        return cls(
            version=int(raw.get("version", "0") or 0),
            user_profile=raw.get("user_profile", "") or "",
            space_ctx=raw.get("space_ctx", "") or "",
            last_k_summary=raw.get("last_k_summary", "") or "",
            top_recent_personal=[
                _memory_item_from_dict(x)
                for x in json.loads(raw.get("top_recent_personal", "[]") or "[]")
            ],
            top_pinned_team=[
                _memory_item_from_dict(x)
                for x in json.loads(raw.get("top_pinned_team", "[]") or "[]")
            ],
        )


def _memory_item_to_dict(m: MemoryItem) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "title": m.title or "",
        "content": m.content,
        "scope": m.scope,
        "tags": list(m.tags or []),
        "pinned": bool(m.pinned),
        "score": float(m.score),
        "created_at": m.created_at.isoformat() if m.created_at else "",
        "last_used_at": m.last_used_at.isoformat() if m.last_used_at else None,
        "sim": float(m.sim),
    }


def _memory_item_from_dict(raw: dict) -> MemoryItem:
    return MemoryItem(
        id=uuid.UUID(raw["id"]) if raw.get("id") else uuid.uuid4(),
        title=raw.get("title", "") or "",
        content=raw.get("content", "") or "",
        scope=raw.get("scope", "personal"),
        tags=list(raw.get("tags") or []),
        pinned=bool(raw.get("pinned", False)),
        score=float(raw.get("score", 0.0)),
        created_at=_parse_datetime(raw.get("created_at")) or datetime.now(UTC),
        last_used_at=_parse_datetime(raw.get("last_used_at")),
        sim=float(raw.get("sim", 0.0)),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scoring helpers (exported for testability)
# ---------------------------------------------------------------------------


W_SIM = 0.5
W_RECENCY = 0.3
W_PINNED = 0.2


def _recency(age_days: float) -> float:
    """``1 / (1 + age_days/7)`` — pure function, monotone-decreasing in age."""
    if age_days < 0:
        age_days = 0.0
    return 1.0 / (1.0 + age_days / 7.0)


def _hybrid_score(sim: float, age_days: float, pinned: bool) -> float:
    """Canonical hybrid score used by warm_recall (R-2.8)."""
    sim = max(0.0, min(1.0, sim))
    age_days = max(0.0, float(age_days))
    return (
        W_SIM * sim
        + W_RECENCY * _recency(age_days)
        + W_PINNED * (1.0 if pinned else 0.0)
    )


def _age_days(created_at: datetime | None, now: datetime | None = None) -> float:
    if created_at is None:
        return 0.0
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    delta = now - created_at
    return max(0.0, delta.total_seconds() / 86400.0)


# ---------------------------------------------------------------------------
# Frontmatter extraction (replacement for python-frontmatter)
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"^---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*\r?\n", re.DOTALL
)


def _extract_frontmatter(text: str) -> dict | None:
    """Minimal YAML-frontmatter extractor.

    Handles the very common shape the project emits — top-level
    ``key: value`` pairs with multi-line block scalars introduced by
    ``|``. Anything fancier should fall back to ``None`` so callers
    treat the page as "no precomputed summary".
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    body = match.group("body")

    try:
        import yaml  # PyYAML is already a transitive dep via markitdown
    except Exception:
        logger.debug("PyYAML unavailable; falling back to naive parser")
        return _naive_frontmatter(body)

    try:
        data = yaml.safe_load(body)
    except Exception:
        logger.debug("frontmatter yaml parse failed", exc_info=True)
        return _naive_frontmatter(body)
    if isinstance(data, dict):
        return data
    return None


def _naive_frontmatter(body: str) -> dict:
    """Last-resort parser: ``key: value`` and ``key: |`` blocks only."""
    result: dict[str, Any] = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z0-9_\-]+):[ \t]*(.*)$", line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        if rest == "|":
            block: list[str] = []
            i += 1
            # collect indented block-scalar lines
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith("  ") or nxt.strip() == "":
                    block.append(nxt[2:] if nxt.startswith("  ") else nxt)
                    i += 1
                else:
                    break
            result[key] = "\n".join(block).strip()
            continue
        result[key] = rest
        i += 1
    return result


# ---------------------------------------------------------------------------
# MemoryTier
# ---------------------------------------------------------------------------


class MemoryTier:
    """Read-side facade over HOT / WARM / COLD memory surfaces."""

    WINDOW_SIZE = 200  # samples per tier before flushing the gauge

    def __init__(
        self,
        *,
        embedding: EmbeddingService | None = None,
        redis_client: Any | None = None,
        db_factory: Any | None = None,
        wiki_root: str | None = None,
    ) -> None:
        self._embedding = embedding if embedding is not None else get_embedding_service()
        self._redis_client = redis_client
        self._db_factory = db_factory or async_session_factory
        self._wiki_root = wiki_root or os.path.join(settings.wiki_path, "wiki")

        # Rolling hit-ratio per tier.
        self._stats: dict[str, dict[str, int]] = {
            "hot": {"hits": 0, "misses": 0},
            "warm": {"hits": 0, "misses": 0},
            "cold": {"hits": 0, "misses": 0},
        }

    # ------------------------------------------------------------------
    # HOT
    # ------------------------------------------------------------------

    async def hot(self, ctx: HotContext) -> HotBlock:
        """Return the HOT block, Redis-first, falling back to DB build."""
        key = f"session:{ctx.session_id}:hot_mem"
        redis = await self._redis()
        try:
            raw = await redis.hgetall(key)
        except Exception:
            logger.debug("hot redis hgetall failed", exc_info=True)
            raw = None

        if raw:
            # Redis decode_responses may or may not be on; normalise keys to str.
            normalised = {
                (k.decode() if isinstance(k, (bytes, bytearray)) else str(k)): (
                    v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
                )
                for k, v in raw.items()
            }
            try:
                block = HotBlock.from_redis_hash(normalised)
                self._record("hot", True)
                return block
            except Exception:
                logger.debug("hot cache decode failed; rebuilding", exc_info=True)

        self._record("hot", False)
        block = await self._build_hot_from_db(ctx)
        try:
            await redis.hset(key, mapping=block.to_redis_hash())
            await redis.expire(key, 600)
        except Exception:
            logger.debug("hot cache write failed", exc_info=True)
        return block

    async def _build_hot_from_db(self, ctx: HotContext) -> HotBlock:
        """Materialise a HotBlock straight from ``agent_memories`` + sessions."""
        async with self._db_factory() as session:
            # hot_memory_version
            ver_row = await session.execute(
                text(
                    "SELECT hot_memory_version FROM sessions "
                    "WHERE id = CAST(:sid AS uuid)"
                ),
                {"sid": ctx.session_id},
            )
            version = ver_row.scalar() or 0

            personal_rows = await session.execute(
                text(
                    """
                    SELECT id, title, content, scope, tags, pinned, created_at,
                           last_used_at
                    FROM agent_memories
                    WHERE user_id = CAST(:uid AS uuid)
                      AND scope = 'personal'
                      AND is_archived = false
                    ORDER BY created_at DESC
                    LIMIT 5
                    """
                ),
                {"uid": ctx.user_id},
            )
            personal = [_row_to_item(r) for r in personal_rows.fetchall()]

            team_params: dict[str, Any] = {}
            team_where = (
                "scope = 'team' AND pinned = true AND is_archived = false"
            )
            if ctx.space_id:
                team_where += (
                    " AND (space_id IS NULL OR space_id = CAST(:sid AS uuid))"
                )
                team_params["sid"] = ctx.space_id
            team_rows = await session.execute(
                text(
                    f"""
                    SELECT id, title, content, scope, tags, pinned, created_at,
                           last_used_at
                    FROM agent_memories
                    WHERE {team_where}
                    ORDER BY created_at DESC
                    LIMIT 5
                    """
                ),
                team_params,
            )
            team = [_row_to_item(r) for r in team_rows.fetchall()]

        return HotBlock(
            version=int(version or 0),
            user_profile=ctx.user_profile_text or "",
            space_ctx="",  # populated by Phase E when space profiles land
            last_k_summary="",  # consolidation worker writes this in Phase E
            top_recent_personal=personal,
            top_pinned_team=team,
        )

    # ------------------------------------------------------------------
    # WARM
    # ------------------------------------------------------------------

    async def warm_recall(
        self, ctx: HotContext, query: str, k: int = 8
    ) -> list[MemoryItem]:
        """Retrieve top-k memories by hybrid score.

        Uses the pgvector ``<=>`` distance operator when embeddings are
        enabled; otherwise falls back to the ILIKE path from
        :mod:`memory_service` so callers never observe failures due to a
        missing API key (R-2.5).
        """
        q = (query or "").strip()

        if self._embedding.enabled and q:
            items = await self._warm_vector(ctx, q, k)
        else:
            items = await self._warm_fallback(ctx, q, k)

        self._record("warm", bool(items))

        # Hybrid score + sort
        now = datetime.now(UTC)
        for m in items:
            age = _age_days(m.created_at, now)
            m.score = _hybrid_score(m.sim, age, m.pinned)
        items.sort(key=lambda m: m.score, reverse=True)
        top = items[:k]

        # Fire-and-forget last_used_at touch.
        if top:
            self._spawn_touch([m.id for m in top])

        return top

    async def _warm_vector(
        self, ctx: HotContext, query: str, k: int
    ) -> list[MemoryItem]:
        try:
            vec = await self._embedding.embed_one(query)
        except Exception:
            logger.exception("warm_recall embedding failed; falling back to ILIKE")
            return await self._warm_fallback(ctx, query, k)
        if not vec:
            return await self._warm_fallback(ctx, query, k)

        params: dict[str, Any] = {
            "uid": ctx.user_id,
            "q": _vec_to_literal(vec),
            "limit": max(1, int(k) * 3),
        }
        space_clause = ""
        if ctx.space_id:
            space_clause = (
                " AND (space_id IS NULL OR space_id = CAST(:sid AS uuid))"
            )
            params["sid"] = ctx.space_id

        stmt = text(
            f"""
            SELECT id, title, content, scope, tags, pinned, created_at,
                   last_used_at,
                   1 - (embedding <=> CAST(:q AS vector)) AS sim
            FROM agent_memories
            WHERE is_archived = false
              AND embedding IS NOT NULL
              AND (scope = 'team' OR user_id = CAST(:uid AS uuid))
              {space_clause}
            ORDER BY embedding <=> CAST(:q AS vector)
            LIMIT :limit
            """
        )

        async with self._db_factory() as session:
            rows = await session.execute(stmt, params)
            items = [_row_to_item(r, with_sim=True) for r in rows.fetchall()]
        return items

    async def _warm_fallback(
        self, ctx: HotContext, query: str, k: int
    ) -> list[MemoryItem]:
        """ILIKE-based fallback — kept schema-compatible with :func:`memory_service.retrieve`."""
        from src.services.memory_service import memory_service

        rows = await memory_service.retrieve(
            query=query,
            user_id=str(ctx.user_id),
            scope="all",
            space_id=str(ctx.space_id) if ctx.space_id else None,
            top_k=max(1, int(k) * 3),
        )
        out: list[MemoryItem] = []
        for r in rows:
            out.append(
                MemoryItem(
                    id=uuid.UUID(r["id"]),
                    title=r.get("title", "") or "",
                    content=r.get("content", "") or "",
                    scope=r.get("scope", "personal"),
                    tags=list(r.get("tags") or []),
                    pinned=False,
                    score=0.0,
                    created_at=_parse_datetime(r.get("created_at")) or datetime.now(UTC),
                    last_used_at=None,
                    # No semantic similarity signal in fallback — hybrid
                    # score degenerates to recency + pinned bonus.
                    sim=0.0,
                )
            )
        return out

    # ------------------------------------------------------------------
    # COLD
    # ------------------------------------------------------------------

    async def cold_lookup(self, slug: str) -> str | None:
        """Return the ``precomputed_summary`` for a wiki page or ``None``."""
        if not slug:
            self._record("cold", False)
            return None

        cache_key = f"wiki:summary:{slug}"
        redis = await self._redis()
        try:
            cached = await redis.get(cache_key)
        except Exception:
            cached = None
        if cached:
            val = cached.decode() if isinstance(cached, (bytes, bytearray)) else str(cached)
            # Distinguish "cached absent" (empty) from "cached present".
            if val == "__MISS__":
                self._record("cold", False)
                return None
            self._record("cold", True)
            return val

        summary = await asyncio.get_running_loop().run_in_executor(
            None, self._read_summary, slug
        )
        hit = summary is not None and summary.strip() != ""
        self._record("cold", hit)
        try:
            # Cache either the summary or a sentinel marker so we don't
            # re-read the disk for every lookup of a page that doesn't
            # carry frontmatter.
            await redis.set(
                cache_key, summary if hit else "__MISS__", ex=300
            )
        except Exception:
            logger.debug("cold cache write failed", exc_info=True)
        return summary if hit else None

    def _read_summary(self, slug: str) -> str | None:
        path = os.path.join(self._wiki_root, f"{slug}.md")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                body = f.read()
        except OSError:
            logger.debug("cold wiki read failed for %s", slug, exc_info=True)
            return None
        fm = _extract_frontmatter(body)
        if not fm:
            return None
        val = fm.get("precomputed_summary")
        if val is None:
            return None
        return str(val).strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _redis(self) -> Any:
        if self._redis_client is not None:
            return self._redis_client
        from src.core.redis import get_redis

        return await get_redis()

    def _record(self, tier: str, hit: bool) -> None:
        stats = self._stats[tier]
        if hit:
            stats["hits"] += 1
        else:
            stats["misses"] += 1
        total = stats["hits"] + stats["misses"]
        if total >= self.WINDOW_SIZE:
            try:
                memory_recall_hit_ratio.labels(tier=tier).set(
                    stats["hits"] / total
                )
            except Exception:
                logger.debug("memory_recall_hit_ratio gauge update failed", exc_info=True)
            stats["hits"] = 0
            stats["misses"] = 0
        else:
            # Also publish the current ratio so callers get live values
            # even before the window fills. Only the long-window reset
            # semantics change.
            try:
                memory_recall_hit_ratio.labels(tier=tier).set(stats["hits"] / total)
            except Exception:
                logger.debug("memory_recall_hit_ratio gauge update failed", exc_info=True)

    def _spawn_touch(self, ids: list[uuid.UUID]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._touch_last_used(ids))

    async def _touch_last_used(self, ids: list[uuid.UUID]) -> None:
        if not ids:
            return
        try:
            async with self._db_factory() as session:
                await session.execute(
                    text(
                        """
                        UPDATE agent_memories
                        SET last_used_at = now()
                        WHERE id = ANY(CAST(:ids AS uuid[]))
                        """
                    ),
                    {"ids": [str(i) for i in ids]},
                )
                await session.commit()
        except Exception:
            logger.debug("last_used_at touch failed", exc_info=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_item(row: Any, *, with_sim: bool = False) -> MemoryItem:
    """Map a SQLAlchemy row (named tuple) to a :class:`MemoryItem`."""
    return MemoryItem(
        id=row.id,
        title=(row.title or "") if hasattr(row, "title") else "",
        content=row.content or "",
        scope=row.scope or "personal",
        tags=list(row.tags or []),
        pinned=bool(getattr(row, "pinned", False)),
        score=0.0,
        created_at=_parse_datetime(row.created_at) or datetime.now(UTC),
        last_used_at=_parse_datetime(getattr(row, "last_used_at", None)),
        sim=float(getattr(row, "sim", 0.0)) if with_sim else 0.0,
    )


def _vec_to_literal(vec: list[float]) -> str:
    """Render a Python list as a pgvector literal: ``[0.1,0.2,...]``."""
    return "[" + ",".join(f"{float(x):.7f}" for x in vec) + "]"


__all__ = [
    "HotBlock",
    "HotContext",
    "MemoryItem",
    "MemoryTier",
    "_hybrid_score",
    "_recency",
]
