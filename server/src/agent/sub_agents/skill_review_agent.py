"""Skill Review Agent — background sub-agent for extracting reusable skills
from session conversation history.

Invoked when a session reaches ``skill_review_due=true`` (after every
``REVIEW_INTERVAL_TURNS``). Analyses the conversation for
repeatable workflows and, where warranted, files a skill *candidate*.

Propose-only (task 21.6 / R-3.10)
--------------------------------

Prior versions of this agent called
:func:`src.agent.tools.skill_manage_tool._create_skill` directly, which
immediately registered the skill in the ``tools`` table, wrote to
``data/skills/<name>/SKILL.md`` and hot-reloaded the tool manager — in
other words, a fresh LLM-generated skill went live without any
evaluation or promotion gate.

R-3.10 requires that skills originating from this agent go through the
same evolution pipeline as skills synthesised by the reflection
worker: the agent now writes a ``status='proposed'`` row to
``skill_candidates`` via :class:`~src.services.evolution.candidate_store.SkillCandidateStore`,
tagging ``proposal_source='skill_review_agent'``. The candidate file
lands under ``data/skills/.candidate/<name>/SKILL.md`` — never the
active skills tree — and nothing is inserted into the ``tools`` table.
The Promoter (task 23.x) picks the candidate up along the normal
``proposed → shadow → ab → active`` path.

The review flow stays non-blocking fire-and-forget: the consolidation
pipeline schedules the task, this agent runs, and the session's
``skill_review_due`` flag is reset regardless of whether any candidate
was generated.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select, update

from src.models.base import async_session_factory
from src.models.session import Message, Session
from src.services.evolution.candidate_store import SkillCandidateStore
from src.services.evolution.reflection_logic import CandidateProposal

logger = logging.getLogger(__name__)

# Stable label surfaced on every ``skill_candidates.proposal_source``
# row this agent writes. The Promoter / observability queries filter
# by this string to distinguish skill_review_agent-originated
# candidates from the reflection worker's (R-3.10).
PROPOSAL_SOURCE = "skill_review_agent"

# How often a session becomes eligible for skill review. Incremented in
# ``_increment_turn`` in the /chat router; when ``turn_count`` reaches
# this threshold, ``skill_review_due`` flips true so the consolidation /
# review pipeline picks the session up on its next pass.
REVIEW_INTERVAL_TURNS: int = 15

# Synthetic cluster name stamped on every proposal. Skill candidates
# from this agent aren't tied to a failure cluster (the reflection
# worker's concept), so we use a fixed tag so downstream consumers can
# still attribute the candidate back to its origin.
_CLUSTER_NAME = "skill_review_agent"

# Minimum skill_prompt length the candidate store accepts. Matches
# ``_SKILL_PROMPT_MIN_LEN`` in reflection_logic; duplicated here as a
# constant so we can short-circuit LLM output that would otherwise
# fail pydantic validation deeper in the store.
_MIN_SKILL_PROMPT_LEN = 50

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
    """Analyse session conversations and propose reusable skill candidates.

    The agent never activates a skill directly (R-3.10). Each
    LLM-identified pattern is materialised as a
    ``skill_candidates(status='proposed', proposal_source='skill_review_agent')``
    row with its SKILL.md in ``data/skills/.candidate/<name>/``.
    """

    def __init__(
        self,
        model: Any | None = None,
        *,
        candidate_store: SkillCandidateStore | None = None,
    ) -> None:
        self._llm = model
        # Injectable so tests can point the agent at an in-memory DB /
        # tmp_path without monkey-patching module-level state.
        self._candidate_store = candidate_store

    async def _get_llm(self) -> Any:
        if self._llm is None:
            from src.core.model_factory import get_default_model

            self._llm = await get_default_model()
        return self._llm

    def _get_candidate_store(self) -> SkillCandidateStore:
        if self._candidate_store is None:
            self._candidate_store = SkillCandidateStore()
        return self._candidate_store

    async def review(self, session_id: str) -> dict[str, Any]:
        """Review one session and propose zero or more skill candidates.

        Returns a small summary dict::

            {"skills_proposed": <int>, "summary": <str>}

        The session's ``skill_review_due`` flag is reset even when no
        candidate is produced (LLM empty output, no messages, etc.)
        so downstream schedulers don't re-trigger the same review on
        every tick.
        """
        conversation = await self._load_messages(session_id)
        if not conversation:
            logger.info(
                "SkillReview: session %s has no messages, skipping", session_id
            )
            await self._reset_review_flag(session_id)
            return {"skills_proposed": 0, "summary": "no messages"}

        suggestions = await self._identify_skills(conversation)
        if not suggestions:
            await self._reset_review_flag(session_id)
            return {"skills_proposed": 0, "summary": "no patterns identified"}

        proposed = 0
        store = self._get_candidate_store()
        for sk in suggestions:
            proposal = self._build_proposal(sk)
            if proposal is None:
                # Skipped: LLM output missing required fields. We
                # don't count this as a proposal — the LLM wasted a
                # slot but produced nothing usable.
                continue
            try:
                persisted = await store.propose(
                    proposal, proposal_source=PROPOSAL_SOURCE
                )
            except Exception:
                logger.exception(
                    "SkillReview: failed to persist candidate %r for session %s",
                    proposal.name,
                    session_id,
                )
                continue

            proposed += 1
            logger.info(
                "SkillReview: proposed skill candidate '%s' "
                "(row=%s, md=%s) from session %s",
                persisted.name,
                persisted.row_id,
                persisted.artifact_path,
                session_id,
            )

        await self._reset_review_flag(session_id)
        summary = f"reviewed session, proposed {proposed} skill candidates"
        logger.info("SkillReview: session %s complete — %s", session_id, summary)
        return {"skills_proposed": proposed, "summary": summary}

    @staticmethod
    def _build_proposal(sk: dict[str, Any]) -> CandidateProposal | None:
        """Coerce one LLM-suggested skill into a :class:`CandidateProposal`.

        Returns ``None`` (and logs) when a required field is missing
        or the ``skill_prompt`` is too short to satisfy the
        ``SkillCandidateStore`` schema. Length / content validation
        lives in ``_SkillCandidateData`` (reflection_logic) — we just
        short-circuit here so we can skip without blowing up with a
        pydantic ValidationError in the persistence path.
        """
        name = str(sk.get("name") or "").strip()
        description = str(sk.get("description") or "").strip()
        skill_prompt = str(sk.get("skill_prompt") or "").strip()
        if not name or not description:
            logger.warning(
                "SkillReview: dropping suggestion missing name/description: %r", sk
            )
            return None
        if len(skill_prompt) < _MIN_SKILL_PROMPT_LEN:
            logger.warning(
                "SkillReview: dropping suggestion %r — skill_prompt too short (%d chars)",
                name,
                len(skill_prompt),
            )
            return None

        tags = sk.get("tags") or []
        if not isinstance(tags, list):
            tags = []

        data: dict[str, Any] = {
            "skill_prompt": skill_prompt,
            "description": description,
            "tags": [str(t) for t in tags if str(t).strip()],
            "tool_names": [],
        }
        # ``category`` isn't part of the candidate schema today, but
        # we surface it inside ``data`` so the Promoter / evaluator
        # can read it back verbatim. Kept separate from ``tags`` so
        # downstream filters don't have to disambiguate.
        category = str(sk.get("category") or "").strip()
        if category:
            data["category"] = category

        return CandidateProposal(
            kind="skill",
            name=name,
            data=data,
            expected_improvement=(
                f"capture reusable skill pattern from session review: {description}"
            )[:2000],
            cluster_name=_CLUSTER_NAME,
            origin_trajectory_ids=[],
        )

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
            skills = data.get("skills", [])
            if not isinstance(skills, list):
                return []
            # Filter out non-dict entries defensively — the LLM can
            # occasionally return a string when it thinks the skill
            # is too trivial to encode.
            return [s for s in skills if isinstance(s, dict)]
        except Exception:
            logger.exception("SkillReview: failed to parse LLM response")
            return []

    @staticmethod
    async def _reset_review_flag(session_id: str) -> None:
        async with async_session_factory() as db:
            await db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(skill_review_due=False)
            )
            await db.commit()
