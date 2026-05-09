"""Memory Consolidation Agent — independent sub-agent for extracting personal
and team memories from session conversation history.

Triggered by:
  - SleepScheduler / ConsolidationWorker (auto: session idle + auto_consolidate=true)
  - Sleep management API  (manual: POST /sleep-management/sessions/{id}/consolidate)
  - Session end lifecycle    (on_session_end hook)
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, update

from src.models.base import async_session_factory
from src.models.session import Message, Session
from src.services.memory_service import memory_service

logger = logging.getLogger(__name__)

MEMORY_CONSOLIDATION_SYSTEM_PROMPT = """你是运维知识的记录者，从对话中提炼有价值的操作经验和决策信息，区分沉淀为【个人记忆】与【组织记忆】。

## 记忆区分标准

### 个人记忆
- 记录用户的操作行为、决策偏好、配置习惯
- 保留关键指令、参数选择、工作流步骤等个性化信息
- 适合个人长期复盘查阅
- 每条记忆必须包含 title（标题）和 content（内容）

### 组织记忆
- 提炼通用的工作流程、工具使用模式、问题解决思路
- **严格去除**：用户名、IP地址、密码、Token、API Key、个人邮箱、手机号、身份证号等敏感信息
- 适配团队全员参考
- 每条记忆必须包含 title（标题）和 content（内容）

## 值得沉淀的内容
- 创建/配置的任务、工作流及其参数（如定时任务的名称、频率、目标）
- 工具使用方式和效果
- 故障排查过程与解决方案
- 配置优化和调整记录
- 用户表达的需求和偏好
- 有效的操作命令和参数组合
- 踩坑教训和注意事项

## 可以忽略的内容
- 纯粹的问候和客套话
- 助手自我介绍和功能列表
- 无操作含义的测试内容

## 输出格式
- 严格返回 JSON，格式为 {"personal": [...], "team": [...]}
- 每个元素：{"title": "简洁标题（≤30字）", "content": "清晰的具体内容"}
- 如果确实没有值得记录的内容，返回 {"personal": [], "team": []}
- **尽量提取**：只要包含具体的操作、配置、决策或工具使用，就值得记录
"""

MIN_CONTENT_LENGTH = 15


class MemoryConsolidationAgent:
    """Extracts dual-scope memories from a session and marks it consolidated."""

    def __init__(self, model=None) -> None:
        """Optionally accept a pre-built model. If not given, uses model_factory."""
        self._llm = model

    async def _get_llm(self):
        if self._llm is None:
            from src.core.model_factory import get_default_model
            self._llm = await get_default_model()
        return self._llm

    @staticmethod
    async def _get_session_space(session_id: str) -> str | None:
        """Read the space_id from the session, if any."""
        from src.models.session import Session
        async with async_session_factory() as db2:
            result = await db2.execute(
                select(Session.space_id).where(Session.id == session_id)
            )
            row = result.one_or_none()
            return str(row[0]) if row and row[0] else None

    async def consolidate(self, session_id: str, user_id: str) -> dict[str, int]:
        """Read session messages, extract memories, store to DB, mark consolidated.

        Only marks the session as *consolidated* when at least one memory was
        actually stored.  Otherwise leaves the session unconsolidated so the
        sleep detector will retry after the conversation grows longer.

        Returns {"personal": count, "team": count}.
        """
        space_id = await self._get_session_space(session_id)
        messages = await self._load_messages(session_id)
        if not messages:
            logger.info("Session %s has no messages, skipping consolidation", session_id)
            return {"personal": 0, "team": 0}

        data = await self._extract_memories(messages)
        personal_items = self._basic_filter(data.get("personal", []))
        team_items = self._basic_filter(data.get("team", []))
        logger.info(
            "Session %s extraction: LLM returned %d personal / %d team candidates; "
            "after basic filter: %d personal / %d team",
            session_id,
            len(data.get("personal", [])), len(data.get("team", [])),
            len(personal_items), len(team_items),
        )
        personal_count = await self._store_memories(
            personal_items, session_id, user_id, scope="personal", space_id=space_id,
        )
        team_count = await self._store_memories(
            team_items, session_id, user_id, scope="team", space_id=space_id,
        )

        await self._mark_consolidated(session_id)
        if personal_count > 0 or team_count > 0:
            logger.info(
                "Session %s consolidated: %d personal, %d team memories",
                session_id, personal_count, team_count,
            )
        else:
            logger.info(
                "Session %s: no memories extracted, marked consolidated (will retry after wake)",
                session_id,
            )

        return {"personal": personal_count, "team": team_count}

    async def _load_messages(self, session_id: str) -> str:
        """Load all messages for a session, return formatted conversation text."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.asc())
                .limit(80)
            )
            msgs = list(result.scalars().all())

        if not msgs:
            return ""

        return "\n".join(
            f"[{m.role}] {m.content[:600]}" for m in msgs
        )

    async def _extract_memories(self, conversation: str) -> dict[str, Any]:
        """Call LLM to extract personal + team memories from conversation text."""
        llm = await self._get_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=MEMORY_CONSOLIDATION_SYSTEM_PROMPT),
            HumanMessage(
                content=f"请分析以下运维对话，提取有价值的经验：\n\n{conversation}\n\n"
                        "请严格返回 JSON，包含 personal 和 team 两个数组。"
            ),
        ])

        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("\n```", 1)[0]
            data = _json.loads(raw)
            logger.info("LLM extraction result: personal=%d team=%d",
                       len(data.get("personal", [])), len(data.get("team", [])))
            return data
        except Exception:
            logger.exception("Failed to parse LLM consolidation result: %s",
                           resp.content[:300] if hasattr(resp, 'content') else 'N/A')
            return {"personal": [], "team": []}

    @staticmethod
    def _basic_filter(items: list[dict[str, str]]) -> list[dict[str, str]]:
        """Discard items that are too short or have empty title/content."""
        result: list[dict[str, str]] = []
        for item in items:
            title = item.get("title", "").strip()
            content = item.get("content", "").strip()
            if not title or not content:
                continue
            if len(content) < MIN_CONTENT_LENGTH:
                continue
            result.append({"title": title, "content": content})
        return result

    async def _filter_valuable(
        self,
        personal: list[dict[str, str]],
        team: list[dict[str, str]],
        conversation: str,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """LLM-based value assessment: score each candidate, discard low-value ones.

        Returns (filtered_personal, filtered_team).
        """
        if not personal and not team:
            return [], []

        candidates_json = _json.dumps(
            {"personal": personal, "team": team}, ensure_ascii=False, indent=2,
        )
        llm = await self._get_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=(
                "你是一个运维知识价值评估器。评估每条候选记忆是否具备长期沉淀价值。\n\n"
                "**有价值**（保留）：包含具体故障现象/解决方案/操作命令/配置要点/踩坑教训/排查流程\n"
                "**无价值**（丢弃）：泛泛而谈、无具体细节、单纯信息查询记录、自我介绍、功能问询\n\n"
                "返回 JSON 格式：{\"personal\": [...], \"team\": [...]}\n"
                "只返回有价值的条目，无价值的直接移除。宁缺毋滥。"
            )),
            HumanMessage(content=(
                f"原始对话摘要：\n{conversation[:1200]}\n\n"
                f"候选记忆：\n{candidates_json}\n\n"
                "请评估每条候选记忆的价值，只返回值得保留的条目。"
            )),
        ])

        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("\n```", 1)[0]
            result = _json.loads(raw)
            filtered_personal = result.get("personal", [])
            filtered_team = result.get("team", [])
            logger.info(
                "Value filter: personal %d→%d, team %d→%d",
                len(personal), len(filtered_personal),
                len(team), len(filtered_team),
            )
            return filtered_personal, filtered_team
        except Exception:
            logger.exception("Value assessment failed, returning unfiltered")
            return personal, team

    async def _store_memories(
        self,
        items: list[dict[str, str]],
        session_id: str,
        user_id: str,
        scope: str,
        space_id: str | None = None,
    ) -> int:
        """Store extracted memories to the database. Returns count stored."""
        count = 0
        for item in items:
            title = item.get("title", "").strip()
            content = item.get("content", "").strip()
            if not title or not content:
                continue
            try:
                await memory_service.store(
                    session_id=session_id,
                    user_id=user_id,
                    content=content,
                    title=title,
                    scope=scope,
                    space_id=space_id,
                    tags=["auto-consolidated"] if scope == "personal"
                    else ["auto-consolidated", "ops-knowledge"],
                )
                count += 1
            except Exception:
                logger.exception("Failed to store %s memory for session %s", scope, session_id)
        return count

    @staticmethod
    async def _mark_consolidated(session_id: str) -> None:
        """Mark a session's memory_status as 'consolidated'."""
        async with async_session_factory() as db:
            await db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(memory_status="consolidated")
            )
            await db.commit()
