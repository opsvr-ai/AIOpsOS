"""Basic tests for ProgressAnalyzer key event identification and progress summary.

These tests verify the enhanced key event identification logic and progress
summary generation for task 14.2.

Requirements:
- 10.2: Identify key events (problem confirmation, solution discussion, 
        operation execution, result verification)
- 10.3: Generate progress summary (completed steps, current phase, pending items)
- 10.4: Calculate processing duration and time spent in each phase
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.services.progress_analyzer import (
    AnalysisPhase,
    KeyEvent,
    KeyEventType,
    PhaseMetrics,
    PhaseTransition,
    ProgressAnalyzer,
    ProgressAnalysisResult,
    KEY_EVENT_KEYWORDS,
    KEY_EVENT_PATTERNS,
    CONTEXT_BOOST_PATTERNS,
)


class TestKeyEventPatterns:
    """Test the regex patterns for key event identification."""

    def test_problem_confirmed_patterns(self):
        """Test patterns for problem confirmation events."""
        test_cases = [
            ("确认故障：数据库连接超时", True),
            ("问题确认：服务不可用", True),
            ("影响范围：所有用户", True),
            ("定位到原因是内存泄漏", True),
            ("初步判断：网络问题", True),
            ("服务超时异常", True),
            ("错误率上升到10%", True),
            ("今天天气不错", False),
        ]
        
        patterns = KEY_EVENT_PATTERNS[KeyEventType.PROBLEM_CONFIRMED]
        for content, should_match in test_cases:
            matched = any(p.search(content) for p in patterns)
            assert matched == should_match, f"Pattern match failed for: {content}"

    def test_solution_discussed_patterns(self):
        """Test patterns for solution discussion events."""
        test_cases = [
            ("建议方案：重启服务", True),
            ("决定执行回滚操作", True),
            ("可以尝试重启", True),
            ("修复方案已确定", True),
            ("临时方案：扩容", True),
            ("评估方案风险", True),
            ("今天天气不错", False),
        ]
        
        patterns = KEY_EVENT_PATTERNS[KeyEventType.SOLUTION_DISCUSSED]
        for content, should_match in test_cases:
            matched = any(p.search(content) for p in patterns)
            assert matched == should_match, f"Pattern match failed for: {content}"

    def test_operation_executed_patterns(self):
        """Test patterns for operation execution events."""
        test_cases = [
            ("已执行重启操作", True),
            ("正在执行配置变更", True),
            ("部署完成", True),
            ("回滚成功", True),
            ("扩容完成", True),
            ("变更已完成", True),
            ("今天天气不错", False),
        ]
        
        patterns = KEY_EVENT_PATTERNS[KeyEventType.OPERATION_EXECUTED]
        for content, should_match in test_cases:
            matched = any(p.search(content) for p in patterns)
            assert matched == should_match, f"Pattern match failed for: {content}"

    def test_result_verified_patterns(self):
        """Test patterns for result verification events."""
        test_cases = [
            ("验证通过", True),
            ("服务恢复正常", True),
            ("问题已解决", True),
            ("监控指标正常", True),
            ("确认效果良好", True),
            ("功能恢复正常", True),
            ("今天天气不错", False),
        ]
        
        patterns = KEY_EVENT_PATTERNS[KeyEventType.RESULT_VERIFIED]
        for content, should_match in test_cases:
            matched = any(p.search(content) for p in patterns)
            assert matched == should_match, f"Pattern match failed for: {content}"


class TestProgressAnalyzerEventDetection:
    """Test the ProgressAnalyzer event detection methods."""

    @pytest.fixture
    def analyzer(self):
        """Create a ProgressAnalyzer instance with mock db."""
        mock_db = MagicMock()
        return ProgressAnalyzer(mock_db)

    def test_detect_problem_confirmed_event(self, analyzer):
        """Test detection of problem confirmation events."""
        content = "确认故障：数据库连接超时，影响范围：所有用户"
        event = analyzer._detect_event_from_content(
            content, content.lower(), "2024-01-01 12:00:00", "msg-1"
        )
        
        assert event is not None
        assert event.event_type == KeyEventType.PROBLEM_CONFIRMED
        assert event.confidence >= 0.4
        assert event.timestamp == "2024-01-01 12:00:00"
        assert event.message_id == "msg-1"

    def test_detect_solution_discussed_event(self, analyzer):
        """Test detection of solution discussion events."""
        content = "建议方案：重启数据库连接池，评估风险较低"
        event = analyzer._detect_event_from_content(
            content, content.lower(), "2024-01-01 12:05:00", "msg-2"
        )
        
        assert event is not None
        assert event.event_type == KeyEventType.SOLUTION_DISCUSSED
        assert event.confidence >= 0.4

    def test_detect_operation_executed_event(self, analyzer):
        """Test detection of operation execution events."""
        content = "已执行重启操作，操作完成"
        event = analyzer._detect_event_from_content(
            content, content.lower(), "2024-01-01 12:10:00", "msg-3"
        )
        
        assert event is not None
        assert event.event_type == KeyEventType.OPERATION_EXECUTED
        assert event.confidence >= 0.4

    def test_detect_result_verified_event(self, analyzer):
        """Test detection of result verification events."""
        content = "验证通过，服务恢复正常，监控指标正常"
        event = analyzer._detect_event_from_content(
            content, content.lower(), "2024-01-01 12:15:00", "msg-4"
        )
        
        assert event is not None
        assert event.event_type == KeyEventType.RESULT_VERIFIED
        assert event.confidence >= 0.4

    def test_detect_escalation_event(self, analyzer):
        """Test detection of escalation events."""
        content = "请求支援，需要DBA帮忙处理"
        event = analyzer._detect_event_from_content(
            content, content.lower(), "2024-01-01 12:20:00", "msg-5"
        )
        
        assert event is not None
        assert event.event_type == KeyEventType.ESCALATION
        assert event.confidence >= 0.4

    def test_detect_status_update_event(self, analyzer):
        """Test detection of status update events."""
        content = "进度更新：目前处理进展顺利"
        event = analyzer._detect_event_from_content(
            content, content.lower(), "2024-01-01 12:25:00", "msg-6"
        )
        
        assert event is not None
        assert event.event_type == KeyEventType.STATUS_UPDATE
        assert event.confidence >= 0.4

    def test_no_event_detected_for_irrelevant_content(self, analyzer):
        """Test that no event is detected for irrelevant content."""
        content = "今天天气不错，适合出去玩"
        event = analyzer._detect_event_from_content(
            content, content.lower(), "2024-01-01 12:30:00", "msg-7"
        )
        
        assert event is None

    def test_confidence_boost_with_context_patterns(self, analyzer):
        """Test that context patterns boost confidence."""
        # Content with context boost pattern
        content_with_context = "经过排查，确认故障原因是数据库连接超时"
        event_with_context = analyzer._detect_event_from_content(
            content_with_context, content_with_context.lower(), 
            "2024-01-01 12:00:00", "msg-1"
        )
        
        # Content without context boost pattern
        content_without_context = "确认故障原因是数据库连接超时"
        event_without_context = analyzer._detect_event_from_content(
            content_without_context, content_without_context.lower(),
            "2024-01-01 12:00:00", "msg-2"
        )
        
        assert event_with_context is not None
        assert event_without_context is not None
        # Context pattern should boost confidence
        assert event_with_context.confidence >= event_without_context.confidence


class TestProgressAnalyzerPhaseDetection:
    """Test the ProgressAnalyzer phase detection methods."""

    @pytest.fixture
    def analyzer(self):
        """Create a ProgressAnalyzer instance with mock db."""
        mock_db = MagicMock()
        return ProgressAnalyzer(mock_db)

    def test_determine_phase_from_no_events(self, analyzer):
        """Test phase determination with no events."""
        mock_session = MagicMock()
        mock_session.status = "active"
        
        phase = analyzer._determine_phase_from_events(mock_session, [])
        assert phase == AnalysisPhase.INVESTIGATION.value

    def test_determine_phase_from_problem_confirmed(self, analyzer):
        """Test phase determination with problem confirmed event."""
        mock_session = MagicMock()
        mock_session.status = "active"
        
        events = [
            KeyEvent(
                event_type=KeyEventType.PROBLEM_CONFIRMED,
                description="问题确认",
                timestamp="2024-01-01 12:00:00",
            )
        ]
        
        phase = analyzer._determine_phase_from_events(mock_session, events)
        assert phase == AnalysisPhase.DIAGNOSIS.value

    def test_determine_phase_from_solution_discussed(self, analyzer):
        """Test phase determination with solution discussed event."""
        mock_session = MagicMock()
        mock_session.status = "active"
        
        events = [
            KeyEvent(
                event_type=KeyEventType.SOLUTION_DISCUSSED,
                description="方案讨论",
                timestamp="2024-01-01 12:00:00",
            )
        ]
        
        phase = analyzer._determine_phase_from_events(mock_session, events)
        assert phase == AnalysisPhase.RESOLUTION.value

    def test_determine_phase_from_operation_executed(self, analyzer):
        """Test phase determination with operation executed event."""
        mock_session = MagicMock()
        mock_session.status = "active"
        
        events = [
            KeyEvent(
                event_type=KeyEventType.OPERATION_EXECUTED,
                description="操作执行",
                timestamp="2024-01-01 12:00:00",
            )
        ]
        
        phase = analyzer._determine_phase_from_events(mock_session, events)
        assert phase == AnalysisPhase.VERIFICATION.value

    def test_determine_phase_from_result_verified(self, analyzer):
        """Test phase determination with result verified event."""
        mock_session = MagicMock()
        mock_session.status = "active"
        
        events = [
            KeyEvent(
                event_type=KeyEventType.RESULT_VERIFIED,
                description="结果验证",
                timestamp="2024-01-01 12:00:00",
            )
        ]
        
        phase = analyzer._determine_phase_from_events(mock_session, events)
        assert phase == AnalysisPhase.COMPLETED.value

    def test_determine_phase_respects_session_status(self, analyzer):
        """Test that session status takes precedence."""
        mock_session = MagicMock()
        mock_session.status = "closed"
        
        events = [
            KeyEvent(
                event_type=KeyEventType.PROBLEM_CONFIRMED,
                description="问题确认",
                timestamp="2024-01-01 12:00:00",
            )
        ]
        
        phase = analyzer._determine_phase_from_events(mock_session, events)
        assert phase == AnalysisPhase.COMPLETED.value


class TestProgressAnalyzerCompletedSteps:
    """Test the ProgressAnalyzer completed steps generation."""

    @pytest.fixture
    def analyzer(self):
        """Create a ProgressAnalyzer instance with mock db."""
        mock_db = MagicMock()
        return ProgressAnalyzer(mock_db)

    def test_build_completed_steps_empty(self, analyzer):
        """Test completed steps with no events."""
        steps = analyzer._build_completed_steps([])
        assert len(steps) == 1
        assert "协同会话已创建" in steps[0]

    def test_build_completed_steps_with_events(self, analyzer):
        """Test completed steps with multiple events."""
        events = [
            KeyEvent(
                event_type=KeyEventType.PROBLEM_CONFIRMED,
                description="问题确认",
                timestamp="2024-01-01 12:00:00",
            ),
            KeyEvent(
                event_type=KeyEventType.SOLUTION_DISCUSSED,
                description="方案讨论",
                timestamp="2024-01-01 12:05:00",
            ),
            KeyEvent(
                event_type=KeyEventType.OPERATION_EXECUTED,
                description="操作执行",
                timestamp="2024-01-01 12:10:00",
            ),
        ]
        
        steps = analyzer._build_completed_steps(events)
        
        assert len(steps) == 4  # 1 initial + 3 events
        assert any("问题确认" in step for step in steps)
        assert any("方案讨论" in step for step in steps)
        assert any("操作执行" in step for step in steps)

    def test_build_completed_steps_includes_timestamps(self, analyzer):
        """Test that completed steps include timestamps."""
        events = [
            KeyEvent(
                event_type=KeyEventType.PROBLEM_CONFIRMED,
                description="问题确认",
                timestamp="2024-01-01 12:00:00",
            ),
        ]
        
        steps = analyzer._build_completed_steps(events)
        
        # Should include time in the step
        assert any("12:00" in step for step in steps)


class TestProgressAnalyzerPendingItems:
    """Test the ProgressAnalyzer pending items generation."""

    @pytest.fixture
    def analyzer(self):
        """Create a ProgressAnalyzer instance with mock db."""
        mock_db = MagicMock()
        return ProgressAnalyzer(mock_db)

    def test_build_pending_items_investigation(self, analyzer):
        """Test pending items for investigation phase."""
        items = analyzer._build_pending_items(AnalysisPhase.INVESTIGATION.value)
        
        assert len(items) > 0
        assert any("确认" in item or "收集" in item for item in items)

    def test_build_pending_items_diagnosis(self, analyzer):
        """Test pending items for diagnosis phase."""
        items = analyzer._build_pending_items(AnalysisPhase.DIAGNOSIS.value)
        
        assert len(items) > 0
        assert any("根因" in item or "方案" in item for item in items)

    def test_build_pending_items_resolution(self, analyzer):
        """Test pending items for resolution phase."""
        items = analyzer._build_pending_items(AnalysisPhase.RESOLUTION.value)
        
        assert len(items) > 0
        assert any("执行" in item or "修复" in item for item in items)

    def test_build_pending_items_verification(self, analyzer):
        """Test pending items for verification phase."""
        items = analyzer._build_pending_items(AnalysisPhase.VERIFICATION.value)
        
        assert len(items) > 0
        assert any("验证" in item or "确认" in item for item in items)

    def test_build_pending_items_completed(self, analyzer):
        """Test pending items for completed phase."""
        items = analyzer._build_pending_items(AnalysisPhase.COMPLETED.value)
        
        assert len(items) == 0


class TestProgressAnalysisResult:
    """Test the ProgressAnalysisResult dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = ProgressAnalysisResult(
            session_id=uuid.uuid4(),
            current_phase=AnalysisPhase.DIAGNOSIS.value,
            completed_steps=["步骤1", "步骤2"],
            pending_items=["待办1"],
            key_events=[
                KeyEvent(
                    event_type=KeyEventType.PROBLEM_CONFIRMED,
                    description="问题确认",
                    timestamp="2024-01-01 12:00:00",
                    confidence=0.8,
                )
            ],
            phase_metrics=[
                PhaseMetrics(
                    phase=AnalysisPhase.INVESTIGATION.value,
                    started_at="2024-01-01 11:00:00",
                    ended_at="2024-01-01 12:00:00",
                    duration_minutes=60,
                    message_count=10,
                    key_events_in_phase=["problem_confirmed"],
                )
            ],
            phase_transitions=[
                PhaseTransition(
                    from_phase=AnalysisPhase.INVESTIGATION.value,
                    to_phase=AnalysisPhase.DIAGNOSIS.value,
                    timestamp="2024-01-01 12:00:00",
                    trigger_event="problem_confirmed",
                )
            ],
            total_duration_minutes=60,
            message_count=10,
        )
        
        d = result.to_dict()
        
        assert d["current_phase"] == AnalysisPhase.DIAGNOSIS.value
        assert len(d["completed_steps"]) == 2
        assert len(d["pending_items"]) == 1
        assert len(d["key_events"]) == 1
        assert d["key_events"][0]["event_type"] == "problem_confirmed"
        assert len(d["phase_metrics"]) == 1
        assert d["phase_metrics"][0]["key_events_in_phase"] == ["problem_confirmed"]
        assert len(d["phase_transitions"]) == 1
        assert d["total_duration_minutes"] == 60
        assert d["message_count"] == 10

    def test_to_summary_dict(self):
        """Test conversion to summary dictionary."""
        result = ProgressAnalysisResult(
            session_id=uuid.uuid4(),
            current_phase=AnalysisPhase.DIAGNOSIS.value,
            completed_steps=["步骤1", "步骤2"],
            pending_items=["待办1"],
            key_events=[
                KeyEvent(
                    event_type=KeyEventType.PROBLEM_CONFIRMED,
                    description="问题确认",
                    timestamp="2024-01-01 12:00:00",
                )
            ],
            total_duration_minutes=60,
            message_count=10,
        )
        
        d = result.to_summary_dict()
        
        assert d["current_phase"] == AnalysisPhase.DIAGNOSIS.value
        assert d["duration_minutes"] == 60
        assert d["key_events_count"] == 1
        assert d["message_count"] == 10
        assert "last_analysis_at" in d
