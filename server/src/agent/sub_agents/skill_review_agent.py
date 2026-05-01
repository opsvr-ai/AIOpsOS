"""Skill Review Agent — background sub-agent for extracting reusable skills
from session conversation history.

Triggered by SleepDetector when a session reaches skill_review_due=true
(after every REVIEW_INTERVAL_TURNS). Analyzes the conversation for
repeatable workflows and creates skills via skill_manage.

Follows the Hermes pattern: non-blocking fire-and-forget background
review that creates skills from accumulated operational experience.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, update

from src.models.base import async_session_factory
from src.models.session import Message, Session

logger = logging.getLogger(__name__)

SKILL_REVIEW_SYSTEM_PROMPT = """你是技能锻造师，从运维对话中识别可复用的操作模式，将其铸造成标准化技能。

## 技能识别标准

**值得创建技能的模式**（满足 3 条以上即可）：
1. 涉及 3 个以上步骤的完整操作流程
2. 有明确的输入/输出（接受某类问题，产出解决方案）
3. 需要特定工具组合才能完成
4. 操作步骤具有通用性，不同场景可复用
5. 涉及专业领域知识（数据库、容器、网络、监控等）
6. 有明确的成功标准和验证方法

**不应创建技能**的内容：
- 单次信息查询（查日志、看状态）
- 纯闲聊或问题澄清
- 过于简单的单步操作
- 只对特定机器/环境有效的操作
- 没有明确流程的随意探索

## 输出格式
严格返回 JSON：
```json
{
  "skills": [
    {
      "name": "skill-name",
      "description": "一句话描述技能用途",
      "skill_prompt": "完整的技能系统提示，包含角色定位、工作流程、工具使用指南、输出规范",
      "category": "分类标签",
      "tags": ["tag1", "tag2"]
    }
  ],
  "summary": "简要说明识别结果"
}
```

## 命名规范
- name: 小写字母+连字符，如 postgresql-health-check, docker-log-analysis
- 名称体现技能的核心功能，简洁易理解

## skill_prompt 编写指南
- 开头：角色定位（你是什么角色，负责什么）
- 中间：详细工作流程（步骤 1/2/3...）
- 工具使用：列出需要的工具及用法
- 结尾：输出规范和约束条件
- 用中文编写
- 长度在 200-800 字之间

## 约束
- 宁缺毋滥：不确定是否值得的技能，放弃
- 每次最多创建 2 个技能
- skill_prompt 要具体可操作，不要泛泛而谈
"""


class SkillReviewAgent:
    """Analyzes session conversations and creates reusable skills from patterns."""

    def __init__(self, model=None) -> None:
        self._llm = model

    async def _get_llm(self):
        if self._llm is None:
            from src.core.model_factory import get_default_model

            self._llm = await get_default_model()
        return self._llm

    async def review(self, session_id: str) -> dict:
        """Load session messages, identify skill patterns, create skills.

        Returns {"skills_created": count, "summary": str}.
        """
        conversation = await self._load_messages(session_id)
        if not conversation:
            logger.info("SkillReview: session %s has no messages, skipping", session_id)
            return {"skills_created": 0, "summary": "no messages"}

        suggestions = await self._identify_skills(conversation)
        if not suggestions:
            await self._reset_review_flag(session_id)
            return {"skills_created": 0, "summary": "no patterns identified"}

        created = 0
        for sk in suggestions:
            try:
                result = await self._create_skill_internal(
                    name=sk["name"],
                    description=sk["description"],
                    skill_prompt=sk["skill_prompt"],
                    category=sk.get("category", ""),
                    tags=sk.get("tags", []),
                )
                if result.get("ok"):
                    created += 1
                    logger.info(
                        "SkillReview: created skill '%s' from session %s",
                        sk["name"], session_id,
                    )
                else:
                    logger.warning(
                        "SkillReview: failed to create '%s': %s",
                        sk["name"], result.get("error"),
                    )
            except Exception:
                logger.exception(
                    "SkillReview: exception creating skill '%s'", sk.get("name"),
                )

        await self._reset_review_flag(session_id)
        summary = f"reviewed session, created {created} skills"
        logger.info("SkillReview: session %s complete — %s", session_id, summary)
        return {"skills_created": created, "summary": summary}

    async def _load_messages(self, session_id: str) -> str:
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

    async def _identify_skills(self, conversation: str) -> list[dict[str, Any]]:
        llm = await self._get_llm()
        resp = await llm.ainvoke([
            SystemMessage(content=SKILL_REVIEW_SYSTEM_PROMPT),
            HumanMessage(
                content=f"请分析以下运维对话，识别可复用的技能模式：\n\n{conversation}\n\n"
                        "请严格返回 JSON，包含 skills 数组和 summary 字段。"
            ),
        ])

        try:
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("\n```", 1)[0]
            data = _json.loads(raw)
            return data.get("skills", [])
        except Exception:
            logger.exception("SkillReview: failed to parse LLM response")
            return []

    @staticmethod
    async def _create_skill_internal(
        name: str,
        description: str,
        skill_prompt: str,
        category: str = "",
        tags: list[str] | None = None,
    ) -> dict:
        """Create a skill directly, bypassing the LangChain tool wrapper."""
        from src.agent.tools.skill_manage_tool import _create_skill

        result_json = await _create_skill(
            name=name,
            description=description,
            skill_prompt=skill_prompt,
            category=category,
            tags=tags or [],
        )
        return _json.loads(result_json)

    @staticmethod
    async def _reset_review_flag(session_id: str) -> None:
        async with async_session_factory() as db:
            await db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(skill_review_due=False)
            )
            await db.commit()
