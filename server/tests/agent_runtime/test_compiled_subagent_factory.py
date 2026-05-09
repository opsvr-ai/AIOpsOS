"""Unit tests for :func:`build_dynamic_subagent`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 19.2,
requirement R-3.21 (subagent uses late-bound system prompt via
:class:`CompiledSubAgent` + :class:`DynamicSystemPromptMiddleware`).

These tests assert the *shape* of the middleware stack the factory
produces rather than running a real LangGraph. We monkeypatch
:func:`langchain.agents.create_agent` to capture the kwargs the
factory hands it, and swap the heavy middleware constructors for
lightweight stubs that remember their init arguments. That lets us
verify the key properties of R-3.21 in isolation:

* :class:`DynamicSystemPromptMiddleware` is always at **position 0**
  of the middleware list (prerequisite for R-3.22 / R-3.25).
* ``system_prompt=_SENTINEL_PROMPT`` is passed to ``create_agent`` so
  :class:`DynamicSystemPromptMiddleware` has a stable anchor to
  replace.
* The returned :class:`CompiledSubAgent` exposes ``name`` /
  ``description`` / ``runnable`` — making it a drop-in replacement for
  a declarative ``SubAgent`` in
  ``deepagents.create_deep_agent(subagents=...)``.
* Optional ``skills`` and ``extra_middleware`` are included when
  supplied and absent otherwise, in the documented positions.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agent.runtime import compiled_subagent_factory as factory_mod
from src.agent.runtime.compiled_subagent_factory import build_dynamic_subagent
from src.agent.runtime.dynamic_prompt_middleware import (
    DynamicSystemPromptMiddleware,
    _SENTINEL_PROMPT,
)


# ---------------------------------------------------------------------------
# Stub middleware + create_agent captor
# ---------------------------------------------------------------------------


class _StubMiddleware:
    """Stand-in for any middleware the factory constructs.

    Stores the init kwargs on the instance so the test can assert
    which concrete middleware class was swapped in. Subclasses add a
    distinguishing ``kind`` marker.
    """

    kind = "generic"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs


class _FakeFilesystem(_StubMiddleware):
    kind = "filesystem"


class _FakeSkills(_StubMiddleware):
    kind = "skills"


class _FakeSummarization(_StubMiddleware):
    kind = "summarization"


class _FakePatchToolCalls(_StubMiddleware):
    kind = "patch_tool_calls"


class _FakeTodoList(_StubMiddleware):
    kind = "todo_list"


class _FakeAnthropicCache(_StubMiddleware):
    kind = "anthropic_cache"


def _fake_summarization_factory(model: Any, backend: Any) -> _FakeSummarization:
    """Mirror :func:`create_summarization_middleware` surface."""
    return _FakeSummarization(model=model, backend=backend)


class _Sentinel:
    """Unique sentinel for captured fields to catch None confusion."""


_UNSET = _Sentinel()


@pytest.fixture
def patched_middleware(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the heavy middleware constructors with capture stubs.

    Returns a dict holding the last-captured ``create_agent`` call so
    individual tests can inspect middleware list and kwargs.
    """
    monkeypatch.setattr(factory_mod, "FilesystemMiddleware", _FakeFilesystem)
    monkeypatch.setattr(factory_mod, "SkillsMiddleware", _FakeSkills)
    monkeypatch.setattr(
        factory_mod,
        "create_summarization_middleware",
        _fake_summarization_factory,
    )
    monkeypatch.setattr(
        factory_mod, "PatchToolCallsMiddleware", _FakePatchToolCalls
    )
    monkeypatch.setattr(factory_mod, "TodoListMiddleware", _FakeTodoList)
    monkeypatch.setattr(
        factory_mod,
        "AnthropicPromptCachingMiddleware",
        _FakeAnthropicCache,
    )

    captured: dict[str, Any] = {}

    def fake_create_agent(model: Any, **kwargs: Any) -> Any:
        captured["model"] = model
        captured["kwargs"] = kwargs
        runnable = object()
        captured["runnable"] = runnable
        return runnable

    monkeypatch.setattr(factory_mod, "create_agent", fake_create_agent)
    return captured


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_returns_compiled_subagent_with_expected_keys(
    patched_middleware: dict[str, Any],
) -> None:
    """Factory output must be a :class:`CompiledSubAgent` drop-in."""
    sub = build_dynamic_subagent(
        name="knowledge",
        description="knowledge base helper",
        model="model-obj",
        tools=[],
        registry=object(),
        backend=object(),
    )

    assert sub["name"] == "knowledge"
    assert sub["description"] == "knowledge base helper"
    # ``runnable`` must be exactly what ``create_agent`` returned.
    assert sub["runnable"] is patched_middleware["runnable"]


def test_create_agent_receives_sentinel_prompt(
    patched_middleware: dict[str, Any],
) -> None:
    """R-3.21 / R-3.22 anchor — sentinel is the compile-time prompt."""
    build_dynamic_subagent(
        name="ops",
        description="ops",
        model="model-obj",
        tools=[],
        registry=object(),
        backend=object(),
    )

    assert patched_middleware["model"] == "model-obj"
    assert (
        patched_middleware["kwargs"]["system_prompt"] == _SENTINEL_PROMPT
    )
    assert patched_middleware["kwargs"]["name"] == "ops"


# ---------------------------------------------------------------------------
# Middleware stack composition
# ---------------------------------------------------------------------------


def test_dynamic_prompt_middleware_is_at_position_zero(
    patched_middleware: dict[str, Any],
) -> None:
    """R-3.22 — :class:`DynamicSystemPromptMiddleware` must lead the stack."""
    registry = object()
    build_dynamic_subagent(
        name="ops",
        description="d",
        model="m",
        tools=[],
        registry=registry,
        backend=object(),
    )

    middleware = patched_middleware["kwargs"]["middleware"]
    assert isinstance(middleware[0], DynamicSystemPromptMiddleware)
    assert middleware[0]._name == "ops"
    assert middleware[0]._registry is registry


def test_middleware_stack_baseline_order_without_optionals(
    patched_middleware: dict[str, Any],
) -> None:
    """Default stack: dynamic-prompt, todo, filesystem, summarization,
    patch-tool-calls, anthropic-cache. No skills / extra middleware."""
    build_dynamic_subagent(
        name="ops",
        description="d",
        model="m",
        tools=[],
        registry=object(),
        backend="backend-obj",
    )

    middleware = patched_middleware["kwargs"]["middleware"]
    kinds = [getattr(m, "kind", type(m).__name__) for m in middleware]
    assert kinds == [
        "DynamicSystemPromptMiddleware",
        "todo_list",
        "filesystem",
        "summarization",
        "patch_tool_calls",
        "anthropic_cache",
    ]

    # Filesystem and summarization both take the backend we passed.
    fs = middleware[2]
    summ = middleware[3]
    assert fs.kwargs["backend"] == "backend-obj"
    assert summ.kwargs == {"model": "m", "backend": "backend-obj"}


def test_skills_middleware_inserted_when_sources_provided(
    patched_middleware: dict[str, Any],
) -> None:
    """Skills middleware lands between patch-tool-calls and anthropic cache."""
    build_dynamic_subagent(
        name="knowledge",
        description="d",
        model="m",
        tools=[],
        registry=object(),
        backend="backend-obj",
        skills=["data/skills"],
    )

    middleware = patched_middleware["kwargs"]["middleware"]
    kinds = [getattr(m, "kind", type(m).__name__) for m in middleware]
    assert kinds == [
        "DynamicSystemPromptMiddleware",
        "todo_list",
        "filesystem",
        "summarization",
        "patch_tool_calls",
        "skills",
        "anthropic_cache",
    ]
    skills_mw = middleware[5]
    assert skills_mw.kwargs == {
        "backend": "backend-obj",
        "sources": ["data/skills"],
    }


def test_skills_omitted_when_none_or_empty(
    patched_middleware: dict[str, Any],
) -> None:
    """``skills=None`` and ``skills=[]`` both skip :class:`SkillsMiddleware`."""
    for skills in (None, []):
        patched_middleware.clear()
        build_dynamic_subagent(
            name="ops",
            description="d",
            model="m",
            tools=[],
            registry=object(),
            backend=object(),
            skills=skills,
        )
        kinds = [
            getattr(m, "kind", type(m).__name__)
            for m in patched_middleware["kwargs"]["middleware"]
        ]
        assert "skills" not in kinds, f"unexpected skills middleware for {skills!r}"


def test_extra_middleware_appended_before_anthropic_cache(
    patched_middleware: dict[str, Any],
) -> None:
    """Caller-supplied middleware lands between skills and Anthropic cache."""
    probe_a = _StubMiddleware()
    probe_b = _StubMiddleware()

    build_dynamic_subagent(
        name="ops",
        description="d",
        model="m",
        tools=[],
        registry=object(),
        backend=object(),
        skills=["data/skills"],
        extra_middleware=[probe_a, probe_b],
    )

    middleware = patched_middleware["kwargs"]["middleware"]
    # Skills is position 5, extras 6 & 7, anthropic cache last.
    assert middleware[5].kind == "skills"
    assert middleware[6] is probe_a
    assert middleware[7] is probe_b
    assert middleware[-1].kind == "anthropic_cache"


def test_extra_middleware_without_skills(
    patched_middleware: dict[str, Any],
) -> None:
    """Extras run after PatchToolCalls when no skills are requested."""
    probe = _StubMiddleware()
    build_dynamic_subagent(
        name="ops",
        description="d",
        model="m",
        tools=[],
        registry=object(),
        backend=object(),
        extra_middleware=[probe],
    )

    middleware = patched_middleware["kwargs"]["middleware"]
    kinds = [getattr(m, "kind", type(m).__name__) for m in middleware]
    assert kinds == [
        "DynamicSystemPromptMiddleware",
        "todo_list",
        "filesystem",
        "summarization",
        "patch_tool_calls",
        "generic",  # extras
        "anthropic_cache",
    ]
    assert middleware[5] is probe


# ---------------------------------------------------------------------------
# create_agent kwargs passthrough
# ---------------------------------------------------------------------------


def test_tools_passed_through_to_create_agent(
    patched_middleware: dict[str, Any],
) -> None:
    """Sub-agent tools reach ``create_agent`` verbatim."""
    tool_a = object()
    tool_b = object()
    build_dynamic_subagent(
        name="ops",
        description="d",
        model="m",
        tools=[tool_a, tool_b],
        registry=object(),
        backend=object(),
    )

    assert patched_middleware["kwargs"]["tools"] == [tool_a, tool_b]


def test_anthropic_cache_configured_for_non_anthropic_models(
    patched_middleware: dict[str, Any],
) -> None:
    """The Anthropic cache middleware is added with ``ignore`` so
    non-Anthropic models see a no-op instead of a warning.
    """
    build_dynamic_subagent(
        name="ops",
        description="d",
        model="m",
        tools=[],
        registry=object(),
        backend=object(),
    )

    cache_mw = patched_middleware["kwargs"]["middleware"][-1]
    assert cache_mw.kind == "anthropic_cache"
    assert cache_mw.kwargs == {"unsupported_model_behavior": "ignore"}


# ---------------------------------------------------------------------------
# Guards on ordering invariants callers cannot override
# ---------------------------------------------------------------------------


def test_extra_middleware_cannot_preempt_dynamic_prompt(
    patched_middleware: dict[str, Any],
) -> None:
    """Even if a caller tries to inject a middleware meant to run
    first, :class:`DynamicSystemPromptMiddleware` stays at index 0
    — otherwise outer middleware could write over the sentinel before
    we get a chance to replace it (breaks R-3.22).
    """
    probe = _StubMiddleware()
    build_dynamic_subagent(
        name="ops",
        description="d",
        model="m",
        tools=[],
        registry=object(),
        backend=object(),
        extra_middleware=[probe],
    )
    first = patched_middleware["kwargs"]["middleware"][0]
    assert isinstance(first, DynamicSystemPromptMiddleware)


def test_registry_is_bound_to_middleware_instance(
    patched_middleware: dict[str, Any],
) -> None:
    """The registry we pass is wired to the installed middleware
    (not copied, not wrapped) — so future prompt promotions
    propagate without rebuilding the runnable (R-3.23)."""
    registry = object()
    build_dynamic_subagent(
        name="ops",
        description="d",
        model="m",
        tools=[],
        registry=registry,
        backend=object(),
    )
    assert (
        patched_middleware["kwargs"]["middleware"][0]._registry is registry
    )
