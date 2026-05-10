"""Unit tests for trend detection conditions in the trigger engine.

Tests cover:
- Trend direction calculation (rising, falling, flat)
- Volatility calculation
- Metric value extraction from alerts
- Edge cases (insufficient data, flat trends, zero values)
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.alert import Alert
from src.services.trigger_engine import (
    DEFAULT_TREND_THRESHOLD,
    DEFAULT_TREND_WINDOW_MINUTES,
    DEFAULT_VOLATILITY_THRESHOLD,
    MIN_TREND_DATA_POINTS,
    _calculate_trend_direction,
    _calculate_volatility,
    _extract_metric_value,
    _has_trend_condition,
    evaluate_trend_condition,
    evaluate_trend_condition_sync,
)


class TestCalculateTrendDirection:
    """Tests for _calculate_trend_direction function."""

    def test_rising_trend(self):
        """Test detection of rising trend."""
        values = [10.0, 12.0, 14.0, 16.0, 18.0]
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        assert direction == "rising"
        assert change_rate > 0

    def test_falling_trend(self):
        """Test detection of falling trend."""
        values = [20.0, 18.0, 16.0, 14.0, 12.0]
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        assert direction == "falling"
        assert change_rate < 0

    def test_flat_trend(self):
        """Test detection of flat/stable trend."""
        values = [10.0, 10.1, 9.9, 10.0, 10.05]
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        assert direction == "flat"
        assert abs(change_rate) < 0.2

    def test_insufficient_data(self):
        """Test handling of insufficient data points."""
        values = [10.0, 12.0]  # Only 2 points, need at least 3
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        assert direction == "insufficient_data"
        assert change_rate == 0.0

    def test_empty_values(self):
        """Test handling of empty values list."""
        values = []
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        assert direction == "insufficient_data"
        assert change_rate == 0.0

    def test_zero_mean_values(self):
        """Test handling of values with zero mean."""
        values = [-1.0, 0.0, 1.0, 0.0, 0.0]
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        assert direction == "flat"
        assert change_rate == 0.0

    def test_all_same_values(self):
        """Test handling of constant values."""
        values = [5.0, 5.0, 5.0, 5.0, 5.0]
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        assert direction == "flat"
        assert change_rate == 0.0

    def test_threshold_boundary_rising(self):
        """Test rising trend at threshold boundary."""
        # Create values that result in exactly threshold change
        values = [10.0, 10.5, 11.0, 11.5, 12.0]  # 20% increase
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        # Should be rising since change is >= threshold
        assert direction == "rising"

    def test_threshold_boundary_falling(self):
        """Test falling trend at threshold boundary."""
        values = [12.0, 11.5, 11.0, 10.5, 10.0]  # 20% decrease
        direction, change_rate = _calculate_trend_direction(values, 0.2)
        assert direction == "falling"


class TestCalculateVolatility:
    """Tests for _calculate_volatility function."""

    def test_high_volatility(self):
        """Test detection of high volatility."""
        values = [10.0, 20.0, 5.0, 25.0, 8.0]  # High variance
        volatility = _calculate_volatility(values)
        assert volatility > DEFAULT_VOLATILITY_THRESHOLD

    def test_low_volatility(self):
        """Test detection of low volatility."""
        values = [10.0, 10.1, 9.9, 10.05, 9.95]  # Low variance
        volatility = _calculate_volatility(values)
        assert volatility < DEFAULT_VOLATILITY_THRESHOLD

    def test_zero_volatility(self):
        """Test zero volatility for constant values."""
        values = [5.0, 5.0, 5.0, 5.0, 5.0]
        volatility = _calculate_volatility(values)
        assert volatility == 0.0

    def test_insufficient_data(self):
        """Test handling of insufficient data points."""
        values = [10.0, 12.0]
        volatility = _calculate_volatility(values)
        assert volatility == 0.0

    def test_zero_mean(self):
        """Test handling of zero mean values."""
        values = [-1.0, 0.0, 1.0, 0.0, 0.0]
        volatility = _calculate_volatility(values)
        assert volatility == 0.0


class TestExtractMetricValue:
    """Tests for _extract_metric_value function."""

    def _create_alert(
        self,
        raw_event: dict | None = None,
        enriched_context: dict | None = None,
    ) -> Alert:
        """Create a mock Alert object for testing."""
        alert = MagicMock(spec=Alert)
        alert.raw_event = raw_event or {}
        alert.enriched_context = enriched_context or {}
        return alert

    def test_extract_from_raw_event(self):
        """Test extraction from raw_event dict."""
        alert = self._create_alert(raw_event={"cpu_usage": 85.5})
        value = _extract_metric_value(alert, "cpu_usage")
        assert value == 85.5

    def test_extract_from_nested_metrics(self):
        """Test extraction from nested metrics dict in raw_event."""
        alert = self._create_alert(
            raw_event={"metrics": {"memory_usage": 72.3}}
        )
        value = _extract_metric_value(alert, "memory_usage")
        assert value == 72.3

    def test_extract_from_nested_data(self):
        """Test extraction from nested data dict in raw_event."""
        alert = self._create_alert(
            raw_event={"data": {"disk_io": 150.0}}
        )
        value = _extract_metric_value(alert, "disk_io")
        assert value == 150.0

    def test_extract_from_enriched_context(self):
        """Test extraction from enriched_context dict."""
        alert = self._create_alert(
            raw_event={},
            enriched_context={"latency_ms": 45.2}
        )
        value = _extract_metric_value(alert, "latency_ms")
        assert value == 45.2

    def test_extract_string_number(self):
        """Test extraction and conversion of string number."""
        alert = self._create_alert(raw_event={"value": "123.45"})
        value = _extract_metric_value(alert, "value")
        assert value == 123.45

    def test_extract_missing_metric(self):
        """Test extraction of non-existent metric."""
        alert = self._create_alert(raw_event={"other": 100})
        value = _extract_metric_value(alert, "missing_metric")
        assert value is None

    def test_extract_non_numeric(self):
        """Test extraction of non-numeric value."""
        alert = self._create_alert(raw_event={"status": "healthy"})
        value = _extract_metric_value(alert, "status")
        assert value is None

    def test_raw_event_priority(self):
        """Test that raw_event takes priority over enriched_context."""
        alert = self._create_alert(
            raw_event={"metric": 100.0},
            enriched_context={"metric": 200.0}
        )
        value = _extract_metric_value(alert, "metric")
        assert value == 100.0


class TestHasTrendCondition:
    """Tests for _has_trend_condition function."""

    def test_simple_trend_condition(self):
        """Test detection of simple trend condition."""
        condition = {
            "type": "simple",
            "op": "trend",
            "trend_config": {"metric": "cpu", "direction": "rising"}
        }
        assert _has_trend_condition(condition) is True

    def test_simple_non_trend_condition(self):
        """Test non-trend simple condition."""
        condition = {
            "type": "simple",
            "field": "severity",
            "op": "eq",
            "value": "critical"
        }
        assert _has_trend_condition(condition) is False

    def test_and_with_trend(self):
        """Test AND condition containing trend."""
        condition = {
            "type": "and",
            "conditions": [
                {"type": "simple", "field": "severity", "op": "eq", "value": "critical"},
                {"type": "simple", "op": "trend", "trend_config": {"metric": "cpu", "direction": "rising"}}
            ]
        }
        assert _has_trend_condition(condition) is True

    def test_and_without_trend(self):
        """Test AND condition without trend."""
        condition = {
            "type": "and",
            "conditions": [
                {"type": "simple", "field": "severity", "op": "eq", "value": "critical"},
                {"type": "simple", "field": "source", "op": "eq", "value": "prometheus"}
            ]
        }
        assert _has_trend_condition(condition) is False

    def test_or_with_trend(self):
        """Test OR condition containing trend."""
        condition = {
            "type": "or",
            "conditions": [
                {"type": "simple", "op": "trend", "trend_config": {"metric": "memory", "direction": "rising"}},
                {"type": "simple", "field": "severity", "op": "eq", "value": "critical"}
            ]
        }
        assert _has_trend_condition(condition) is True

    def test_not_with_trend(self):
        """Test NOT condition containing trend."""
        condition = {
            "type": "not",
            "condition": {
                "type": "simple",
                "op": "trend",
                "trend_config": {"metric": "cpu", "direction": "falling"}
            }
        }
        assert _has_trend_condition(condition) is True

    def test_nested_trend(self):
        """Test deeply nested trend condition."""
        condition = {
            "type": "and",
            "conditions": [
                {
                    "type": "or",
                    "conditions": [
                        {"type": "simple", "field": "severity", "op": "eq", "value": "critical"},
                        {
                            "type": "not",
                            "condition": {
                                "type": "simple",
                                "op": "trend",
                                "trend_config": {"metric": "cpu", "direction": "rising"}
                            }
                        }
                    ]
                }
            ]
        }
        assert _has_trend_condition(condition) is True


class TestEvaluateTrendConditionSync:
    """Tests for evaluate_trend_condition_sync function."""

    def test_rising_trend_match(self):
        """Test matching rising trend."""
        values = [10.0, 12.0, 14.0, 16.0, 18.0]
        result = evaluate_trend_condition_sync(values, "rising")
        assert result is True

    def test_rising_trend_no_match(self):
        """Test non-matching rising trend."""
        values = [18.0, 16.0, 14.0, 12.0, 10.0]
        result = evaluate_trend_condition_sync(values, "rising")
        assert result is False

    def test_falling_trend_match(self):
        """Test matching falling trend."""
        values = [20.0, 18.0, 16.0, 14.0, 12.0]
        result = evaluate_trend_condition_sync(values, "falling")
        assert result is True

    def test_falling_trend_no_match(self):
        """Test non-matching falling trend."""
        values = [10.0, 12.0, 14.0, 16.0, 18.0]
        result = evaluate_trend_condition_sync(values, "falling")
        assert result is False

    def test_volatile_trend_match(self):
        """Test matching volatile trend."""
        values = [10.0, 20.0, 5.0, 25.0, 8.0]
        result = evaluate_trend_condition_sync(values, "volatile")
        assert result is True

    def test_volatile_trend_no_match(self):
        """Test non-matching volatile trend."""
        values = [10.0, 10.1, 9.9, 10.05, 9.95]
        result = evaluate_trend_condition_sync(values, "volatile")
        assert result is False

    def test_insufficient_data(self):
        """Test with insufficient data points."""
        values = [10.0, 12.0]
        result = evaluate_trend_condition_sync(values, "rising")
        assert result is False

    def test_custom_threshold(self):
        """Test with custom threshold."""
        values = [10.0, 10.5, 11.0, 11.5, 12.0]  # ~20% increase
        # With high threshold, should not be considered rising
        result = evaluate_trend_condition_sync(values, "rising", threshold=0.5)
        assert result is False
        # With low threshold, should be considered rising
        result = evaluate_trend_condition_sync(values, "rising", threshold=0.1)
        assert result is True


@pytest.mark.asyncio
class TestEvaluateTrendConditionAsync:
    """Tests for evaluate_trend_condition async function."""

    async def test_missing_trend_config(self):
        """Test handling of missing trend_config."""
        db = AsyncMock()
        condition = {"type": "simple", "op": "trend"}
        result = await evaluate_trend_condition(db, condition)
        assert result is False

    async def test_missing_metric(self):
        """Test handling of missing metric in trend_config."""
        db = AsyncMock()
        condition = {
            "type": "simple",
            "op": "trend",
            "trend_config": {"direction": "rising"}
        }
        result = await evaluate_trend_condition(db, condition)
        assert result is False

    async def test_invalid_direction(self):
        """Test handling of invalid direction."""
        db = AsyncMock()
        condition = {
            "type": "simple",
            "op": "trend",
            "trend_config": {
                "metric": "cpu_usage",
                "direction": "invalid_direction"
            }
        }
        result = await evaluate_trend_condition(db, condition)
        assert result is False

    async def test_metric_from_field_fallback(self):
        """Test that metric can be specified via field if not in trend_config."""
        db = AsyncMock()
        # Mock empty result
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        condition = {
            "type": "simple",
            "field": "cpu_usage",
            "op": "trend",
            "trend_config": {"direction": "rising"}
        }
        # Should not fail, just return False due to insufficient data
        result = await evaluate_trend_condition(db, condition)
        assert result is False

    async def test_default_values(self):
        """Test that default values are used when not specified."""
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        db.execute.return_value = mock_result

        condition = {
            "type": "simple",
            "op": "trend",
            "trend_config": {
                "metric": "cpu_usage",
                "direction": "rising"
                # threshold and window_minutes not specified
            }
        }
        result = await evaluate_trend_condition(db, condition)
        # Should use defaults and return False due to no data
        assert result is False
