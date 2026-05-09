"""Unit tests for :class:`DynamicSystemPromptMiddleware`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 19.1.
Covers requirements R-3.21, R-3.22, R-3.24, R-3.25.

These tests exercise the middleware in isolation against a tiny fake
registry and hand-built :class:`ModelRequest` instances. No LangGraph
graph is constructed — ``wrap_model_call`` and ``awrap_model_call``
are invoked directly.

What's validated here (the specific PBT properties P-HotReload-6/7/8
have their own dedicated test modules in later tasks 19.4–19.6):

* Sentinel always replaced ⇒ post-swap text doesn't carry the
  sentinel marker.
* Suffix preservation ⇒ text appended after the sentinel by a
  (simulated) outer middleware survives the swap.
* Metadata tagging ⇒ every call publishes
  ``sub_agent_name / prompt_version_id / prompt_version_no /
  prompt_source`` to the :data:`_CURRENT_PROMPT_ATTRIBUTION`
  ContextVar (consumers like TrajectorySink snapshot it inside their
  handlers). The middleware must *not* inject the attribution into
  ``request.model_settings`` — LangChain 1.0 spreads that map directly
  into ``model.bind_tools(...)`` and any unknown kwarg raises a
  ``TypeError`` at the OpenAI SDK boundary.
* Variant pinning ⇒ if a runtime context sets a specific version id,
  that version is used.
* Default fallback ⇒ if the registry has no row for the name we still
  get the configured default (R-3.20 contract flows through).
* Both sync and async code paths produce identical outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.runtime.dynamic_prompt_middleware import (
    DynamicSystemPromptMiddleware,
    _MODEL_SETTINGS_NAMESPACE,
    _SENTINEL_PROMPT,
    get_current_prompt_attribution,
)
from src.services.evolution.prompt_registry import PromptVersion


# ---------------------------------------------------------------------------
# Minimal fakes (no DB, no LLM)
# ---------------------------------------------------------------------------


class _FakeModel:
    """Placeholder for ``ModelRequest.model``.

    :class:`ModelRequest` only uses ``model`` as a payload to pass
    through — nothing in the middleware path calls methods on it.
    Using a bare object keeps the test fast and avoids pulling in
    chat-model dependencies.
    """

    def __repr__(self) -> str:  # pragma: no cover - diag only
        return "<FakeModel>"


@dataclass
class _FakeRuntime:
    """Stands in for ``langgraph.runtime.Runtime[ContextT]``.

    Only the ``context`` attribute is read by the middleware; anything
    else is defensively ignored.
    """

    context: Any = None


class _FakeRegistry:
    """In-memory stand-in for :class:`SubAgentPromptRegistry`.

    Implements just the two methods the middleware calls:
    ``get_active`` and ``get_by_id``. Versions are stored in a plain
    dict keyed by ``sub_agent_name`` (for active lookups) and by ``id``
    (for pinned lookups). Matching the registry's "never return None
    from ``get_active``" contract means we synthesise a default when
    the name is unknown.
    """

    def __init__(self) -> None:
        self._active: dict[str, PromptVersion] = {}
        self._by_id: dict[str, PromptVersion] = {}

    def set_active(self, pv: PromptVersion) -> None:
        self._active[pv.sub_agent_name] = pv
        self._by_id[pv.id] = pv

    def register(self, pv: PromptVersion) -> None:
        """Make ``pv`` discoverable via ``get_by_id`` without setting active."""
        self._by_id[pv.id] = pv

    def get_active(self, sub_agent_name: str) -> PromptVersion:
        pv = self._active.get(sub_agent_name)
        if pv is not None:
            return pv
        # Mirrors ``SubAgentPromptRegistry.get_active`` fallback.
        fallback = PromptVersion(
            id=f"default::{sub_agent_name}",
            sub_agent_name=sub_agent_name,
            status="active",
            system_prompt="",
            version_no=0,
            manifest_sha256="",
            parent_version_id=None,
            activated_at=None,
            source="default",
        )
        return fallback

    def get_by_id(self, version_id: str) -> PromptVersion | None:
        return self._by_id.get(str(version_id))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _pv(
    *,
    name: str,
    text: str,
    version_id: str = "v-active",
    version_no: int = 3,
    source: str = "db",
) -> PromptVersion:
    return PromptVersion(
        id=version_id,
        sub_agent_name=name,
        status="active",
        system_prompt=text,
        version_no=version_no,
        manifest_sha256="",
        parent_version_id=None,
        activated_at=None,
        source=source,  # type: ignore[arg-type]
    )


def _make_request(
    *,
    system_text: str | None = _SENTINEL_PROMPT,
    model_settings: dict[str, Any] | None = None,
    runtime: _FakeRuntime | None = None,
):
    """Build a :class:`ModelRequest` with sensible defaults.

    Defaults mirror what ``create_agent(system_prompt=_SENTINEL_PROMPT)``
    would produce: a ``SystemMessage`` carrying the sentinel, a single
    user message, no tools. Tests override just the fields they care
    about.
    """
    from langchain.agents.middleware.types import ModelRequest

    sys_msg = (
        None if system_text is None else SystemMessage(content=system_text)
    )
    return ModelRequest(
        model=_FakeModel(),  # type: ignore[arg-type]
        messages=[HumanMessage(content="ping")],
        system_message=sys_msg,
        model_settings=dict(model_settings) if model_settings else {},
        runtime=runtime,  # type: ignore[arg-type]
    )


def _run_sync(middleware, request):
    """Invoke ``wrap_model_call`` and capture what the inner handler sees.

    Returns a ``(inner_request, attribution, response)`` tuple. The
    attribution dict is a snapshot of
    :func:`get_current_prompt_attribution` taken inside the handler —
    i.e. at exactly the moment the model would be invoked — so tests
    observe the same value a real consumer (e.g. TrajectorySink) would
    see.
    """
    captured: dict[str, Any] = {}

    def handler(inner_request):
        captured["request"] = inner_request
        captured["attribution"] = get_current_prompt_attribution()
        # Return a minimally-valid response; the middleware doesn't
        # read from it.
        from langchain_core.messages import AIMessage

        from langchain.agents.middleware.types import ModelResponse

        return ModelResponse(result=[AIMessage(content="ok")])

    response = middleware.wrap_model_call(request, handler)
    return captured["request"], captured["attribution"], response


async def _run_async(middleware, request):
    """Async counterpart of :func:`_run_sync`."""
    captured: dict[str, Any] = {}

    async def handler(inner_request):
        captured["request"] = inner_request
        captured["attribution"] = get_current_prompt_attribution()
        from langchain_core.messages import AIMessage

        from langchain.agents.middleware.types import ModelResponse

        return ModelResponse(result=[AIMessage(content="ok")])

    response = await middleware.awrap_model_call(request, handler)
    return captured["request"], captured["attribution"], response


# ---------------------------------------------------------------------------
# Sentinel is always replaced
# ---------------------------------------------------------------------------


def test_sentinel_is_replaced_with_registry_prompt() -> None:
    """R-3.22 / P-HotReload-6: post-swap text must not carry the sentinel."""
    reg = _FakeRegistry()
    reg.set_active(
        _pv(name="ops", text="You are an ops agent.", version_id="v1")
    )
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    inner, _attrib, _ = _run_sync(mw, _make_request(system_text=_SENTINEL_PROMPT))

    text = inner.system_message.text
    assert not str(text).startswith(_SENTINEL_PROMPT)
    assert str(text) == "You are an ops agent."


def test_original_request_is_not_mutated() -> None:
    """`override` returns a new request; the original stays untouched."""
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="fresh", version_id="v1"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    original = _make_request(system_text=_SENTINEL_PROMPT)
    inner, _attrib, _ = _run_sync(mw, original)

    # Original keeps the sentinel; only the inner (new) request has
    # the swapped content. The two objects must be distinct.
    assert original is not inner
    assert str(original.system_message.text) == _SENTINEL_PROMPT
    assert str(inner.system_message.text) == "fresh"


# ---------------------------------------------------------------------------
# Suffix preservation (R-3.25, P-HotReload-7)
# ---------------------------------------------------------------------------


def test_suffix_appended_by_outer_middleware_is_preserved() -> None:
    """R-3.25: ``sentinel + X`` ⇒ ``registry.prompt + X``.

    Simulates an outer middleware (SkillsMiddleware / Summarization)
    having already appended instructions onto the sentinel before we
    get to execute.
    """
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="BASE", version_id="v1"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    suffix = "\n\n[skill: read-file]\n\nFollow the skill contract."
    req = _make_request(system_text=_SENTINEL_PROMPT + suffix)
    inner, _attrib, _ = _run_sync(mw, req)

    assert str(inner.system_message.text) == "BASE" + suffix


def test_empty_suffix_when_sentinel_is_intact() -> None:
    """No outer append ⇒ post-swap text equals the registry prompt exactly."""
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="EXACT", version_id="v1"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    req = _make_request(system_text=_SENTINEL_PROMPT)
    inner, _attrib, _ = _run_sync(mw, req)

    assert str(inner.system_message.text) == "EXACT"


def test_suffix_discarded_when_sentinel_missing_defensive_fallback() -> None:
    """If the outer chain wiped the sentinel, we can't preserve X safely.

    The middleware then replaces wholesale with the registry prompt —
    that's better than propagating an unknown prior prompt.
    """
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="BASE", version_id="v1"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    # Outer middleware replaced the sentinel entirely with its own text.
    req = _make_request(system_text="UNEXPECTED_OLD_PROMPT")
    inner, _attrib, _ = _run_sync(mw, req)

    assert str(inner.system_message.text) == "BASE"


def test_none_system_message_is_handled() -> None:
    """Defensive: ``system_message=None`` shouldn't blow up."""
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="BASE", version_id="v1"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    req = _make_request(system_text=None)
    inner, _attrib, _ = _run_sync(mw, req)

    assert inner.system_message is not None
    assert str(inner.system_message.text) == "BASE"


# ---------------------------------------------------------------------------
# Metadata tagging (R-3.24, P-HotReload-8)
# ---------------------------------------------------------------------------


def test_attribution_published_via_contextvar() -> None:
    """R-3.24: every call publishes a full attribution dict to the
    :data:`_CURRENT_PROMPT_ATTRIBUTION` ContextVar for the duration of
    the handler. A consumer snapshotting it inside the handler (as the
    TrajectorySink does) sees all four attribution fields.
    """
    reg = _FakeRegistry()
    pv = _pv(
        name="knowledge",
        text="K",
        version_id="v-known",
        version_no=7,
        source="db",
    )
    reg.set_active(pv)
    mw = DynamicSystemPromptMiddleware(name="knowledge", registry=reg)

    _inner, attribution, _ = _run_sync(mw, _make_request())
    assert attribution == {
        "sub_agent_name": "knowledge",
        "prompt_version_id": "v-known",
        "prompt_version_no": 7,
        "prompt_source": "db",
    }


def test_attribution_does_not_leak_into_model_settings() -> None:
    """The middleware must never inject its namespace key into
    ``request.model_settings`` — as of LangChain 1.0 that map is
    spread directly into ``model.bind_tools(**model_settings)`` →
    ``OpenAI SDK create(**payload)`` and any unknown kwarg raises a
    ``TypeError``. Regression guard for the production crash
    ``AsyncCompletions.create() got an unexpected keyword argument
    'aiopsos'``.
    """
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="T", version_id="v1"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    req = _make_request(
        model_settings={"temperature": 0.2, "max_tokens": 1024}
    )
    inner, _attrib, _ = _run_sync(mw, req)

    # Caller-supplied keys survive...
    assert inner.model_settings["temperature"] == 0.2
    assert inner.model_settings["max_tokens"] == 1024
    # ...but our namespace is never added.
    assert _MODEL_SETTINGS_NAMESPACE not in inner.model_settings


def test_caller_model_settings_not_mutated_in_place() -> None:
    """The middleware must not write through the caller's dict.

    Otherwise concurrent requests sharing ``model_settings`` templates
    would see cross-talk.
    """
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="T", version_id="v1"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    original_settings = {"temperature": 0.0}
    req = _make_request(model_settings=original_settings)
    _run_sync(mw, req)

    assert _MODEL_SETTINGS_NAMESPACE not in original_settings


def test_default_source_flows_through_attribution() -> None:
    """An unknown sub-agent resolves to a default; attribution reports ``default``."""
    reg = _FakeRegistry()
    # No set_active → get_active returns synthesised default.
    mw = DynamicSystemPromptMiddleware(name="unknown", registry=reg)

    inner, attribution, _ = _run_sync(mw, _make_request())

    assert attribution is not None
    assert attribution["sub_agent_name"] == "unknown"
    assert attribution["prompt_source"] == "default"
    assert attribution["prompt_version_id"] == "default::unknown"
    # Post-swap text for a default registry is an empty prompt.
    assert str(inner.system_message.text) == ""


# ---------------------------------------------------------------------------
# Variant pinning
# ---------------------------------------------------------------------------


def test_pinned_variant_overrides_active() -> None:
    """When the runtime context pins a specific id, that id wins."""
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="ACTIVE", version_id="v-active"))
    reg.register(
        _pv(name="ops", text="SHADOW", version_id="v-shadow", version_no=99)
    )
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    ctx = {"prompt_variant": {"ops": "v-shadow"}}
    req = _make_request(runtime=_FakeRuntime(context=ctx))
    inner, attribution, _ = _run_sync(mw, req)

    assert str(inner.system_message.text) == "SHADOW"
    assert attribution is not None
    assert attribution["prompt_version_id"] == "v-shadow"


def test_pinned_variant_unknown_id_falls_back_to_active() -> None:
    """Stale pins (unknown id) don't crash the middleware — just fall back."""
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="ACTIVE", version_id="v-active"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    ctx = {"prompt_variant": {"ops": "v-ghost"}}
    req = _make_request(runtime=_FakeRuntime(context=ctx))
    inner, _attrib, _ = _run_sync(mw, req)

    assert str(inner.system_message.text) == "ACTIVE"


def test_pinned_variant_for_other_subagent_ignored() -> None:
    """Pin for ``other`` must not affect the ``ops`` middleware instance."""
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="ACTIVE", version_id="v-active"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    ctx = {"prompt_variant": {"other_agent": "v-shadow"}}
    req = _make_request(runtime=_FakeRuntime(context=ctx))
    inner, _attrib, _ = _run_sync(mw, req)

    assert str(inner.system_message.text) == "ACTIVE"


def test_runtime_context_as_dataclass_attribute() -> None:
    """Context sometimes exposes variants as an attribute, not dict key."""
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="ACTIVE", version_id="v-active"))
    reg.register(
        _pv(name="ops", text="AB", version_id="v-ab", version_no=42)
    )
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    @dataclass
    class Ctx:
        prompt_variant: dict[str, str] = field(
            default_factory=lambda: {"ops": "v-ab"}
        )

    req = _make_request(runtime=_FakeRuntime(context=Ctx()))
    inner, _attrib, _ = _run_sync(mw, req)

    assert str(inner.system_message.text) == "AB"


# ---------------------------------------------------------------------------
# Sync / async parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_and_sync_produce_identical_requests() -> None:
    """``wrap_model_call`` and ``awrap_model_call`` must agree byte-for-byte."""
    reg = _FakeRegistry()
    reg.set_active(_pv(name="ops", text="PROMPT", version_id="v1"))
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    suffix = "\n\n[extra-context]"
    base = _make_request(
        system_text=_SENTINEL_PROMPT + suffix,
        model_settings={"temperature": 0.3},
    )
    sync_inner, sync_attrib, _ = _run_sync(mw, base)
    async_inner, async_attrib, _ = await _run_async(mw, base)

    assert (
        str(sync_inner.system_message.text)
        == str(async_inner.system_message.text)
        == "PROMPT" + suffix
    )
    assert sync_attrib == async_attrib
    assert (
        sync_inner.model_settings["temperature"]
        == async_inner.model_settings["temperature"]
        == 0.3
    )
    # Neither path leaks the namespace into model_settings.
    assert _MODEL_SETTINGS_NAMESPACE not in sync_inner.model_settings
    assert _MODEL_SETTINGS_NAMESPACE not in async_inner.model_settings


# ---------------------------------------------------------------------------
# Defensive: registry returning the sentinel itself
# ---------------------------------------------------------------------------


def test_registry_returning_sentinel_is_stripped() -> None:
    """Belt-and-braces: if the registry ever returns the sentinel as its
    prompt, the middleware strips it rather than re-emitting the sentinel
    marker downstream.
    """
    reg = _FakeRegistry()
    reg.set_active(
        _pv(name="ops", text=_SENTINEL_PROMPT + "tail", version_id="v1")
    )
    mw = DynamicSystemPromptMiddleware(name="ops", registry=reg)

    inner, _attrib, _ = _run_sync(mw, _make_request())
    text = str(inner.system_message.text)
    assert not text.startswith(_SENTINEL_PROMPT)
    assert text == "tail"
