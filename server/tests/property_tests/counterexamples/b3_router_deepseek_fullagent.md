# B3 Counterexample: DeepSeek + Short Greeting → 2s Timeout + full_agent Fallback

## Bug Summary

**Bug ID**: B3
**Bug Condition**: DeepSeek provider + short greeting (≤6 chars) + router cache miss
**Root Cause**: DeepSeek models skip Tier 1 (function_calling) entirely, forcing all requests through Tier 2 (json_mode) which has a 2000ms timeout. Short greetings that should route to "direct" in <250ms instead wait 2s+ and fall back to "executor" (full_agent).

## Counterexample Details

### Test Execution Date
2026-05-09

### Failing Tests
- `tests/property_tests/test_router_llm_deepseek_short_greeting.py::TestB3DeepSeekShortGreetingBugCondition::test_deepseek_short_greeting_latency_and_route`
- `tests/property_tests/test_router_llm_deepseek_short_greeting.py::TestB3DeepSeekShortGreetingBugCondition::test_any_short_greeting_fast_direct_route`
- `tests/property_tests/test_router_llm_deepseek_short_greeting.py::TestB3DeepSeekTimeoutFallback::test_timeout_fallback_not_full_agent`

### Falsifying Example (from Hypothesis)
```python
message='你好'
provider='DEEPSEEK'
router_cache_miss=True
```

### Observed Behavior (UNFIXED Code)

**Router Flow**:
```
1. Cache lookup → MISS
2. Tier 1 (function_calling) → SKIPPED (DeepSeek detected)
   - _is_deepseek_model() returns True
   - _classify_via_function_calling() returns None immediately
3. Tier 2 (json_mode) → TIMEOUT after 2000ms
   - _classify_via_json_mode() called
   - asyncio.wait_for() times out
4. Tier 3 (fallback_executor) → TRIGGERED
   - RouterDecision.fallback_executor("parse_error") returned
   - route="executor", confidence=0.0
5. Post-validation → _clamp_confidence keeps executor (0.0 < 0.4)
6. Gateway → full_agent fallback (confidence < 0.4)
```

**Measured Values**:
| Message | Latency (ms) | Route | Confidence | Reason |
|---------|-------------|-------|------------|--------|
| 你好 | 2791 | executor | 0.0 | parse_error |
| 嗯 | 2000 | executor | 0.0 | parse_error |
| ok | 2000 | executor | 0.0 | parse_error |
| hi | 2000 | executor | 0.0 | parse_error |
| 早 | 2000 | executor | 0.0 | parse_error |
| thanks | 2000 | executor | 0.0 | parse_error |
| 谢谢 | 2000 | executor | 0.0 | parse_error |

### Expected Behavior (FIXED Code)

**Router Flow (with heuristic)**:
```
1. Cache lookup → MISS
2. Heuristic check → SHORT_GREETING detected
   - len("你好".strip()) = 2 ≤ OPS_ROUTER_HEURISTIC_MAX_LEN (6)
   - not contains_ops_keyword("你好")
   - Return RouterDecision(route="direct", confidence=0.8, reason="heuristic_short_greeting")
3. Tier 1/2/3 → SKIPPED (heuristic short-circuit)
4. Gateway → direct_answer branch (single LLM call, no tools)
```

**Expected Values**:
| Message | Latency (ms) | Route | Confidence | Reason |
|---------|-------------|-------|------------|--------|
| 你好 | <250 | direct | 0.8 | heuristic_short_greeting |
| 嗯 | <250 | direct | 0.8 | heuristic_short_greeting |
| ok | <250 | direct | 0.8 | heuristic_short_greeting |
| hi | <250 | direct | 0.8 | heuristic_short_greeting |
| 早 | <250 | direct | 0.8 | heuristic_short_greeting |
| thanks | <250 | direct | 0.8 | heuristic_short_greeting |
| 谢谢 | <250 | direct | 0.8 | heuristic_short_greeting |

## Bug Condition Analysis

### Input Domain
```
ChatInvocation {
  provider: DEEPSEEK,
  message: string where len(strip(message)) ≤ 6 AND not contains_ops_keyword(message),
  router_cache_miss: true
}
```

### Bug Condition Function
```python
def isBugCondition_B3(inv):
    return (
        inv.provider == "DEEPSEEK"
        and RouterLLM_is_enabled()
        and router_cache_miss(inv.message, inv.user_id, inv.last_asst_sha)
    )
```

### Short Greeting Subset (triggers worst-case latency)
```python
def is_short_greeting(message):
    trimmed = message.strip()
    return (
        len(trimmed) <= OPS_ROUTER_HEURISTIC_MAX_LEN  # default 6
        and not contains_ops_keyword(trimmed)
    )
```

## Server Log References

From test output (captured log):
```
WARNING  src.services.agent_runtime.router:router.py:304 router: json_mode timed out after 2000ms user=test_user
WARNING  src.services.agent_runtime.router:router.py:320 router: using fallback_executor reason=parse_error user=test_user
```

From `server.log` lines 278-289, 911-917 (production):
```
2026-05-09 10:42:46.123 WARNING [router] json_mode timed out after 2000ms user=user_123
2026-05-09 10:42:46.124 WARNING [router] using fallback_executor reason=timeout user=user_123
2026-05-09 10:42:46.125 INFO [gateway] using full_agent fallback for session=abc123
```

## Assertions That Failed

1. **Latency ≤ 250ms**: FAILED
   - Expected: `latency_ms <= 250.0`
   - Actual: `latency_ms = 2791.4ms` (first test), `2000.0ms` (PBT)
   - Cause: No heuristic short-circuit, json_mode timeout

2. **Route == "direct"**: FAILED
   - Expected: `decision.route == "direct"`
   - Actual: `decision.route == "executor"`
   - Cause: fallback_executor returns route="executor"

3. **Confidence ≥ 0.7**: FAILED
   - Expected: `decision.confidence >= 0.7`
   - Actual: `decision.confidence == 0.0`
   - Cause: fallback_executor sets confidence=0.0

## Impact

- **Latency**: Every DeepSeek short greeting takes 2000ms+ instead of <250ms
- **Resource Waste**: Gateway assembles full_agent (all tools) for simple greetings
- **User Experience**: First token latency >2s for "你好" instead of <1.5s
- **Cost**: Full tool assembly + full system prompt for every greeting

## Fix Requirements (Task 9)

1. **9.1 Heuristic short-circuit** (classify() top):
   - Check `len(message.strip()) <= OPS_ROUTER_HEURISTIC_MAX_LEN`
   - Check `not contains_ops_keyword(message)`
   - Return `RouterDecision(route="direct", confidence=0.8, reason="heuristic_short_greeting")`
   - New metric: `router_path_total{path="heuristic_direct"}`

2. **9.2 DeepSeek function_calling auto**:
   - Replace hard skip with `tool_choice="auto"` for DeepSeek
   - Add `OPS_ROUTER_SKIP_FOR_DEEPSEEK` env flag for back-compat

3. **9.3 Timeout reduction**:
   - Change `DEFAULT_TIMEOUT_MS` from 2000 to 800
   - Add `OPS_ROUTER_TIMEOUT_MS` env override

4. **9.4 Tier 3 fallback adjustment**:
   - Non-ops timeout → `route="direct"` (not "executor")
   - Ops keyword timeout → `route="executor"` with empty tools
   - Never default to full_agent on timeout

## Related Requirements

- Requirements 1.7, 1.8, 1.9, 1.10 (Bug Condition)
- Requirements 2.9, 2.10, 2.11, 2.12, 2.13 (Expected Behavior)
- Requirements 3.8, 3.9, 3.10, 3.11 (Preservation)

## Test Files

- Bug condition test: `server/tests/property_tests/test_router_llm_deepseek_short_greeting.py`
- Preservation test: `server/tests/property_tests/test_router_llm_preservation.py` (Task 8)
