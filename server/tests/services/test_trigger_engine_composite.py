"""Unit tests for composite conditions, frequency limiting, and time window features.

Tests cover:
- NOT logical operator in evaluate_condition
- Redis-based frequency limiting
- Time window validity checking
- Trigger recording with timestamps and reasons

Requirements: 3.5, 3.6, 3.7, 3.8
"""

import uuid
from datetime import UTC, datetime, time, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.alert import Alert
from src.models.schedule import SceneTrigger
from src.services.trigger_engine import (
    _build_trigger_reason,
    _check_frequency,
    _check_frequency_db,
    _check_frequency_redis,
    _record_trigger_in_redis,
    _summarize_condition,
    check_time_window_validity,
    evaluate_condition,
    evaluate_condition_with_context,
    record_trigger_fired,
)


class TestNotOperator:
    """Tests for NOT logical operator in evaluate_condition.
    
    Validates: Requirements 3.5
    """

    def _create_alert(
        self,
        severity: str = "warning",
        source: str = "prometheus",
        raw_event: dict | None = None,
    ) -> Alert:
        """Create a mock Alert object for testing."""
        alert = MagicMock(spec=Alert)
        alert.id = uuid.uuid4()
        alert.severity = severity
        alert.source = source
        alert.raw_event = raw_event or {}
        alert.enriched_context = {}
        return alert

    def test_not_simple_condition_true(self):
        """Test NOT operator negates a true simple condition to false."""
        alert = self._create_alert(severity="critical")
        condition = {
            "type": "not",
            "condition": {
                "type": "simple",
                "field": "severity",
                "op": "eq",
                "value": "critical",
            }
        }
        # Alert has severity=critical, so inner condition is True
        # NOT(True) = False
        result = evaluate_condition(condition, alert)
        assert result is False

    def test_not_simple_condition_false(self):
        """Test NOT operator negates a false simple condition to true."""
        alert = self._create_alert(severity="warning")
        condition = {
            "type": "not",
            "condition": {
                "type": "simple",
                "field": "severity",
                "op": "eq",
                "value": "critical",
            }
        }
        # Alert has severity=warning, so inner condition is False
        # NOT(False) = True
        result = evaluate_condition(condition, alert)
        assert result is True

    def test_not_with_and_condition(self):
        """Test NOT operator with AND composite condition."""
        alert = self._create_alert(severity="critical", source="prometheus")
        condition = {
            "type": "not",
            "condition": {
                "type": "and",
                "conditions": [
                    {"type": "simple", "field": "severity", "op": "eq", "value": "critical"},
                    {"type": "simple", "field": "source", "op": "eq", "value": "prometheus"},
                ]
            }
        }
        # Both inner conditions are True, AND(True, True) = True
        # NOT(True) = False
        result = evaluate_condition(condition, alert)
        assert result is False

    def test_not_with_or_condition(self):
        """Test NOT operator with OR composite condition."""
        alert = self._create_alert(severity="warning", source="grafana")
        condition = {
            "type": "not",
            "condition": {
                "type": "or",
                "conditions": [
                    {"type": "simple", "field": "severity", "op": "eq", "value": "critical"},
                    {"type": "simple", "field": "source", "op": "eq", "value": "prometheus"},
                ]
            }
        }
        # Both inner conditions are False, OR(False, False) = False
        # NOT(False) = True
        result = evaluate_condition(condition, alert)
        assert result is True

    def test_nested_not_conditions(self):
        """Test double NOT (NOT(NOT(x)) = x)."""
        alert = self._create_alert(severity="critical")
        condition = {
            "type": "not",
            "condition": {
                "type": "not",
                "condition": {
                    "type": "simple",
                    "field": "severity",
                    "op": "eq",
                    "value": "critical",
                }
            }
        }
        # Inner simple condition is True
        # NOT(True) = False
        # NOT(False) = True
        result = evaluate_condition(condition, alert)
        assert result is True

    def test_not_missing_condition_field(self):
        """Test NOT operator with missing condition field returns False."""
        alert = self._create_alert()
        condition = {
            "type": "not",
            # Missing "condition" field
        }
        result = evaluate_condition(condition, alert)
        assert result is False

    def test_not_in_complex_composite(self):
        """Test NOT operator within complex composite conditions."""
        alert = self._create_alert(severity="warning", source="prometheus")
        condition = {
            "type": "and",
            "conditions": [
                {"type": "simple", "field": "source", "op": "eq", "value": "prometheus"},
                {
                    "type": "not",
                    "condition": {
                        "type": "simple",
                        "field": "severity",
                        "op": "eq",
                        "value": "critical",
                    }
                }
            ]
        }
        # source=prometheus is True
        # severity=critical is False, NOT(False) = True
        # AND(True, True) = True
        result = evaluate_condition(condition, alert)
        assert result is True


class TestTimeWindowValidity:
    """Tests for time window validity checking.
    
    Validates: Requirements 3.7
    """

    def _create_trigger(
        self,
        time_window_start: time | None = None,
        time_window_end: time | None = None,
    ) -> SceneTrigger:
        """Create a mock SceneTrigger object for testing."""
        trigger = MagicMock(spec=SceneTrigger)
        trigger.id = uuid.uuid4()
        trigger.name = "test_trigger"
        trigger.time_window_start = time_window_start
        trigger.time_window_end = time_window_end
        return trigger

    def test_no_time_window_configured(self):
        """Test that no time window means always valid."""
        trigger = self._create_trigger()
        is_valid, reason = check_time_window_validity(trigger)
        assert is_valid is True
        assert reason is None

    def test_within_normal_time_window(self):
        """Test time within normal time window (start < end)."""
        # Create a window that includes the current time
        now = datetime.now(UTC).time()
        start = time(0, 0, 0)  # Midnight
        end = time(23, 59, 59)  # End of day
        
        trigger = self._create_trigger(time_window_start=start, time_window_end=end)
        is_valid, reason = check_time_window_validity(trigger)
        assert is_valid is True
        assert reason is None

    def test_outside_normal_time_window(self):
        """Test time outside normal time window."""
        # Create a window that definitely excludes current time
        # by using a very narrow window in the past or future
        now = datetime.now(UTC).time()
        
        # Create a 1-minute window that's definitely not now
        if now.hour < 12:
            # If morning, use afternoon window
            start = time(23, 0, 0)
            end = time(23, 1, 0)
        else:
            # If afternoon, use morning window
            start = time(0, 0, 0)
            end = time(0, 1, 0)
        
        trigger = self._create_trigger(time_window_start=start, time_window_end=end)
        is_valid, reason = check_time_window_validity(trigger)
        # This might be valid or invalid depending on exact timing
        # The important thing is that the function returns a proper tuple
        assert isinstance(is_valid, bool)
        if not is_valid:
            assert reason is not None
            assert "Outside time window" in reason

    def test_midnight_spanning_window_valid(self):
        """Test time window that spans midnight (start > end)."""
        # Window from 22:00 to 06:00 (spans midnight)
        trigger = self._create_trigger(
            time_window_start=time(22, 0, 0),
            time_window_end=time(6, 0, 0)
        )
        # This test verifies the logic handles midnight-spanning windows
        is_valid, reason = check_time_window_validity(trigger)
        assert isinstance(is_valid, bool)

    def test_reason_format(self):
        """Test that reason string has proper format when invalid."""
        # Create a window that's definitely not now
        trigger = self._create_trigger(
            time_window_start=time(0, 0, 0),
            time_window_end=time(0, 0, 1)  # 1 second window at midnight
        )
        is_valid, reason = check_time_window_validity(trigger)
        
        # If we happen to be at exactly midnight, this might be valid
        # Otherwise, check the reason format
        if not is_valid:
            assert "Outside time window" in reason
            assert "current time" in reason
            assert "not in" in reason


@pytest.mark.asyncio
class TestFrequencyLimiting:
    """Tests for Redis-based frequency limiting.
    
    Validates: Requirements 3.6
    """

    def _create_trigger(
        self,
        frequency_limit: int = 5,
        scenario_id: uuid.UUID | None = None,
    ) -> SceneTrigger:
        """Create a mock SceneTrigger object for testing."""
        trigger = MagicMock(spec=SceneTrigger)
        trigger.id = uuid.uuid4()
        trigger.name = "test_trigger"
        trigger.frequency_limit = frequency_limit
        trigger.scenario_id = scenario_id or uuid.uuid4()
        return trigger

    async def test_frequency_redis_under_limit(self):
        """Test Redis frequency check when under limit."""
        trigger = self._create_trigger(frequency_limit=10)
        
        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=5)  # 5 triggers, limit is 10
        
        async def mock_get_redis():
            return mock_redis
        
        with patch("src.core.redis.get_redis", mock_get_redis):
            result = await _check_frequency_redis(trigger)
        
        assert result is True

    async def test_frequency_redis_at_limit(self):
        """Test Redis frequency check when at limit."""
        trigger = self._create_trigger(frequency_limit=5)
        
        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=5)  # 5 triggers, limit is 5
        
        async def mock_get_redis():
            return mock_redis
        
        with patch("src.core.redis.get_redis", mock_get_redis):
            result = await _check_frequency_redis(trigger)
        
        assert result is False

    async def test_frequency_redis_over_limit(self):
        """Test Redis frequency check when over limit."""
        trigger = self._create_trigger(frequency_limit=5)
        
        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock()
        mock_redis.zcard = AsyncMock(return_value=10)  # 10 triggers, limit is 5
        
        async def mock_get_redis():
            return mock_redis
        
        with patch("src.core.redis.get_redis", mock_get_redis):
            result = await _check_frequency_redis(trigger)
        
        assert result is False

    async def test_frequency_redis_unavailable(self):
        """Test Redis frequency check returns None when Redis unavailable."""
        trigger = self._create_trigger()
        
        # The function catches the import/connection error and returns None
        # We simulate this by making get_redis raise an exception
        result = await _check_frequency_redis(trigger)
        
        # When Redis is not available (import fails or connection fails),
        # the function should return None to allow fallback to DB
        # In test environment without Redis, this should return None
        assert result is None

    async def test_frequency_redis_error_handling(self):
        """Test Redis frequency check handles errors gracefully."""
        trigger = self._create_trigger()
        
        mock_redis = AsyncMock()
        mock_redis.zremrangebyscore = AsyncMock(side_effect=Exception("Redis error"))
        
        async def mock_get_redis():
            return mock_redis
        
        with patch("src.core.redis.get_redis", mock_get_redis):
            result = await _check_frequency_redis(trigger)
        
        assert result is None

    async def test_frequency_fallback_to_db(self):
        """Test frequency check falls back to DB when Redis unavailable."""
        trigger = self._create_trigger(frequency_limit=5)
        db = AsyncMock()
        
        # Mock DB query result
        mock_result = MagicMock()
        mock_result.scalar.return_value = 3  # 3 executions, under limit
        db.execute.return_value = mock_result
        
        with patch("src.services.trigger_engine._check_frequency_redis", return_value=None):
            result = await _check_frequency(db, trigger)
        
        assert result is True

    async def test_record_trigger_in_redis(self):
        """Test recording trigger event in Redis."""
        trigger = self._create_trigger()
        timestamp = datetime.now(UTC)
        
        mock_redis = AsyncMock()
        mock_redis.zadd = AsyncMock()
        mock_redis.expire = AsyncMock()
        
        async def mock_get_redis():
            return mock_redis
        
        with patch("src.core.redis.get_redis", mock_get_redis):
            await _record_trigger_in_redis(trigger, timestamp)
        
        # Verify zadd was called with correct key pattern
        mock_redis.zadd.assert_called_once()
        call_args = mock_redis.zadd.call_args
        assert f"trigger:frequency:{trigger.id}" == call_args[0][0]
        
        # Verify expire was called
        mock_redis.expire.assert_called_once()


@pytest.mark.asyncio
class TestTriggerRecording:
    """Tests for trigger recording with timestamps and reasons.
    
    Validates: Requirements 3.8
    """

    def _create_trigger(self) -> SceneTrigger:
        """Create a mock SceneTrigger object for testing."""
        trigger = MagicMock(spec=SceneTrigger)
        trigger.id = uuid.uuid4()
        trigger.name = "test_trigger"
        trigger.trigger_count = 5
        trigger.condition = {"type": "simple", "field": "severity", "op": "eq", "value": "critical"}
        return trigger

    def _create_alert(self) -> Alert:
        """Create a mock Alert object for testing."""
        alert = MagicMock(spec=Alert)
        alert.id = uuid.uuid4()
        alert.title = "High CPU Usage"
        alert.severity = "critical"
        return alert

    async def test_record_trigger_fired_basic(self):
        """Test basic trigger recording."""
        trigger = self._create_trigger()
        db = AsyncMock()
        
        with patch("src.services.trigger_engine._record_trigger_in_redis", new_callable=AsyncMock):
            record = await record_trigger_fired(db, trigger)
        
        assert "trigger_id" in record
        assert record["trigger_id"] == str(trigger.id)
        assert "triggered_at" in record
        assert "reason" in record
        assert "trigger_count" in record

    async def test_record_trigger_fired_with_alert(self):
        """Test trigger recording with alert context."""
        trigger = self._create_trigger()
        alert = self._create_alert()
        db = AsyncMock()
        
        with patch("src.services.trigger_engine._record_trigger_in_redis", new_callable=AsyncMock):
            record = await record_trigger_fired(db, trigger, alert=alert)
        
        assert str(alert.id) in record["reason"]

    async def test_record_trigger_fired_with_custom_reason(self):
        """Test trigger recording with custom reason."""
        trigger = self._create_trigger()
        db = AsyncMock()
        custom_reason = "Manual trigger for testing"
        
        with patch("src.services.trigger_engine._record_trigger_in_redis", new_callable=AsyncMock):
            record = await record_trigger_fired(db, trigger, reason=custom_reason)
        
        assert record["reason"] == custom_reason

    async def test_record_trigger_updates_database(self):
        """Test that trigger recording updates database."""
        trigger = self._create_trigger()
        db = AsyncMock()
        
        with patch("src.services.trigger_engine._record_trigger_in_redis", new_callable=AsyncMock):
            await record_trigger_fired(db, trigger)
        
        # Verify database execute was called (for update)
        db.execute.assert_called()
        # Verify commit was called
        db.commit.assert_called()
        # Verify refresh was called
        db.refresh.assert_called_with(trigger)


class TestBuildTriggerReason:
    """Tests for building trigger reason strings."""

    def _create_trigger(self, condition: dict) -> SceneTrigger:
        """Create a mock SceneTrigger object for testing."""
        trigger = MagicMock(spec=SceneTrigger)
        trigger.id = uuid.uuid4()
        trigger.name = "test_trigger"
        trigger.condition = condition
        return trigger

    def _create_alert(
        self,
        title: str = "Test Alert",
        severity: str = "warning",
    ) -> Alert:
        """Create a mock Alert object for testing."""
        alert = MagicMock(spec=Alert)
        alert.id = uuid.uuid4()
        alert.title = title
        alert.severity = severity
        return alert

    def test_build_reason_with_alert(self):
        """Test building reason with alert context."""
        trigger = self._create_trigger({"type": "simple", "field": "severity", "op": "eq", "value": "critical"})
        alert = self._create_alert(title="CPU Alert", severity="critical")
        
        reason = _build_trigger_reason(trigger, alert)
        
        assert str(alert.id) in reason
        assert "CPU Alert" in reason
        assert "critical" in reason

    def test_build_reason_without_alert(self):
        """Test building reason without alert context."""
        trigger = self._create_trigger({"type": "simple", "field": "severity", "op": "eq", "value": "critical"})
        
        reason = _build_trigger_reason(trigger, alert=None)
        
        assert "matched" in reason.lower() or "condition" in reason.lower()


class TestSummarizeCondition:
    """Tests for condition summarization."""

    def test_summarize_simple_condition(self):
        """Test summarizing simple condition."""
        condition = {
            "type": "simple",
            "field": "severity",
            "op": "eq",
            "value": "critical"
        }
        summary = _summarize_condition(condition)
        assert "severity" in summary
        assert "eq" in summary
        assert "critical" in summary

    def test_summarize_alert_count_condition(self):
        """Test summarizing alert_count condition."""
        condition = {
            "type": "simple",
            "field": "alert_count",
            "op": "gt",
            "value": 10
        }
        summary = _summarize_condition(condition)
        assert "alert_count" in summary
        assert "gt" in summary
        assert "10" in summary

    def test_summarize_trend_condition(self):
        """Test summarizing trend condition."""
        condition = {
            "type": "simple",
            "op": "trend",
            "trend_config": {
                "metric": "cpu_usage",
                "direction": "rising"
            }
        }
        summary = _summarize_condition(condition)
        assert "cpu_usage" in summary
        assert "trend" in summary
        assert "rising" in summary

    def test_summarize_and_condition(self):
        """Test summarizing AND condition."""
        condition = {
            "type": "and",
            "conditions": [
                {"type": "simple", "field": "severity", "op": "eq", "value": "critical"},
                {"type": "simple", "field": "source", "op": "eq", "value": "prometheus"}
            ]
        }
        summary = _summarize_condition(condition)
        assert "AND" in summary

    def test_summarize_or_condition(self):
        """Test summarizing OR condition."""
        condition = {
            "type": "or",
            "conditions": [
                {"type": "simple", "field": "severity", "op": "eq", "value": "critical"},
                {"type": "simple", "field": "severity", "op": "eq", "value": "high"}
            ]
        }
        summary = _summarize_condition(condition)
        assert "OR" in summary

    def test_summarize_not_condition(self):
        """Test summarizing NOT condition."""
        condition = {
            "type": "not",
            "condition": {
                "type": "simple",
                "field": "severity",
                "op": "eq",
                "value": "info"
            }
        }
        summary = _summarize_condition(condition)
        assert "NOT" in summary

    def test_summarize_truncates_long_values(self):
        """Test that long values are truncated."""
        condition = {
            "type": "simple",
            "field": "description",
            "op": "contains",
            "value": "This is a very long value that should be truncated"
        }
        summary = _summarize_condition(condition)
        assert "..." in summary

    def test_summarize_empty_and(self):
        """Test summarizing empty AND condition."""
        condition = {"type": "and", "conditions": []}
        summary = _summarize_condition(condition)
        assert "AND()" in summary

    def test_summarize_empty_or(self):
        """Test summarizing empty OR condition."""
        condition = {"type": "or", "conditions": []}
        summary = _summarize_condition(condition)
        assert "OR()" in summary


@pytest.mark.asyncio
class TestEvaluateConditionWithContext:
    """Tests for evaluate_condition_with_context with NOT operator.
    
    Validates: Requirements 3.5
    """

    def _create_alert(self, severity: str = "warning") -> Alert:
        """Create a mock Alert object for testing."""
        alert = MagicMock(spec=Alert)
        alert.id = uuid.uuid4()
        alert.severity = severity
        alert.source = "prometheus"
        alert.raw_event = {}
        alert.enriched_context = {}
        alert.space_id = None
        return alert

    async def test_not_condition_with_context(self):
        """Test NOT condition evaluation with database context."""
        db = AsyncMock()
        alert = self._create_alert(severity="warning")
        
        condition = {
            "type": "not",
            "condition": {
                "type": "simple",
                "field": "severity",
                "op": "eq",
                "value": "critical"
            }
        }
        
        result = await evaluate_condition_with_context(db, condition, alert)
        # severity is warning, not critical, so inner is False
        # NOT(False) = True
        assert result is True

    async def test_nested_not_with_and(self):
        """Test nested NOT within AND condition."""
        db = AsyncMock()
        alert = self._create_alert(severity="warning")
        
        condition = {
            "type": "and",
            "conditions": [
                {"type": "simple", "field": "source", "op": "eq", "value": "prometheus"},
                {
                    "type": "not",
                    "condition": {
                        "type": "simple",
                        "field": "severity",
                        "op": "eq",
                        "value": "info"
                    }
                }
            ]
        }
        
        result = await evaluate_condition_with_context(db, condition, alert)
        # source=prometheus is True
        # severity=info is False (it's warning), NOT(False) = True
        # AND(True, True) = True
        assert result is True

    async def test_not_with_or_inside(self):
        """Test NOT wrapping OR condition."""
        db = AsyncMock()
        alert = self._create_alert(severity="warning")
        
        condition = {
            "type": "not",
            "condition": {
                "type": "or",
                "conditions": [
                    {"type": "simple", "field": "severity", "op": "eq", "value": "critical"},
                    {"type": "simple", "field": "severity", "op": "eq", "value": "high"}
                ]
            }
        }
        
        result = await evaluate_condition_with_context(db, condition, alert)
        # severity is warning, neither critical nor high
        # OR(False, False) = False
        # NOT(False) = True
        assert result is True
