"""Recommendation Engine service for collaboration sessions.

This module provides intelligent recommendation generation for collaboration
sessions based on progress analysis results, scenario context, and knowledge
base integration using LLM.

Requirements:
- 11.1: Generate next-step recommendations based on progress analysis results
- 11.2: Combine with associated knowledge base documents to provide reference information
- 11.3: Provide targeted recommendations based on scenario type and template
- 11.4: Provide priority and estimated impact for each recommendation
"""

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.agent import Scenario
from src.models.collaboration import (
    CollaborationRecommendation,
    CollaborationSession,
)

logger = logging.getLogger(__name__)


class RecommendationType(str, Enum):  # noqa: UP042
    """Types of recommendations that can be generated.

    These types help categorize recommendations for better organization
    and filtering in the UI.
    """
    ACTION = "action"  # Specific action to take
    INVESTIGATION = "investigation"  # Further investigation needed
    ESCALATION = "escalation"  # Escalate to higher level
    VERIFICATION = "verification"  # Verify or validate something
    COMMUNICATION = "communication"  # Communication-related recommendation
    DOCUMENTATION = "documentation"  # Documentation or record-keeping
    PREVENTION = "prevention"  # Preventive measures for future


class RecommendationPriority(int, Enum):  # noqa: UP042
    """Priority levels for recommendations.

    Requirements:
    - 11.4: Provide priority for each recommendation
    """
    LOW = 0
    MEDIUM = 1
    HIGH = 2


@dataclass
class RecommendationItem:
    """A single recommendation item.

    Attributes:
        content: The recommendation text content
        recommendation_type: Type of recommendation (action, investigation, etc.)
        priority: Priority level (0=low, 1=medium, 2=high)
        estimated_impact: Description of expected impact if adopted
        reference_docs: List of referenced knowledge documents
        rationale: Explanation of why this recommendation is made
        confidence: Confidence score (0.0-1.0)

    Requirements:
    - 11.1: Generate next-step recommendations
    - 11.2: Include reference documents from knowledge base
    - 11.4: Provide priority and estimated impact
    """
    content: str
    recommendation_type: RecommendationType = RecommendationType.ACTION
    priority: int = RecommendationPriority.MEDIUM.value
    estimated_impact: str | None = None
    reference_docs: list[dict[str, str]] = field(default_factory=list)
    rationale: str | None = None
    confidence: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "content": self.content,
            "recommendation_type": self.recommendation_type.value if isinstance(
                self.recommendation_type, RecommendationType
            ) else self.recommendation_type,
            "priority": self.priority,
            "estimated_impact": self.estimated_impact,
            "reference_docs": self.reference_docs,
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


@dataclass
class RecommendationResult:
    """Result of recommendation generation.

    Contains all generated recommendations along with metadata about
    the generation process.

    Attributes:
        session_id: The collaboration session ID
        recommendations: List of generated recommendations
        generation_timestamp: When recommendations were generated
        based_on_phase: The phase that recommendations are based on
        raw_llm_output: Raw output from LLM (for debugging)
        error: Error message if generation failed
    """
    session_id: uuid.UUID
    recommendations: list[RecommendationItem] = field(default_factory=list)
    generation_timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    based_on_phase: str | None = None
    raw_llm_output: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "session_id": str(self.session_id),
            "recommendations": [r.to_dict() for r in self.recommendations],
            "generation_timestamp": self.generation_timestamp,
            "based_on_phase": self.based_on_phase,
            "error": self.error,
        }


# System prompt for LLM-based recommendation generation
RECOMMENDATION_SYSTEM_PROMPT = """你是一位资深的运维专家和应急响应顾问。
你的任务是基于当前的协同会话进度分析结果，生成下一步操作建议，帮助团队高效解决问题。

## 建议生成原则

### 1. 建议类型

根据当前阶段和情况，生成以下类型的建议：

**action（操作建议）**：
- 具体的操作步骤，如重启服务、修改配置、执行脚本等
- 应该是可执行的、明确的操作
- 适用于 resolution 阶段

**investigation（调查建议）**：
- 进一步排查的方向和方法
- 需要收集的信息或日志
- 适用于 investigation 和 diagnosis 阶段

**escalation（升级建议）**：
- 何时需要升级处理
- 应该通知哪些人员或团队
- 适用于问题复杂或超出当前团队能力时

**verification（验证建议）**：
- 如何验证修复效果
- 需要检查的指标和系统
- 适用于 verification 阶段

**communication（沟通建议）**：
- 需要同步的信息和对象
- 状态更新的内容和频率
- 适用于整个处理过程

**documentation（文档建议）**：
- 需要记录的信息
- 事后复盘的要点
- 适用于 verification 和 completed 阶段

**prevention（预防建议）**：
- 防止问题再次发生的措施
- 系统改进建议
- 适用于问题解决后

### 2. 优先级判断

**high（高优先级）**：
- 直接影响问题解决的关键操作
- 时间敏感的操作
- 可能导致问题恶化的风险缓解措施

**medium（中优先级）**：
- 有助于问题解决但非关键的操作
- 信息收集和验证操作
- 常规的沟通和同步

**low（低优先级）**：
- 可以延后处理的操作
- 文档记录和复盘
- 长期改进措施

### 3. 影响评估

为每条建议提供预估影响，包括：
- 对问题解决的直接影响
- 执行所需的时间和资源
- 可能的风险和副作用

### 4. 阶段针对性建议

根据当前阶段提供针对性建议：

**created/investigation 阶段**：
- 重点：快速确认问题范围和影响
- 建议类型：investigation, communication
- 优先级：高优先级的信息收集

**diagnosis 阶段**：
- 重点：定位根因，制定方案
- 建议类型：investigation, action (方案制定)
- 优先级：根因分析为高优先级

**resolution 阶段**：
- 重点：执行修复操作
- 建议类型：action, verification
- 优先级：修复操作为高优先级

**verification 阶段**：
- 重点：验证修复效果
- 建议类型：verification, communication, documentation
- 优先级：验证操作为高优先级

**completed 阶段**：
- 重点：总结和预防
- 建议类型：documentation, prevention, communication
- 优先级：文档记录为中优先级

## 输出格式

请以 JSON 格式输出建议列表：
```json
{
  "recommendations": [
    {
      "content": "建议的具体内容，应该清晰、可执行",
      "recommendation_type": "action|investigation|escalation|...",
      "priority": 0|1|2,
      "estimated_impact": "预估影响的描述",
      "rationale": "为什么提出这个建议的原因",
      "confidence": 0.9
    }
  ],
  "summary": "一句话总结当前最重要的下一步"
}
```

请只输出 JSON，不要包含其他文字。确保 JSON 格式正确，可以被解析。
建议数量控制在 3-5 条，按优先级排序。"""


# Template-specific recommendation hints
TEMPLATE_RECOMMENDATION_HINTS: dict[str, dict[str, Any]] = {
    "fault_isolation": {
        "focus_areas": ["故障定界", "影响范围确认", "根因定位"],
        "key_actions": [
            "检查相关服务的健康状态",
            "分析错误日志和监控指标",
            "确认故障边界和影响范围",
            "排查网络、存储、计算资源",
        ],
        "escalation_triggers": ["影响范围扩大", "无法定位根因", "需要跨团队协作"],
    },
    "health_inspection": {
        "focus_areas": ["系统健康检查", "性能指标分析", "容量评估"],
        "key_actions": [
            "检查关键服务的运行状态",
            "分析性能指标趋势",
            "评估资源使用情况",
            "识别潜在风险点",
        ],
        "escalation_triggers": ["发现严重问题", "需要紧急处理", "资源即将耗尽"],
    },
    "capacity_prediction": {
        "focus_areas": ["容量趋势分析", "资源规划", "扩容建议"],
        "key_actions": [
            "分析历史容量数据",
            "预测未来资源需求",
            "制定扩容计划",
            "评估成本影响",
        ],
        "escalation_triggers": ["容量即将不足", "需要紧急扩容", "预算超出"],
    },
    "alert_analysis": {
        "focus_areas": ["告警分析", "告警关联", "根因追溯"],
        "key_actions": [
            "分析告警模式和趋势",
            "关联相关告警事件",
            "追溯告警根因",
            "评估告警规则有效性",
        ],
        "escalation_triggers": ["告警风暴", "关键告警未响应", "告警规则失效"],
    },
}


class RecommendationEngine:
    """Service for generating intelligent recommendations for collaboration sessions.

    This service generates next-step recommendations based on progress analysis
    results, scenario context, and knowledge base integration using LLM.

    Features:
    - Generate recommendations based on progress analysis results
    - Integrate with knowledge base for reference information
    - Provide targeted recommendations based on scenario type and template
    - Include priority and estimated impact for each recommendation
    - Support different recommendation types (action, investigation, escalation, etc.)

    Requirements:
    - 11.1: Generate next-step recommendations based on progress analysis results
    - 11.2: Combine with associated knowledge base documents to provide reference information
    - 11.3: Provide targeted recommendations based on scenario type and template
    - 11.4: Provide priority and estimated impact for each recommendation

    Example:
        ```python
        async with async_session_factory() as db:
            engine = RecommendationEngine(db)
            result = await engine.generate_recommendations(
                session_id=session_id,
                progress_analysis=progress_result,
            )
            for rec in result.recommendations:
                print(f"[{rec.priority}] {rec.content}")
        ```
    """

    def __init__(
        self,
        db: AsyncSession,
        llm: Any | None = None,
    ) -> None:
        """Initialize the RecommendationEngine.

        Args:
            db: Async database session for persistence operations.
            llm: Optional pre-configured LLM instance. If not provided,
                the default model will be loaded lazily.
        """
        self._db = db
        self._llm = llm

    async def _get_llm(self) -> Any:
        """Get or create the LLM instance.

        Lazily initializes the LLM from the platform's configured ModelProvider.

        Returns:
            The LLM instance for recommendation generation.
        """
        if self._llm is None:
            from src.core.model_factory import get_default_model
            self._llm = await get_default_model()
        return self._llm

    async def generate_recommendations(
        self,
        session_id: uuid.UUID,
        progress_analysis: dict[str, Any] | None = None,
        save_to_db: bool = True,
    ) -> RecommendationResult:
        """Generate recommendations for a collaboration session.

        Generates intelligent recommendations based on the current progress
        analysis, scenario context, and knowledge base documents.

        Args:
            session_id: The ID of the collaboration session.
            progress_analysis: Optional progress analysis result. If not provided,
                will use the session's current progress_summary.
            save_to_db: Whether to save recommendations to the database.
                Defaults to True.

        Returns:
            RecommendationResult containing generated recommendations.

        Raises:
            ValueError: If the session is not found.

        Requirements:
        - 11.1: Generate next-step recommendations based on progress analysis results
        - 11.2: Combine with associated knowledge base documents
        - 11.3: Provide targeted recommendations based on scenario type and template
        - 11.4: Provide priority and estimated impact for each recommendation
        """
        logger.info("Generating recommendations for session %s", session_id)

        # Load session with scenario
        session = await self._get_session_with_scenario(session_id)
        if session is None:
            raise ValueError(f"Collaboration session '{session_id}' not found")

        # Get progress context
        progress_context = progress_analysis or session.progress_summary or {}

        # Get scenario context
        scenario_context = await self._build_scenario_context(session)

        # Get knowledge base references
        knowledge_refs = await self._get_knowledge_references(session)

        try:
            # Generate recommendations using LLM
            result = await self._generate_with_llm(
                session=session,
                progress_context=progress_context,
                scenario_context=scenario_context,
                knowledge_refs=knowledge_refs,
            )

            # Save to database if requested
            if save_to_db and result.recommendations:
                await self._save_recommendations(session, result.recommendations)

            logger.info(
                "Generated %d recommendations for session %s",
                len(result.recommendations),
                session_id,
            )

            return result

        except Exception as exc:
            logger.exception(
                "Failed to generate recommendations for session %s: %s",
                session_id,
                exc,
            )
            # Fallback to template-based recommendations
            result = self._generate_from_templates(
                session_id=session_id,
                current_phase=progress_context.get("current_phase", "investigation"),
                progress_analysis=progress_context,
            )
            result.error = f"LLM generation failed, using templates: {exc}"
            return result

    async def _get_session_with_scenario(
        self,
        session_id: uuid.UUID,
    ) -> CollaborationSession | None:
        """Load a session with its scenario eagerly loaded.

        Args:
            session_id: The session ID to load.

        Returns:
            The CollaborationSession with scenario, or None if not found.
        """
        query = (
            select(CollaborationSession)
            .where(CollaborationSession.id == session_id)
            .options(selectinload(CollaborationSession.scenario))
        )
        result = await self._db.execute(query)
        return result.scalar_one_or_none()

    async def _build_scenario_context(
        self,
        session: CollaborationSession,
    ) -> dict[str, Any]:
        """Build scenario context for recommendation generation.

        Args:
            session: The collaboration session.

        Returns:
            Dictionary containing scenario context.

        Requirements:
        - 11.3: Provide targeted recommendations based on scenario type and template
        """
        scenario = session.scenario
        config = session.config_snapshot or {}

        scenario_name = config.get("scenario_name")
        if not scenario_name:
            scenario_name = scenario.name if scenario else "未知场景"

        scenario_type = config.get("scenario_type")
        if not scenario_type:
            scenario_type = scenario.scenario_type if scenario else "command"

        context = {
            "scenario_name": scenario_name,
            "scenario_type": scenario_type,
            "template_id": scenario.template_id if scenario else None,
            "trigger_reason": session.trigger_reason,
            "session_status": session.status,
        }

        # Add template-specific hints if available
        template_id = context.get("template_id")
        if template_id and template_id in TEMPLATE_RECOMMENDATION_HINTS:
            context["template_hints"] = TEMPLATE_RECOMMENDATION_HINTS[template_id]

        return context

    async def _get_knowledge_references(
        self,
        session: CollaborationSession,
    ) -> list[dict[str, Any]]:
        """Get knowledge base references for the session's scenario.

        Retrieves associated knowledge documents that can provide
        reference information for recommendations.

        Args:
            session: The collaboration session.

        Returns:
            List of knowledge document references.

        Requirements:
        - 11.2: Combine with associated knowledge base documents
        """
        if not session.scenario:
            return []

        # Load scenario with knowledge docs
        query = (
            select(Scenario)
            .where(Scenario.id == session.scenario_id)
            .options(selectinload(Scenario.knowledge_docs))
        )
        result = await self._db.execute(query)
        scenario = result.scalar_one_or_none()

        if not scenario or not scenario.knowledge_docs:
            return []

        # Build reference list
        refs = []
        for doc in scenario.knowledge_docs[:5]:  # Limit to 5 docs
            refs.append({
                "doc_id": str(doc.id),
                "title": doc.title,
                "summary": doc.summary[:200] if doc.summary else None,
            })

        return refs

    async def _generate_with_llm(
        self,
        session: CollaborationSession,
        progress_context: dict[str, Any],
        scenario_context: dict[str, Any],
        knowledge_refs: list[dict[str, Any]],
    ) -> RecommendationResult:
        """Generate recommendations using LLM.

        Args:
            session: The collaboration session.
            progress_context: Progress analysis context.
            scenario_context: Scenario context.
            knowledge_refs: Knowledge base references.

        Returns:
            RecommendationResult from LLM generation.
        """
        llm = await self._get_llm()

        # Build prompt context
        prompt_context = self._build_prompt_context(
            session=session,
            progress_context=progress_context,
            scenario_context=scenario_context,
            knowledge_refs=knowledge_refs,
        )

        # Invoke LLM
        response = await llm.ainvoke([
            SystemMessage(content=RECOMMENDATION_SYSTEM_PROMPT),
            HumanMessage(content=prompt_context),
        ])

        # Parse response
        raw_output = response.content if hasattr(response, "content") else str(response)
        return self._parse_llm_response(session.id, raw_output, scenario_context, progress_context)

    def _build_prompt_context(
        self,
        session: CollaborationSession,
        progress_context: dict[str, Any],
        scenario_context: dict[str, Any],
        knowledge_refs: list[dict[str, Any]],
    ) -> str:
        """Build the prompt context for LLM.

        Args:
            session: The collaboration session.
            progress_context: Progress analysis context.
            scenario_context: Scenario context.
            knowledge_refs: Knowledge base references.

        Returns:
            Formatted prompt context string.
        """
        parts = [
            "## 协同会话信息",
            f"- 会话ID: {session.id}",
            f"- 场景名称: {scenario_context.get('scenario_name', '未知')}",
            f"- 场景类型: {scenario_context.get('scenario_type', 'command')}",
            f"- 触发原因: {session.trigger_reason or '未指定'}",
            f"- 当前状态: {session.status}",
            "",
        ]

        # Add progress context
        parts.append("## 进度分析结果")
        current_phase = progress_context.get("current_phase", "unknown")
        parts.append(f"- 当前阶段: {current_phase}")

        completed_steps = progress_context.get("completed_steps", [])
        if completed_steps:
            parts.append(f"- 已完成步骤: {', '.join(completed_steps)}")

        pending_items = progress_context.get("pending_items", [])
        if pending_items:
            parts.append(f"- 待处理事项: {', '.join(pending_items)}")

        duration = progress_context.get("duration_minutes", 0)
        parts.append(f"- 处理时长: {duration} 分钟")

        # Add key events if available
        key_events = progress_context.get("key_events", [])
        if key_events:
            parts.append("")
            parts.append("## 关键事件")
            for event in key_events[-5:]:  # Last 5 events
                event_type = event.get("event_type", "unknown")
                description = event.get("description", "")
                parts.append(f"- [{event_type}] {description}")

        # Add template hints if available
        template_hints = scenario_context.get("template_hints")
        if template_hints:
            parts.append("")
            parts.append("## 场景模板提示")
            focus_areas = ", ".join(template_hints.get("focus_areas", []))
            key_actions = ", ".join(template_hints.get("key_actions", []))
            escalation_triggers = ", ".join(template_hints.get("escalation_triggers", []))
            parts.append(f"- 重点关注: {focus_areas}")
            parts.append(f"- 关键操作: {key_actions}")
            parts.append(f"- 升级触发条件: {escalation_triggers}")

        # Add knowledge references if available
        if knowledge_refs:
            parts.append("")
            parts.append("## 相关知识库文档")
            for ref in knowledge_refs:
                title = ref.get("title", "未知文档")
                summary = ref.get("summary", "")
                parts.append(f"- {title}")
                if summary:
                    parts.append(f"  摘要: {summary}")

        parts.append("")
        parts.append("请基于以上信息，生成下一步操作建议。")

        return "\n".join(parts)

    def _parse_llm_response(
        self,
        session_id: uuid.UUID,
        raw_output: str,
        scenario_context: dict[str, Any],
        progress_context: dict[str, Any],
    ) -> RecommendationResult:
        """Parse LLM response into RecommendationResult.

        Args:
            session_id: The session ID.
            raw_output: Raw LLM output.
            scenario_context: Scenario context.
            progress_context: Progress context.

        Returns:
            Parsed RecommendationResult.
        """
        result = RecommendationResult(
            session_id=session_id,
            raw_llm_output=raw_output,
            based_on_phase=progress_context.get("current_phase"),
        )

        try:
            # Extract JSON from response
            json_str = self._extract_json(raw_output)
            data = json.loads(json_str)

            # Parse recommendations
            recommendations_data = data.get("recommendations", [])
            for rec_data in recommendations_data:
                rec = self._parse_recommendation_item(rec_data)
                if rec:
                    result.recommendations.append(rec)

            # Sort by priority (high to low)
            result.recommendations.sort(key=lambda r: r.priority, reverse=True)

        except json.JSONDecodeError as exc:
            logger.warning(
                "Failed to parse LLM response as JSON for session %s: %s",
                session_id,
                exc,
            )
            result.error = f"JSON parse error: {exc}"
        except Exception as exc:
            logger.warning(
                "Error parsing LLM response for session %s: %s",
                session_id,
                exc,
            )
            result.error = str(exc)

        return result

    def _extract_json(self, text: str) -> str:
        """Extract JSON from text that may contain markdown code blocks.

        Args:
            text: Text potentially containing JSON.

        Returns:
            Extracted JSON string.
        """
        # Try to find JSON in code blocks
        import re

        # Match ```json ... ``` or ``` ... ```
        code_block_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(code_block_pattern, text)
        if matches:
            return matches[0].strip()

        # Try to find raw JSON object
        json_pattern = r"\{[\s\S]*\}"
        matches = re.findall(json_pattern, text)
        if matches:
            # Return the longest match (likely the full JSON)
            return max(matches, key=len)

        return text.strip()

    def _parse_recommendation_item(
        self,
        data: dict[str, Any],
    ) -> RecommendationItem | None:
        """Parse a single recommendation item from data.

        Args:
            data: Dictionary containing recommendation data.

        Returns:
            RecommendationItem or None if parsing fails.
        """
        content = data.get("content")
        if not content:
            return None

        # Parse recommendation type
        rec_type_str = data.get("recommendation_type", "action")
        try:
            rec_type = RecommendationType(rec_type_str)
        except ValueError:
            rec_type = RecommendationType.ACTION

        # Parse priority
        priority = data.get("priority", 1)
        if isinstance(priority, str):
            priority_map = {"low": 0, "medium": 1, "high": 2}
            priority = priority_map.get(priority.lower(), 1)
        priority = max(0, min(2, int(priority)))  # Clamp to 0-2

        # Parse confidence
        confidence = data.get("confidence", 0.8)
        if isinstance(confidence, str):
            try:
                confidence = float(confidence)
            except ValueError:
                confidence = 0.8
        confidence = max(0.0, min(1.0, confidence))  # Clamp to 0-1

        return RecommendationItem(
            content=content,
            recommendation_type=rec_type,
            priority=priority,
            estimated_impact=data.get("estimated_impact"),
            rationale=data.get("rationale"),
            confidence=confidence,
        )

    async def _save_recommendations(
        self,
        session: CollaborationSession,
        recommendations: list[RecommendationItem],
    ) -> list[CollaborationRecommendation]:
        """Save recommendations to the database.

        Args:
            session: The collaboration session.
            recommendations: List of recommendations to save.

        Returns:
            List of saved CollaborationRecommendation records.
        """
        saved = []
        for rec in recommendations:
            db_rec = CollaborationRecommendation(
                session_id=session.id,
                content=rec.content,
                priority=rec.priority,
                estimated_impact=rec.estimated_impact,
                reference_docs=rec.reference_docs,
                status="pending",
            )
            self._db.add(db_rec)
            saved.append(db_rec)

        await self._db.flush()

        logger.info(
            "Saved %d recommendations for session %s",
            len(saved),
            session.id,
        )

        return saved

    def _generate_from_templates(
        self,
        session_id: uuid.UUID,
        current_phase: str,
        progress_analysis: dict[str, Any],
    ) -> RecommendationResult:
        """Generate recommendations from phase-based templates.

        Fallback method when LLM is unavailable. Uses predefined templates
        based on the current phase.

        Args:
            session_id: The session ID.
            current_phase: Current phase of the session.
            progress_analysis: The progress analysis result.

        Returns:
            RecommendationResult from templates.
        """
        result = RecommendationResult(session_id=session_id)

        # Get templates for current phase
        templates = PHASE_RECOMMENDATIONS.get(current_phase, [])
        if not templates:
            # Default to investigation phase templates
            templates = PHASE_RECOMMENDATIONS.get("investigation", [])

        # Filter out completed steps
        completed_steps = set(progress_analysis.get("completed_steps", []))

        for template in templates:
            content = template["content"]
            # Skip if this step is already completed
            if any(content in step for step in completed_steps):
                continue

            rec_type = template.get("type", RecommendationType.ACTION)
            if isinstance(rec_type, str):
                try:
                    rec_type = RecommendationType(rec_type)
                except ValueError:
                    rec_type = RecommendationType.ACTION

            rec = RecommendationItem(
                content=content,
                recommendation_type=rec_type,
                priority=template.get("priority", 1),
                estimated_impact=template.get("impact"),
                confidence=0.6,  # Lower confidence for template-based
            )
            result.recommendations.append(rec)

        result.based_on_phase = current_phase
        return result


# Phase-based recommendation templates for fallback when LLM is unavailable
PHASE_RECOMMENDATIONS: dict[str, list[dict[str, Any]]] = {
    "created": [
        {
            "content": "确认问题现象和影响范围",
            "type": RecommendationType.INVESTIGATION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "明确问题边界，为后续排查提供方向",
        },
        {
            "content": "收集相关日志和监控数据",
            "type": RecommendationType.INVESTIGATION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "获取问题诊断所需的关键信息",
        },
        {
            "content": "通知相关团队成员加入协同",
            "type": RecommendationType.COMMUNICATION,
            "priority": RecommendationPriority.MEDIUM.value,
            "impact": "确保有足够的人员参与问题处理",
        },
    ],
    "investigation": [
        {
            "content": "分析日志中的错误信息和异常模式",
            "type": RecommendationType.INVESTIGATION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "定位问题根因",
        },
        {
            "content": "检查最近的变更记录（部署、配置等）",
            "type": RecommendationType.INVESTIGATION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "排查变更引入的问题",
        },
        {
            "content": "确认问题是否可复现",
            "type": RecommendationType.VERIFICATION,
            "priority": RecommendationPriority.MEDIUM.value,
            "impact": "验证问题的稳定性和可调试性",
        },
    ],
    "diagnosis": [
        {
            "content": "制定修复方案并评估风险",
            "type": RecommendationType.ACTION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "为问题修复提供明确的执行计划",
        },
        {
            "content": "准备回滚方案以防修复失败",
            "type": RecommendationType.ACTION,
            "priority": RecommendationPriority.MEDIUM.value,
            "impact": "降低修复操作的风险",
        },
        {
            "content": "如问题复杂，考虑升级到更高级别处理",
            "type": RecommendationType.ESCALATION,
            "priority": RecommendationPriority.MEDIUM.value,
            "impact": "获取更多专业支持",
        },
    ],
    "resolution": [
        {
            "content": "执行修复操作并记录操作步骤",
            "type": RecommendationType.ACTION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "解决问题并留下操作记录",
        },
        {
            "content": "监控修复后的系统状态",
            "type": RecommendationType.VERIFICATION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "确保修复生效且无副作用",
        },
        {
            "content": "同步修复进度给相关干系人",
            "type": RecommendationType.COMMUNICATION,
            "priority": RecommendationPriority.MEDIUM.value,
            "impact": "保持信息透明",
        },
    ],
    "verification": [
        {
            "content": "验证所有受影响的功能已恢复正常",
            "type": RecommendationType.VERIFICATION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "确保问题完全解决",
        },
        {
            "content": "确认监控指标恢复到正常水平",
            "type": RecommendationType.VERIFICATION,
            "priority": RecommendationPriority.HIGH.value,
            "impact": "通过数据验证修复效果",
        },
        {
            "content": "编写问题总结和复盘文档",
            "type": RecommendationType.DOCUMENTATION,
            "priority": RecommendationPriority.LOW.value,
            "impact": "积累经验，防止问题再次发生",
        },
    ],
    "completed": [
        {
            "content": "关闭协同会话并生成总结报告",
            "type": RecommendationType.ACTION,
            "priority": RecommendationPriority.MEDIUM.value,
            "impact": "正式结束协同流程",
        },
        {
            "content": "安排问题复盘会议",
            "type": RecommendationType.COMMUNICATION,
            "priority": RecommendationPriority.LOW.value,
            "impact": "总结经验教训",
        },
        {
            "content": "更新知识库文档",
            "type": RecommendationType.DOCUMENTATION,
            "priority": RecommendationPriority.LOW.value,
            "impact": "沉淀问题处理经验",
        },
    ],
}
