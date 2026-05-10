# B2 Counterexample: Sub-agent Timeout Kills SSE Stream

## Bug Summary

**Bug ID**: B2
**Bug Condition**: Sub-agent LLM timeout (DeepSeek TLS handshake failure) kills the entire SSE stream
**Root Cause**: The outer `except Exception` handler in `event_stream()` catches timeout exceptions and yields only an `error` event, then returns without yielding a `done` event.

## Counterexample Details

### Test Execution Date
2026-05-09

### Failing Test
`tests/pbt/test_b2_subagent_timeout.py::TestB2SubagentTimeoutBugCondition::test_any_timeout_exception_produces_proper_event_sequence`

### Falsifying Example (from Hypothesis)
```python
exc_type='openai.APITimeoutError'
subagent_type='analysis'
```

### Observed Behavior (UNFIXED Code)

**SSE Event Sequence**:
```
event: status
data: {"message": "正在理解意图..."}

event: intent
data: {"intent": "执行运维命令"}

event: status
data: {"message": "正在规划任务..."}

event: tool_start
data: {"name": "task", "tool_type": "sub_agent"}

event: sub_agent_start
data: {"name": "analysis"}

event: error
data: {"message": "APITimeoutError: Request timed out"}

[STREAM ENDS - NO DONE EVENT]
```

### Expected Behavior (FIXED Code)

**SSE Event Sequence**:
```
event: status
data: {"message": "正在理解意图..."}

event: intent
data: {"intent": "执行运维命令"}

event: status
data: {"message": "正在规划任务..."}

event: tool_start
data: {"name": "task", "tool_type": "sub_agent"}

event: sub_agent_start
data: {"name": "analysis"}

event: sub_agent_error
data: {"name": "analysis", "error_kind": "APITimeoutError", "error_message_preview": "Request timed out"}

event: tool_error
data: {"name": "task", "step": 1}

event: error
data: {"message": "Sub-agent timed out"}

event: done
data: {"session_id": "...", "reply": "子任务超时，请稍后重试"}
```

## Bug Condition Analysis

### Input Domain
```
ChatInvocation {
  will_trigger_tool: true,
  tool_name: "task",
  subagent_will_timeout: true,
  timeout_exception_type: one of {
    openai.APITimeoutError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    asyncio.TimeoutError
  }
}
```

### Bug Condition Function
```python
def isBugCondition_B2(inv):
    return (
        inv.will_trigger_tool == True
        and "task" in inv.tools_invoked
        and inv.subagent_llm_raises_timeout_exception
    )
```

## Server Log References

From `server.log` lines 289-500, 913-1020:

```
2026-05-09 10:42:46.123 ERROR [event_stream] SSE chat error: APITimeoutError: Request timed out
Traceback (most recent call last):
  File "server/src/api/execution/router.py", line 1062, in event_stream
    async for event in _agent.astream_events(...):
  ...
  File "langchain_openai/chat_models/base.py", line 456, in _agenerate
    raise openai.APITimeoutError(request=request)
openai.APITimeoutError: Request timed out
```

## Assertions That Failed

1. **Last event is 'done'**: FAILED
   - Expected: `event_types[-1] == "done"`
   - Actual: `event_types[-1] == "error"`

2. **Contains 'sub_agent_error'**: FAILED
   - Expected: `"sub_agent_error" in event_types`
   - Actual: `"sub_agent_error" not in event_types`

3. **Contains 'tool_error'**: FAILED
   - Expected: `"tool_error" in event_types`
   - Actual: `"tool_error" not in event_types`

## Impact

- Frontend receives `error` as the last event, interprets conversation as "failed"
- No `done` event means frontend SSE handler doesn't properly close the stream
- Assistant message `delivery_status` remains `pending` instead of being updated
- User sees "对话没完成" instead of a graceful degradation message

## Fix Requirements

1. Add `_SUBAGENT_TIMEOUT_ERRORS` whitelist tuple
2. Wrap `astream_events` loop in `try/except _SUBAGENT_TIMEOUT_ERRORS`
3. On timeout: yield `sub_agent_error`, `tool_error` events
4. Inject `ToolMessage` with error info back to agent
5. Allow retry (up to 2 times) or graceful close
6. Always end with `done` event (after `error` if needed)
7. Update `collected_steps[...].status = "error"`
8. Set `delivery_status = "failed"` for fatal path

## Related Requirements

- Requirements 1.4, 1.5, 1.6 (Bug Condition)
- Requirements 2.5, 2.6, 2.7, 2.8 (Expected Behavior)
- Requirements 3.4, 3.5, 3.6, 3.7 (Preservation)
