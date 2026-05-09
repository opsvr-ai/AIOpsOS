"""Pluggable memory providers for persistent agent recall across sessions.

One built-in provider (MEMORY.md / USER.md filesystem) is always active.
External providers are additive. Only one external provider runs at a time.

Lifecycle (called by MemoryManager):
  initialize()          — connect, create resources
  system_prompt_block() — static text for system prompt
  prefetch(query)       — recall relevant context before each turn
  sync_turn(user, asst) — persist completed turn
  on_session_end(msgs)  — end-of-session fact extraction
  shutdown()            — clean exit
"""

from __future__ import annotations

import asyncio as _asyncio
import logging
import time as _time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _future_ts(delay_seconds: float) -> float:
    """Return ``now() + delay`` as a Unix timestamp (float seconds).

    Used as a Redis ZSET score so the scheduler can pull due sessions
    with ``ZRANGEBYSCORE sleep:queue 0 now()``.
    """
    return _time.time() + float(delay_seconds)


# ═══════════════════════════════════════════════════════════════════════
# Abstract Base Class
# ═══════════════════════════════════════════════════════════════════════


class MemoryProvider(ABC):
    """Abstract base class for memory providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier (e.g. 'builtin', 'mem0', 'holographic')."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if configured, has credentials, and ready to use."""

    @abstractmethod
    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Initialize for a session. Called once at agent startup."""

    async def system_prompt_block(self) -> str:
        return ""

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    async def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Persist a completed turn to the backend. Non-blocking."""

    async def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Called when a session ends for fact extraction / summarization."""

    def shutdown(self) -> None:
        """Clean shutdown — flush queues, close connections."""


# ═══════════════════════════════════════════════════════════════════════
# Built-in Filesystem Provider
# ═══════════════════════════════════════════════════════════════════════


class BuiltinMemoryProvider(MemoryProvider):
    """Filesystem-backed provider using MEMORY.md and USER.md files.

    Always active — provides the base memory layer. Other providers
    layer on top of this.
    """

    def __init__(self, data_dir: str = "data") -> None:
        import os

        self._data_dir = os.path.abspath(data_dir)
        self._session_id: str = ""
        self._memory_path: str = ""
        self._user_path: str = ""

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        import os

        self._session_id = session_id
        self._memory_path = os.path.join(self._data_dir, "MEMORY.md")
        self._user_path = os.path.join(self._data_dir, "USER.md")

        for path in (self._memory_path, self._user_path):
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")

    async def system_prompt_block(self) -> str:
        parts: list[str] = []
        for label, path in [("MEMORY", self._memory_path), ("USER", self._user_path)]:
            try:
                content = open(path, encoding="utf-8").read().strip()
                if content:
                    parts.append(f"## {label}.md\n\n{content}")
            except OSError:
                pass
        return "\n\n".join(parts) if parts else ""

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        import re

        keywords = re.findall(r"[一-鿿\w]{2,}", query)
        lines: list[str] = []

        for path in (self._memory_path, self._user_path):
            try:
                content = open(path, encoding="utf-8").read()
                for line in content.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    for kw in keywords:
                        if kw.lower() in line.lower():
                            lines.append(f"- {line}")
                            break
            except OSError:
                pass

        if lines:
            return "## 相关记忆\n\n" + "\n".join(lines[:20])
        return ""

    def shutdown(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════
# Database-Backed Two-Tier Memory Provider
# ═══════════════════════════════════════════════════════════════════════


class DatabaseMemoryProvider(MemoryProvider):
    """PostgreSQL-backed two-tier memory provider.

    Stores personal memories (user+session scoped) and team memories
    (org-scoped, anonymized). Uses LLM-based extraction for both
    per-turn sync and session-end summarization.

    Context fencing: injected memories are wrapped in <memory-context>
    XML tags to separate memory context from conversation.
    """

    def __init__(self) -> None:
        self._session_id: str = ""
        self._user_id: str = ""
        self._space_id: str = ""
        self._pending_tasks: list[_asyncio.Task] = []

    @property
    def name(self) -> str:
        return "database"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._user_id = str(kwargs.get("user_id", ""))
        self._space_id = str(kwargs.get("space_id", ""))

    async def system_prompt_block(self) -> str:
        """Return recent personal + team memories with XML context fencing.

        Note: does NOT filter by space_id — personal memories belong to the user
        regardless of which space they're currently chatting in.
        """
        try:
            personal = await self._fetch_memories(scope="personal", limit=5, filter_space=False)
            team = await self._fetch_memories(scope="team", limit=5, filter_space=True)
        except Exception:
            logger.debug("system_prompt_block fetch failed", exc_info=True)
            return ""

        if not personal and not team:
            return ""

        parts: list[str] = ["<memory-context>"]
        if personal:
            parts.append("## 个人记忆\n")
            for m in personal:
                title = m.get("title", "") or m.get("content", "")[:60]
                parts.append(f"- {title}")
        if team:
            parts.append("\n## 团队记忆\n")
            for m in team:
                title = m.get("title", "") or m.get("content", "")[:60]
                parts.append(f"- {title}")
        parts.append("</memory-context>")
        return "\n".join(parts)

    async def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Search personal + team memories relevant to query.

        Returns both keyword-matched results AND recent memories as fallback,
        so the agent gets relevant context even for semantically different queries.
        """
        import asyncio as _asyncio

        async def _search():
            if not query:
                return []
            try:
                return await self._fetch_memories(query=query, scope="all", limit=8)
            except Exception:
                logger.debug("prefetch keyword search failed", exc_info=True)
                return []

        async def _recent():
            try:
                return await self._fetch_memories(query="", scope="all", limit=5, filter_space=False)
            except Exception:
                return []

        keyword_results, recent_results = await _asyncio.gather(_search(), _recent())

        # Merge: keyword results first, then recent (deduplicate by id)
        seen: set[str] = set()
        merged: list[dict] = []
        for m in keyword_results + recent_results:
            mid = m.get("id", "")
            if mid and mid not in seen:
                seen.add(mid)
                merged.append(m)

        if not merged:
            return ""

        lines = ["<memory-context>", "## 相关记忆\n"]
        for m in merged:
            title = m.get("title", "") or m.get("content", "")[:60]
            scope_label = "个人" if m.get("scope") == "personal" else "团队"
            lines.append(f"- [{scope_label}] {title}")
        lines.append("</memory-context>")
        return "\n".join(lines)

    async def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Persist a completed turn (R-2.1 / R-9.3).

        Emits a lightweight hint to the ConsolidationWorker via Redis
        (``session:{sid}:pending`` + ``sleep:queue``) and returns
        immediately. No LLM extraction runs in-request — the worker
        picks up the session once it goes idle. Task 25.2 deleted the
        legacy in-request buffer + flush path; this is now the only
        write path.
        """
        await self._emit_async_hint()

    async def _emit_async_hint(self) -> None:
        """Signal the ConsolidationWorker that a new turn just landed.

        Writes two small keys:

        * ``session:{sid}:pending`` — HINCRBY ``turns`` + EXPIRE 7200s.
          Used by :class:`~src.services.sleep_scheduler.SleepScheduler`
          to prioritise recently-active sessions.
        * ``sleep:queue`` — ZADD with score ``now() + 300`` so the
          scheduler picks it up after a 5-minute idle window.
        """
        sid = self._session_id
        if not sid:
            return
        try:
            from src.core.redis import get_redis

            redis = await get_redis()
            pipe = redis.pipeline()
            pipe.hincrby(f"session:{sid}:pending", "turns", 1)
            pipe.expire(f"session:{sid}:pending", 7200)
            pipe.zadd("sleep:queue", {sid: _future_ts(300)})
            await pipe.execute()
        except Exception:
            logger.debug("sync_turn async emit failed", exc_info=True)

    async def flush(self) -> None:
        """No-op. Retained for backwards compatibility.

        The legacy in-request LLM extraction path was removed in task
        25.2. Turns are now emitted to the ConsolidationWorker via
        :meth:`_emit_async_hint` and there is nothing to flush locally.
        This method is kept so existing callers (session-end paths,
        shutdown hooks, tests that stub it) don't need to change.
        """
        return None

    async def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Heavy session summarization via LLM. Runs on session close."""
        if not messages:
            return

        sid = self._session_id
        uid = self._user_id

        async def _summarize():
            from src.core.model_factory import get_default_model
            from src.services.memory_service import memory_service

            try:
                llm = await get_default_model()
                result = await memory_service.summarize_session(sid, uid, llm)
                logger.info("Session-end summarization for %s: personal=%d team=%d",
                            sid, result.get("personal", 0), result.get("team", 0))
            except Exception:
                logger.warning("Session-end summarization failed", exc_info=True)

        task = _asyncio.create_task(_summarize())
        self._pending_tasks.append(task)
        self._pending_tasks = [t for t in self._pending_tasks if not t.done()]

    async def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Mirror built-in memory writes to database."""
        sid = self._session_id
        uid = self._user_id

        async def _mirror():
            from src.services.memory_service import memory_service
            await memory_service.store(
                session_id=sid,
                user_id=uid,
                content=content,
                title=f"[{target}] {action}",
                scope="personal",
                tags=["mirrored", target],
                memory_type="fact",
                space_id=self._space_id,
            )

        task = _asyncio.create_task(_mirror())
        self._pending_tasks.append(task)
        self._pending_tasks = [t for t in self._pending_tasks if not t.done()]

    def shutdown(self) -> None:
        for task in self._pending_tasks:
            if not task.done():
                task.cancel()
        self._pending_tasks.clear()

    async def _fetch_memories(
        self, query: str = "", scope: str = "all", limit: int = 5,
        filter_space: bool = True,
    ) -> list[dict]:
        """Async helper to fetch memories from the database, with Redis cache."""
        from hashlib import sha256

        from src.core.redis import cache_get, cache_set
        from src.services.memory_service import memory_service

        space_filter = self._space_id if filter_space else "__none__"
        cache_key = (
            f"mem:fetch:{self._user_id}:{scope}:{limit}:{space_filter}"
            f":{sha256(query.encode()).hexdigest()[:12]}"
        )

        try:
            cached = await cache_get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            return cached

        result = await memory_service.retrieve(
            query=query,
            user_id=self._user_id,
            scope=scope,
            top_k=limit,
            space_id=self._space_id if filter_space else None,
        )

        try:
            await cache_set(cache_key, result, ttl=30)
        except Exception:
            pass
        return result


# ═══════════════════════════════════════════════════════════════════════
# Memory Manager
# ═══════════════════════════════════════════════════════════════════════


class MemoryManager:
    """Manages active memory providers for an agent session.

    Built-in provider is always active. At most one external provider
    can be active at a time.
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._builtin = BuiltinMemoryProvider(data_dir=data_dir)
        self._external: MemoryProvider | None = None
        self._session_id: str = ""
        self._auto_activate_database()

    @property
    def providers(self) -> list[MemoryProvider]:
        result: list[MemoryProvider] = [self._builtin]
        if self._external:
            result.append(self._external)
        return result

    def set_external(self, provider: MemoryProvider | None) -> None:
        if self._external:
            self._external.shutdown()
        self._external = provider

    def _auto_activate_database(self) -> None:
        """Activate DatabaseMemoryProvider by default."""
        try:
            db_provider = DatabaseMemoryProvider()
            self._external = db_provider
        except Exception:
            logger.exception("Failed to activate DatabaseMemoryProvider")

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._builtin.initialize(session_id, **kwargs)
        if self._external and self._external.is_available():
            self._external.initialize(session_id, **kwargs)

    async def system_prompt_block(self) -> str:
        blocks: list[str] = []
        for p in self.providers:
            try:
                block = await p.system_prompt_block()
                if block:
                    blocks.append(block)
            except Exception:
                logger.exception("Memory provider '%s' system_prompt_block failed", p.name)
        return "\n\n".join(blocks)

    async def prefetch(self, query: str) -> str:
        results: list[str] = []
        for p in self.providers:
            try:
                r = await p.prefetch(query, session_id=self._session_id)
                if r:
                    results.append(r)
            except Exception:
                logger.exception("Memory provider '%s' prefetch failed", p.name)
        return "\n\n".join(results)

    async def sync_turn(self, user_content: str, assistant_content: str) -> None:
        for p in self.providers:
            try:
                await p.sync_turn(user_content, assistant_content, session_id=self._session_id)
            except Exception:
                logger.exception("Memory provider '%s' sync_turn failed", p.name)

    async def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        for p in self.providers:
            try:
                await p.on_session_end(messages)
            except Exception:
                logger.exception("Memory provider '%s' on_session_end failed", p.name)

    def shutdown(self) -> None:
        for p in self.providers:
            try:
                p.shutdown()
            except Exception:
                logger.exception("Memory provider '%s' shutdown failed", p.name)


# ═══════════════════════════════════════════════════════════════════════
# Session-Scoped MemoryManager Cache
# ═══════════════════════════════════════════════════════════════════════

_mm_cache: dict[str, tuple[MemoryManager, float]] = {}
_MM_CACHE_TTL: float = 60.0


def get_memory_manager(
    session_id: str,
    *,
    user_id: str = "",
    platform: str = "web",
    space_id: str = "",
    data_dir: str = "data",
) -> MemoryManager:
    """Get or create a cached MemoryManager for the session.

    Eliminates redundant filesystem reads (MEMORY.md, USER.md) on every
    turn by reusing the manager for 60s. After TTL expires, a fresh
    instance picks up new file/memory changes.
    """
    now = _time.time()
    cached = _mm_cache.get(session_id)
    if cached:
        mm, expires = cached
        if now < expires:
            return mm
        mm.shutdown()

    mm = MemoryManager(data_dir=data_dir)
    mm.initialize(session_id, user_id=user_id, platform=platform, space_id=space_id)
    _mm_cache[session_id] = (mm, now + _MM_CACHE_TTL)
    return mm


def invalidate_memory_manager(session_id: str) -> None:
    """Remove a session from the cache (call on session delete/end)."""
    cached = _mm_cache.pop(session_id, None)
    if cached:
        cached[0].shutdown()
