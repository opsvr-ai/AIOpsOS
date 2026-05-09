"""Unit tests for ``deep_agent._build_subagents`` and the registry dicts.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 19.3,
requirements R-3.20 (cold-start defaults), R-3.21 (late-bound prompts
via :class:`CompiledSubAgent` + :class:`DynamicSystemPromptMiddleware`).

These tests assert the *contract* between ``deep_agent.py`` and the
prompt registry: that ``_DEFAULT_SUBAGENT_PROMPTS`` carries one entry
per known sub-agent (the cold-start safety net), that ``_build_subagents``
emits one :class:`CompiledSubAgent` per entry, and that each emission
is produced through :func:`build_dynamic_subagent` — which in turn
guarantees :class:`DynamicSystemPromptMiddleware` at position 0.

We *don't* run a real LangGraph here. The factory is monkey-patched to
a lightweight capturing stub so tests finish in milliseconds and don't
depend on any model provider config.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.agent import deep_agent as _da
from src.agent.runtime import compiled_subagent_factory as _factory_mod


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_default_subagent_prompts_covers_all_known_names() -> None:
    """Every known sub-agent must have a default prompt string.

    Cold-start fallback (R-3.20) only works if the registry seed is
    complete — if a new sub-agent name is added to
    ``_SUBAGENT_DESCRIPTIONS`` but forgotten here, the registry would
    serve an empty system_prompt on first boot.
    """
    desc_names = set(_da._SUBAGENT_DESCRIPTIONS.keys())
    default_names = set(_da._DEFAULT_SUBAGENT_PROMPTS.keys())
    assert default_names == desc_names, (
        f"mismatch between descriptions and defaults: "
        f"only_in_defaults={default_names - desc_names}, "
        f"only_in_descriptions={desc_names - default_names}"
    )


def test_default_subagent_prompts_values_are_non_empty() -> None:
    """Cold-start defaults must carry real prompt text, not placeholders."""
    for name, prompt in _da._DEFAULT_SUBAGENT_PROMPTS.items():
        assert isinstance(prompt, str), f"{name} default is not a str"
        assert prompt.strip(), f"{name} default is empty"


def test_default_subagent_prompts_matches_module_constants() -> None:
    """Each default is the canonical module-level constant.

    Keeps ``SubAgentPromptRegistry(defaults=_DEFAULT_SUBAGENT_PROMPTS)``
    pointing at the live text — no drift between the dict and the
    ``*_SYSTEM_PROMPT`` constants the module advertises elsewhere.
    """
    expected = {
        "knowledge": _da.KNOWLEDGE_SYSTEM_PROMPT,
        "monitor": _da.MONITOR_SYSTEM_PROMPT,
        "ops": _da.OPS_SYSTEM_PROMPT,
        "analysis": _da.ANALYSIS_SYSTEM_PROMPT,
        "memory": _da.MEMORY_SYSTEM_PROMPT,
        "cmdb_ingestion": _da.CMDB_SYSTEM_PROMPT,
        "a2ui_generator": _da.A2UI_GENERATOR_SYSTEM_PROMPT,
        "report_generator": _da.REPORT_GENERATOR_SYSTEM_PROMPT,
    }
    assert _da._DEFAULT_SUBAGENT_PROMPTS == expected


def test_back_compat_subagents_list_carries_hardcoded_prompts() -> None:
    """``SUBAGENTS`` must still be consumable by main.py / executor_pool.

    The legacy static list is rebuilt from ``_DEFAULT_SUBAGENT_PROMPTS`` so
    downstream seeders keep working. Each entry must carry ``name``,
    ``description``, and the default ``system_prompt``.
    """
    assert len(_da.SUBAGENTS) == len(_da._DEFAULT_SUBAGENT_PROMPTS)
    by_name = {sa["name"]: sa for sa in _da.SUBAGENTS}
    assert set(by_name) == set(_da._DEFAULT_SUBAGENT_PROMPTS)
    for name, sa in by_name.items():
        assert sa["system_prompt"] == _da._DEFAULT_SUBAGENT_PROMPTS[name]
        assert sa["description"] == _da._SUBAGENT_DESCRIPTIONS[name]

    # ``knowledge`` is the only sub-agent with skill sources today.
    assert by_name["knowledge"].get("skills") == ["data/skills"]
    # ``memory`` / ``knowledge`` / ``report_generator`` advertise tool lists.
    assert by_name["knowledge"].get("tools")
    assert by_name["memory"].get("tools")
    assert by_name["report_generator"].get("tools")


# ---------------------------------------------------------------------------
# _build_subagents
# ---------------------------------------------------------------------------


class _Captured:
    """Container for ``build_dynamic_subagent`` invocations."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []


@pytest.fixture
def patched_factory(monkeypatch: pytest.MonkeyPatch) -> _Captured:
    """Replace :func:`build_dynamic_subagent` with a capturing stub.

    ``_build_subagents`` imports the factory lazily, so we patch the
    module attribute rather than a reference at ``_build_subagents``
    call-site. That mirrors the production import path precisely.
    """
    captured = _Captured()

    def _fake(**kwargs: Any) -> dict[str, Any]:
        captured.calls.append(dict(kwargs))
        return {
            "name": kwargs["name"],
            "description": kwargs["description"],
            "runnable": object(),
        }

    monkeypatch.setattr(_factory_mod, "build_dynamic_subagent", _fake)
    return captured


class _FakeRegistry:
    """Placeholder registry — we only pass-through, never call methods."""


@pytest.mark.asyncio
async def test_build_subagents_emits_one_per_default(
    patched_factory: _Captured,
) -> None:
    """``_build_subagents`` produces one entry per default key, in order."""
    registry = _FakeRegistry()
    backend = object()
    model = object()

    compiled = await _da._build_subagents(
        model=model, backend=backend, registry=registry,
    )

    assert len(compiled) == len(_da._DEFAULT_SUBAGENT_PROMPTS)
    assert [sa["name"] for sa in compiled] == list(
        _da._DEFAULT_SUBAGENT_PROMPTS.keys()
    )
    for call in patched_factory.calls:
        assert call["model"] is model
        assert call["backend"] is backend
        assert call["registry"] is registry


@pytest.mark.asyncio
async def test_build_subagents_passes_descriptions_and_tools(
    patched_factory: _Captured,
) -> None:
    """Descriptions and the default tools_map land on the factory call."""
    await _da._build_subagents(
        model=object(), backend=object(), registry=_FakeRegistry(),
    )

    by_name = {c["name"]: c for c in patched_factory.calls}
    assert by_name["knowledge"]["description"] == (
        _da._SUBAGENT_DESCRIPTIONS["knowledge"]
    )
    # knowledge + memory + report_generator carry tools
    assert by_name["knowledge"]["tools"] == list(_da.KNOWLEDGE_TOOLS)
    assert by_name["memory"]["tools"] == list(_da.MEMORY_TOOLS)
    assert by_name["report_generator"]["tools"] == [_da.save_report_tool]
    # Sub-agents without extra tools get an empty list (not None).
    assert by_name["ops"]["tools"] == []
    assert by_name["monitor"]["tools"] == []


@pytest.mark.asyncio
async def test_build_subagents_sets_skills_only_for_knowledge(
    patched_factory: _Captured,
) -> None:
    """Only ``knowledge`` loads llm-wiki skills today."""
    await _da._build_subagents(
        model=object(), backend=object(), registry=_FakeRegistry(),
    )

    by_name = {c["name"]: c for c in patched_factory.calls}
    assert by_name["knowledge"]["skills"] == ["data/skills"]
    for name, call in by_name.items():
        if name == "knowledge":
            continue
        assert call["skills"] is None, f"unexpected skills for {name}"


@pytest.mark.asyncio
async def test_build_subagents_honors_tools_map_override(
    patched_factory: _Captured,
) -> None:
    """Caller-provided ``tools_map`` wins over the hardcoded defaults.

    ``build_deep_agent_from_db`` merges DB-driven tool lists into the
    map before calling the builder; this test pins that contract.
    """
    custom_tool = object()
    await _da._build_subagents(
        model=object(),
        backend=object(),
        registry=_FakeRegistry(),
        tools_map={"ops": [custom_tool]},
    )

    by_name = {c["name"]: c for c in patched_factory.calls}
    assert by_name["ops"]["tools"] == [custom_tool]
    # Names not in the override still get an empty list (the override
    # wholesale replaces the defaults — callers must include entries
    # for every sub-agent they want tools for).
    assert by_name["knowledge"]["tools"] == []


@pytest.mark.asyncio
async def test_build_subagents_returns_compiled_subagent_shape(
    patched_factory: _Captured,
) -> None:
    """Each returned dict matches :class:`CompiledSubAgent`."""
    compiled = await _da._build_subagents(
        model=object(), backend=object(), registry=_FakeRegistry(),
    )
    for sa in compiled:
        assert set(sa.keys()) >= {"name", "description", "runnable"}
        assert sa["name"] in _da._DEFAULT_SUBAGENT_PROMPTS
