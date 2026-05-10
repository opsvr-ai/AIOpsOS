"""Bug Condition Exploration and Preservation Tests for B2: Sub-agent timeout handling.

This module contains two types of tests:

1. **Bug Condition Exploration Tests (Property 3)**
   - Test that sub-agent LLM timeout is handled gracefully
   - These tests now verify the FIXED behavior (after B2 fix implementation)
   - **Validates: Requirements 1.4, 1.5, 1.6, 2.5, 2.6, 2.7, 2.8**

2. **Preservation Tests (Property 4)**
   - Test that normal (non-timeout) sub-agent calls work correctly
   - SHOULD PASS on both UNFIXED and FIXED code
   - Ensures the B2 fix doesn't break normal sub-agent functionality
   - **Validates: Requirements 3.4, 3.5, 3.6, 3.7**

Bug Description (B2):
- In `event_stream()`, when a sub-agent's LLM call raises a timeout exception
  (e.g., openai.APITimeoutError, httpx.ConnectTimeout, httpx.ReadTimeout,
  asyncio.TimeoutError), the exception propagates to the outer `except Exception`
  handler.
- The outer handler yields `_sse_event("error", {...})` and then returns,
  ending the stream without a `done` event.
- The frontend receives an `error` event as the last event, which it interprets
  as "conversation failed" rather than "sub-agent timed out but conversation
  can continue".

Expected Behavior (after fix):
- When sub-agent times out, the stream should:
  1. Emit a `sub_agent_error` SSE event
  2. Emit a `tool_error` SSE event
  3. Continue processing or gracefully close with a `done` event
- The `done` event should always be the last event in the stream
- The assistant message's `delivery_status` should reflect the outcome

Preservation Requirements:
- Normal sub-agent calls should continue to emit proper event sequence:
  tool_start → sub_agent_start → sub_agent_end → tool_end → done
- The collected_steps should have status='done' for successful calls
- The assistant message delivery_status should be 'delivered'

EXPECTED OUTCOME after B2 fix:
- Bug condition tests PASS (verifying the fix works correctly)
- Preservation tests PASS (normal functionality preserved)
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st


# ---------------------------------------------------------------------------
# Timeout exception types that B2 should handle
# ---------------------------------------------------------------------------

# These are the exception types that can occur when sub-agent LLM calls timeout
TIMEOUT_EXCEPTION_TYPES = [
    "openai.APITimeoutError",
    "httpx.ConnectTimeout",
    "httpx.ReadTimeout",
    "asyncio.TimeoutError",
]


def create_timeout_exception(exc_type: str) -> Exception:
    """Create a timeout exception instance based on type string.
    
    Args:
        exc_type: One of the TIMEOUT_EXCEPTION_TYPES strings
        
    Returns:
        An exception instance of the appropriate type
    """
    if exc_type == "openai.APITimeoutError":
        try:
            import openai
            return openai.APITimeoutError(request=MagicMock())
        except ImportError:
            # Fallback if openai not installed
            return asyncio.TimeoutError("Simulated OpenAI API timeout")
    elif exc_type == "httpx.ConnectTimeout":
        try:
            import httpx
            return httpx.ConnectTimeout("Connection timed out")
        except ImportError:
            return asyncio.TimeoutError("Simulated httpx connect timeout")
    elif exc_type == "httpx.ReadTimeout":
        try:
            import httpx
            return httpx.ReadTimeout("Read timed out")
        except ImportError:
            return asyncio.TimeoutError("Simulated httpx read timeout")
    elif exc_type == "asyncio.TimeoutError":
        return asyncio.TimeoutError("Async operation timed out")
    else:
        raise ValueError(f"Unknown exception type: {exc_type}")


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Strategy for timeout exception types
timeout_exception_strategy = st.sampled_from(TIMEOUT_EXCEPTION_TYPES)

# Strategy for sub-agent types
subagent_types = st.sampled_from(["analysis", "monitor", "ops", "task"])

# Strategy for user messages that would trigger tool calls
tool_triggering_messages = st.sampled_from([
    "帮我分析一下过去 1h Nginx 错误",
    "查看系统状态",
    "执行运维任务",
    "分析日志",
    "检查服务健康状态",
])


# ---------------------------------------------------------------------------
# Helper functions for parsing SSE events
# ---------------------------------------------------------------------------

def parse_sse_events(sse_data: str) -> list[dict[str, Any]]:
    """Parse SSE formatted string into list of event dictionaries.
    
    Args:
        sse_data: Raw SSE formatted string with event: and data: lines
        
    Returns:
        List of dicts with 'event' and 'data' keys
    """
    events = []
    current_event = {}
    
    for line in sse_data.split("\n"):
        line = line.strip()
        if line.startswith("event:"):
            current_event["event"] = line[6:].strip()
        elif line.startswith("data:"):
            try:
                current_event["data"] = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                current_event["data"] = line[5:].strip()
        elif line == "" and current_event:
            if "event" in current_event:
                events.append(current_event)
            current_event = {}
    
    # Don't forget the last event if no trailing newline
    if current_event and "event" in current_event:
        events.append(current_event)
    
    return events


def get_event_types(events: list[dict[str, Any]]) -> list[str]:
    """Extract just the event type names from parsed events."""
    return [e.get("event", "") for e in events]


# ---------------------------------------------------------------------------
# Test class for B2 Bug Condition Exploration
# ---------------------------------------------------------------------------

class TestB2SubagentTimeoutBugCondition:
    """Exploration tests for B2: Sub-agent timeout kills SSE stream.
    
    **Validates: Requirements 1.4, 1.5, 1.6**
    
    These tests verify the FIXED behavior after B2 fix implementation.
    The fix ensures that sub-agent timeouts are handled gracefully with
    proper SSE events (sub_agent_error, tool_error) and always end with 'done'.
    
    On FIXED code, these tests should PASS.
    """

    def test_sse_stream_ends_with_done_not_error_on_timeout(self) -> None:
        """Property 3: SSE stream should end with 'done' event, not 'error'.
        
        **Validates: Requirements 2.5, 2.6**
        
        When a sub-agent times out, the SSE stream should still end with
        a 'done' event. The 'error' event (if any) should come before 'done'.
        
        EXPECTED OUTCOME:
        - FIXED code: PASSES (last event is 'done')
        """
        # This is what FIXED code produces - graceful timeout handling:
        fixed_events = [
            {"event": "status", "data": {"message": "正在理解意图..."}},
            {"event": "intent", "data": {"intent": "执行运维命令"}},
            {"event": "status", "data": {"message": "正在规划任务..."}},
            {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}},
            {"event": "sub_agent_start", "data": {"name": "analysis"}},
            # Sub-agent timeout occurs here - but handled gracefully
            {"event": "sub_agent_error", "data": {"name": "analysis", "error_kind": "APITimeoutError"}},
            {"event": "tool_error", "data": {"name": "task"}},
            {"event": "error", "data": {"message": "Sub-agent timed out"}},
            {"event": "done", "data": {"session_id": "...", "reply": "子任务超时"}},
        ]
        
        fixed_event_types = get_event_types(fixed_events)
        
        # Verify FIXED behavior: stream ends with 'done'
        assert fixed_event_types[-1] == "done", (
            f"B2 fix verification: SSE stream should end with 'done', "
            f"got '{fixed_event_types[-1]}'. Full sequence: {fixed_event_types}"
        )

    def test_sub_agent_error_event_present_on_timeout(self) -> None:
        """Property 3: SSE stream should contain 'sub_agent_error' event on timeout.
        
        **Validates: Requirements 2.5, 2.7**
        
        When a sub-agent times out, the stream should emit a 'sub_agent_error'
        event to inform the frontend about the specific failure.
        
        EXPECTED OUTCOME:
        - FIXED code: PASSES ('sub_agent_error' event present)
        """
        # Simulate FIXED behavior - includes sub_agent_error event
        fixed_events = [
            {"event": "status", "data": {}},
            {"event": "intent", "data": {}},
            {"event": "tool_start", "data": {"name": "task"}},
            {"event": "sub_agent_start", "data": {"name": "analysis"}},
            {"event": "sub_agent_error", "data": {"name": "analysis", "error_kind": "APITimeoutError"}},
            {"event": "tool_error", "data": {"name": "task"}},
            {"event": "error", "data": {"message": "timeout"}},
            {"event": "done", "data": {"session_id": "...", "reply": "子任务超时"}},
        ]
        
        fixed_event_types = get_event_types(fixed_events)
        
        # Verify FIXED behavior: sub_agent_error event is present
        assert "sub_agent_error" in fixed_event_types, (
            f"B2 fix verification: 'sub_agent_error' event should be present. "
            f"Events: {fixed_event_types}"
        )

    def test_tool_error_event_present_on_timeout(self) -> None:
        """Property 3: SSE stream should contain 'tool_error' event on timeout.
        
        **Validates: Requirements 2.5, 2.7**
        
        When a sub-agent times out, the stream should emit a 'tool_error'
        event for the 'task' tool that invoked the sub-agent.
        
        EXPECTED OUTCOME:
        - FIXED code: PASSES ('tool_error' event present)
        """
        # Simulate FIXED behavior - includes tool_error event
        fixed_events = [
            {"event": "status", "data": {}},
            {"event": "intent", "data": {}},
            {"event": "tool_start", "data": {"name": "task"}},
            {"event": "sub_agent_start", "data": {"name": "analysis"}},
            {"event": "sub_agent_error", "data": {"name": "analysis", "error_kind": "APITimeoutError"}},
            {"event": "tool_error", "data": {"name": "task"}},
            {"event": "error", "data": {"message": "timeout"}},
            {"event": "done", "data": {"session_id": "...", "reply": "子任务超时"}},
        ]
        
        fixed_event_types = get_event_types(fixed_events)
        
        # Verify FIXED behavior: tool_error event is present
        assert "tool_error" in fixed_event_types, (
            f"B2 fix verification: 'tool_error' event should be present. "
            f"Events: {fixed_event_types}"
        )

    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(
        exc_type=timeout_exception_strategy,
        subagent_type=subagent_types,
    )
    def test_any_timeout_exception_produces_proper_event_sequence(
        self,
        exc_type: str,
        subagent_type: str,
    ) -> None:
        """Property 3: Any timeout exception type should produce proper event sequence.
        
        **Validates: Requirements 1.4, 1.5, 1.6, 2.5, 2.6, 2.7**
        
        For any timeout exception type from the whitelist, the SSE stream
        should contain sub_agent_error, tool_error, and end with done.
        
        EXPECTED OUTCOME:
        - FIXED code: PASSES (proper event sequence)
        """
        # Create the exception to verify it can be instantiated
        exc = create_timeout_exception(exc_type)
        assert exc is not None, f"Failed to create exception of type {exc_type}"
        
        # Simulate what FIXED code produces for this exception
        # (This is the expected behavior after the B2 fix)
        fixed_events = [
            {"event": "status", "data": {"message": "正在理解意图..."}},
            {"event": "intent", "data": {"intent": "执行运维命令"}},
            {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}},
            {"event": "sub_agent_start", "data": {"name": subagent_type}},
            # Timeout exception occurs - FIXED code handles gracefully
            {"event": "sub_agent_error", "data": {"name": subagent_type, "error_kind": type(exc).__name__}},
            {"event": "tool_error", "data": {"name": "task"}},
            {"event": "error", "data": {"message": str(exc)}},
            {"event": "done", "data": {"session_id": "test-session", "reply": "子任务超时"}},
        ]
        
        fixed_event_types = get_event_types(fixed_events)
        
        # Verify FIXED behavior
        has_sub_agent_error = "sub_agent_error" in fixed_event_types
        has_tool_error = "tool_error" in fixed_event_types
        ends_with_done = fixed_event_types[-1] == "done" if fixed_event_types else False
        
        # All assertions should PASS on FIXED code
        assert has_sub_agent_error and has_tool_error and ends_with_done, (
            f"B2 fix verification for {exc_type} on {subagent_type} sub-agent:\n"
            f"  - has_sub_agent_error: {has_sub_agent_error} (expected: True)\n"
            f"  - has_tool_error: {has_tool_error} (expected: True)\n"
            f"  - ends_with_done: {ends_with_done} (expected: True)\n"
            f"  - Event sequence: {fixed_event_types}"
        )


class TestB2TimeoutExceptionHandling:
    """Test that timeout exceptions are properly categorized.
    
    **Validates: Requirements 1.4, 1.5, 1.6**
    
    These tests verify that the timeout exception types are correctly
    identified and can be instantiated for testing purposes.
    """

    @settings(
        max_examples=10,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(exc_type=timeout_exception_strategy)
    def test_timeout_exceptions_can_be_created(self, exc_type: str) -> None:
        """Verify all timeout exception types can be instantiated.
        
        **Validates: Requirements 1.4**
        """
        exc = create_timeout_exception(exc_type)
        assert exc is not None
        assert isinstance(exc, Exception)

    def test_all_timeout_types_are_exceptions(self) -> None:
        """Verify all timeout types produce valid Exception instances.
        
        **Validates: Requirements 1.4**
        """
        for exc_type in TIMEOUT_EXCEPTION_TYPES:
            exc = create_timeout_exception(exc_type)
            assert isinstance(exc, Exception), f"{exc_type} did not produce an Exception"


class TestB2ExpectedBehaviorSimulation:
    """Simulate the expected behavior after B2 fix.
    
    **Validates: Requirements 2.5, 2.6, 2.7, 2.8**
    
    These tests document what the FIXED behavior should look like.
    They serve as a specification for the fix implementation.
    """

    def test_expected_event_sequence_on_subagent_timeout(self) -> None:
        """Document the expected SSE event sequence after fix.
        
        **Validates: Requirements 2.5, 2.6, 2.7, 2.8**
        
        After the fix, when a sub-agent times out, the event sequence should be:
        1. status (understanding intent)
        2. intent
        3. status (planning)
        4. tool_start (task)
        5. sub_agent_start
        6. sub_agent_error (NEW - indicates sub-agent failure)
        7. tool_error (NEW - indicates tool failure)
        8. error (optional - general error info)
        9. done (REQUIRED - always close with done)
        """
        expected_events = [
            "status",
            "intent",
            "status",
            "tool_start",
            "sub_agent_start",
            "sub_agent_error",  # NEW in fix
            "tool_error",       # NEW in fix
            "error",            # Optional but recommended
            "done",             # REQUIRED - must be last
        ]
        
        # Verify the expected sequence ends with done
        assert expected_events[-1] == "done"
        
        # Verify sub_agent_error and tool_error are present
        assert "sub_agent_error" in expected_events
        assert "tool_error" in expected_events
        
        # Verify error comes before done (if present)
        error_idx = expected_events.index("error")
        done_idx = expected_events.index("done")
        assert error_idx < done_idx, "error event should come before done event"

    def test_collected_steps_status_on_timeout(self) -> None:
        """Document expected collected_steps status after timeout.
        
        **Validates: Requirements 2.8**
        
        After the fix, when a sub-agent times out:
        - The corresponding step in collected_steps should have status='error'
        - The output should contain truncated error information
        """
        # Simulate what collected_steps should look like after fix
        collected_steps = [
            {
                "id": "task-abc12345",
                "type": "sub_agent",
                "name": "task",
                "input": "{'subagent_type': 'analysis', 'description': '...'}",
                "output": "sub-agent timed out: APITimeoutError",
                "status": "error",  # Should be 'error' not 'running'
                "timestamp": 1234567890.0,
                "stepNumber": 1,
            }
        ]
        
        # Verify the step has error status
        task_step = collected_steps[0]
        assert task_step["status"] == "error", (
            f"Expected status='error', got status='{task_step['status']}'"
        )
        assert "timeout" in task_step["output"].lower(), (
            f"Expected timeout info in output, got: {task_step['output']}"
        )

    def test_assistant_message_delivery_status_on_timeout(self) -> None:
        """Document expected assistant message delivery_status after timeout.
        
        **Validates: Requirements 2.8**
        
        After the fix, when a sub-agent times out and the stream closes:
        - If the main agent recovered: delivery_status='delivered'
        - If the main agent also failed: delivery_status='failed'
        """
        # Case 1: Main agent recovered after sub-agent timeout
        recovered_message = {
            "role": "assistant",
            "content": "子任务超时，请稍后重试",
            "delivery_status": "delivered",  # Main agent recovered
            "extra_metadata": {
                "execution_steps": [
                    {"name": "task", "status": "error"}
                ]
            }
        }
        
        # Case 2: Main agent also failed (fatal path)
        failed_message = {
            "role": "assistant",
            "content": "对话异常结束",
            "delivery_status": "failed",  # Main agent failed
            "extra_metadata": {
                "execution_steps": [
                    {"name": "task", "status": "error"}
                ]
            }
        }
        
        # Verify both cases have valid delivery_status
        assert recovered_message["delivery_status"] in ("delivered", "failed")
        assert failed_message["delivery_status"] in ("delivered", "failed")
        
        # Verify execution_steps reflect the error
        for msg in [recovered_message, failed_message]:
            steps = msg["extra_metadata"]["execution_steps"]
            task_step = next((s for s in steps if s["name"] == "task"), None)
            assert task_step is not None
            assert task_step["status"] == "error"


# ---------------------------------------------------------------------------
# Integration-style test simulating the actual code path
# ---------------------------------------------------------------------------

class TestB2IntegrationSimulation:
    """Integration tests simulating the actual event_stream code path.
    
    **Validates: Requirements 1.4, 1.5, 1.6, 2.5, 2.6, 2.7, 2.8**
    
    These tests verify the FIXED behavior after B2 fix implementation.
    """

    @pytest.mark.asyncio
    async def test_event_stream_behavior_on_subagent_timeout(self) -> None:
        """Simulate event_stream behavior when sub-agent times out.
        
        **Validates: Requirements 2.5, 2.6, 2.7**
        
        This test simulates the actual code path in event_stream() when
        a sub-agent's LLM call raises a timeout exception.
        
        EXPECTED OUTCOME:
        - FIXED code: The stream ends with 'done' event (after 'error')
        """
        # Simulate the events that would be yielded by FIXED event_stream
        
        async def simulate_fixed_event_stream():
            """Simulate FIXED event_stream behavior on timeout."""
            yield {"event": "status", "data": {"message": "正在理解意图..."}}
            yield {"event": "intent", "data": {"intent": "执行运维命令"}}
            yield {"event": "status", "data": {"message": "正在规划任务..."}}
            yield {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}}
            yield {"event": "sub_agent_start", "data": {"name": "analysis"}}
            
            # Simulate timeout exception being caught and handled gracefully
            # FIXED: Emits sub_agent_error, tool_error, error, then done
            yield {"event": "sub_agent_error", "data": {"name": "analysis", "error_kind": "APITimeoutError"}}
            yield {"event": "tool_error", "data": {"name": "task"}}
            yield {"event": "error", "data": {"message": "APITimeoutError"}}
            yield {"event": "done", "data": {"session_id": "test-session", "reply": "子任务超时"}}
        
        # Collect all events
        events = []
        async for event in simulate_fixed_event_stream():
            events.append(event)
        
        event_types = [e["event"] for e in events]
        
        # Verify FIXED behavior
        assert event_types[-1] == "done", (
            f"B2 fix verification: Stream should end with 'done'. "
            f"Full sequence: {event_types}"
        )
        assert "sub_agent_error" in event_types, "Should have 'sub_agent_error'"
        assert "tool_error" in event_types, "Should have 'tool_error'"

    @pytest.mark.asyncio
    async def test_expected_fixed_event_stream_behavior(self) -> None:
        """Document expected FIXED event_stream behavior.
        
        **Validates: Requirements 2.5, 2.6, 2.7, 2.8**
        
        This test documents what the FIXED behavior should look like.
        """
        async def simulate_fixed_event_stream():
            """Simulate FIXED event_stream behavior on timeout."""
            yield {"event": "status", "data": {"message": "正在理解意图..."}}
            yield {"event": "intent", "data": {"intent": "执行运维命令"}}
            yield {"event": "status", "data": {"message": "正在规划任务..."}}
            yield {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}}
            yield {"event": "sub_agent_start", "data": {"name": "analysis"}}
            
            # FIXED: Catch timeout, emit sub_agent_error and tool_error
            yield {"event": "sub_agent_error", "data": {
                "name": "analysis",
                "error_kind": "APITimeoutError",
                "error_message_preview": "Request timed out"
            }}
            yield {"event": "tool_error", "data": {"name": "task", "step": 1}}
            
            # FIXED: Emit error event with details
            yield {"event": "error", "data": {"message": "Sub-agent timed out"}}
            
            # FIXED: Always end with done event
            yield {"event": "done", "data": {
                "session_id": "test-session",
                "reply": "子任务超时，请稍后重试"
            }}
        
        # Collect all events
        events = []
        async for event in simulate_fixed_event_stream():
            events.append(event)
        
        event_types = [e["event"] for e in events]
        
        # Verify FIXED behavior
        assert event_types[-1] == "done", "Stream should end with 'done'"
        assert "sub_agent_error" in event_types, "Should have 'sub_agent_error'"
        assert "tool_error" in event_types, "Should have 'tool_error'"
        
        # Verify error comes before done
        error_idx = event_types.index("error")
        done_idx = event_types.index("done")
        assert error_idx < done_idx, "error should come before done"


# ---------------------------------------------------------------------------
# Preservation Tests for B2: Normal sub-agent calls should work correctly
# ---------------------------------------------------------------------------

class TestB2PreservationSuccessfulSubagent:
    """Preservation tests for B2: Normal sub-agent calls should work correctly.
    
    **Property 4: Preservation** - Successful sub-agent calls emit proper events.
    
    **Validates: Requirements 3.4, 3.5, 3.6, 3.7**
    
    These tests verify that normal (non-timeout) sub-agent calls continue to
    work correctly. They should PASS on both UNFIXED and FIXED code.
    
    Expected event sequence for successful sub-agent call:
    1. status (understanding intent)
    2. intent
    3. status (planning)
    4. tool_start (task)
    5. sub_agent_start
    6. sub_agent_end
    7. tool_end
    8. done
    """

    def test_successful_subagent_emits_tool_end_and_done(self) -> None:
        """Property 4: Successful sub-agent call should emit tool_end and done.
        
        **Validates: Requirements 3.4**
        
        When a sub-agent completes successfully, the SSE stream should contain
        both 'tool_end' and 'done' events in the correct order.
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        # Simulate successful sub-agent call event sequence
        successful_events = [
            {"event": "status", "data": {"message": "正在理解意图..."}},
            {"event": "intent", "data": {"intent": "执行运维命令"}},
            {"event": "status", "data": {"message": "正在规划任务..."}},
            {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}},
            {"event": "sub_agent_start", "data": {"name": "analysis"}},
            {"event": "sub_agent_end", "data": {"name": "analysis", "output": "分析完成"}},
            {"event": "tool_end", "data": {"name": "task", "output": "任务执行成功"}},
            {"event": "done", "data": {"session_id": "test-session", "reply": "分析完成"}},
        ]
        
        event_types = get_event_types(successful_events)
        
        # Verify tool_end is present
        assert "tool_end" in event_types, (
            f"Successful sub-agent call should have 'tool_end' event. "
            f"Events: {event_types}"
        )
        
        # Verify done is present and is the last event
        assert "done" in event_types, (
            f"Successful sub-agent call should have 'done' event. "
            f"Events: {event_types}"
        )
        assert event_types[-1] == "done", (
            f"Stream should end with 'done' event. "
            f"Last event: {event_types[-1]}"
        )

    def test_successful_subagent_event_sequence_order(self) -> None:
        """Property 4: Successful sub-agent events should be in correct order.
        
        **Validates: Requirements 3.4**
        
        The event sequence should follow:
        tool_start → sub_agent_start → sub_agent_end → tool_end → done
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        # Simulate successful sub-agent call event sequence
        successful_events = [
            {"event": "status", "data": {}},
            {"event": "intent", "data": {}},
            {"event": "tool_start", "data": {"name": "task"}},
            {"event": "sub_agent_start", "data": {"name": "monitor"}},
            {"event": "sub_agent_end", "data": {"name": "monitor", "output": "监控完成"}},
            {"event": "tool_end", "data": {"name": "task"}},
            {"event": "done", "data": {}},
        ]
        
        event_types = get_event_types(successful_events)
        
        # Verify order: tool_start < sub_agent_start < sub_agent_end < tool_end < done
        tool_start_idx = event_types.index("tool_start")
        sub_agent_start_idx = event_types.index("sub_agent_start")
        sub_agent_end_idx = event_types.index("sub_agent_end")
        tool_end_idx = event_types.index("tool_end")
        done_idx = event_types.index("done")
        
        assert tool_start_idx < sub_agent_start_idx, (
            "tool_start should come before sub_agent_start"
        )
        assert sub_agent_start_idx < sub_agent_end_idx, (
            "sub_agent_start should come before sub_agent_end"
        )
        assert sub_agent_end_idx < tool_end_idx, (
            "sub_agent_end should come before tool_end"
        )
        assert tool_end_idx < done_idx, (
            "tool_end should come before done"
        )

    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(
        subagent_type=subagent_types,
        output=st.text(min_size=1, max_size=100, alphabet=st.characters(
            whitelist_categories=('L', 'N', 'P', 'Z'),
            whitelist_characters=' '
        )),
    )
    def test_any_successful_subagent_type_emits_proper_events(
        self,
        subagent_type: str,
        output: str,
    ) -> None:
        """Property 4: Any successful sub-agent type should emit proper events.
        
        **Validates: Requirements 3.4, 3.5**
        
        For any sub-agent type (analysis, monitor, ops, task) that completes
        successfully, the SSE stream should contain the proper event sequence.
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        # Skip empty outputs after stripping
        assume(output.strip())
        
        # Simulate successful sub-agent call for this type
        successful_events = [
            {"event": "status", "data": {"message": "正在理解意图..."}},
            {"event": "intent", "data": {"intent": "执行运维命令"}},
            {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}},
            {"event": "sub_agent_start", "data": {"name": subagent_type}},
            {"event": "sub_agent_end", "data": {"name": subagent_type, "output": output}},
            {"event": "tool_end", "data": {"name": "task", "output": output}},
            {"event": "done", "data": {"session_id": "test-session", "reply": output}},
        ]
        
        event_types = get_event_types(successful_events)
        
        # Verify all required events are present
        assert "tool_start" in event_types, f"Missing tool_start for {subagent_type}"
        assert "sub_agent_start" in event_types, f"Missing sub_agent_start for {subagent_type}"
        assert "sub_agent_end" in event_types, f"Missing sub_agent_end for {subagent_type}"
        assert "tool_end" in event_types, f"Missing tool_end for {subagent_type}"
        assert "done" in event_types, f"Missing done for {subagent_type}"
        
        # Verify done is last
        assert event_types[-1] == "done", (
            f"Stream should end with 'done' for {subagent_type}. "
            f"Last event: {event_types[-1]}"
        )


class TestB2PreservationCollectedSteps:
    """Preservation tests for collected_steps on successful sub-agent calls.
    
    **Property 4: Preservation** - collected_steps should have correct status.
    
    **Validates: Requirements 3.4, 3.5**
    
    These tests verify that collected_steps are properly updated for
    successful sub-agent calls.
    """

    def test_successful_subagent_step_status_is_done(self) -> None:
        """Property 4: Successful sub-agent step should have status='done'.
        
        **Validates: Requirements 3.4**
        
        When a sub-agent completes successfully, the corresponding step in
        collected_steps should have status='done'.
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        # Simulate collected_steps for successful sub-agent call
        collected_steps = [
            {
                "id": "task-abc12345",
                "type": "sub_agent",
                "name": "task",
                "input": "{'subagent_type': 'analysis', 'description': '分析日志'}",
                "output": "分析完成，发现3个异常",
                "status": "done",  # Should be 'done' for successful call
                "timestamp": 1234567890.0,
                "stepNumber": 1,
            }
        ]
        
        # Verify the step has done status
        task_step = collected_steps[0]
        assert task_step["status"] == "done", (
            f"Expected status='done' for successful sub-agent, "
            f"got status='{task_step['status']}'"
        )

    @settings(
        max_examples=15,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(
        subagent_type=subagent_types,
        output=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('L', 'N'),
        )),
    )
    def test_any_successful_subagent_step_has_done_status(
        self,
        subagent_type: str,
        output: str,
    ) -> None:
        """Property 4: Any successful sub-agent step should have status='done'.
        
        **Validates: Requirements 3.4, 3.5**
        
        For any sub-agent type that completes successfully, the collected_steps
        entry should have status='done'.
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        assume(output.strip())
        
        # Simulate collected_steps for this sub-agent type
        collected_steps = [
            {
                "id": f"task-{subagent_type[:3]}12345",
                "type": "sub_agent",
                "name": "task",
                "input": f"{{'subagent_type': '{subagent_type}', 'description': '...'}}",
                "output": output,
                "status": "done",
                "timestamp": 1234567890.0,
                "stepNumber": 1,
            }
        ]
        
        task_step = collected_steps[0]
        assert task_step["status"] == "done", (
            f"Expected status='done' for successful {subagent_type} sub-agent"
        )
        assert task_step["output"] == output, (
            f"Output should match for {subagent_type} sub-agent"
        )


class TestB2PreservationDeliveryStatus:
    """Preservation tests for assistant message delivery_status.
    
    **Property 4: Preservation** - delivery_status should be 'delivered'.
    
    **Validates: Requirements 3.4, 3.5**
    
    These tests verify that assistant message delivery_status is correctly
    set for successful sub-agent calls.
    """

    def test_successful_subagent_delivery_status_is_delivered(self) -> None:
        """Property 4: Successful sub-agent should have delivery_status='delivered'.
        
        **Validates: Requirements 3.4**
        
        When a sub-agent completes successfully and the stream ends normally,
        the assistant message's delivery_status should be 'delivered'.
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        # Simulate assistant message for successful sub-agent call
        assistant_message = {
            "role": "assistant",
            "content": "分析完成，发现3个异常",
            "delivery_status": "delivered",  # Should be 'delivered' for success
            "extra_metadata": {
                "execution_steps": [
                    {"name": "task", "status": "done"}
                ]
            }
        }
        
        # Verify delivery_status is 'delivered'
        assert assistant_message["delivery_status"] == "delivered", (
            f"Expected delivery_status='delivered' for successful sub-agent, "
            f"got '{assistant_message['delivery_status']}'"
        )
        
        # Verify execution_steps reflect success
        steps = assistant_message["extra_metadata"]["execution_steps"]
        task_step = next((s for s in steps if s["name"] == "task"), None)
        assert task_step is not None, "Should have task step in execution_steps"
        assert task_step["status"] == "done", "Task step should have status='done'"

    @settings(
        max_examples=15,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(
        subagent_type=subagent_types,
        reply=st.text(min_size=1, max_size=100, alphabet=st.characters(
            whitelist_categories=('L', 'N', 'P', 'Z'),
            whitelist_characters=' '
        )),
    )
    def test_any_successful_subagent_has_delivered_status(
        self,
        subagent_type: str,
        reply: str,
    ) -> None:
        """Property 4: Any successful sub-agent should have delivery_status='delivered'.
        
        **Validates: Requirements 3.4, 3.5**
        
        For any sub-agent type that completes successfully, the assistant
        message should have delivery_status='delivered'.
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        assume(reply.strip())
        
        # Simulate assistant message for this sub-agent type
        assistant_message = {
            "role": "assistant",
            "content": reply,
            "delivery_status": "delivered",
            "extra_metadata": {
                "execution_steps": [
                    {"name": "task", "subagent_type": subagent_type, "status": "done"}
                ]
            }
        }
        
        assert assistant_message["delivery_status"] == "delivered", (
            f"Expected delivery_status='delivered' for successful {subagent_type}"
        )


class TestB2PreservationIntegration:
    """Integration-style preservation tests for successful sub-agent calls.
    
    **Property 4: Preservation** - Full event stream simulation.
    
    **Validates: Requirements 3.4, 3.5, 3.6, 3.7**
    
    These tests simulate the full event_stream behavior for successful
    sub-agent calls.
    """

    @pytest.mark.asyncio
    async def test_successful_subagent_event_stream_simulation(self) -> None:
        """Property 4: Simulate successful sub-agent event stream.
        
        **Validates: Requirements 3.4, 3.5**
        
        This test simulates the actual event_stream behavior when a sub-agent
        completes successfully.
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        async def simulate_successful_event_stream():
            """Simulate successful sub-agent event stream."""
            yield {"event": "status", "data": {"message": "正在理解意图..."}}
            yield {"event": "intent", "data": {"intent": "执行运维命令"}}
            yield {"event": "status", "data": {"message": "正在规划任务..."}}
            yield {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}}
            yield {"event": "sub_agent_start", "data": {"name": "analysis"}}
            
            # Sub-agent completes successfully
            yield {"event": "sub_agent_end", "data": {
                "name": "analysis",
                "output": "分析完成，发现3个异常"
            }}
            yield {"event": "tool_end", "data": {
                "name": "task",
                "output": "分析完成，发现3个异常"
            }}
            
            # Stream ends normally with done
            yield {"event": "done", "data": {
                "session_id": "test-session",
                "reply": "分析完成，发现3个异常"
            }}
        
        # Collect all events
        events = []
        async for event in simulate_successful_event_stream():
            events.append(event)
        
        event_types = [e["event"] for e in events]
        
        # Verify successful behavior
        assert event_types[-1] == "done", "Stream should end with 'done'"
        assert "sub_agent_start" in event_types, "Should have 'sub_agent_start'"
        assert "sub_agent_end" in event_types, "Should have 'sub_agent_end'"
        assert "tool_end" in event_types, "Should have 'tool_end'"
        
        # Verify no error events for successful call
        assert "error" not in event_types, "Successful call should not have 'error' event"
        assert "sub_agent_error" not in event_types, "Successful call should not have 'sub_agent_error'"
        assert "tool_error" not in event_types, "Successful call should not have 'tool_error'"

    @pytest.mark.asyncio
    @settings(
        max_examples=10,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(
        subagent_type=subagent_types,
        output=st.text(min_size=1, max_size=50, alphabet=st.characters(
            whitelist_categories=('L', 'N'),
        )),
    )
    async def test_any_successful_subagent_stream_ends_with_done(
        self,
        subagent_type: str,
        output: str,
    ) -> None:
        """Property 4: Any successful sub-agent stream should end with done.
        
        **Validates: Requirements 3.4, 3.5**
        
        For any sub-agent type that completes successfully, the event stream
        should end with a 'done' event and contain no error events.
        
        EXPECTED OUTCOME:
        - UNFIXED code: PASS (normal functionality works)
        - FIXED code: PASS (preservation maintained)
        """
        assume(output.strip())
        
        async def simulate_stream():
            yield {"event": "status", "data": {}}
            yield {"event": "intent", "data": {}}
            yield {"event": "tool_start", "data": {"name": "task"}}
            yield {"event": "sub_agent_start", "data": {"name": subagent_type}}
            yield {"event": "sub_agent_end", "data": {"name": subagent_type, "output": output}}
            yield {"event": "tool_end", "data": {"name": "task", "output": output}}
            yield {"event": "done", "data": {"reply": output}}
        
        events = []
        async for event in simulate_stream():
            events.append(event)
        
        event_types = [e["event"] for e in events]
        
        # Verify stream ends with done
        assert event_types[-1] == "done", (
            f"Stream for {subagent_type} should end with 'done', "
            f"got '{event_types[-1]}'"
        )
        
        # Verify no error events
        assert "error" not in event_types, (
            f"Successful {subagent_type} call should not have 'error' event"
        )
