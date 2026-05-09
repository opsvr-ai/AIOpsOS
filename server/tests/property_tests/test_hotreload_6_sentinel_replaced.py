"""P-HotReload-6: the sentinel is always replaced.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 19.4.
**Validates Requirements R-3.22, R-3.25**.

Property (design.md § Correctness Properties, P-HotReload-6)::

    ∀ (any LLM call through a DynamicSystemPromptMiddleware-owned
       sub-agent): request.system_message.text MUST NOT start with
       _SENTINEL_PROMPT.

That is, after the middleware finishes ``wrap_model_call`` /
``awrap_model_call``, whatever the *next* layer (or the model itself)
sees must be the live registry prompt (plus any outer-middleware
suffix) — never the raw sentinel the factory passed to
``create_agent(system_prompt=_SENTINEL_PROMPT)``.

Test strategy
-------------
We hook directly into the middleware by installing a capture handler
that records every inbound request. Hypothesis varies:

* ``prompt_text``: the registry's current system prompt (including
  pathological shapes: empty, exact-sentinel, sentinel-prefix-then-
  tail, CJK, control chars).
* ``suffix``: what an outer middleware may have appended after the
  sentinel before we executed (empty, whitespace, markdown, binary-ish
  bytes).
* ``model_settings``: dict shape + pre-existing ``aiopsos`` namespace
  keys.
* ``name``: sub-agent identifier (ASCII + CJK) — both to confirm the
  property is lane-independent and to check that the registry can key
  off any well-formed name.

For every generated example we invoke both the sync and async entry
points and assert the captured downstream request never carries the
sentinel prefix. This is the universal invariant; it must hold for
100% of calls.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agent.runtime.dynamic_prompt_middleware import (
    DynamicSystemPromptMiddleware,
    _SENTINEL_PROMPT,
)
from src.services.evolution.prompt_registry import PromptVersion


pytestmark = pytest.mark.property


# ---------------------------------------------------------------------------
# Minimal fakes (no DB, no LLM, no LangGraph)
# ---------------------------------------------------------------------------


class _FakeModel:
    """Placeholder ``ModelRequest.model`` — never invoked by the middleware."""


@dataclass
class _FakeRuntime:
    """Stand-in for :class:`langgraph.runtime.Runtime`. Only ``context`` is read."""

    context: Any = None


class _FakeRegistry:
    """In-memory substitute for :class:`SubAgentPromptRegistry`.

    Mirrors the real registry's ``get_active`` fallback contract (R-3.20):
    an unknown lane synthesises a ``source='default'`` version so callers
    never see ``None``.
    """

    def __init__(self) -> None:
        self._active: dict[str, PromptVersion] = {}

    def set_active(self, pv: PromptVersion) -> None:
        self._active[pv.sub_agent_name] = pv

    def get_active(self, sub_agent_name: str) -> PromptVersion:
        pv = self._active.get(sub_agent_name)
        if pv is not None:
            return pv
        return PromptVersion(
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

    def get_by_id(self, version_id: str) -> PromptVersion | None:
        for pv in self._active.values():
            if pv.id == version_id:
                return pv
        return None


def _make_pv(name: str, text: str) -> PromptVersion:
    return PromptVersion(
        id=f"v-{name}-1",
        sub_agent_name=name,
        status="active",
        system_prompt=text,
        version_no=1,
        manifest_sha256="",
        parent_version_id=None,
        activated_at=None,
        source="db",
    )


def _make_request(
    *,
    system_text: str | None,
    model_settings: dict[str, Any] | None = None,
) -> ModelRequest:
    sys_msg = (
        None if system_text is None else SystemMessage(content=system_text)
    )
    return ModelRequest(
        model=_FakeModel(),  # type: ignore[arg-type]
        messages=[HumanMessage(content="ping")],
        system_message=sys_msg,
        model_settings=dict(model_settings or {}),
    )


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


# Sub-agent names: a mix of canonical ASCII lanes, CJK, and some edge
# shapes. We intentionally exclude empty strings — the registry keys
# off name, and an empty name would collide with the default fallback
# in ways unrelated to this property.
_name_strategy = st.one_of(
    st.sampled_from(
        ["knowledge", "ops", "research", "planning", "general-purpose"]
    ),
    st.text(
        alphabet=st.characters(
            blacklist_categories=("Cs",),  # no surrogates
            blacklist_characters="\x00",
        ),
        min_size=1,
        max_size=32,
    ),
)


# Registry prompt text: everything from empty to sentinel-shaped
# garbage. The middleware is expected to handle each correctly (the
# sentinel-as-prompt case is covered by a defensive strip-and-log in
# the implementation, so the property must still hold).
_prompt_strategy = st.one_of(
    st.just(""),
    st.text(max_size=500),
    st.just(_SENTINEL_PROMPT),
    st.text(max_size=100).map(lambda s: _SENTINEL_PROMPT + s),
    st.text(max_size=100).map(lambda s: s + _SENTINEL_PROMPT),
)


# Suffix appended by a hypothetical outer middleware: empty, benign
# whitespace, markdown-ish, short unicode. We keep suffixes bounded
# because Hypothesis' shrinker is more effective when the search
# space is proportional to what we expect in production (a few hundred
# characters of extra instruction).
_suffix_strategy = st.text(max_size=300)


# Pre-existing ``model_settings`` dict. Includes cases where the
# caller already wrote to the ``aiopsos`` namespace — we want to be
# sure our merge doesn't accidentally re-emit the sentinel via
# metadata either.
_model_settings_strategy = st.one_of(
    st.just({}),
    st.fixed_dictionaries({"temperature": st.floats(0.0, 1.0)}),
    st.fixed_dictionaries(
        {
            "aiopsos": st.fixed_dictionaries(
                {"request_id": st.text(min_size=1, max_size=12)}
            )
        }
    ),
)


# ---------------------------------------------------------------------------
# Property: sentinel is never the prefix of the downstream request
# ---------------------------------------------------------------------------


_PBT_SETTINGS = hsettings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


def _capture_handler() -> tuple[list[ModelRequest], Any]:
    """Return a list-captor and a handler that records + returns a dummy response."""
    captured: list[ModelRequest] = []

    def handler(inner: ModelRequest) -> ModelResponse:
        captured.append(inner)
        return ModelResponse(result=[AIMessage(content="ok")])

    return captured, handler


def _async_capture_handler() -> tuple[list[ModelRequest], Any]:
    captured: list[ModelRequest] = []

    async def handler(inner: ModelRequest) -> ModelResponse:
        captured.append(inner)
        return ModelResponse(result=[AIMessage(content="ok")])

    return captured, handler


@given(
    name=_name_strategy,
    prompt_text=_prompt_strategy,
    suffix=_suffix_strategy,
    model_settings=_model_settings_strategy,
)
@_PBT_SETTINGS
def test_sync_sentinel_never_reaches_downstream(
    name: str,
    prompt_text: str,
    suffix: str,
    model_settings: dict[str, Any],
) -> None:
    """P-HotReload-6: for every sync model call, the inbound request's
    ``system_message.text`` never starts with ``_SENTINEL_PROMPT``.
    """
    reg = _FakeRegistry()
    reg.set_active(_make_pv(name, prompt_text))
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)
    captured, handler = _capture_handler()

    # Simulate the message the middleware receives from create_agent:
    # the sentinel (possibly with an outer-appended suffix).
    req = _make_request(
        system_text=_SENTINEL_PROMPT + suffix,
        model_settings=model_settings,
    )

    mw.wrap_model_call(req, handler)

    assert len(captured) == 1
    inner = captured[0]
    text = str(inner.system_message.text) if inner.system_message else ""
    assert not text.startswith(_SENTINEL_PROMPT), (
        f"Sentinel leaked downstream for name={name!r}: {text[:120]!r}"
    )


@given(
    name=_name_strategy,
    prompt_text=_prompt_strategy,
    suffix=_suffix_strategy,
    model_settings=_model_settings_strategy,
)
@_PBT_SETTINGS
def test_async_sentinel_never_reaches_downstream(
    name: str,
    prompt_text: str,
    suffix: str,
    model_settings: dict[str, Any],
) -> None:
    """Async variant of :func:`test_sync_sentinel_never_reaches_downstream`.

    Both code paths share ``_swap_prompt`` internally, but we still
    verify the async dispatch because any future divergence (e.g. a
    pre-call hook added to one code path only) would silently break
    half the production calls.
    """
    reg = _FakeRegistry()
    reg.set_active(_make_pv(name, prompt_text))
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)
    captured, handler = _async_capture_handler()

    req = _make_request(
        system_text=_SENTINEL_PROMPT + suffix,
        model_settings=model_settings,
    )

    asyncio.run(mw.awrap_model_call(req, handler))

    assert len(captured) == 1
    inner = captured[0]
    text = str(inner.system_message.text) if inner.system_message else ""
    assert not text.startswith(_SENTINEL_PROMPT), (
        f"Sentinel leaked downstream (async) for name={name!r}: {text[:120]!r}"
    )


@given(
    name=_name_strategy,
    # Edge case: the request the middleware receives already had the
    # sentinel stripped by something else, or arrived as None. The
    # property still holds — no sentinel reaches the handler.
    system_text=st.one_of(
        st.none(),
        st.text(max_size=300),
    ),
    prompt_text=_prompt_strategy,
)
@_PBT_SETTINGS
def test_missing_or_stripped_sentinel_still_holds(
    name: str, system_text: str | None, prompt_text: str
) -> None:
    """Defensive fallback: if the sentinel isn't at the front of the
    inbound message, the middleware still must not emit the sentinel
    downstream. Combined with the belt-and-braces strip when the
    registry itself returns the sentinel, this closes all paths.
    """
    reg = _FakeRegistry()
    reg.set_active(_make_pv(name, prompt_text))
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)
    captured, handler = _capture_handler()

    req = _make_request(system_text=system_text)
    mw.wrap_model_call(req, handler)

    inner = captured[0]
    text = str(inner.system_message.text) if inner.system_message else ""
    assert not text.startswith(_SENTINEL_PROMPT)
