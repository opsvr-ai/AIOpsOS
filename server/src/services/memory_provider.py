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
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


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
        self._pending_tasks: list[_asyncio.Task] = []

    @property
    def name(self) -> str:
        return "database"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = session_id
        self._user_id = str(kwargs.get("user_id", ""))

    async def system_prompt_block(self) -> str:
        """Return recent personal + team memories with XML context fencing."""
        try:
            personal = await self._fetch_memories(scope="personal", limit=5)
            team = await self._fetch_memories(scope="team", limit=5)
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
        """Search personal + team memories relevant to query."""
        if not query:
            return ""

        try:
            results = await self._fetch_memories(query=query, scope="all", limit=8)
        except Exception:
            logger.debug("prefetch query failed", exc_info=True)
            return ""

        if not results:
            return ""

        lines = ["<memory-context>", "## 相关记忆\n"]
        for m in results:
            title = m.get("title", "") or m.get("content", "")[:60]
            scope_label = "个人" if m.get("scope") == "personal" else "团队"
            lines.append(f"- [{scope_label}] {title}")
        lines.append("</memory-context>")
        return "\n".join(lines)

    async def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """LLM-based per-turn memory extraction for both personal and team scope.

        Runs as a background task — never blocks the chat response.
        """
        import json as _json

        sid = session_id or self._session_id
        uid = self._user_id

        async def _extract():
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage
            from src.config import settings
            from src.services.memory_service import memory_service

            llm = ChatOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model="deepseek-v4-flash",
                temperature=0.3,
            )

            prompt = (
                "从以下运维对话中提取有价值的经验，区分个人记忆和团队记忆：\n\n"
                f"用户：{user_content[:500]}\n"
                f"助手：{assistant_content[:800]}\n\n"
                "返回JSON，包含personal和team两个数组。\n"
                "- personal: 个人操作细节（指令、配置、排查步骤），每条有title和content\n"
                "- team: 团队通用知识（故障现象、解决方案、风险），去除用户名/IP/密码等敏感信息\n"
                '格式: {"personal": [{"title": "...", "content": "..."}], "team": [...]}\n'
                "如果无有价值内容，返回空数组。只返回JSON。"
            )

            try:
                resp = await llm.ainvoke([
                    SystemMessage(content="你是运维经验提取助手。只返回JSON。"),
                    HumanMessage(content=prompt),
                ])

                raw = resp.content.strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1].rsplit("\n```", 1)[0]

                data = _json.loads(raw)

                for item in data.get("personal", []):
                    await memory_service.store(
                        session_id=sid, user_id=uid,
                        content=item.get("content", ""),
                        title=item.get("title", f"[Session] Memory"),
                        scope="personal", tags=["per-turn"],
                    )

                for item in data.get("team", []):
                    await memory_service.store(
                        session_id=sid, user_id=uid,
                        content=item.get("content", ""),
                        title=item.get("title", ""),
                        scope="team", tags=["ops-knowledge", "per-turn"],
                    )
            except Exception:
                logger.debug("Per-turn LLM extraction failed", exc_info=True)

        task = _asyncio.create_task(_extract())
        self._pending_tasks.append(task)
        self._pending_tasks = [t for t in self._pending_tasks if not t.done()]

    async def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Heavy session summarization via LLM. Runs on session close."""
        if not messages:
            return

        sid = self._session_id
        uid = self._user_id

        async def _summarize():
            from langchain_openai import ChatOpenAI
            from src.config import settings
            from src.services.memory_service import memory_service

            llm = ChatOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model="deepseek-v4-flash",
                temperature=0.3,
            )
            await memory_service.summarize_session(sid, uid, llm)

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
        self, query: str = "", scope: str = "all", limit: int = 5
    ) -> list[dict]:
        """Async helper to fetch memories from the database."""
        from src.services.memory_service import memory_service

        return await memory_service.retrieve(
            query=query,
            user_id=self._user_id,
            scope=scope,
            session_id=self._session_id,
            top_k=limit,
        )


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
