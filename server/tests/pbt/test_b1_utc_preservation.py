"""Preservation property test for B1: UTC timestamp formatting.

**Validates: Requirements 3.1, 3.2, 3.3**

This test verifies that datetime objects (both timezone-aware and naive)
can be correctly formatted as valid ISO 8601 strings when used in
TrajectoryEvent timestamps.

The B1 bug is about UnboundLocalError when accessing UTC in nested closures,
but the underlying timestamp formatting logic itself is correct. This
preservation test confirms that the formatting logic works correctly
and will continue to work after the fix is applied.

**Property 2: Preservation** - The timestamp formatting function produces
valid ISO 8601 strings for any datetime input.

This test SHOULD PASS on current (unfixed) code because the formatting
logic itself is correct - the bug is in the import scoping, not the
formatting.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timezone, timedelta

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from src.schemas.trajectory import TrajectoryEvent


# ISO 8601 datetime pattern (simplified but covers common formats)
# Matches: 2024-01-15T10:30:00, 2024-01-15T10:30:00Z, 2024-01-15T10:30:00+00:00, etc.
ISO8601_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"  # Basic datetime
    r"(\.\d+)?"  # Optional microseconds
    r"(Z|[+-]\d{2}:\d{2})?$"  # Optional timezone
)


def _format_timestamp(dt: datetime) -> str:
    """Format a datetime as ISO 8601 string.
    
    This mirrors the behavior used in TrajectoryEvent serialization.
    The function handles both timezone-aware and naive datetime objects.
    
    Args:
        dt: A datetime object (can be timezone-aware or naive)
        
    Returns:
        ISO 8601 formatted string representation
    """
    return dt.isoformat()


def is_valid_iso8601(s: str) -> bool:
    """Check if a string is a valid ISO 8601 datetime format."""
    return bool(ISO8601_PATTERN.match(s))


# ---------------------------------------------------------------------------
# Hypothesis strategies for datetime generation
# ---------------------------------------------------------------------------

# Strategy for timezone-aware datetimes with UTC
@st.composite
def utc_datetimes(draw: st.DrawFn) -> datetime:
    """Generate timezone-aware datetime objects with UTC timezone."""
    dt = draw(st.datetimes(
        min_value=datetime(1970, 1, 1),
        max_value=datetime(2100, 12, 31),
    ))
    return dt.replace(tzinfo=UTC)


# Strategy for timezone-aware datetimes with various offsets
@st.composite
def tz_aware_datetimes(draw: st.DrawFn) -> datetime:
    """Generate timezone-aware datetime objects with various timezone offsets."""
    dt = draw(st.datetimes(
        min_value=datetime(1970, 1, 1),
        max_value=datetime(2100, 12, 31),
    ))
    # Generate offset between -12 and +14 hours (covers all real timezones)
    offset_hours = draw(st.integers(min_value=-12, max_value=14))
    offset_minutes = draw(st.sampled_from([0, 30, 45]))  # Common minute offsets
    tz = timezone(timedelta(hours=offset_hours, minutes=offset_minutes))
    return dt.replace(tzinfo=tz)


# Strategy for naive datetimes (no timezone info)
naive_datetimes = st.datetimes(
    min_value=datetime(1970, 1, 1),
    max_value=datetime(2100, 12, 31),
)


# Combined strategy for any datetime
any_datetime = st.one_of(
    utc_datetimes(),
    tz_aware_datetimes(),
    naive_datetimes,
)


# ---------------------------------------------------------------------------
# Property-Based Tests
# ---------------------------------------------------------------------------

class TestB1UTCPreservation:
    """Preservation tests for B1: timestamp formatting produces valid ISO 8601.
    
    **Validates: Requirements 3.1, 3.2, 3.3**
    
    These tests verify that the timestamp formatting logic works correctly
    for all types of datetime inputs. The tests should PASS on both
    unfixed and fixed code because the formatting logic itself is correct.
    """

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(dt=utc_datetimes())
    def test_utc_datetime_produces_valid_iso8601(self, dt: datetime) -> None:
        """Property: UTC datetime formatting always produces valid ISO 8601.
        
        **Validates: Requirements 3.1**
        
        For any timezone-aware datetime with UTC timezone, the formatted
        string must be a valid ISO 8601 representation.
        """
        result = _format_timestamp(dt)
        
        assert isinstance(result, str), f"Expected string, got {type(result)}"
        assert is_valid_iso8601(result), f"Invalid ISO 8601 format: {result}"
        # Verify the result can be parsed back
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None, "UTC datetime should preserve timezone info"

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(dt=tz_aware_datetimes())
    def test_tz_aware_datetime_produces_valid_iso8601(self, dt: datetime) -> None:
        """Property: Timezone-aware datetime formatting produces valid ISO 8601.
        
        **Validates: Requirements 3.2**
        
        For any timezone-aware datetime (with any valid offset), the formatted
        string must be a valid ISO 8601 representation.
        """
        result = _format_timestamp(dt)
        
        assert isinstance(result, str), f"Expected string, got {type(result)}"
        assert is_valid_iso8601(result), f"Invalid ISO 8601 format: {result}"
        # Verify the result can be parsed back
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None, "TZ-aware datetime should preserve timezone info"

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(dt=naive_datetimes)
    def test_naive_datetime_produces_valid_iso8601(self, dt: datetime) -> None:
        """Property: Naive datetime formatting produces valid ISO 8601.
        
        **Validates: Requirements 3.3**
        
        For any naive datetime (no timezone info), the formatted string
        must be a valid ISO 8601 representation (without timezone suffix).
        """
        result = _format_timestamp(dt)
        
        assert isinstance(result, str), f"Expected string, got {type(result)}"
        assert is_valid_iso8601(result), f"Invalid ISO 8601 format: {result}"
        # Verify the result can be parsed back
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is None, "Naive datetime should not have timezone info"

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(dt=any_datetime)
    def test_any_datetime_roundtrips_correctly(self, dt: datetime) -> None:
        """Property: Any datetime can be formatted and parsed back.
        
        **Validates: Requirements 3.1, 3.2, 3.3**
        
        For any datetime (UTC, timezone-aware, or naive), the formatted
        string can be parsed back to an equivalent datetime.
        """
        result = _format_timestamp(dt)
        parsed = datetime.fromisoformat(result)
        
        # Compare the datetime values (accounting for potential microsecond precision)
        assert parsed.year == dt.year
        assert parsed.month == dt.month
        assert parsed.day == dt.day
        assert parsed.hour == dt.hour
        assert parsed.minute == dt.minute
        assert parsed.second == dt.second
        
        # Timezone info should be preserved (or both None for naive)
        if dt.tzinfo is not None:
            assert parsed.tzinfo is not None
        else:
            assert parsed.tzinfo is None


class TestTrajectoryEventTimestamp:
    """Test that TrajectoryEvent correctly handles datetime timestamps.
    
    **Validates: Requirements 3.1, 3.2, 3.3**
    
    These tests verify that TrajectoryEvent can be created with various
    datetime inputs and serializes them correctly to ISO 8601 format.
    """

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(dt=utc_datetimes())
    def test_trajectory_event_with_utc_timestamp(self, dt: datetime) -> None:
        """Property: TrajectoryEvent accepts UTC datetime and serializes correctly.
        
        **Validates: Requirements 3.1**
        """
        import uuid
        
        event = TrajectoryEvent(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            kind="turn",
            ts=dt,
            outcome="ok",
        )
        
        # Verify the event was created successfully
        assert event.ts == dt
        
        # Verify serialization produces valid ISO 8601
        serialized = event.model_dump_json()
        assert isinstance(serialized, str)
        
        # The ts field should be serialized as ISO 8601
        event_dict = event.model_dump(mode="json")
        ts_str = event_dict["ts"]
        assert is_valid_iso8601(ts_str), f"Invalid ISO 8601 in serialized event: {ts_str}"

    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(dt=tz_aware_datetimes())
    def test_trajectory_event_with_tz_aware_timestamp(self, dt: datetime) -> None:
        """Property: TrajectoryEvent accepts tz-aware datetime and serializes correctly.
        
        **Validates: Requirements 3.2**
        """
        import uuid
        
        event = TrajectoryEvent(
            id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            kind="turn",
            ts=dt,
            outcome="ok",
        )
        
        # Verify the event was created successfully
        assert event.ts == dt
        
        # Verify serialization produces valid ISO 8601
        event_dict = event.model_dump(mode="json")
        ts_str = event_dict["ts"]
        assert is_valid_iso8601(ts_str), f"Invalid ISO 8601 in serialized event: {ts_str}"


# ---------------------------------------------------------------------------
# Direct unit tests for edge cases
# ---------------------------------------------------------------------------

class TestTimestampEdgeCases:
    """Unit tests for timestamp formatting edge cases.
    
    **Validates: Requirements 3.1, 3.2, 3.3**
    """

    def test_current_utc_time(self) -> None:
        """Test formatting of current UTC time (the actual use case in B1)."""
        now = datetime.now(UTC)
        result = _format_timestamp(now)
        
        assert is_valid_iso8601(result)
        assert "+00:00" in result or "Z" in result or result.endswith("+00:00")

    def test_epoch_time(self) -> None:
        """Test formatting of Unix epoch time."""
        epoch = datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC)
        result = _format_timestamp(epoch)
        
        assert is_valid_iso8601(result)
        assert "1970-01-01" in result

    def test_far_future_time(self) -> None:
        """Test formatting of far future time."""
        future = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
        result = _format_timestamp(future)
        
        assert is_valid_iso8601(result)
        assert "2099-12-31" in result

    def test_microsecond_precision(self) -> None:
        """Test that microseconds are preserved in formatting."""
        dt = datetime(2024, 6, 15, 10, 30, 45, 123456, tzinfo=UTC)
        result = _format_timestamp(dt)
        
        assert is_valid_iso8601(result)
        assert "123456" in result

    def test_negative_utc_offset(self) -> None:
        """Test formatting with negative UTC offset."""
        tz = timezone(timedelta(hours=-5))
        dt = datetime(2024, 6, 15, 10, 30, 0, tzinfo=tz)
        result = _format_timestamp(dt)
        
        assert is_valid_iso8601(result)
        assert "-05:00" in result

    def test_positive_utc_offset(self) -> None:
        """Test formatting with positive UTC offset."""
        tz = timezone(timedelta(hours=8))
        dt = datetime(2024, 6, 15, 10, 30, 0, tzinfo=tz)
        result = _format_timestamp(dt)
        
        assert is_valid_iso8601(result)
        assert "+08:00" in result
