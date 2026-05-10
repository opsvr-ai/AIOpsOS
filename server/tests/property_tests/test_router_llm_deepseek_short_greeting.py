"""Bug Condition Exploration Test for B3: DeepSeek + Short Greeting Timeout.

**Property 5: Bug Condition** - DeepSeek + short greetings inevitably fall into
json_mode timeout (2s+) and then route to full_agent via fallback_executor.

**Validates: Requirements 1.7, 1.8, 1.9, 1.10**

This test was designed to FAIL on UNFIXED code (proving the bug exists) and
PASS on FIXED code (proving the bug is resolved).

Bug Description:
- In `RouterLLM._classify_via_function_calling()`, DeepSeek models are detected
  and immediately return None (skip Tier 1 entirely).
- This forces all DeepSeek requests to Tier 2 (json_mode), which has a 2000ms
  timeout (`DEFAULT_TIMEOUT_MS = 2000`).
- For short greetings like "你好", "嗯", "ok", the router should respond quickly
  with `route="direct"`, but instead it waits 2s+ and falls back to
  `fallback_executor("timeout")` with `route="executor"` and `confidence=0.0`.
- The gateway then routes to `full_agent` (full toolset assembly), which is
  wasteful for simple greetings.

Expected Fix (Task 9):
- Add heuristic short-circuit: messages ≤ 6 chars without ops keywords → direct
- Let DeepSeek use function_calling with `tool_choice="auto"` instead of skipping
- Reduce timeout from 2000ms to 800ms
- On timeout, route to direct (not executor) for non-ops messages

Test Approach:
- Mock `_is_deepseek_model` to return True
- Mock `_classify_via_json_mode` to sleep 2.1s (simulate timeout)
- Clear router cache to ensure cache miss
- Set `OPS_ROUTER_HEURISTIC_MAX_LEN=6` to lock threshold semantics
- Assert: latency ≤ 250ms (UNFIXED will be ≥ 2000ms)
- Assert: route == "direct" and confidence ≥ 0.7 (UNFIXED will be executor/0.0)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from src.services.agent_runtime.router import RouterLLM, DEFAULT_TIMEOUT_MS
from src.services.agent_runtime.router_schema import RouterDecision


# ---------------------------------------------------------------------------
# Strategies for generating test inputs
# ---------------------------------------------------------------------------

# Short greeting messages that should NOT trigger ops keyword promotion
# All have length ≤ 6 (matching OPS_ROUTER_HEURISTIC_MAX_LEN default)
SHORT_GREETINGS = st.sampled_from(["你好", "嗯", "ok", "hi", "早", "thanks", "谢谢"])


# ---------------------------------------------------------------------------
# Helper: Create a mock LLM that simulates DeepSeek behavior
# ---------------------------------------------------------------------------

def create_deepseek_mock_llm() -> MagicMock:
    """Create a mock LLM that looks like a DeepSeek model."""
    mock_llm = MagicMock()
    mock_llm.openai_api_base = "https://api.deepseek.com/v1"
    mock_llm.model_name = "deepseek-chat"
    return mock_llm


# ---------------------------------------------------------------------------
# Bug Condition Exploration Tests
# ---------------------------------------------------------------------------

class TestB3DeepSeekShortGreetingBugCondition:
    """Exploration tests for B3: DeepSeek + short greeting timeout.
    
    **Validates: Requirements 1.7, 1.8, 1.9, 1.10**
    
    These tests verify that the bug exists in UNFIXED code:
    - DeepSeek + short greetings take ≥ 2000ms (json_mode timeout)
    - Result is fallback_executor with route="executor", confidence=0.0
    
    On FIXED code, these tests should PASS:
    - Heuristic short-circuit returns in ≤ 250ms
    - Result is route="direct", confidence ≥ 0.7
    """

    @pytest.mark.asyncio
    async def test_deepseek_short_greeting_latency_and_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Property 5: DeepSeek + short greeting should be fast and route direct.
        
        **Validates: Requirements 1.7, 1.8**
        
        Bug Condition:
        - provider = DEEPSEEK
        - message = short greeting (≤ 6 chars, no ops keywords)
        - router cache miss
        
        EXPECTED OUTCOME on UNFIXED code:
        - FAIL: latency ≥ 2000ms (json_mode timeout)
        - FAIL: route = "executor" (fallback_executor)
        - FAIL: confidence = 0.0 (fallback)
        
        EXPECTED OUTCOME on FIXED code:
        - PASS: latency ≤ 250ms (heuristic short-circuit)
        - PASS: route = "direct"
        - PASS: confidence ≥ 0.7
        """
        # Lock the heuristic threshold to 6 (default)
        monkeypatch.setenv("OPS_ROUTER_HEURISTIC_MAX_LEN", "6")
        
        # Create mock LLM that looks like DeepSeek
        mock_llm = create_deepseek_mock_llm()
        
        # Create router with no cache (force cache miss)
        router = RouterLLM(
            llm=mock_llm,
            redis_client=None,  # No cache
            timeout_ms=DEFAULT_TIMEOUT_MS,  # Use default 2000ms
        )
        
        # Patch _is_deepseek_model to return True
        with patch(
            "src.services.agent_runtime.router._is_deepseek_model",
            return_value=True
        ):
            # Patch _classify_via_json_mode to simulate timeout (sleep 2.1s)
            async def slow_json_mode(*args: Any, **kwargs: Any) -> None:
                await asyncio.sleep(2.1)  # Exceed 2000ms timeout
                return None
            
            with patch.object(
                router,
                "_classify_via_json_mode",
                side_effect=slow_json_mode
            ):
                # Test with a short greeting
                message = "你好"
                
                t0 = time.perf_counter()
                decision = await router.classify(
                    message,
                    user_id="test_user",
                    last_assistant_sha="",
                )
                latency_ms = (time.perf_counter() - t0) * 1000.0
        
        # Assertions that will FAIL on UNFIXED code
        # 
        # On UNFIXED code:
        # - latency will be ≥ 2000ms (json_mode timeout)
        # - route will be "executor" (fallback_executor)
        # - confidence will be 0.0 (fallback)
        #
        # On FIXED code (with heuristic):
        # - latency will be ≤ 250ms (heuristic short-circuit, no LLM call)
        # - route will be "direct"
        # - confidence will be ≥ 0.7
        
        assert latency_ms <= 250.0, (
            f"Expected latency ≤ 250ms (heuristic short-circuit), "
            f"but got {latency_ms:.1f}ms. "
            f"This indicates the heuristic is not working and json_mode timed out."
        )
        
        assert decision.route == "direct", (
            f"Expected route='direct' for short greeting, "
            f"but got route='{decision.route}'. "
            f"This indicates fallback_executor was triggered instead of heuristic."
        )
        
        assert decision.confidence >= 0.7, (
            f"Expected confidence ≥ 0.7 for heuristic decision, "
            f"but got confidence={decision.confidence}. "
            f"This indicates fallback_executor (confidence=0.0) was used."
        )

    @pytest.mark.asyncio
    @settings(
        max_examples=20,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(message=SHORT_GREETINGS)
    async def test_any_short_greeting_fast_direct_route(
        self, message: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Property 5: Any short greeting should be fast and route direct.
        
        **Validates: Requirements 1.7, 1.8, 1.9**
        
        For any message from the short greeting set (length ≤ 6, no ops keywords),
        the router should:
        - Return in ≤ 250ms (heuristic, no LLM call)
        - Return route="direct"
        - Return confidence ≥ 0.7
        
        EXPECTED OUTCOME on UNFIXED code: FAIL
        EXPECTED OUTCOME on FIXED code: PASS
        """
        # Lock the heuristic threshold
        monkeypatch.setenv("OPS_ROUTER_HEURISTIC_MAX_LEN", "6")
        
        # Verify message is within threshold
        assert len(message.strip()) <= 6, f"Test message '{message}' exceeds threshold"
        
        # Create mock LLM that looks like DeepSeek
        mock_llm = create_deepseek_mock_llm()
        
        # Create router with no cache
        router = RouterLLM(
            llm=mock_llm,
            redis_client=None,
            timeout_ms=DEFAULT_TIMEOUT_MS,
        )
        
        with patch(
            "src.services.agent_runtime.router._is_deepseek_model",
            return_value=True
        ):
            async def slow_json_mode(*args: Any, **kwargs: Any) -> None:
                await asyncio.sleep(2.1)
                return None
            
            with patch.object(
                router,
                "_classify_via_json_mode",
                side_effect=slow_json_mode
            ):
                t0 = time.perf_counter()
                decision = await router.classify(
                    message,
                    user_id="test_user",
                    last_assistant_sha="",
                )
                latency_ms = (time.perf_counter() - t0) * 1000.0
        
        # Assertions
        assert latency_ms <= 250.0, (
            f"Message '{message}': Expected latency ≤ 250ms, got {latency_ms:.1f}ms"
        )
        assert decision.route == "direct", (
            f"Message '{message}': Expected route='direct', got '{decision.route}'"
        )
        assert decision.confidence >= 0.7, (
            f"Message '{message}': Expected confidence ≥ 0.7, got {decision.confidence}"
        )


class TestB3DeepSeekTimeoutFallback:
    """Tests for the timeout fallback behavior.
    
    **Validates: Requirements 1.9, 1.10**
    
    When the router LLM times out, the fallback should NOT default to full_agent.
    """

    @pytest.mark.asyncio
    async def test_timeout_fallback_not_full_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Property 5 (second part): Timeout should not fall back to full_agent.
        
        **Validates: Requirements 1.9, 1.10**
        
        When router LLM times out on a non-ops message, the fallback should be:
        - route="direct" (not "executor")
        - confidence > 0 (not 0.0 from fallback_executor)
        
        On UNFIXED code:
        - FAIL: route="executor" (fallback_executor)
        - FAIL: confidence=0.0
        
        On FIXED code:
        - PASS: route="direct" (timeout_non_ops fallback)
        - PASS: confidence=0.3 (degraded but not zero)
        """
        # Disable heuristic to test the timeout fallback path
        monkeypatch.setenv("OPS_ROUTER_HEURISTIC_MAX_LEN", "0")
        
        mock_llm = create_deepseek_mock_llm()
        
        router = RouterLLM(
            llm=mock_llm,
            redis_client=None,
            timeout_ms=100,  # Very short timeout to trigger fallback quickly
        )
        
        with patch(
            "src.services.agent_runtime.router._is_deepseek_model",
            return_value=True
        ):
            # Both tiers will timeout
            async def slow_tier(*args: Any, **kwargs: Any) -> None:
                await asyncio.sleep(0.5)  # Exceed 100ms timeout
                return None
            
            with patch.object(router, "_classify_via_function_calling", side_effect=slow_tier):
                with patch.object(router, "_classify_via_json_mode", side_effect=slow_tier):
                    # Use a longer message that won't trigger heuristic
                    # but also doesn't contain ops keywords
                    message = "今天天气怎么样"  # Weather question, not ops
                    
                    decision = await router.classify(
                        message,
                        user_id="test_user",
                        last_assistant_sha="",
                    )
        
        # On UNFIXED code, this will be fallback_executor with route="executor"
        # On FIXED code, this should be route="direct" for non-ops timeout
        #
        # Note: The current UNFIXED behavior is:
        # - fallback_executor("timeout") → route="executor", confidence=0.0
        # - Then _clamp_confidence keeps it as executor (since 0.0 < 0.4)
        #
        # The FIXED behavior should be:
        # - timeout_non_ops → route="direct", confidence=0.3
        
        # This assertion will FAIL on UNFIXED code
        assert decision.route == "direct", (
            f"Expected route='direct' for non-ops timeout fallback, "
            f"but got route='{decision.route}'. "
            f"This indicates fallback_executor is still defaulting to executor."
        )


class TestB3CurrentBehaviorDocumentation:
    """Document the current (UNFIXED) behavior for counterexample recording.
    
    These tests are designed to PASS on UNFIXED code to capture the buggy behavior.
    They serve as documentation of what the bug looks like.
    """

    @pytest.mark.asyncio
    async def test_document_unfixed_behavior(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Document the UNFIXED behavior for counterexample.
        
        This test captures what happens in UNFIXED code:
        - DeepSeek skips Tier 1 (function_calling)
        - Tier 2 (json_mode) times out after 2000ms
        - Falls back to fallback_executor("timeout")
        - Returns route="executor", confidence=0.0
        
        This test should PASS on UNFIXED code (documenting the bug).
        After the fix, this test's assertions would need to be updated.
        """
        # Lock the heuristic threshold (even though it doesn't exist in UNFIXED)
        monkeypatch.setenv("OPS_ROUTER_HEURISTIC_MAX_LEN", "6")
        
        mock_llm = create_deepseek_mock_llm()
        
        router = RouterLLM(
            llm=mock_llm,
            redis_client=None,
            timeout_ms=DEFAULT_TIMEOUT_MS,
        )
        
        with patch(
            "src.services.agent_runtime.router._is_deepseek_model",
            return_value=True
        ):
            async def slow_json_mode(*args: Any, **kwargs: Any) -> None:
                await asyncio.sleep(2.1)
                return None
            
            with patch.object(
                router,
                "_classify_via_json_mode",
                side_effect=slow_json_mode
            ):
                message = "你好"
                
                t0 = time.perf_counter()
                decision = await router.classify(
                    message,
                    user_id="test_user",
                    last_assistant_sha="",
                )
                latency_ms = (time.perf_counter() - t0) * 1000.0
        
        # Document the UNFIXED behavior (these assertions match buggy behavior)
        # 
        # In UNFIXED code:
        # - Tier 1 returns None immediately (DeepSeek skip)
        # - Tier 2 times out after 2000ms
        # - fallback_executor("timeout") is returned
        # - route="executor", confidence=0.0
        #
        # Record these values for the counterexample document
        print(f"\n=== B3 UNFIXED Behavior Documentation ===")
        print(f"Message: '{message}'")
        print(f"Latency: {latency_ms:.1f}ms")
        print(f"Route: {decision.route}")
        print(f"Confidence: {decision.confidence}")
        print(f"Reason: {decision.reason}")
        print(f"Suggested Tools: {decision.suggested_tools}")
        print(f"==========================================\n")
        
        # These assertions document the UNFIXED behavior
        # They will PASS on UNFIXED code
        # After the fix, the main test assertions should PASS instead
        
        # Note: We don't assert here because this is just documentation
        # The main tests above have the real assertions that will FAIL on UNFIXED


# ---------------------------------------------------------------------------
# Counterexample Recording Helper
# ---------------------------------------------------------------------------

class TestB3CounterexampleRecording:
    """Helper to record counterexample for documentation."""

    @pytest.mark.asyncio
    async def test_record_counterexample(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record counterexample data for b3_router_deepseek_fullagent.md.
        
        This test runs the buggy scenario and prints the counterexample data
        that should be recorded in the counterexample document.
        """
        monkeypatch.setenv("OPS_ROUTER_HEURISTIC_MAX_LEN", "6")
        
        mock_llm = create_deepseek_mock_llm()
        
        router = RouterLLM(
            llm=mock_llm,
            redis_client=None,
            timeout_ms=DEFAULT_TIMEOUT_MS,
        )
        
        results = []
        
        with patch(
            "src.services.agent_runtime.router._is_deepseek_model",
            return_value=True
        ):
            async def slow_json_mode(*args: Any, **kwargs: Any) -> None:
                await asyncio.sleep(2.1)
                return None
            
            with patch.object(
                router,
                "_classify_via_json_mode",
                side_effect=slow_json_mode
            ):
                for message in ["你好", "嗯", "ok", "hi", "早", "thanks", "谢谢"]:
                    t0 = time.perf_counter()
                    decision = await router.classify(
                        message,
                        user_id="test_user",
                        last_assistant_sha="",
                    )
                    latency_ms = (time.perf_counter() - t0) * 1000.0
                    
                    results.append({
                        "message": message,
                        "latency_ms": latency_ms,
                        "route": decision.route,
                        "confidence": decision.confidence,
                        "reason": decision.reason,
                    })
        
        print("\n=== B3 Counterexample Data ===")
        for r in results:
            print(f"Message: '{r['message']}' | "
                  f"Latency: {r['latency_ms']:.0f}ms | "
                  f"Route: {r['route']} | "
                  f"Confidence: {r['confidence']} | "
                  f"Reason: {r['reason']}")
        print("==============================\n")
        
        # This test always passes - it's just for recording data
        assert True

