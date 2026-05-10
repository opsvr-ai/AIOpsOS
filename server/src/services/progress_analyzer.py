"""Progress Analyzer service for collaboration sessions.

This module provides intelligent analysis of collaboration session progress,
including message analysis, key event identification, and progress summary
generation using LLM integration.

Requirements:
- 10.1: Periodically analyze collaboration session messages and operation records
- 10.2: Identify key events (problem confirmation, solution discussion, operation execution, result verification)
- 10.3: Generate progress summary (completed steps, current phase, pending items)
- 10.4: Calculate processing duration and time spent in each phase
- 10.5: Support manual trigger of progress analysis
- 10.6: Support configurable automatic analysis interval
- 10.7: Update collaboration session progress status when analysis completes
- 10.8: Store analysis results in collaboration session records
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.models.collaboration import (
    CollaborationMessage,
    CollaborationSession,
    ProgressAnalysisRecord,
)

logger = logging.getLogger(__name__)


class AnalysisPhase(str, Enum):
    """Phases of incident handling in a collaboration session.
    
    These phases represent the typical lifecycle of incident response:
    - CREATED: Session just created, not yet started
    - INVESTIGATION: Initial investigation and problem confirmation
    - DIAGNOSIS: Root cause analysis and diagnosis
    - RESOLUTION: Implementing fixes and solutions
    - VERIFICATION: Verifying the fix and monitoring
    - COMPLETED: Issue resolved and verified
    """
    CREATED = "created"
    INVESTIGATION = "investigation"
    DIAGNOSIS = "diagnosis"
    RESOLUTION = "resolution"
    VERIFICATION = "verification"
    COMPLETED = "completed"


class KeyEventType(str, Enum):
    """Types of key events that can be identified in messages.
    
    Requirements:
    - 10.2: Identify key events including problem confirmation, solution discussion,
            operation execution, result verification
    """
    PROBLEM_CONFIRMED = "problem_confirmed"  # 问题确认
    SOLUTION_DISCUSSED = "solution_discussed"  # 方案讨论
    OPERATION_EXECUTED = "operation_executed"  # 操作执行
    RESULT_VERIFIED = "result_verified"  # 结果验证
    ESCALATION = "escalation"  # 升级处理
    STATUS_UPDATE = "status_update"  # 状态更新
    OTHER = "other"


@dataclass
class KeyEvent:
    """Represents a key event identified during analysis.
    
    Attributes:
        event_type: The type of key event
        description: Brief description of the event
        timestamp: When the event occurred
        message_id: Reference to the source message, if applicable
        confidence: Confidence score of the identification (0.0-1.0)
    """
    event_type: KeyEventType
    description: str
    timestamp: str
    message_id: str | None = None
    confidence: float = 0.8


@dataclass
class PhaseMetrics:
    """Metrics for a specific phase of incident handling.
    
    Attributes:
        phase: The phase name
        started_at: When this phase started
        ended_at: When this phase ended (None if ongoing)
        duration_minutes: Time spent in this phase
        message_count: Number of messages in this phase
        key_events_in_phase: Key events that occurred in this phase
    """
    phase: str
    started_at: str | None = None
    ended_at: str | None = None
    duration_minutes: int = 0
    message_count: int = 0
    key_events_in_phase: list[str] = field(default_factory=list)


@dataclass
class PhaseTransition:
    """Represents a transition between phases.
    
    Attributes:
        from_phase: The phase transitioned from
        to_phase: The phase transitioned to
        timestamp: When the transition occurred
        trigger_event: The key event that triggered the transition
    """
    from_phase: str
    to_phase: str
    timestamp: str
    trigger_event: str | None = None


@dataclass
class ProgressAnalysisResult:
    """Result of a progress analysis.
    
    Contains the complete analysis output including current phase,
    completed steps, pending items, key events, and timing metrics.
    
    Requirements:
    - 10.3: Generate progress summary (completed steps, current phase, pending items)
    - 10.4: Calculate processing duration and time spent in each phase
    """
    session_id: uuid.UUID
    current_phase: str
    completed_steps: list[str] = field(default_factory=list)
    pending_items: list[str] = field(default_factory=list)
    key_events: list[KeyEvent] = field(default_factory=list)
    phase_metrics: list[PhaseMetrics] = field(default_factory=list)
    phase_transitions: list[PhaseTransition] = field(default_factory=list)
    total_duration_minutes: int = 0
    message_count: int = 0
    analysis_timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    raw_llm_output: str | None = None
    error: str | None = None
    # Additional metadata for storage (Requirement 10.8)
    trigger_type: str = "manual"  # manual | automatic
    previous_phase: str | None = None
    phase_changed: bool = False
    analysis_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage.
        
        Returns a complete dictionary representation suitable for storing
        in the collaboration session's progress_summary field.
        
        Requirements:
        - 10.8: Store analysis results in collaboration session records
        """
        return {
            "session_id": str(self.session_id),
            "current_phase": self.current_phase,
            "completed_steps": self.completed_steps,
            "pending_items": self.pending_items,
            "key_events": [
                {
                    "event_type": e.event_type.value if isinstance(e.event_type, KeyEventType) else e.event_type,
                    "description": e.description,
                    "timestamp": e.timestamp,
                    "message_id": e.message_id,
                    "confidence": e.confidence,
                }
                for e in self.key_events
            ],
            "phase_metrics": [
                {
                    "phase": m.phase,
                    "started_at": m.started_at,
                    "ended_at": m.ended_at,
                    "duration_minutes": m.duration_minutes,
                    "message_count": m.message_count,
                    "key_events_in_phase": m.key_events_in_phase,
                }
                for m in self.phase_metrics
            ],
            "phase_transitions": [
                {
                    "from_phase": t.from_phase,
                    "to_phase": t.to_phase,
                    "timestamp": t.timestamp,
                    "trigger_event": t.trigger_event,
                }
                for t in self.phase_transitions
            ],
            "total_duration_minutes": self.total_duration_minutes,
            "message_count": self.message_count,
            "analysis_timestamp": self.analysis_timestamp,
            "error": self.error,
            # Additional metadata
            "trigger_type": self.trigger_type,
            "previous_phase": self.previous_phase,
            "phase_changed": self.phase_changed,
            "analysis_version": self.analysis_version,
        }
    
    def to_summary_dict(self) -> dict[str, Any]:
        """Convert to a summary dictionary for the session's progress_summary field.
        
        Returns a condensed version suitable for quick access and display.
        
        Requirements:
        - 10.7: Update collaboration session progress status when analysis completes
        """
        return {
            "current_phase": self.current_phase,
            "completed_steps": self.completed_steps,
            "pending_items": self.pending_items,
            "duration_minutes": self.total_duration_minutes,
            "last_analysis_at": self.analysis_timestamp,
            "key_events_count": len(self.key_events),
            "message_count": self.message_count,
            "trigger_type": self.trigger_type,
            "phase_changed": self.phase_changed,
            "previous_phase": self.previous_phase,
        }


# Keywords for rule-based key event identification
# These are used as fallback when LLM analysis fails
KEY_EVENT_KEYWORDS: dict[KeyEventType, list[str]] = {
    KeyEventType.PROBLEM_CONFIRMED: [
        "确认", "问题确认", "故障确认", "影响范围", "故障现象", "问题现象",
        "已确认", "确定是", "确认故障", "问题已确认", "影响了", "受影响",
        "故障原因", "根因", "定位到", "发现问题", "问题是", "原因是",
        "排查发现", "初步判断", "确认影响", "影响评估",
    ],
    KeyEventType.SOLUTION_DISCUSSED: [
        "方案", "解决方案", "修复方案", "处理方案", "建议", "计划",
        "讨论", "商议", "决定", "采用", "方案是", "打算", "准备",
        "修复计划", "处理计划", "应急方案", "临时方案", "回滚方案",
        "可以尝试", "建议采用", "推荐", "考虑", "评估方案",
    ],
    KeyEventType.OPERATION_EXECUTED: [
        "执行", "操作", "已执行", "已操作", "完成", "已完成",
        "重启", "配置", "修改", "变更", "部署", "发布", "回滚",
        "扩容", "缩容", "切换", "迁移", "升级", "降级", "修复",
        "已重启", "已配置", "已修改", "已变更", "已部署", "已发布",
        "执行完成", "操作完成", "正在执行", "开始执行",
    ],
    KeyEventType.RESULT_VERIFIED: [
        "验证", "确认", "检查", "测试", "已验证", "已确认",
        "恢复正常", "问题解决", "故障恢复", "服务恢复", "已恢复",
        "验证通过", "测试通过", "检查通过", "正常了", "好了",
        "问题已解决", "故障已恢复", "服务已恢复", "验证完成",
        "效果确认", "结果确认", "监控正常", "指标正常",
    ],
    KeyEventType.ESCALATION: [
        "升级", "上报", "通知", "请求支援", "需要帮助", "协助",
        "转交", "移交", "请", "帮忙", "支持", "介入",
        "升级处理", "上报领导", "通知相关人员", "请求协助",
    ],
    KeyEventType.STATUS_UPDATE: [
        "进度", "状态", "更新", "汇报", "同步", "通报",
        "目前", "当前", "现在", "进展", "情况", "报告",
        "进度更新", "状态更新", "情况汇报", "进展汇报",
    ],
}

# Regex patterns for more sophisticated key event identification
# These patterns capture common operational message formats
# Requirements: 10.2 - Identify key events with sophisticated detection logic
KEY_EVENT_PATTERNS: dict[KeyEventType, list[re.Pattern[str]]] = {
    KeyEventType.PROBLEM_CONFIRMED: [
        re.compile(r"确认.*?(故障|问题|异常|错误)", re.IGNORECASE),
        re.compile(r"(故障|问题|异常).*?确认", re.IGNORECASE),
        re.compile(r"影响范围[：:].+", re.IGNORECASE),
        re.compile(r"(定位|发现).*?(原因|根因|问题)", re.IGNORECASE),
        re.compile(r"初步(判断|分析|定位)[：:]", re.IGNORECASE),
        re.compile(r"(服务|系统|接口).*?(不可用|超时|异常|报错)", re.IGNORECASE),
        re.compile(r"(错误率|失败率|延迟).*?(上升|增加|超过)", re.IGNORECASE),
    ],
    KeyEventType.SOLUTION_DISCUSSED: [
        re.compile(r"(建议|方案|计划)[：:].+", re.IGNORECASE),
        re.compile(r"(决定|准备|打算).*?(执行|操作|修复|重启|回滚)", re.IGNORECASE),
        re.compile(r"(可以|需要|应该).*?(尝试|执行|操作)", re.IGNORECASE),
        re.compile(r"(修复|解决|处理)方案", re.IGNORECASE),
        re.compile(r"(临时|应急|紧急)方案", re.IGNORECASE),
        re.compile(r"评估.*?(风险|影响|方案)", re.IGNORECASE),
    ],
    KeyEventType.OPERATION_EXECUTED: [
        re.compile(r"(已|正在)(执行|操作|重启|配置|修改|部署|发布|回滚)", re.IGNORECASE),
        re.compile(r"(执行|操作|重启|配置|修改|部署|发布|回滚).*?(完成|成功|结束)", re.IGNORECASE),
        re.compile(r"(开始|启动).*?(执行|操作|重启|修复)", re.IGNORECASE),
        re.compile(r"(扩容|缩容|切换|迁移|升级|降级).*?(完成|成功|中)", re.IGNORECASE),
        re.compile(r"变更.*?(已|正在|完成)", re.IGNORECASE),
    ],
    KeyEventType.RESULT_VERIFIED: [
        re.compile(r"(验证|测试|检查).*?(通过|成功|正常|完成)", re.IGNORECASE),
        re.compile(r"(服务|系统|接口).*?(恢复|正常)", re.IGNORECASE),
        re.compile(r"(问题|故障|异常).*?(解决|恢复|修复)", re.IGNORECASE),
        re.compile(r"(监控|指标|数据).*?(正常|恢复|稳定)", re.IGNORECASE),
        re.compile(r"(确认|验证).*?(效果|结果)", re.IGNORECASE),
        re.compile(r"(功能|业务).*?(正常|恢复)", re.IGNORECASE),
    ],
    KeyEventType.ESCALATION: [
        re.compile(r"(升级|上报).*?(处理|领导|上级)", re.IGNORECASE),
        re.compile(r"(请求|需要).*?(支援|帮助|协助)", re.IGNORECASE),
        re.compile(r"(转交|移交).*?(处理|团队)", re.IGNORECASE),
        re.compile(r"@.+?(帮忙|协助|支持|介入)", re.IGNORECASE),
        re.compile(r"(通知|告知).*?(相关|负责)人员", re.IGNORECASE),
    ],
    KeyEventType.STATUS_UPDATE: [
        re.compile(r"(进度|状态)(更新|汇报|同步)[：:]", re.IGNORECASE),
        re.compile(r"(目前|当前|现在).*?(进展|情况|状态)", re.IGNORECASE),
        re.compile(r"(汇报|同步|通报)[：:].+", re.IGNORECASE),
        re.compile(r"(处理|修复|排查).*?(进度|进展)", re.IGNORECASE),
    ],
}

# Context patterns that increase confidence when found near key event keywords
# These help distinguish actual events from casual mentions
CONTEXT_BOOST_PATTERNS: dict[KeyEventType, list[str]] = {
    KeyEventType.PROBLEM_CONFIRMED: [
        "经过排查", "经过分析", "经过检查", "排查结果", "分析结果",
        "确认如下", "情况如下", "现象如下", "影响如下",
    ],
    KeyEventType.SOLUTION_DISCUSSED: [
        "经过讨论", "经过评估", "综合考虑", "建议如下", "方案如下",
        "计划如下", "步骤如下", "操作步骤",
    ],
    KeyEventType.OPERATION_EXECUTED: [
        "操作记录", "执行记录", "变更记录", "操作日志",
        "执行结果", "操作结果", "变更结果",
    ],
    KeyEventType.RESULT_VERIFIED: [
        "验证结果", "测试结果", "检查结果", "确认结果",
        "恢复情况", "修复效果", "处理效果",
    ],
}

# Phase transition indicators based on key events
PHASE_TRANSITION_RULES: dict[str, dict[str, list[KeyEventType]]] = {
    AnalysisPhase.CREATED.value: {
        "next_phase": AnalysisPhase.INVESTIGATION.value,
        "required_events": [],  # Any message starts investigation
    },
    AnalysisPhase.INVESTIGATION.value: {
        "next_phase": AnalysisPhase.DIAGNOSIS.value,
        "required_events": [KeyEventType.PROBLEM_CONFIRMED],
    },
    AnalysisPhase.DIAGNOSIS.value: {
        "next_phase": AnalysisPhase.RESOLUTION.value,
        "required_events": [KeyEventType.SOLUTION_DISCUSSED],
    },
    AnalysisPhase.RESOLUTION.value: {
        "next_phase": AnalysisPhase.VERIFICATION.value,
        "required_events": [KeyEventType.OPERATION_EXECUTED],
    },
    AnalysisPhase.VERIFICATION.value: {
        "next_phase": AnalysisPhase.COMPLETED.value,
        "required_events": [KeyEventType.RESULT_VERIFIED],
    },
}

# System prompt for LLM-based progress analysis
PROGRESS_ANALYSIS_SYSTEM_PROMPT = """你是一位资深的运维专家和事件分析师。你的任务是分析应急协同会话中的消息记录，
识别关键事件，评估当前处理进度，并生成结构化的进度报告。

## 分析要求

### 1. 识别关键事件

从消息中识别以下类型的关键事件，每种类型的识别标准如下：

**problem_confirmed（问题确认）**：
- 确认了故障现象（如：服务不可用、响应超时、错误率上升等）
- 确认了影响范围（如：影响了哪些用户、服务、地区等）
- 确认了问题的严重程度
- 初步定位了问题原因
- 关键词：确认、故障确认、影响范围、问题现象、定位到、发现问题

**solution_discussed（方案讨论）**：
- 讨论了可能的解决方案
- 制定了修复计划或应急方案
- 评估了不同方案的风险和影响
- 决定采用某种处理方式
- 关键词：方案、解决方案、修复计划、建议、决定采用、准备

**operation_executed（操作执行）**：
- 执行了具体的修复操作（如：重启服务、修改配置、回滚版本等）
- 进行了配置变更或部署
- 执行了扩容、切换、迁移等操作
- 关键词：执行、已执行、重启、配置、修改、部署、回滚、已完成

**result_verified（结果验证）**：
- 验证了修复效果
- 确认问题已解决或服务已恢复
- 检查了监控指标恢复正常
- 进行了功能测试确认
- 关键词：验证、已验证、恢复正常、问题解决、测试通过、监控正常

**escalation（升级处理）**：
- 问题升级到更高级别处理
- 请求其他团队或专家支援
- 通知管理层或相关干系人
- 关键词：升级、上报、请求支援、需要帮助、转交

**status_update（状态更新）**：
- 进度汇报和状态同步
- 当前处理情况通报
- 关键词：进度、状态、更新、汇报、目前、当前

### 2. 判断当前阶段

根据已识别的关键事件和消息内容，判断当前处于哪个阶段：

- **created**: 刚创建，尚未开始处理（无实质性消息）
- **investigation**: 调查阶段，正在确认问题（有消息但未确认问题）
- **diagnosis**: 诊断阶段，正在分析根因（已确认问题，正在讨论方案）
- **resolution**: 解决阶段，正在执行修复（已有方案，正在执行操作）
- **verification**: 验证阶段，正在验证效果（已执行操作，正在验证结果）
- **completed**: 已完成，问题已解决（已验证结果正常）

阶段判断规则：
1. 如果有 result_verified 事件且验证通过 → completed
2. 如果有 operation_executed 事件 → verification 或 resolution
3. 如果有 solution_discussed 事件 → resolution 或 diagnosis
4. 如果有 problem_confirmed 事件 → diagnosis 或 investigation
5. 如果只有普通消息 → investigation

### 3. 生成进度摘要

**已完成步骤**：根据识别的关键事件，列出已经完成的关键步骤，例如：
- "问题确认：确认了数据库连接超时问题"
- "方案讨论：决定采用重启数据库连接池的方案"
- "操作执行：已重启数据库连接池"

**待处理事项**：根据当前阶段，列出接下来需要处理的事项，例如：
- investigation 阶段：["确认问题范围", "收集更多日志", "分析根因"]
- diagnosis 阶段：["确定根因", "制定解决方案", "评估方案风险"]
- resolution 阶段：["执行修复操作", "监控执行效果"]
- verification 阶段：["验证修复效果", "确认服务恢复", "关闭会话"]

### 4. 计算阶段时间

为每个已经历的阶段估算开始时间和结束时间（基于消息时间戳）。

## 输出格式

请以 JSON 格式输出分析结果：
```json
{
  "current_phase": "investigation|diagnosis|resolution|verification|completed",
  "completed_steps": ["步骤1描述", "步骤2描述"],
  "pending_items": ["待办1", "待办2"],
  "key_events": [
    {
      "event_type": "problem_confirmed|solution_discussed|operation_executed|result_verified|escalation|status_update",
      "description": "事件的具体描述",
      "timestamp": "YYYY-MM-DD HH:MM:SS",
      "message_id": "消息ID（如果有）",
      "confidence": 0.9
    }
  ],
  "phase_transitions": [
    {
      "from_phase": "investigation",
      "to_phase": "diagnosis",
      "timestamp": "YYYY-MM-DD HH:MM:SS",
      "trigger_event": "problem_confirmed"
    }
  ],
  "summary": "一句话总结当前进度"
}
```

请只输出 JSON，不要包含其他文字。确保 JSON 格式正确，可以被解析。"""


class ProgressAnalyzer:
    """Service for analyzing collaboration session progress.
    
    This service provides intelligent analysis of collaboration sessions
    by examining messages and operations, identifying key events, and
    generating progress summaries using LLM integration.
    
    Features:
    - Periodic analysis of session messages and operations
    - Key event identification (problem confirmation, solution discussion, etc.)
    - Progress summary generation (completed steps, current phase, pending items)
    - Duration calculation for total time and per-phase metrics
    - Manual and automatic analysis triggers
    - LLM integration for intelligent analysis
    - Configurable analysis interval from settings
    
    Requirements:
    - 10.1: Periodically analyze collaboration session messages and operation records
    - 10.2: Identify key events
    - 10.3: Generate progress summary
    - 10.4: Calculate processing duration and phase timing
    - 10.5: Support manual trigger of progress analysis
    - 10.6: Support configurable automatic analysis interval
    - 10.7: Update collaboration session progress status when analysis completes
    - 10.8: Store analysis results in collaboration session records
    
    Example:
        ```python
        async with async_session_factory() as db:
            analyzer = ProgressAnalyzer(db)
            result = await analyzer.analyze_session(session_id)
            print(f"Current phase: {result.current_phase}")
            print(f"Completed steps: {result.completed_steps}")
        ```
    """
    
    # Default interval for automatic analysis (in seconds)
    # This can be overridden by settings.progress_analysis_interval
    DEFAULT_ANALYSIS_INTERVAL: int = 300  # 5 minutes
    
    def __init__(
        self,
        db: AsyncSession,
        analysis_interval: int | None = None,
        llm: Any | None = None,
    ) -> None:
        """Initialize the ProgressAnalyzer.
        
        Args:
            db: Async database session for persistence operations.
            analysis_interval: Interval in seconds for automatic analysis.
                If not provided, uses settings.progress_analysis_interval,
                falling back to DEFAULT_ANALYSIS_INTERVAL (300 seconds).
            llm: Optional pre-configured LLM instance. If not provided,
                the default model will be loaded lazily.
        
        Requirements:
        - 10.6: Support configurable automatic analysis interval
        """
        self._db = db
        # Use provided interval, or settings, or default (Requirement 10.6)
        if analysis_interval is not None:
            self._analysis_interval = analysis_interval
        else:
            self._analysis_interval = getattr(
                settings, 'progress_analysis_interval', self.DEFAULT_ANALYSIS_INTERVAL
            )
        self._llm = llm
        self._scheduler_task: asyncio.Task | None = None
        self._running = False
        self._active_sessions: dict[uuid.UUID, asyncio.Task] = {}  # Track per-session tasks
    
    async def _get_llm(self) -> Any:
        """Get or create the LLM instance.
        
        Lazily initializes the LLM from the platform's configured ModelProvider.
        
        Returns:
            The LLM instance for analysis.
        """
        if self._llm is None:
            from src.core.model_factory import get_default_model
            self._llm = await get_default_model()
        return self._llm
    
    async def analyze_session(
        self,
        session_id: uuid.UUID,
        update_session: bool = True,
        trigger_type: str = "manual",
    ) -> ProgressAnalysisResult:
        """Analyze a collaboration session's progress.
        
        Performs intelligent analysis of the session's messages and operations
        to identify key events, determine current phase, and generate a
        progress summary.
        
        Args:
            session_id: The ID of the collaboration session to analyze.
            update_session: Whether to update the session's progress_summary
                field with the analysis results. Defaults to True.
            trigger_type: How the analysis was triggered ("manual" or "automatic").
                Defaults to "manual".
        
        Returns:
            ProgressAnalysisResult containing the analysis output.
        
        Raises:
            ValueError: If the session is not found.
        
        Requirements:
        - 10.1: Analyze collaboration session messages and operation records
        - 10.5: Support manual trigger of progress analysis
        - 10.7: Update collaboration session progress status when analysis completes
        - 10.8: Store analysis results in collaboration session records
        """
        logger.info(
            "Starting progress analysis for session %s (trigger_type=%s)",
            session_id, trigger_type
        )
        
        # Load session with messages
        session = await self._get_session_with_messages(session_id)
        if session is None:
            raise ValueError(f"Collaboration session '{session_id}' not found")
        
        # Get previous phase for tracking phase changes (Requirement 10.7)
        previous_phase = None
        if session.progress_summary:
            previous_phase = session.progress_summary.get("current_phase")
        
        # Calculate basic metrics
        messages = session.messages or []
        message_count = len(messages)
        total_duration = self._calculate_duration(session)
        
        logger.debug(
            "Analyzing session %s: %d messages, %d minutes duration, previous_phase=%s",
            session_id, message_count, total_duration, previous_phase
        )
        
        # If no messages, return basic result
        if message_count == 0:
            result = ProgressAnalysisResult(
                session_id=session_id,
                current_phase=session.status or AnalysisPhase.CREATED.value,
                completed_steps=["协同会话已创建"],
                pending_items=["等待消息和操作记录"],
                total_duration_minutes=total_duration,
                message_count=0,
                trigger_type=trigger_type,
                previous_phase=previous_phase,
                phase_changed=False,
            )
            if update_session:
                await self._update_session_progress(session, result)
            return result
        
        # Perform LLM-based analysis
        try:
            result = await self._analyze_with_llm(session, messages)
            result.total_duration_minutes = total_duration
            result.message_count = message_count
            result.trigger_type = trigger_type
            result.previous_phase = previous_phase
            result.phase_changed = (
                previous_phase is not None and 
                previous_phase != result.current_phase
            )
            
            # Calculate phase metrics if not provided by LLM
            if not result.phase_metrics:
                result.phase_metrics = self._calculate_phase_metrics(
                    session, messages, result.key_events, result.phase_transitions
                )
            
            # Detect phase transitions if not provided by LLM
            if not result.phase_transitions and result.key_events:
                result.phase_transitions = self._detect_phase_transitions(result.key_events)
                
        except Exception as exc:
            logger.exception("LLM analysis failed for session %s: %s", session_id, exc)
            # Fallback to basic analysis
            result = self._basic_analysis(session, messages)
            result.error = str(exc)
            result.trigger_type = trigger_type
            result.previous_phase = previous_phase
            result.phase_changed = (
                previous_phase is not None and 
                previous_phase != result.current_phase
            )
        
        # Update session if requested
        if update_session:
            await self._update_session_progress(session, result)
        
        logger.info(
            "Progress analysis completed for session %s: phase=%s, events=%d, phase_changed=%s",
            session_id, result.current_phase, len(result.key_events), result.phase_changed
        )
        
        return result
    
    async def _get_session_with_messages(
        self,
        session_id: uuid.UUID,
    ) -> CollaborationSession | None:
        """Load a session with its messages eagerly loaded.
        
        Args:
            session_id: The session ID to load.
        
        Returns:
            The CollaborationSession with messages, or None if not found.
        """
        query = (
            select(CollaborationSession)
            .where(CollaborationSession.id == session_id)
            .options(selectinload(CollaborationSession.messages))
        )
        result = await self._db.execute(query)
        return result.scalar_one_or_none()
    
    def _calculate_duration(self, session: CollaborationSession) -> int:
        """Calculate the total duration of a session in minutes.
        
        Args:
            session: The collaboration session.
        
        Returns:
            Duration in minutes from session creation to now (or closed_at).
        
        Requirements:
        - 10.4: Calculate processing duration
        """
        start_time = session.created_at
        if start_time is None:
            return 0
        
        # Use closed_at if session is closed, otherwise use current time
        end_time = session.closed_at or datetime.now(UTC)
        
        # Ensure both times are timezone-aware
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=UTC)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=UTC)
        
        duration = end_time - start_time
        return int(duration.total_seconds() / 60)
    
    async def _analyze_with_llm(
        self,
        session: CollaborationSession,
        messages: list[CollaborationMessage],
    ) -> ProgressAnalysisResult:
        """Perform LLM-based analysis of session messages.
        
        Uses the configured LLM to analyze messages and identify key events,
        current phase, and generate progress summary.
        
        Args:
            session: The collaboration session.
            messages: List of messages to analyze.
        
        Returns:
            ProgressAnalysisResult from LLM analysis.
        
        Requirements:
        - 10.1: Integrate LLM service for intelligent analysis
        - 10.2: Identify key events
        - 10.3: Generate progress summary
        """
        llm = await self._get_llm()
        
        # Build message context for LLM
        message_context = self._build_message_context(session, messages)
        
        # Invoke LLM
        response = await llm.ainvoke([
            SystemMessage(content=PROGRESS_ANALYSIS_SYSTEM_PROMPT),
            HumanMessage(content=message_context),
        ])
        
        # Parse LLM response
        raw_output = response.content if hasattr(response, "content") else str(response)
        return self._parse_llm_response(session.id, raw_output)
    
    def _build_message_context(
        self,
        session: CollaborationSession,
        messages: list[CollaborationMessage],
    ) -> str:
        """Build the message context string for LLM analysis.
        
        Args:
            session: The collaboration session.
            messages: List of messages to include.
        
        Returns:
            Formatted string containing session info and messages.
        """
        # Session info
        config = session.config_snapshot or {}
        scenario_name = config.get("scenario_name", "未知场景")
        
        context_parts = [
            f"## 协同会话信息",
            f"- 会话ID: {session.id}",
            f"- 场景: {scenario_name}",
            f"- 触发原因: {session.trigger_reason or '未指定'}",
            f"- 当前状态: {session.status}",
            f"- 创建时间: {session.created_at.isoformat() if session.created_at else 'N/A'}",
            "",
            "## 消息记录",
        ]
        
        # Add messages (limit to most recent 50 for context window)
        recent_messages = messages[-50:] if len(messages) > 50 else messages
        
        for msg in recent_messages:
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S") if msg.created_at else "N/A"
            sender = msg.sender_name or msg.sender_id or "系统"
            channel = msg.source_channel
            content = msg.content[:500] if len(msg.content) > 500 else msg.content
            
            context_parts.append(
                f"[{timestamp}] [{channel}] {sender}: {content}"
            )
        
        if len(messages) > 50:
            context_parts.insert(
                len(context_parts) - len(recent_messages),
                f"(显示最近 50 条消息，共 {len(messages)} 条)"
            )
        
        return "\n".join(context_parts)
    
    def _parse_llm_response(
        self,
        session_id: uuid.UUID,
        raw_output: str,
    ) -> ProgressAnalysisResult:
        """Parse the LLM response into a ProgressAnalysisResult.
        
        Args:
            session_id: The session ID.
            raw_output: Raw LLM output string.
        
        Returns:
            Parsed ProgressAnalysisResult.
        
        Requirements:
        - 10.2: Identify key events
        - 10.3: Generate progress summary
        """
        result = ProgressAnalysisResult(
            session_id=session_id,
            current_phase=AnalysisPhase.INVESTIGATION.value,
            raw_llm_output=raw_output,
        )
        
        try:
            # Extract JSON from response (handle markdown code blocks)
            json_str = raw_output.strip()
            if "```" in json_str:
                # Extract content between code blocks
                parts = json_str.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("{"):
                        json_str = part
                        break
            
            data = json.loads(json_str)
            
            # Parse current phase
            phase = data.get("current_phase", "investigation")
            if phase in [p.value for p in AnalysisPhase]:
                result.current_phase = phase
            
            # Parse completed steps
            result.completed_steps = data.get("completed_steps", [])
            
            # Parse pending items
            result.pending_items = data.get("pending_items", [])
            
            # Parse key events
            for event_data in data.get("key_events", []):
                event_type_str = event_data.get("event_type", "other")
                try:
                    event_type = KeyEventType(event_type_str)
                except ValueError:
                    event_type = KeyEventType.OTHER
                
                result.key_events.append(KeyEvent(
                    event_type=event_type,
                    description=event_data.get("description", ""),
                    timestamp=event_data.get("timestamp", ""),
                    message_id=event_data.get("message_id"),
                    confidence=event_data.get("confidence", 0.8),
                ))
            
            # Parse phase transitions
            for transition_data in data.get("phase_transitions", []):
                result.phase_transitions.append(PhaseTransition(
                    from_phase=transition_data.get("from_phase", ""),
                    to_phase=transition_data.get("to_phase", ""),
                    timestamp=transition_data.get("timestamp", ""),
                    trigger_event=transition_data.get("trigger_event"),
                ))
            
        except json.JSONDecodeError as exc:
            logger.warning(
                "Failed to parse LLM response as JSON for session %s: %s",
                session_id, exc
            )
            result.error = f"JSON parse error: {exc}"
        except Exception as exc:
            logger.warning(
                "Error parsing LLM response for session %s: %s",
                session_id, exc
            )
            result.error = str(exc)
        
        return result
    
    def _basic_analysis(
        self,
        session: CollaborationSession,
        messages: list[CollaborationMessage],
    ) -> ProgressAnalysisResult:
        """Perform basic analysis without LLM (fallback).
        
        This is used when LLM analysis fails. It provides rule-based
        analysis using keyword matching for key event identification
        and phase detection.
        
        Args:
            session: The collaboration session.
            messages: List of messages.
        
        Returns:
            Basic ProgressAnalysisResult with key events and phase metrics.
        
        Requirements:
        - 10.2: Identify key events
        - 10.3: Generate progress summary
        - 10.4: Calculate processing duration and phase timing
        """
        message_count = len(messages)
        
        # Identify key events using keyword matching
        key_events = self._identify_key_events_by_keywords(messages)
        
        # Determine current phase based on key events
        current_phase = self._determine_phase_from_events(session, key_events)
        
        # Detect phase transitions
        phase_transitions = self._detect_phase_transitions(key_events)
        
        # Calculate phase metrics
        phase_metrics = self._calculate_phase_metrics(
            session, messages, key_events, phase_transitions
        )
        
        # Build completed steps based on key events
        completed_steps = self._build_completed_steps(key_events)
        
        # Build pending items based on current phase
        pending_items = self._build_pending_items(current_phase)
        
        return ProgressAnalysisResult(
            session_id=session.id,
            current_phase=current_phase,
            completed_steps=completed_steps,
            pending_items=pending_items,
            key_events=key_events,
            phase_metrics=phase_metrics,
            phase_transitions=phase_transitions,
            total_duration_minutes=self._calculate_duration(session),
            message_count=message_count,
        )
    
    def _identify_key_events_by_keywords(
        self,
        messages: list[CollaborationMessage],
    ) -> list[KeyEvent]:
        """Identify key events from messages using keyword and pattern matching.
        
        This method uses a sophisticated multi-layer detection approach:
        1. Keyword matching for basic event identification
        2. Regex pattern matching for structured message formats
        3. Context boost patterns for confidence adjustment
        
        Args:
            messages: List of messages to analyze.
        
        Returns:
            List of identified KeyEvent objects.
        
        Requirements:
        - 10.2: Identify key events (problem confirmation, solution discussion,
                operation execution, result verification)
        """
        key_events: list[KeyEvent] = []
        
        for msg in messages:
            content = msg.content
            content_lower = content.lower()
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S") if msg.created_at else ""
            message_id = str(msg.id) if msg.id else None
            
            # Try to identify event using combined detection
            event = self._detect_event_from_content(
                content, content_lower, timestamp, message_id
            )
            if event:
                key_events.append(event)
        
        return key_events
    
    def _detect_event_from_content(
        self,
        content: str,
        content_lower: str,
        timestamp: str,
        message_id: str | None,
    ) -> KeyEvent | None:
        """Detect a key event from message content using multiple detection methods.
        
        Uses a combination of keyword matching, pattern matching, and context
        analysis to identify key events with appropriate confidence scores.
        
        Args:
            content: Original message content.
            content_lower: Lowercase message content for matching.
            timestamp: Message timestamp.
            message_id: Message ID.
        
        Returns:
            KeyEvent if detected, None otherwise.
        
        Requirements:
        - 10.2: Identify key events with sophisticated detection logic
        """
        best_event_type: KeyEventType | None = None
        best_confidence: float = 0.0
        best_matched_keywords: list[str] = []
        best_pattern_match: str | None = None
        
        # Check each event type
        for event_type in KeyEventType:
            if event_type == KeyEventType.OTHER:
                continue
            
            confidence = 0.0
            matched_keywords: list[str] = []
            pattern_match: str | None = None
            
            # 1. Keyword matching (base confidence: 0.4-0.7)
            keywords = KEY_EVENT_KEYWORDS.get(event_type, [])
            matched_keywords = [kw for kw in keywords if kw in content_lower]
            if matched_keywords:
                # Base confidence from keyword count
                confidence = min(0.4 + len(matched_keywords) * 0.05, 0.7)
            
            # 2. Pattern matching (adds 0.15-0.25 confidence)
            patterns = KEY_EVENT_PATTERNS.get(event_type, [])
            for pattern in patterns:
                match = pattern.search(content)
                if match:
                    pattern_match = match.group(0)
                    confidence += 0.2
                    break
            
            # 3. Context boost patterns (adds 0.05-0.1 confidence)
            context_patterns = CONTEXT_BOOST_PATTERNS.get(event_type, [])
            context_matches = [cp for cp in context_patterns if cp in content_lower]
            if context_matches:
                confidence += min(len(context_matches) * 0.05, 0.1)
            
            # Only consider if we have some evidence
            if confidence > 0 and confidence > best_confidence:
                best_event_type = event_type
                best_confidence = confidence
                best_matched_keywords = matched_keywords
                best_pattern_match = pattern_match
        
        # Return event if confidence threshold met
        if best_event_type and best_confidence >= 0.4:
            description = self._generate_event_description(
                best_event_type, content, best_matched_keywords, best_pattern_match
            )
            return KeyEvent(
                event_type=best_event_type,
                description=description,
                timestamp=timestamp,
                message_id=message_id,
                confidence=min(best_confidence, 0.95),
            )
        
        return None
    
    def _extract_events_from_patterns(
        self,
        content: str,
        timestamp: str,
        message_id: str | None,
    ) -> list[KeyEvent]:
        """Extract multiple key events from a single message using pattern matching.
        
        Some messages may contain multiple key events (e.g., a status update
        that mentions both problem confirmation and solution discussion).
        This method extracts all identifiable events.
        
        Args:
            content: Message content to analyze.
            timestamp: Message timestamp.
            message_id: Message ID.
        
        Returns:
            List of KeyEvent objects found in the content.
        
        Requirements:
        - 10.2: Identify key events from message content patterns
        """
        events: list[KeyEvent] = []
        content_lower = content.lower()
        
        for event_type, patterns in KEY_EVENT_PATTERNS.items():
            for pattern in patterns:
                match = pattern.search(content)
                if match:
                    # Calculate confidence based on match quality
                    matched_text = match.group(0)
                    confidence = 0.6  # Base confidence for pattern match
                    
                    # Boost confidence if keywords also match
                    keywords = KEY_EVENT_KEYWORDS.get(event_type, [])
                    matched_keywords = [kw for kw in keywords if kw in content_lower]
                    if matched_keywords:
                        confidence += min(len(matched_keywords) * 0.05, 0.2)
                    
                    # Check context patterns
                    context_patterns = CONTEXT_BOOST_PATTERNS.get(event_type, [])
                    if any(cp in content_lower for cp in context_patterns):
                        confidence += 0.1
                    
                    events.append(KeyEvent(
                        event_type=event_type,
                        description=f"{self._get_event_type_name(event_type)}: {matched_text}",
                        timestamp=timestamp,
                        message_id=message_id,
                        confidence=min(confidence, 0.95),
                    ))
                    break  # Only one event per type per message
        
        return events
    
    def _get_event_type_name(self, event_type: KeyEventType) -> str:
        """Get the Chinese name for an event type.
        
        Args:
            event_type: The event type.
        
        Returns:
            Chinese name for the event type.
        """
        event_type_names = {
            KeyEventType.PROBLEM_CONFIRMED: "问题确认",
            KeyEventType.SOLUTION_DISCUSSED: "方案讨论",
            KeyEventType.OPERATION_EXECUTED: "操作执行",
            KeyEventType.RESULT_VERIFIED: "结果验证",
            KeyEventType.ESCALATION: "升级处理",
            KeyEventType.STATUS_UPDATE: "状态更新",
            KeyEventType.OTHER: "其他事件",
        }
        return event_type_names.get(event_type, "事件")
    
    def _generate_event_description(
        self,
        event_type: KeyEventType,
        content: str,
        matched_keywords: list[str],
        pattern_match: str | None = None,
    ) -> str:
        """Generate a description for a key event.
        
        Creates a meaningful description based on the event type, matched
        keywords, and pattern match results.
        
        Args:
            event_type: The type of event.
            content: The message content.
            matched_keywords: Keywords that matched.
            pattern_match: Text matched by regex pattern, if any.
        
        Returns:
            A brief description of the event.
        """
        type_name = self._get_event_type_name(event_type)
        
        # Use pattern match if available (more specific)
        if pattern_match:
            return f"{type_name}: {pattern_match}"
        
        # Otherwise, truncate content for description
        truncated = content[:100] + "..." if len(content) > 100 else content
        return f"{type_name}: {truncated}"
    
    def _determine_phase_from_events(
        self,
        session: CollaborationSession,
        key_events: list[KeyEvent],
    ) -> str:
        """Determine the current phase based on identified key events.
        
        Uses the phase transition rules to determine the most advanced
        phase that has been reached based on the key events.
        
        Args:
            session: The collaboration session.
            key_events: List of identified key events.
        
        Returns:
            The current phase as a string.
        """
        # Check session status first
        if session.status == "closed":
            return AnalysisPhase.COMPLETED.value
        if session.status == "resolved":
            return AnalysisPhase.VERIFICATION.value
        
        # If no events, check if there are any messages
        if not key_events:
            return AnalysisPhase.INVESTIGATION.value
        
        # Get event types present
        event_types = {e.event_type for e in key_events}
        
        # Determine phase based on highest-level event
        if KeyEventType.RESULT_VERIFIED in event_types:
            return AnalysisPhase.COMPLETED.value
        if KeyEventType.OPERATION_EXECUTED in event_types:
            return AnalysisPhase.VERIFICATION.value
        if KeyEventType.SOLUTION_DISCUSSED in event_types:
            return AnalysisPhase.RESOLUTION.value
        if KeyEventType.PROBLEM_CONFIRMED in event_types:
            return AnalysisPhase.DIAGNOSIS.value
        
        return AnalysisPhase.INVESTIGATION.value
    
    def _detect_phase_transitions(
        self,
        key_events: list[KeyEvent],
    ) -> list[PhaseTransition]:
        """Detect phase transitions based on key events.
        
        Args:
            key_events: List of identified key events.
        
        Returns:
            List of PhaseTransition objects.
        """
        transitions: list[PhaseTransition] = []
        
        # Track which phases we've transitioned through
        current_phase = AnalysisPhase.CREATED.value
        
        # Sort events by timestamp
        sorted_events = sorted(key_events, key=lambda e: e.timestamp)
        
        for event in sorted_events:
            # Check if this event triggers a phase transition
            if current_phase in PHASE_TRANSITION_RULES:
                rule = PHASE_TRANSITION_RULES[current_phase]
                required_events = rule.get("required_events", [])
                
                # If no required events, any event triggers transition
                if not required_events or event.event_type in required_events:
                    next_phase = rule.get("next_phase")
                    if next_phase and next_phase != current_phase:
                        transitions.append(PhaseTransition(
                            from_phase=current_phase,
                            to_phase=next_phase,
                            timestamp=event.timestamp,
                            trigger_event=event.event_type.value,
                        ))
                        current_phase = next_phase
        
        return transitions
    
    def _calculate_phase_metrics(
        self,
        session: CollaborationSession,
        messages: list[CollaborationMessage],
        key_events: list[KeyEvent],
        phase_transitions: list[PhaseTransition],
    ) -> list[PhaseMetrics]:
        """Calculate metrics for each phase.
        
        Args:
            session: The collaboration session.
            messages: List of messages.
            key_events: List of identified key events.
            phase_transitions: List of phase transitions.
        
        Returns:
            List of PhaseMetrics for each phase.
        
        Requirements:
        - 10.4: Calculate processing duration and time spent in each phase
        """
        metrics: list[PhaseMetrics] = []
        
        # Get session start time
        session_start = session.created_at
        if session_start is None:
            return metrics
        
        # Ensure timezone-aware
        if session_start.tzinfo is None:
            session_start = session_start.replace(tzinfo=UTC)
        
        # Build phase timeline from transitions
        phase_timeline: list[tuple[str, datetime, datetime | None]] = []
        
        if not phase_transitions:
            # No transitions, entire session is in investigation phase
            end_time = session.closed_at or datetime.now(UTC)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=UTC)
            phase_timeline.append((
                AnalysisPhase.INVESTIGATION.value,
                session_start,
                end_time,
            ))
        else:
            # Build timeline from transitions
            current_start = session_start
            for i, transition in enumerate(phase_transitions):
                # Parse transition timestamp
                try:
                    transition_time = datetime.fromisoformat(transition.timestamp)
                    if transition_time.tzinfo is None:
                        transition_time = transition_time.replace(tzinfo=UTC)
                except (ValueError, TypeError):
                    transition_time = current_start
                
                # Add the from_phase
                phase_timeline.append((
                    transition.from_phase,
                    current_start,
                    transition_time,
                ))
                current_start = transition_time
            
            # Add the final phase (current phase)
            if phase_transitions:
                final_phase = phase_transitions[-1].to_phase
                end_time = session.closed_at or datetime.now(UTC)
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=UTC)
                phase_timeline.append((
                    final_phase,
                    current_start,
                    end_time,
                ))
        
        # Calculate metrics for each phase
        for phase_name, start_time, end_time in phase_timeline:
            # Calculate duration
            if end_time:
                duration = int((end_time - start_time).total_seconds() / 60)
            else:
                duration = 0
            
            # Count messages in this phase
            msg_count = 0
            events_in_phase: list[str] = []
            for msg in messages:
                msg_time = msg.created_at
                if msg_time:
                    if msg_time.tzinfo is None:
                        msg_time = msg_time.replace(tzinfo=UTC)
                    if start_time <= msg_time <= (end_time or datetime.now(UTC)):
                        msg_count += 1
            
            # Find key events in this phase
            for event in key_events:
                try:
                    event_time = datetime.fromisoformat(event.timestamp)
                    if event_time.tzinfo is None:
                        event_time = event_time.replace(tzinfo=UTC)
                    if start_time <= event_time <= (end_time or datetime.now(UTC)):
                        events_in_phase.append(event.event_type.value)
                except (ValueError, TypeError):
                    pass
            
            metrics.append(PhaseMetrics(
                phase=phase_name,
                started_at=start_time.isoformat(),
                ended_at=end_time.isoformat() if end_time else None,
                duration_minutes=duration,
                message_count=msg_count,
                key_events_in_phase=events_in_phase,
            ))
        
        return metrics
    
    def _build_completed_steps(
        self,
        key_events: list[KeyEvent],
    ) -> list[str]:
        """Build list of completed steps based on key events.
        
        Creates a detailed list of completed steps by analyzing key events
        and extracting meaningful descriptions from each event.
        
        Args:
            key_events: List of identified key events.
        
        Returns:
            List of completed step descriptions with timestamps.
        
        Requirements:
        - 10.3: Generate progress summary (completed steps)
        """
        completed_steps = ["协同会话已创建"]
        
        # Track events by type for detailed step generation
        events_by_type: dict[KeyEventType, list[KeyEvent]] = {}
        for event in key_events:
            if event.event_type not in events_by_type:
                events_by_type[event.event_type] = []
            events_by_type[event.event_type].append(event)
        
        # Build detailed steps for each event type
        step_order = [
            KeyEventType.PROBLEM_CONFIRMED,
            KeyEventType.SOLUTION_DISCUSSED,
            KeyEventType.OPERATION_EXECUTED,
            KeyEventType.RESULT_VERIFIED,
            KeyEventType.ESCALATION,
            KeyEventType.STATUS_UPDATE,
        ]
        
        for event_type in step_order:
            events = events_by_type.get(event_type, [])
            if not events:
                continue
            
            # Get the first (most significant) event of this type
            first_event = events[0]
            
            # Build step description with timestamp
            timestamp_str = ""
            if first_event.timestamp:
                try:
                    # Extract just the time portion for brevity
                    ts = datetime.fromisoformat(first_event.timestamp)
                    timestamp_str = f" ({ts.strftime('%H:%M')})"
                except (ValueError, TypeError):
                    pass
            
            # Generate step based on event type with more detail
            if event_type == KeyEventType.PROBLEM_CONFIRMED:
                step = f"问题确认{timestamp_str}：已确认故障现象和影响范围"
                if len(events) > 1:
                    step += f" (共{len(events)}次确认)"
            elif event_type == KeyEventType.SOLUTION_DISCUSSED:
                step = f"方案讨论{timestamp_str}：已讨论并确定解决方案"
                if len(events) > 1:
                    step += f" (共{len(events)}次讨论)"
            elif event_type == KeyEventType.OPERATION_EXECUTED:
                step = f"操作执行{timestamp_str}：已执行修复操作"
                if len(events) > 1:
                    step += f" (共{len(events)}次操作)"
            elif event_type == KeyEventType.RESULT_VERIFIED:
                step = f"结果验证{timestamp_str}：已验证修复效果"
                if len(events) > 1:
                    step += f" (共{len(events)}次验证)"
            elif event_type == KeyEventType.ESCALATION:
                step = f"升级处理{timestamp_str}：已升级或请求支援"
            elif event_type == KeyEventType.STATUS_UPDATE:
                step = f"状态更新{timestamp_str}：已同步处理进度"
                if len(events) > 1:
                    step += f" (共{len(events)}次更新)"
            else:
                continue
            
            completed_steps.append(step)
        
        return completed_steps
    
    def _build_detailed_progress_summary(
        self,
        session: CollaborationSession,
        key_events: list[KeyEvent],
        phase_metrics: list[PhaseMetrics],
        current_phase: str,
    ) -> str:
        """Build a detailed text summary of the progress.
        
        Creates a human-readable summary of the collaboration session's
        progress including key events, phase durations, and current status.
        
        Args:
            session: The collaboration session.
            key_events: List of identified key events.
            phase_metrics: List of phase metrics.
            current_phase: The current phase.
        
        Returns:
            A detailed text summary.
        
        Requirements:
        - 10.3: Generate progress summary
        - 10.4: Calculate processing duration and phase timing
        """
        summary_parts = []
        
        # Session overview
        config = session.config_snapshot or {}
        scenario_name = config.get("scenario_name", "未知场景")
        summary_parts.append(f"场景: {scenario_name}")
        summary_parts.append(f"当前阶段: {self._get_phase_display_name(current_phase)}")
        
        # Duration summary
        total_duration = sum(m.duration_minutes for m in phase_metrics)
        if total_duration > 0:
            hours = total_duration // 60
            minutes = total_duration % 60
            if hours > 0:
                summary_parts.append(f"处理时长: {hours}小时{minutes}分钟")
            else:
                summary_parts.append(f"处理时长: {minutes}分钟")
        
        # Phase breakdown
        if phase_metrics:
            phase_summary = []
            for metric in phase_metrics:
                phase_name = self._get_phase_display_name(metric.phase)
                if metric.duration_minutes > 0:
                    phase_summary.append(f"{phase_name}({metric.duration_minutes}分钟)")
            if phase_summary:
                summary_parts.append(f"阶段耗时: {' → '.join(phase_summary)}")
        
        # Key events summary
        if key_events:
            event_counts: dict[str, int] = {}
            for event in key_events:
                type_name = self._get_event_type_name(event.event_type)
                event_counts[type_name] = event_counts.get(type_name, 0) + 1
            
            event_summary = ", ".join(f"{name}({count})" for name, count in event_counts.items())
            summary_parts.append(f"关键事件: {event_summary}")
        
        return " | ".join(summary_parts)
    
    def _get_phase_display_name(self, phase: str) -> str:
        """Get the display name for a phase.
        
        Args:
            phase: The phase value.
        
        Returns:
            Chinese display name for the phase.
        """
        phase_names = {
            AnalysisPhase.CREATED.value: "已创建",
            AnalysisPhase.INVESTIGATION.value: "调查中",
            AnalysisPhase.DIAGNOSIS.value: "诊断中",
            AnalysisPhase.RESOLUTION.value: "处理中",
            AnalysisPhase.VERIFICATION.value: "验证中",
            AnalysisPhase.COMPLETED.value: "已完成",
        }
        return phase_names.get(phase, phase)
    
    def _build_pending_items(
        self,
        current_phase: str,
    ) -> list[str]:
        """Build list of pending items based on current phase.
        
        Args:
            current_phase: The current phase.
        
        Returns:
            List of pending item descriptions.
        
        Requirements:
        - 10.3: Generate progress summary (pending items)
        """
        pending_items: list[str] = []
        
        if current_phase == AnalysisPhase.COMPLETED.value:
            return pending_items
        
        if current_phase == AnalysisPhase.CREATED.value:
            pending_items = [
                "开始问题调查",
                "收集故障信息",
                "确认问题范围",
            ]
        elif current_phase == AnalysisPhase.INVESTIGATION.value:
            pending_items = [
                "确认问题范围和影响",
                "收集更多日志和监控数据",
                "分析问题根因",
            ]
        elif current_phase == AnalysisPhase.DIAGNOSIS.value:
            pending_items = [
                "确定问题根因",
                "制定解决方案",
                "评估方案风险",
            ]
        elif current_phase == AnalysisPhase.RESOLUTION.value:
            pending_items = [
                "执行修复操作",
                "监控执行效果",
                "准备回滚方案",
            ]
        elif current_phase == AnalysisPhase.VERIFICATION.value:
            pending_items = [
                "验证修复效果",
                "确认服务恢复正常",
                "关闭协同会话",
            ]
        
        return pending_items
    
    async def _update_session_progress(
        self,
        session: CollaborationSession,
        result: ProgressAnalysisResult,
    ) -> None:
        """Update the session's progress_summary with analysis results.
        
        This method updates the session's progress_summary field with the
        complete analysis results, including key events, phase metrics,
        and phase transitions. It also updates the session status if the
        analysis phase indicates a status change is appropriate.
        
        Args:
            session: The collaboration session to update.
            result: The analysis result.
        
        Requirements:
        - 10.7: Update collaboration session progress status when analysis completes
        - 10.8: Store analysis results in collaboration session records
        """
        # Store full analysis results in progress_summary (Requirement 10.8)
        session.progress_summary = {
            "current_phase": result.current_phase,
            "completed_steps": result.completed_steps,
            "pending_items": result.pending_items,
            "duration_minutes": result.total_duration_minutes,
            "last_analysis_at": result.analysis_timestamp,
            "key_events_count": len(result.key_events),
            "message_count": result.message_count,
            # Additional metadata for tracking
            "trigger_type": result.trigger_type,
            "phase_changed": result.phase_changed,
            "previous_phase": result.previous_phase,
            "analysis_version": result.analysis_version,
            # Full key events data
            "key_events": [
                {
                    "event_type": e.event_type.value if isinstance(e.event_type, KeyEventType) else e.event_type,
                    "description": e.description,
                    "timestamp": e.timestamp,
                    "confidence": e.confidence,
                }
                for e in result.key_events
            ],
            # Phase metrics data
            "phase_metrics": [
                {
                    "phase": m.phase,
                    "started_at": m.started_at,
                    "ended_at": m.ended_at,
                    "duration_minutes": m.duration_minutes,
                    "message_count": m.message_count,
                }
                for m in result.phase_metrics
            ],
            # Phase transitions data
            "phase_transitions": [
                {
                    "from_phase": t.from_phase,
                    "to_phase": t.to_phase,
                    "timestamp": t.timestamp,
                    "trigger_event": t.trigger_event,
                }
                for t in result.phase_transitions
            ],
        }
        
        # Update session status if phase indicates a status change (Requirement 10.7)
        # Map analysis phases to session statuses
        phase_to_status_map = {
            AnalysisPhase.CREATED.value: "created",
            AnalysisPhase.INVESTIGATION.value: "active",
            AnalysisPhase.DIAGNOSIS.value: "active",
            AnalysisPhase.RESOLUTION.value: "active",
            AnalysisPhase.VERIFICATION.value: "active",
            AnalysisPhase.COMPLETED.value: "resolved",
        }
        
        suggested_status = phase_to_status_map.get(result.current_phase)
        
        # Only update status if:
        # 1. The phase changed
        # 2. The suggested status is different from current status
        # 3. The transition is valid (don't revert from resolved/closed)
        if (
            result.phase_changed
            and suggested_status
            and suggested_status != session.status
            and session.status not in ("resolved", "closed")
        ):
            old_status = session.status
            session.status = suggested_status
            logger.info(
                "Updated session %s status from '%s' to '%s' based on phase change to '%s'",
                session.id, old_status, suggested_status, result.current_phase
            )
        
        # If phase is completed and session is not yet resolved, mark as resolved
        if (
            result.current_phase == AnalysisPhase.COMPLETED.value
            and session.status not in ("resolved", "closed")
        ):
            session.status = "resolved"
            session.resolved_at = datetime.now(UTC)
            logger.info(
                "Session %s marked as resolved based on completed phase",
                session.id
            )
        
        await self._db.flush()
        
        logger.debug(
            "Updated progress summary for session %s: phase=%s, events=%d, metrics=%d, phase_changed=%s",
            session.id, result.current_phase, len(result.key_events), 
            len(result.phase_metrics), result.phase_changed
        )
        
        # Auto-generate recommendations after analysis (Requirement 10.7, 11.1)
        # Only generate if session is active and not in terminal state
        if session.status in ("active", "created") and result.phase_changed:
            await self._auto_generate_recommendations(session, result)
    
    async def _auto_generate_recommendations(
        self,
        session: CollaborationSession,
        result: ProgressAnalysisResult,
    ) -> None:
        """Auto-generate recommendations after progress analysis.
        
        This method integrates with the RecommendationEngine to automatically
        generate recommendations when the analysis phase changes. This ensures
        that users receive timely suggestions based on the current progress.
        
        Args:
            session: The collaboration session.
            result: The analysis result.
        
        Requirements:
        - 10.7: Update collaboration session progress status when analysis completes
        - 11.1: Generate recommendations based on progress analysis results
        """
        try:
            # Lazy import to avoid circular dependencies
            from src.services.recommendation_engine import RecommendationEngine
            
            engine = RecommendationEngine(self._db, llm=self._llm)
            rec_result = await engine.generate_recommendations(
                session_id=session.id,
                context={
                    "current_phase": result.current_phase,
                    "completed_steps": result.completed_steps,
                    "pending_items": result.pending_items,
                    "key_events": [
                        {
                            "event_type": e.event_type.value if isinstance(e.event_type, KeyEventType) else e.event_type,
                            "description": e.description,
                        }
                        for e in result.key_events[-5:]  # Last 5 events for context
                    ],
                    "phase_changed": result.phase_changed,
                    "previous_phase": result.previous_phase,
                },
            )
            
            if rec_result.recommendations:
                logger.info(
                    "Auto-generated %d recommendations for session %s after phase change to '%s'",
                    len(rec_result.recommendations), session.id, result.current_phase
                )
            else:
                logger.debug(
                    "No recommendations generated for session %s (phase: %s)",
                    session.id, result.current_phase
                )
                
        except ImportError:
            logger.debug(
                "RecommendationEngine not available, skipping auto-recommendation generation"
            )
        except Exception as exc:
            # Don't fail the analysis if recommendation generation fails
            logger.warning(
                "Failed to auto-generate recommendations for session %s: %s",
                session.id, exc
            )
    
    async def start_periodic_analysis(
        self,
        session_id: uuid.UUID,
    ) -> None:
        """Start periodic analysis for a session.
        
        Begins a background task that periodically analyzes the session
        at the configured interval.
        
        Args:
            session_id: The session ID to analyze periodically.
        
        Requirements:
        - 10.1: Periodically analyze collaboration session messages
        - 10.6: Support configurable automatic analysis interval
        """
        if self._running:
            logger.warning(
                "Periodic analysis already running for analyzer instance"
            )
            return
        
        self._running = True
        self._scheduler_task = asyncio.create_task(
            self._periodic_analysis_loop(session_id)
        )
        
        logger.info(
            "Started periodic analysis for session %s (interval=%ds)",
            session_id, self._analysis_interval
        )
    
    async def stop_periodic_analysis(self) -> None:
        """Stop the periodic analysis task.
        
        Cancels the background analysis task if running.
        """
        self._running = False
        
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        
        logger.info("Stopped periodic analysis")
    
    async def _periodic_analysis_loop(
        self,
        session_id: uuid.UUID,
    ) -> None:
        """Background loop for periodic analysis.
        
        Args:
            session_id: The session ID to analyze.
        
        Requirements:
        - 10.1: Periodically analyze collaboration session messages
        - 10.6: Support configurable automatic analysis interval
        """
        while self._running:
            try:
                await asyncio.sleep(self._analysis_interval)
                
                if not self._running:
                    break
                
                # Check if session is still active
                session = await self._get_session_with_messages(session_id)
                if session is None:
                    logger.warning(
                        "Session %s not found, stopping periodic analysis",
                        session_id
                    )
                    break
                
                if session.status in ("closed", "resolved"):
                    logger.info(
                        "Session %s is %s, stopping periodic analysis",
                        session_id, session.status
                    )
                    break
                
                # Perform analysis with automatic trigger type
                await self.analyze_session(
                    session_id, 
                    update_session=True,
                    trigger_type="automatic"
                )
                
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(
                    "Error in periodic analysis for session %s: %s",
                    session_id, exc
                )
                # Continue running despite errors
    
    async def store_analysis_result(
        self,
        result: ProgressAnalysisResult,
        analysis_type: str = "manual",
        trigger_source: str = "api",
        analysis_config: dict[str, Any] | None = None,
    ) -> ProgressAnalysisRecord:
        """Store an analysis result as a persistent record.
        
        Creates a ProgressAnalysisRecord in the database to maintain
        a history of all analyses performed on a session.
        
        Args:
            result: The ProgressAnalysisResult to store.
            analysis_type: Type of analysis (manual, automatic, force_refresh).
            trigger_source: What triggered the analysis (api, scheduler, system).
            analysis_config: Configuration used for this analysis.
        
        Returns:
            The created ProgressAnalysisRecord.
        
        Requirements:
        - 10.8: Store analysis results in collaboration session records
        
        Example:
            ```python
            result = await analyzer.analyze_session(session_id)
            record = await analyzer.store_analysis_result(
                result,
                analysis_type="manual",
                trigger_source="api",
            )
            print(f"Stored analysis record: {record.id}")
            ```
        """
        logger.info(
            "Storing analysis result for session %s (type=%s, source=%s)",
            result.session_id, analysis_type, trigger_source
        )
        
        # Convert key events to dict format
        key_events_data = [
            {
                "event_type": e.event_type.value if isinstance(e.event_type, KeyEventType) else e.event_type,
                "description": e.description,
                "timestamp": e.timestamp,
                "message_id": e.message_id,
                "confidence": e.confidence,
            }
            for e in result.key_events
        ]
        
        # Convert phase metrics to dict format
        phase_metrics_data = [
            {
                "phase": m.phase,
                "started_at": m.started_at,
                "ended_at": m.ended_at,
                "duration_minutes": m.duration_minutes,
                "message_count": m.message_count,
            }
            for m in result.phase_metrics
        ]
        
        record = ProgressAnalysisRecord(
            session_id=result.session_id,
            current_phase=result.current_phase,
            completed_steps=result.completed_steps,
            pending_items=result.pending_items,
            key_events=key_events_data,
            phase_metrics=phase_metrics_data,
            total_duration_minutes=result.total_duration_minutes,
            message_count=result.message_count,
            analysis_type=analysis_type,
            trigger_source=trigger_source,
            raw_llm_output=result.raw_llm_output,
            error=result.error,
            analysis_config=analysis_config or {},
        )
        
        self._db.add(record)
        await self._db.flush()
        
        logger.debug(
            "Created analysis record %s for session %s",
            record.id, result.session_id
        )
        
        return record
    
    async def get_analysis_history(
        self,
        session_id: uuid.UUID,
        limit: int = 10,
        offset: int = 0,
        analysis_type: str | None = None,
    ) -> list[ProgressAnalysisRecord]:
        """Retrieve the analysis history for a session.
        
        Returns past analysis records for a collaboration session,
        ordered by creation time (most recent first).
        
        Args:
            session_id: The session ID to get history for.
            limit: Maximum number of records to return. Defaults to 10.
            offset: Number of records to skip. Defaults to 0.
            analysis_type: Optional filter by analysis type (manual, automatic, force_refresh).
        
        Returns:
            List of ProgressAnalysisRecord objects.
        
        Requirements:
        - 10.8: Store analysis results in collaboration session records
        
        Example:
            ```python
            history = await analyzer.get_analysis_history(
                session_id,
                limit=5,
                analysis_type="automatic",
            )
            for record in history:
                print(f"{record.created_at}: {record.current_phase}")
            ```
        """
        logger.debug(
            "Getting analysis history for session %s (limit=%d, offset=%d, type=%s)",
            session_id, limit, offset, analysis_type
        )
        
        query = (
            select(ProgressAnalysisRecord)
            .where(ProgressAnalysisRecord.session_id == session_id)
            .order_by(ProgressAnalysisRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        
        if analysis_type:
            query = query.where(ProgressAnalysisRecord.analysis_type == analysis_type)
        
        result = await self._db.execute(query)
        records = list(result.scalars().all())
        
        logger.debug(
            "Found %d analysis records for session %s",
            len(records), session_id
        )
        
        return records
    
    async def trigger_manual_analysis(
        self,
        session_id: uuid.UUID,
        force_refresh: bool = False,
        include_history: bool = False,
        store_result: bool = True,
    ) -> tuple[ProgressAnalysisResult, list[ProgressAnalysisRecord] | None]:
        """Manually trigger a progress analysis with enhanced options.
        
        Provides a comprehensive manual analysis trigger with options
        for force refresh and including historical analysis records.
        
        Args:
            session_id: The session ID to analyze.
            force_refresh: If True, bypasses any caching and performs
                a fresh analysis. Defaults to False.
            include_history: If True, also returns past analysis records.
                Defaults to False.
            store_result: If True, stores the analysis result as a record.
                Defaults to True.
        
        Returns:
            A tuple of (ProgressAnalysisResult, list of ProgressAnalysisRecord or None).
            The history list is None if include_history is False.
        
        Requirements:
        - 10.5: Support manual trigger of progress analysis
        - 10.8: Store analysis results in collaboration session records
        
        Example:
            ```python
            result, history = await analyzer.trigger_manual_analysis(
                session_id,
                force_refresh=True,
                include_history=True,
            )
            print(f"Current phase: {result.current_phase}")
            if history:
                print(f"Previous analyses: {len(history)}")
            ```
        """
        logger.info(
            "Manual analysis triggered for session %s (force_refresh=%s, include_history=%s)",
            session_id, force_refresh, include_history
        )
        
        # Determine analysis type
        analysis_type = "force_refresh" if force_refresh else "manual"
        
        # Build analysis config
        analysis_config = {
            "force_refresh": force_refresh,
            "include_history": include_history,
            "triggered_at": datetime.now(UTC).isoformat(),
        }
        
        # Perform the analysis with manual trigger type
        result = await self.analyze_session(
            session_id, 
            update_session=True,
            trigger_type=analysis_type
        )
        
        # Store the result if requested
        if store_result:
            await self.store_analysis_result(
                result,
                analysis_type=analysis_type,
                trigger_source="api",
                analysis_config=analysis_config,
            )
        
        # Get history if requested
        history: list[ProgressAnalysisRecord] | None = None
        if include_history:
            history = await self.get_analysis_history(session_id, limit=10)
        
        return result, history
    
    async def configure_auto_analysis(
        self,
        session_id: uuid.UUID,
        interval_seconds: int,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Configure automatic analysis interval for a session.
        
        Updates the session's configuration to enable or disable
        automatic progress analysis at the specified interval.
        
        Args:
            session_id: The session ID to configure.
            interval_seconds: The interval in seconds between automatic analyses.
                Must be at least 60 seconds.
            enabled: Whether automatic analysis is enabled. Defaults to True.
        
        Returns:
            A dict containing the updated configuration.
        
        Raises:
            ValueError: If the session is not found or interval is invalid.
        
        Requirements:
        - 10.6: Support configurable automatic analysis interval
        
        Example:
            ```python
            config = await analyzer.configure_auto_analysis(
                session_id,
                interval_seconds=300,  # 5 minutes
                enabled=True,
            )
            print(f"Auto-analysis configured: {config}")
            ```
        """
        logger.info(
            "Configuring auto-analysis for session %s (interval=%ds, enabled=%s)",
            session_id, interval_seconds, enabled
        )
        
        # Validate interval
        if interval_seconds < 60:
            raise ValueError("Analysis interval must be at least 60 seconds")
        
        # Load session
        session = await self._get_session_with_messages(session_id)
        if session is None:
            raise ValueError(f"Collaboration session '{session_id}' not found")
        
        # Update config_snapshot with auto-analysis settings
        config = session.config_snapshot or {}
        config["auto_analysis"] = {
            "enabled": enabled,
            "interval_seconds": interval_seconds,
            "configured_at": datetime.now(UTC).isoformat(),
        }
        session.config_snapshot = config
        
        await self._db.flush()
        
        # Update the analyzer's interval if this is the active session
        if enabled:
            self._analysis_interval = interval_seconds
        
        logger.debug(
            "Updated auto-analysis config for session %s: enabled=%s, interval=%ds",
            session_id, enabled, interval_seconds
        )
        
        return {
            "session_id": str(session_id),
            "auto_analysis_enabled": enabled,
            "interval_seconds": interval_seconds,
            "configured_at": config["auto_analysis"]["configured_at"],
        }
    
    async def get_auto_analysis_config(
        self,
        session_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Get the automatic analysis configuration for a session.
        
        Args:
            session_id: The session ID to get configuration for.
        
        Returns:
            A dict containing the auto-analysis configuration.
        
        Raises:
            ValueError: If the session is not found.
        
        Requirements:
        - 10.6: Support configurable automatic analysis interval
        """
        session = await self._get_session_with_messages(session_id)
        if session is None:
            raise ValueError(f"Collaboration session '{session_id}' not found")
        
        config = session.config_snapshot or {}
        auto_analysis = config.get("auto_analysis", {})
        
        return {
            "session_id": str(session_id),
            "auto_analysis_enabled": auto_analysis.get("enabled", False),
            "interval_seconds": auto_analysis.get("interval_seconds", self.DEFAULT_ANALYSIS_INTERVAL),
            "configured_at": auto_analysis.get("configured_at"),
        }
    
    async def start_auto_analysis_for_session(
        self,
        session_id: uuid.UUID,
    ) -> bool:
        """Start automatic analysis for a session based on its configuration.
        
        Reads the session's auto-analysis configuration and starts
        periodic analysis if enabled.
        
        Args:
            session_id: The session ID to start auto-analysis for.
        
        Returns:
            True if auto-analysis was started, False if not enabled.
        
        Raises:
            ValueError: If the session is not found.
        
        Requirements:
        - 10.1: Periodically analyze collaboration session messages
        - 10.6: Support configurable automatic analysis interval
        """
        config = await self.get_auto_analysis_config(session_id)
        
        if not config["auto_analysis_enabled"]:
            logger.info(
                "Auto-analysis not enabled for session %s",
                session_id
            )
            return False
        
        # Update interval from config
        self._analysis_interval = config["interval_seconds"]
        
        # Start periodic analysis
        await self.start_periodic_analysis(session_id)
        
        return True

    @property
    def analysis_interval(self) -> int:
        """Get the current analysis interval in seconds."""
        return self._analysis_interval
    
    @analysis_interval.setter
    def analysis_interval(self, value: int) -> None:
        """Set the analysis interval in seconds.
        
        Args:
            value: New interval in seconds. Must be positive.
        
        Raises:
            ValueError: If value is not positive.
        """
        if value <= 0:
            raise ValueError("Analysis interval must be positive")
        self._analysis_interval = value
    
    @property
    def is_running(self) -> bool:
        """Check if periodic analysis is currently running."""
        return self._running


async def analyze_session_progress(
    db: AsyncSession,
    session_id: uuid.UUID,
    update_session: bool = True,
) -> ProgressAnalysisResult:
    """Convenience function to analyze a session's progress.
    
    Creates a ProgressAnalyzer instance and performs analysis.
    
    Args:
        db: Async database session.
        session_id: The session ID to analyze.
        update_session: Whether to update the session record.
    
    Returns:
        ProgressAnalysisResult from the analysis.
    
    Example:
        ```python
        async with async_session_factory() as db:
            result = await analyze_session_progress(db, session_id)
            print(f"Phase: {result.current_phase}")
        ```
    """
    analyzer = ProgressAnalyzer(db)
    return await analyzer.analyze_session(session_id, update_session=update_session)


async def trigger_manual_analysis(
    db: AsyncSession,
    session_id: uuid.UUID,
    force_refresh: bool = False,
    include_history: bool = False,
    store_result: bool = True,
) -> tuple[ProgressAnalysisResult, list[ProgressAnalysisRecord] | None]:
    """Convenience function to manually trigger progress analysis.
    
    Creates a ProgressAnalyzer instance and performs manual analysis
    with the specified options.
    
    Args:
        db: Async database session.
        session_id: The session ID to analyze.
        force_refresh: If True, bypasses any caching.
        include_history: If True, also returns past analysis records.
        store_result: If True, stores the analysis result.
    
    Returns:
        A tuple of (ProgressAnalysisResult, list of ProgressAnalysisRecord or None).
    
    Requirements:
    - 10.5: Support manual trigger of progress analysis
    
    Example:
        ```python
        async with async_session_factory() as db:
            result, history = await trigger_manual_analysis(
                db, session_id, force_refresh=True, include_history=True
            )
        ```
    """
    analyzer = ProgressAnalyzer(db)
    return await analyzer.trigger_manual_analysis(
        session_id,
        force_refresh=force_refresh,
        include_history=include_history,
        store_result=store_result,
    )


async def get_analysis_history(
    db: AsyncSession,
    session_id: uuid.UUID,
    limit: int = 10,
    offset: int = 0,
    analysis_type: str | None = None,
) -> list[ProgressAnalysisRecord]:
    """Convenience function to get analysis history for a session.
    
    Args:
        db: Async database session.
        session_id: The session ID to get history for.
        limit: Maximum number of records to return.
        offset: Number of records to skip.
        analysis_type: Optional filter by analysis type.
    
    Returns:
        List of ProgressAnalysisRecord objects.
    
    Requirements:
    - 10.8: Store analysis results in collaboration session records
    
    Example:
        ```python
        async with async_session_factory() as db:
            history = await get_analysis_history(db, session_id, limit=5)
        ```
    """
    analyzer = ProgressAnalyzer(db)
    return await analyzer.get_analysis_history(
        session_id,
        limit=limit,
        offset=offset,
        analysis_type=analysis_type,
    )


async def configure_auto_analysis(
    db: AsyncSession,
    session_id: uuid.UUID,
    interval_seconds: int,
    enabled: bool = True,
) -> dict[str, Any]:
    """Convenience function to configure automatic analysis for a session.
    
    Args:
        db: Async database session.
        session_id: The session ID to configure.
        interval_seconds: The interval in seconds between analyses.
        enabled: Whether automatic analysis is enabled.
    
    Returns:
        A dict containing the updated configuration.
    
    Requirements:
    - 10.6: Support configurable automatic analysis interval
    
    Example:
        ```python
        async with async_session_factory() as db:
            config = await configure_auto_analysis(
                db, session_id, interval_seconds=300
            )
        ```
    """
    analyzer = ProgressAnalyzer(db)
    return await analyzer.configure_auto_analysis(
        session_id,
        interval_seconds=interval_seconds,
        enabled=enabled,
    )
