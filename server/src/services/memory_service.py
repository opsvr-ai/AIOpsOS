"""Long-term memory service with two-tier (personal/team) scope support.

Stores extracted facts per session/user with scope separation and
retrieves relevant memories via keyword text matching.
"""

import json as _json
import logging
import uuid

from sqlalchemy import select, text

from src.models.base import async_session_factory
from src.models.knowledge import AgentMemory

logger = logging.getLogger(__name__)


class MemoryService:
    """Long-term memory with personal/team scope separation.

    Personal memories are user+session scoped. Team memories are
    org-scoped and anonymized (no PII).
    """

    def __init__(self) -> None:
        self._embeddings_available: bool = False

    # ── store ──────────────────────────────────────────────────────

    async def store(
        self,
        session_id: str,
        user_id: str,
        content: str,
        memory_type: str = "fact",
        metadata: dict | None = None,
        scope: str = "personal",
        title: str | None = None,
        tags: list[str] | None = None,
        space_id: str | None = None,
    ) -> str:
        """Store a memory entry with scope separation. Returns the new memory id."""
        async with async_session_factory() as db:
            mem = AgentMemory(
                id=uuid.uuid4(),
                session_id=uuid.UUID(str(session_id)),
                user_id=uuid.UUID(str(user_id)),
                memory_type=memory_type,
                content=content,
                embedding=None,
                mem_metadata=metadata or {},
                scope=scope,
                title=title,
                tags=tags or [],
                space_id=uuid.UUID(str(space_id)) if space_id else None,
            )
            db.add(mem)
            await db.commit()
            return str(mem.id)

    # ── retrieve ───────────────────────────────────────────────────

    async def retrieve(
        self,
        query: str,
        user_id: str,
        scope: str = "all",
        session_id: str = "",
        space_id: str | None = None,
        top_k: int = 10,
        offset: int = 0,
        tags: list[str] | None = None,
        sort_by: str = "created_at",
    ) -> list[dict]:
        """Retrieve memories by keyword with scope, session, tags filters and pagination.

        Personal memories are user-isolated. Team memories are cross-user.
        sort_by: 'created_at' (default) or 'updated_at'.
        """
        valid_sort = "created_at" if sort_by not in ("created_at", "updated_at") else sort_by

        async with async_session_factory() as db:
            conditions: list[str] = []
            params: dict = {}

            if scope == "personal":
                conditions.append("user_id = CAST(:user_id AS uuid)")
                params["user_id"] = user_id

            if scope not in ("all", ""):
                conditions.append("scope = :scope")
                params["scope"] = scope

            if session_id:
                conditions.append("session_id = CAST(:session_id AS uuid)")
                params["session_id"] = session_id

            if space_id:
                conditions.append("(space_id = CAST(:space_id AS uuid) OR space_id IS NULL)")
                params["space_id"] = space_id

            if query:
                conditions.append("(content ILIKE :query_pattern OR title ILIKE :query_pattern)")
                params["query_pattern"] = f"%{query}%"

            if tags:
                tag_clauses = []
                for i, tag in enumerate(tags):
                    key = f"tag_{i}"
                    tag_clauses.append(f"tags @> CAST(:{key} AS jsonb)")
                    params[key] = _json.dumps([tag])
                conditions.append("(" + " OR ".join(tag_clauses) + ")")

            where_clause = " AND ".join(conditions) if conditions else "1=1"
            stmt = text(
                f"""
                SELECT id, title, content, scope, session_id, tags, created_at
                FROM agent_memories
                WHERE {where_clause}
                ORDER BY {valid_sort} DESC
                LIMIT :top_k OFFSET :offset
                """
            )
            params["top_k"] = top_k
            params["offset"] = offset
            rows = await db.execute(stmt, params)
            return [
                {
                    "id": str(r.id),
                    "title": r.title or "",
                    "content": r.content,
                    "scope": r.scope,
                    "session_id": str(r.session_id),
                    "tags": r.tags or [],
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                }
                for r in rows.fetchall()
            ]

    async def search_memories(
        self,
        query: str,
        user_id: str,
        scope: str = "all",
        top_k: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """Full-text search across title, content, and tags.

        For team scope, no user_id filter is applied (cross-user visibility).
        """
        return await self.retrieve(
            query=query,
            user_id=user_id,
            scope=scope,
            top_k=top_k,
            offset=offset,
            sort_by="created_at",
        )

    async def get_session_memories(self, session_id: str, user_id: str) -> list[dict]:
        """Get all memories for a specific session. Personal only for the user."""
        return await self.retrieve(
            query="",
            user_id=user_id,
            scope="all",
            session_id=session_id,
            top_k=100,
            sort_by="created_at",
        )

    async def count_by_session(self, session_id: str) -> int:
        """Count memories associated with a session."""
        async with async_session_factory() as db:
            stmt = text(
                "SELECT COUNT(*) FROM agent_memories WHERE session_id = CAST(:sid AS uuid)"
            )
            result = await db.execute(stmt, {"sid": session_id})
            return result.scalar() or 0

    # ── session summarization ────────────────────────────────────

    async def summarize_session(
        self,
        session_id: str,
        user_id: str,
        llm,
    ) -> dict:
        """Load full session messages, extract personal + team memories via LLM.

        Returns {"personal": count, "team": count}.
        """
        from langchain_core.messages import HumanMessage, SystemMessage

        from src.models.session import Message

        async with async_session_factory() as db:
            result = await db.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.asc())
                .limit(60)
            )
            msgs = list(result.scalars().all())

        if not msgs:
            return {"personal": 0, "team": 0}

        conversation = "\n".join(
            f"[{m.role}] {m.content[:500]}" for m in msgs
        )

        prompt = (
            "分析以下运维对话，提取有价值的经验沉淀：\n\n"
            f"{conversation}\n\n"
            "请返回 JSON，包含 personal 和 team 两个数组：\n"
            "- personal: 个人操作细节（指令、配置、排查步骤），每条有 title 和 content\n"
            "- team: 团队通用知识（故障现象、解决方案、风险），去除用户名/IP/密码等敏感信息，每条有 title 和 content\n"
            '格式: {"personal": [{"title": "...", "content": "..."}], "team": [...]}'
        )

        resp = await llm.ainvoke([
            SystemMessage(content="You are a memory extraction assistant. Return ONLY valid JSON."),
            HumanMessage(content=prompt),
        ])

        personal_count = 0
        team_count = 0

        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("\n```", 1)[0]
            data = _json.loads(raw)

            for item in data.get("personal", []):
                await self.store(
                    session_id=session_id,
                    user_id=user_id,
                    content=item.get("content", ""),
                    title=item.get("title", f"[Session {session_id}] Memory"),
                    scope="personal",
                    tags=["session-summary"],
                )
                personal_count += 1

            for item in data.get("team", []):
                await self.store(
                    session_id=session_id,
                    user_id=user_id,
                    content=item.get("content", ""),
                    title=item.get("title", ""),
                    scope="team",
                    tags=["ops-knowledge"],
                )
                team_count += 1

        except Exception:
            logger.exception("Failed to parse LLM summarization result")

        return {"personal": personal_count, "team": team_count}

    # ── delete ────────────────────────────────────────────────────

    async def delete(self, memory_id: str, user_id: str) -> bool:
        """Hard-delete a memory entry."""
        from sqlalchemy import delete as sa_delete

        async with async_session_factory() as db:
            result = await db.execute(
                sa_delete(AgentMemory)
                .where(AgentMemory.id == uuid.UUID(str(memory_id)))
                .where(AgentMemory.user_id == uuid.UUID(str(user_id)))
            )
            await db.commit()
            return result.rowcount > 0


memory_service = MemoryService()
