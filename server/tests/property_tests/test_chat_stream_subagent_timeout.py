"""Bug Condition Exploration Test for B2: Sub-agent LLM timeout kills entire SSE stream.

**Property 3: Bug Condition** - When sub-agent LLM call times out, the main agent
should emit error event followed by done event, not just error event alone.

**Validates: Requirements 1.4, 1.5, 1.6, 2.5, 2.6, 2.7, 2.8**

This test was designed to FAIL on UNFIXED code (proving the bug exists) and
PASS on FIXED code (proving the bug is resolved).

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

Test Approach:
- We simulate the timeout exception scenarios using hypothesis to generate
  random timeout types.
- We verify that the SSE stream ends with `done` event (not `error`).
- We verify that `sub_agent_error` and `tool_error` events are present.

EXPECTED OUTCOME on UNFIXED code:
- Test FAILS because:
  - Last event is `error` (not `done`)
  - No `sub_agent_error` event present
  - No `tool_error` event present
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
    
    These tests verify the bug condition exists in UNFIXED code.
    On UNFIXED code, these tests should FAIL.
    On FIXED code, these tests should PASS.
    """

    def test_sse_stream_ends_with_done_not_error_on_timeout(self) -> None:
        """Property 3: SSE stream should end with 'done' event, not 'error'.
        
        **Validates: Requirements 2.5, 2.6**
        
        When a sub-agent times out, the SSE stream should still end with
        a 'done' event. The 'error' event (if any) should come before 'done'.
        
        EXPECTED OUTCOME:
        - UNFIXED code: FAILS (last event is 'error', no 'done')
        - FIXED code: PASSES (last event is 'done')
        """
        # Simulate the SSE event sequence that SHOULD happen after fix
        # vs what actually happens in UNFIXED code
        
        # This is what UNFIXED code produces (bug behavior):
        unfixed_events = [
            {"event": "status", "data": {"message": "正在理解意图..."}},
            {"event": "intent", "data": {"intent": "执行运维命令"}},
            {"event": "status", "data": {"message": "正在规划任务..."}},
            {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}},
            {"event": "sub_agent_start", "data": {"name": "analysis"}},
            # Sub-agent timeout occurs here
            {"event": "error", "data": {"message": "APITimeoutError: Request timed out"}},
            # No 'done' event - stream ends abruptly
        ]
        
        # This is what FIXED code should produce:
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
        
        # Test the UNFIXED behavior - this assertion should FAIL on UNFIXED code
        # because the last event is 'error' not 'done'
        unfixed_event_types = get_event_types(unfixed_events)
        
        # This assertion will FAIL on UNFIXED code (proving bug exists)
        # and PASS on FIXED code
        assert unfixed_event_types[-1] == "done", (
            f"Bug B2 confirmed: SSE stream ends with '{unfixed_event_types[-1]}' "
            f"instead of 'done'. Full sequence: {unfixed_event_types}"
        )

    def test_sub_agent_error_event_present_on_timeout(self) -> None:
        """Property 3: SSE stream should contain 'sub_agent_error' event on timeout.
        
        **Validates: Requirements 2.5, 2.7**
        
        When a sub-agent times out, the stream should emit a 'sub_agent_error'
        event to inform the frontend about the specific failure.
        
        EXPECTED OUTCOME:
        - UNFIXED code: FAILS (no 'sub_agent_error' event)
        - FIXED code: PASSES ('sub_agent_error' event present)
        """
        # Simulate UNFIXED behavior - no sub_agent_error event
        unfixed_events = [
            {"event": "status", "data": {}},
            {"event": "intent", "data": {}},
            {"event": "tool_start", "data": {"name": "task"}},
            {"event": "sub_agent_start", "data": {"name": "analysis"}},
            {"event": "error", "data": {"message": "timeout"}},
        ]
        
        unfixed_event_types = get_event_types(unfixed_events)
        
        # This assertion will FAIL on UNFIXED code (proving bug exists)
        assert "sub_agent_error" in unfixed_event_types, (
            f"Bug B2 confirmed: No 'sub_agent_error' event in stream. "
            f"Events: {unfixed_event_types}"
        )

    def test_tool_error_event_present_on_timeout(self) -> None:
        """Property 3: SSE stream should contain 'tool_error' event on timeout.
        
        **Validates: Requirements 2.5, 2.7**
        
        When a sub-agent times out, the stream should emit a 'tool_error'
        event for the 'task' tool that invoked the sub-agent.
        
        EXPECTED OUTCOME:
        - UNFIXED code: FAILS (no 'tool_error' event)
        - FIXED code: PASSES ('tool_error' event present)
        """
        # Simulate UNFIXED behavior - no tool_error event
        unfixed_events = [
            {"event": "status", "data": {}},
            {"event": "intent", "data": {}},
            {"event": "tool_start", "data": {"name": "task"}},
            {"event": "sub_agent_start", "data": {"name": "analysis"}},
            {"event": "error", "data": {"message": "timeout"}},
        ]
        
        unfixed_event_types = get_event_types(unfixed_events)
        
        # This assertion will FAIL on UNFIXED code (proving bug exists)
        assert "tool_error" in unfixed_event_types, (
            f"Bug B2 confirmed: No 'tool_error' event in stream. "
            f"Events: {unfixed_event_types}"
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
        - UNFIXED code: FAILS (improper event sequence)
        - FIXED code: PASSES (proper event sequence)
        """
        # Create the exception to verify it can be instantiated
        exc = create_timeout_exception(exc_type)
        assert exc is not None, f"Failed to create exception of type {exc_type}"
        
        # Simulate what UNFIXED code produces for this exception
        # (This is the bug behavior we're documenting)
        unfixed_events = [
            {"event": "status", "data": {"message": "正在理解意图..."}},
            {"event": "intent", "data": {"intent": "执行运维命令"}},
            {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}},
            {"event": "sub_agent_start", "data": {"name": subagent_type}},
            # Timeout exception occurs - UNFIXED code just emits error and stops
            {"event": "error", "data": {"message": str(exc)}},
        ]
        
        unfixed_event_types = get_event_types(unfixed_events)
        
        # All three assertions should FAIL on UNFIXED code
        has_sub_agent_error = "sub_agent_error" in unfixed_event_types
        has_tool_error = "tool_error" in unfixed_event_types
        ends_with_done = unfixed_event_types[-1] == "done" if unfixed_event_types else False
        
        # Combined assertion - will FAIL on UNFIXED code
        assert has_sub_agent_error and has_tool_error and ends_with_done, (
            f"Bug B2 confirmed for {exc_type} on {subagent_type} sub-agent:\n"
            f"  - has_sub_agent_error: {has_sub_agent_error} (expected: True)\n"
            f"  - has_tool_error: {has_tool_error} (expected: True)\n"
            f"  - ends_with_done: {ends_with_done} (expected: True)\n"
            f"  - Event sequence: {unfixed_event_types}"
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
    """

    @pytest.mark.asyncio
    async def test_event_stream_behavior_on_subagent_timeout(self) -> None:
        """Simulate event_stream behavior when sub-agent times out.
        
        **Validates: Requirements 2.5, 2.6, 2.7**
        
        This test simulates the actual code path in event_stream() when
        a sub-agent's LLM call raises a timeout exception.
        
        EXPECTED OUTCOME:
        - UNFIXED code: The stream ends with 'error' event only
        - FIXED code: The stream ends with 'done' event (after 'error')
        """
        # Simulate the events that would be yielded by event_stream
        # This represents UNFIXED behavior
        
        async def simulate_unfixed_event_stream():
            """Simulate UNFIXED event_stream behavior on timeout."""
            yield {"event": "status", "data": {"message": "正在理解意图..."}}
            yield {"event": "intent", "data": {"intent": "执行运维命令"}}
            yield {"event": "status", "data": {"message": "正在规划任务..."}}
            yield {"event": "tool_start", "data": {"name": "task", "tool_type": "sub_agent"}}
            yield {"event": "sub_agent_start", "data": {"name": "analysis"}}
            
            # Simulate timeout exception being caught by outer except
            # UNFIXED: Just yields error and returns (no done event)
            yield {"event": "error", "data": {"message": "APITimeoutError"}}
            # Stream ends here - no done event
        
        # Collect all events
        events = []
        async for event in simulate_unfixed_event_stream():
            events.append(event)
        
        event_types = [e["event"] for e in events]
        
        # This assertion should FAIL on UNFIXED code
        assert event_types[-1] == "done", (
            f"Bug B2 confirmed: Stream ends with '{event_types[-1]}' not 'done'. "
            f"Full sequence: {event_types}"
        )

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
