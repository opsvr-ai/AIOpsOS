# B1 Counterexample: UnboundLocalError — UTC in event_stream trajectory emit

**Bug**: `UnboundLocalError: cannot access local variable 'UTC'` in `event_stream()` trajectory emit block  
**Property**: Property 1 — Bug Condition  
**Test File**: `server/tests/property_tests/test_chat_stream_trajectory_unbound_utc.py`  
**Requirements**: 1.1, 1.2, 1.3  
**Status**: CONFIRMED (bug reproduced; fix applied in Task 3.1)  
**Date**: 2026-05-09

---

## Bug Description

在 `chat_stream()` 的嵌套闭包 `event_stream()` 内，第 1177 行存在 `from datetime import UTC`，位于 `on_tool_start` 分支内部。

根据 Python PEP 227 的作用域规则：**函数体内任何地方出现的赋值/import 都让该名字成为 local**，即使该语句位于条件分支中也不例外（`dis` 输出可见 `STORE_FAST UTC`）。

因此，对 `event_stream` 而言，`UTC` 是一个 local 变量。当对话无工具调用时（纯文本对话），`on_tool_start` 分支从未执行，第 1177 行的 import 也从未执行，到第 1372 行 `datetime.now(UTC)` 时 local 未绑定，抛出 `UnboundLocalError`。

该异常被 `logger.debug("trajectory emit ...", exc_info=True)` 静默吞掉，导致 bug 长期 silent，`agent_trajectories` 表中缺少对应记录。

---

## Counterexample Details

### Falsifying Example

```python
# Bug condition: will_trigger_tool=False, trajectory_enabled=True
message = "你好"   # Short greeting, no tool calls triggered
provider = "DEEPSEEK"
will_trigger_tool = False
trajectory_enabled = True
```

### Observed Behavior (UNFIXED Code)

**Python Scoping Trap**:
```python
# Inside event_stream() (nested closure of chat_stream):

def event_stream():
    # ... (no UTC import at top of event_stream)
    
    async for event in _agent.astream_events(...):
        if event["event"] == "on_tool_start":
            from datetime import UTC   # ← Line 1177: makes UTC a LOCAL variable
            from datetime import datetime as _dt  # ← Line 1179
            # ...
        
        # ... later, in trajectory emit block:
        ts = datetime.now(UTC)  # ← Line 1372: UnboundLocalError when tool branch never ran
```

**Error Stack Trace** (from server.log lines 498–504):
```
ERROR    server.api.execution.router:router.py:1372
  UnboundLocalError: cannot access local variable 'UTC' before assignment
  
  File "server/src/api/execution/router.py", line 1372, in event_stream
    ts=datetime.now(UTC).timestamp(),
  
  During handling of the above exception, another exception occurred:
  
  File "server/src/api/execution/router.py", line 1374, in event_stream
    logger.debug("trajectory emit failed", exc_info=True)
```

**Test Assertion Failures** (on UNFIXED code):
```
FAILED test_chat_stream_trajectory_unbound_utc.py::TestTrajectoryEmitIntegration::test_trajectory_sink_emit_called_for_pure_text_chat
  AssertionError: assert mock_sink.emit.called
  # emit was never reached because datetime.now(UTC) raised UnboundLocalError first

FAILED test_chat_stream_trajectory_unbound_utc.py::TestB1UTCUnboundLocalError::test_utc_accessible_in_nested_closure_without_tool_branch
  UnboundLocalError: cannot access local variable 'UTC' before assignment
```

### Measured Impact

| Message | Tool Calls | trajectory_enabled | emit.called | Error |
|---------|-----------|-------------------|-------------|-------|
| 你好 | False | True | False | UnboundLocalError |
| 嗯 | False | True | False | UnboundLocalError |
| ok | False | True | False | UnboundLocalError |
| hi | False | True | False | UnboundLocalError |
| 早 | False | True | False | UnboundLocalError |
| 谢谢 | False | True | False | UnboundLocalError |

---

## Root Cause Analysis

### Python Closure Local Binding (PEP 227)

```python
import dis

def outer():
    from datetime import UTC, datetime
    
    def inner(execute_branch):
        if execute_branch:
            from datetime import UTC  # ← This makes UTC LOCAL to inner()
        return datetime.now(UTC)      # ← UnboundLocalError when branch not taken
    
    return inner

# dis.dis(outer()) shows:
#   LOAD_FAST 'UTC'   ← UTC is a FAST (local) variable
#   not LOAD_DEREF    ← would be DEREF if it were a closure variable
```

The `from datetime import UTC` inside the `if execute_branch:` block causes Python to treat `UTC` as a local variable for the **entire** `inner()` function, even before the `if` statement. When `execute_branch=False`, the assignment never happens, so accessing `UTC` raises `UnboundLocalError`.

### Historical Context

The duplicate import was added when `collected_steps` logic was added to the `on_tool_start` branch. The author forgot that `chat_stream()` already imports `UTC` at line 758. The `logger.debug` exception handler silenced the error for months.

---

## Fix Applied (Task 3.1)

**File**: `server/src/api/execution/router.py`

**Change**:
1. Removed `from datetime import UTC` (line 1177) from inside `event_stream()`
2. Removed `from datetime import datetime as _dt` (line 1179) from inside `event_stream()`
3. Changed `_dt.now(UTC).timestamp()` → `datetime.now(UTC).timestamp()` (reusing outer scope import)
4. Added comment: `# UTC/datetime 已在 chat_stream 顶部 import；不要在嵌套闭包内重复 import，否则会触发 Python local 绑定陷阱 (PEP 227)`

**Verification**: After fix, `test_chat_stream_trajectory_unbound_utc.py` — all tests PASS.

---

## Related Requirements

- Requirements 1.1, 1.2, 1.3 (Bug Condition)
- Requirements 2.1, 2.2, 2.3, 2.4 (Expected Behavior)
- Requirements 3.1, 3.2, 3.3 (Preservation)

## Test Files

- Bug condition test: `server/tests/property_tests/test_chat_stream_trajectory_unbound_utc.py`
- Preservation test: `server/tests/property_tests/test_chat_stream_with_tools_golden.py` (Task 2)
