"""Integration tests for :class:`ExecutorAgentPool`'s dispatcher wiring.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 16.3.

Covers the narrow but critical seam between the pool and the
dispatcher wrapper:

* When the ``tool_dispatcher_enabled`` feature flag is on AND the
  pool's constructor-level kill-switch is on, tools passed to
  ``create_deep_agent`` are :class:`_DispatchedTool` proxies.
* When either gate is off, tools pass through unwrapped.
* The wrapper preserves tool ``name``/``description`` so downstream
  LangChain bindings still see the correct signature.

The tests patch ``deepagents.create_deep_agent`` so no real
LangGraph graph construction happens; we only inspect the ``tools``
kwarg it receives.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.services.agent_runtime import executor_pool as exec_pool_mod
from src.services.agent_runtime.executor_pool import (
    ExecutorAgentPool,
    _reset_singleton_for_tests,
)
from src.services.agent_runtime.tool_node_wrapper import _DispatchedTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pool_singleton():
    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()


class _StubTool:
    """Minimal BaseTool stand-in — the wrapper reads ``.name`` + ``.description``."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description or name
        self.args_schema = None


class _CapturingDeepAgent:
    """Fake ``create_deep_agent`` that captures its kwargs."""

    def __init__(self) -> None:
        self.captured: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> Any:
        self.captured = dict(kwargs)
        return "fake-graph"


async def _model_builder() -> str:
    return "fake-model"


def _backend_builder() -> str:
    return "fake-backend"


def _skills_provider() -> list[str] | None:
    return None


def _pool_with_provider(
    *,
    dispatcher_enabled: bool,
    tool_names: list[str],
) -> ExecutorAgentPool:
    return ExecutorAgentPool(
        model_builder=_model_builder,
        backend_builder=_backend_builder,
        skills_provider=_skills_provider,
        tool_provider=lambda _names: [_StubTool(n) for n in tool_names],
        subagents_provider=lambda _sub: None,
        dispatcher_enabled=dispatcher_enabled,
    )


class _FakeFlagService:
    def __init__(self, value: bool) -> None:
        self.value = value
        self.calls: list[tuple[str, Any]] = []

    def is_enabled(self, key: str, user_id: Any = None) -> bool:
        self.calls.append((key, user_id))
        return self.value


def _patch_flags(monkeypatch, value: bool) -> _FakeFlagService:
    svc = _FakeFlagService(value)

    async def _get_feature_flags():
        return svc

    # _flag_enabled in executor_pool imports get_feature_flags late, so
    # we patch the actual module-level symbol.
    monkeypatch.setattr(
        "src.services.feature_flags.get_feature_flags",
        _get_feature_flags,
    )
    return svc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_wraps_tools_when_flag_on(monkeypatch):
    """Flag on + constructor-enable on → tools arrive as ``_DispatchedTool``."""
    _patch_flags(monkeypatch, True)
    pool = _pool_with_provider(dispatcher_enabled=True, tool_names=["grep_kb"])
    capture = _CapturingDeepAgent()

    with patch("deepagents.create_deep_agent", capture):
        graph = await pool.build_for(["grep_kb"], None)

    assert graph == "fake-graph"
    tools = capture.captured.get("tools", [])
    assert len(tools) == 1
    assert isinstance(tools[0], _DispatchedTool)
    assert tools[0].name == "grep_kb"


@pytest.mark.asyncio
async def test_pool_skips_wrapping_when_flag_off(monkeypatch):
    """Flag off → tools pass through unwrapped."""
    _patch_flags(monkeypatch, False)
    pool = _pool_with_provider(dispatcher_enabled=True, tool_names=["grep_kb"])
    capture = _CapturingDeepAgent()

    with patch("deepagents.create_deep_agent", capture):
        await pool.build_for(["grep_kb"], None)

    tools = capture.captured.get("tools", [])
    assert len(tools) == 1
    # Passthrough — not a _DispatchedTool.
    assert not isinstance(tools[0], _DispatchedTool)
    assert tools[0].name == "grep_kb"


@pytest.mark.asyncio
async def test_pool_constructor_killswitch_disables_wrapping(monkeypatch):
    """Even with flag on, ``dispatcher_enabled=False`` bypasses the wrapper."""
    _patch_flags(monkeypatch, True)  # flag ON
    pool = _pool_with_provider(
        dispatcher_enabled=False, tool_names=["grep_kb"]
    )
    capture = _CapturingDeepAgent()

    with patch("deepagents.create_deep_agent", capture):
        await pool.build_for(["grep_kb"], None)

    tools = capture.captured.get("tools", [])
    assert len(tools) == 1
    assert not isinstance(tools[0], _DispatchedTool)


@pytest.mark.asyncio
async def test_pool_flag_service_failure_falls_back_to_unwrapped(monkeypatch):
    """If the flag lookup raises, we fail closed (no wrapping)."""

    async def _blow_up():
        raise RuntimeError("flag svc down")

    monkeypatch.setattr(
        "src.services.feature_flags.get_feature_flags",
        _blow_up,
    )

    pool = _pool_with_provider(
        dispatcher_enabled=True, tool_names=["grep_kb"]
    )
    capture = _CapturingDeepAgent()

    with patch("deepagents.create_deep_agent", capture):
        await pool.build_for(["grep_kb"], None)

    tools = capture.captured.get("tools", [])
    assert len(tools) == 1
    assert not isinstance(tools[0], _DispatchedTool)
