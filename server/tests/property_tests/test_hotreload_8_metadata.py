"""P-HotReload-8: every sub-agent LLM call is attributed.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 19.6.
**Validates Requirements R-3.24**.

Property (design.md § Correctness Properties, P-HotReload-8)::

    ∀ (any LLM call routed through a DynamicSystemPromptMiddleware-owned
       sub-agent): the consumer running inside the inner handler MUST
       observe ``sub_agent_name`` and ``prompt_version_id`` (and for
       completeness ``prompt_version_no`` / ``prompt_source``) so the
       TrajectorySink can attribute the call back to the specific
       prompt version.

Where does the annotation live?
-------------------------------
The task description originally said "``request.metadata``" in
shorthand, and the first implementation piggy-backed on
``request.model_settings['aiopsos']``. As of LangChain 1.0 the agent
factory spreads ``**request.model_settings`` directly into
``model.bind_tools(...)`` → ``create(**payload)``, so any key there
becomes a literal API kwarg and the OpenAI SDK raises
``TypeError: got an unexpected keyword argument 'aiopsos'``.

The middleware therefore publishes attribution through a
:class:`~contextvars.ContextVar`
(``_CURRENT_PROMPT_ATTRIBUTION``), exposed publicly by
:func:`get_current_prompt_attribution`. A consumer snapshotting it
inside the inner handler (as the TrajectorySink does) sees exactly
the same attribution it used to read from ``model_settings['aiopsos']``.

Test strategy
-------------
For every Hypothesis-generated (sub-agent name, prompt-version shape,
caller-supplied settings) tuple we confirm:

1. The attribution dict observable via
   :func:`get_current_prompt_attribution` during the inner handler
   contains all four attribution keys.
2. ``sub_agent_name`` matches the middleware's configured lane
   (possibly overridden by a variant pin on the runtime context).
3. ``prompt_version_id`` / ``prompt_version_no`` / ``prompt_source``
   match the registry entry that ultimately served the call —
   verifying variant pinning and default-fallback paths route the
   annotation correctly, not just the active-lane happy path.
4. The middleware never leaks the ``aiopsos`` namespace back into
   ``request.model_settings`` (regression guard against the SDK
   TypeError).
5. The original caller ``model_settings`` dict is not mutated in
   place (important for concurrent requests sharing a template).
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
    _MODEL_SETTINGS_NAMESPACE,
    _SENTINEL_PROMPT,
    get_current_prompt_attribution,
)
from src.services.evolution.prompt_registry import PromptVersion


pytestmark = pytest.mark.property


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


class _FakeModel:
    """Placeholder ``ModelRequest.model``."""


@dataclass
class _FakeRuntime:
    context: Any = None


class _FakeRegistry:
    """In-memory substitute exposing the same surface the middleware
    uses (``get_active`` / ``get_by_id``).

    ``register_shadow`` is a convenience for the variant-pin tests —
    it stores a version reachable only by id, not by lane lookup.
    """

    def __init__(self) -> None:
        self._active: dict[str, PromptVersion] = {}
        self._by_id: dict[str, PromptVersion] = {}

    def set_active(self, pv: PromptVersion) -> None:
        self._active[pv.sub_agent_name] = pv
        self._by_id[pv.id] = pv

    def register_shadow(self, pv: PromptVersion) -> None:
        self._by_id[pv.id] = pv

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
        return self._by_id.get(str(version_id))


def _pv(
    *,
    name: str,
    version_id: str,
    version_no: int,
    source: str,
    text: str = "prompt",
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
    model_settings: dict[str, Any] | None = None,
    runtime: _FakeRuntime | None = None,
) -> ModelRequest:
    return ModelRequest(
        model=_FakeModel(),  # type: ignore[arg-type]
        messages=[HumanMessage(content="ping")],
        system_message=SystemMessage(content=_SENTINEL_PROMPT),
        model_settings=dict(model_settings) if model_settings else {},
        runtime=runtime,  # type: ignore[arg-type]
    )


def _run_sync(
    mw: DynamicSystemPromptMiddleware, req: ModelRequest
) -> tuple[ModelRequest, dict[str, Any] | None]:
    """Invoke the sync wrap and snapshot the attribution ContextVar
    as observed from inside the handler (mirrors how TrajectorySink
    reads it)."""
    captured: dict[str, Any] = {}

    def handler(inner: ModelRequest) -> ModelResponse:
        captured["request"] = inner
        captured["attribution"] = get_current_prompt_attribution()
        return ModelResponse(result=[AIMessage(content="ok")])

    mw.wrap_model_call(req, handler)
    return captured["request"], captured["attribution"]


def _run_async(
    mw: DynamicSystemPromptMiddleware, req: ModelRequest
) -> tuple[ModelRequest, dict[str, Any] | None]:
    captured: dict[str, Any] = {}

    async def handler(inner: ModelRequest) -> ModelResponse:
        captured["request"] = inner
        captured["attribution"] = get_current_prompt_attribution()
        return ModelResponse(result=[AIMessage(content="ok")])

    asyncio.run(mw.awrap_model_call(req, handler))
    return captured["request"], captured["attribution"]


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


_PBT_SETTINGS = hsettings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


_name_strategy = st.sampled_from(
    [
        "knowledge",
        "ops",
        "research",
        "planning",
        "general-purpose",
        "memory-consolidation",
    ]
)


_version_id_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"),
        whitelist_characters="-_",
    ),
    min_size=1,
    max_size=36,
)


_version_no_strategy = st.integers(min_value=0, max_value=9999)


_source_strategy = st.sampled_from(["db", "default"])


# Model settings strategies: the caller may provide an empty dict or
# arbitrary passthrough keys. Previously the middleware merged into a
# caller-supplied ``aiopsos`` sub-dict; that behaviour is gone (see
# module docstring), so we only generate non-``aiopsos`` keys here.
_caller_settings_strategy = st.one_of(
    st.just({}),
    st.fixed_dictionaries({"temperature": st.floats(0.0, 1.0)}),
    st.fixed_dictionaries(
        {
            "temperature": st.floats(0.0, 1.0),
            "max_tokens": st.integers(min_value=1, max_value=4096),
        }
    ),
    st.fixed_dictionaries(
        {
            "temperature": st.floats(0.0, 1.0),
            "top_p": st.floats(0.0, 1.0),
            "presence_penalty": st.floats(-2.0, 2.0),
        }
    ),
)


# ---------------------------------------------------------------------------
# Active-lane metadata property
# ---------------------------------------------------------------------------


@given(
    name=_name_strategy,
    version_id=_version_id_strategy,
    version_no=_version_no_strategy,
    source=_source_strategy,
    caller_settings=_caller_settings_strategy,
)
@_PBT_SETTINGS
def test_active_lane_metadata_annotated_on_every_call(
    name: str,
    version_id: str,
    version_no: int,
    source: str,
    caller_settings: dict[str, Any],
) -> None:
    """R-3.24 / P-HotReload-8: attribution observable via ContextVar.

    All four attribution keys must be present and match the resolved
    registry version. The middleware must NOT leak its namespace into
    ``request.model_settings`` (regression guard against the LangChain
    1.0 → OpenAI SDK ``TypeError`` for unknown kwargs).
    """
    reg = _FakeRegistry()
    reg.set_active(
        _pv(
            name=name,
            version_id=version_id,
            version_no=version_no,
            source=source,
        )
    )
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)

    # Snapshot the caller's settings so we can verify non-mutation.
    original_snapshot = {
        k: (dict(v) if isinstance(v, dict) else v)
        for k, v in caller_settings.items()
    }

    req = _make_request(model_settings=caller_settings)
    inner, attribution = _run_sync(mw, req)

    assert attribution is not None
    assert attribution["sub_agent_name"] == name
    assert attribution["prompt_version_id"] == version_id
    assert attribution["prompt_version_no"] == version_no
    assert attribution["prompt_source"] == source

    # The middleware must not inject its namespace back into the
    # settings dict LangChain spreads into the model call.
    assert _MODEL_SETTINGS_NAMESPACE not in inner.model_settings

    # Top-level caller keys passthrough intact — including any
    # pre-existing ``aiopsos`` key the caller supplied for their own
    # purposes (we leave it alone).
    for k, v in caller_settings.items():
        assert inner.model_settings[k] == v

    # And the caller's dict was NOT mutated — we check a post-call
    # equality against the pre-call snapshot.
    assert caller_settings == original_snapshot


@given(
    name=_name_strategy,
    version_id=_version_id_strategy,
    version_no=_version_no_strategy,
    source=_source_strategy,
)
@_PBT_SETTINGS
def test_active_lane_metadata_annotated_on_async_path(
    name: str, version_id: str, version_no: int, source: str
) -> None:
    """Async path publishes the same attribution as the sync path."""
    reg = _FakeRegistry()
    reg.set_active(
        _pv(
            name=name,
            version_id=version_id,
            version_no=version_no,
            source=source,
        )
    )
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)

    req = _make_request()
    inner, attribution = _run_async(mw, req)

    assert attribution is not None
    assert attribution["sub_agent_name"] == name
    assert attribution["prompt_version_id"] == version_id
    assert attribution["prompt_version_no"] == version_no
    assert attribution["prompt_source"] == source
    assert _MODEL_SETTINGS_NAMESPACE not in inner.model_settings


# ---------------------------------------------------------------------------
# Variant-pin metadata property
# ---------------------------------------------------------------------------


@given(
    name=_name_strategy,
    active_id=_version_id_strategy,
    shadow_id=_version_id_strategy,
    shadow_no=_version_no_strategy,
)
@_PBT_SETTINGS
def test_pinned_variant_attribution_reflects_pinned_version(
    name: str, active_id: str, shadow_id: str, shadow_no: int
) -> None:
    """When a ShadowABRouter pins a non-active version for the turn,
    the attribution metadata must report the *pinned* id, not active.

    This is critical for blast-radius diagnosis: if a shadow prompt
    regresses, trajectory must attribute the failure to the shadow
    version, not the active lane.
    """
    # Skip degenerate cases where the two ids collide — the property
    # is trivially true and Hypothesis picks them on shrink.
    if active_id == shadow_id:
        return

    reg = _FakeRegistry()
    reg.set_active(
        _pv(name=name, version_id=active_id, version_no=1, source="db")
    )
    reg.register_shadow(
        _pv(
            name=name,
            version_id=shadow_id,
            version_no=shadow_no,
            source="db",
        )
    )
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)

    ctx = {"prompt_variant": {name: shadow_id}}
    req = _make_request(runtime=_FakeRuntime(context=ctx))
    _inner, attribution = _run_sync(mw, req)

    assert attribution is not None
    assert attribution["sub_agent_name"] == name
    assert attribution["prompt_version_id"] == shadow_id
    assert attribution["prompt_version_no"] == shadow_no


# ---------------------------------------------------------------------------
# Default-fallback metadata property
# ---------------------------------------------------------------------------


@given(name=_name_strategy)
@_PBT_SETTINGS
def test_default_fallback_attribution_reports_default_source(
    name: str,
) -> None:
    """When the registry has no row for a lane (R-3.20 cold start) the
    synthesised default version must still produce full attribution —
    with ``prompt_source='default'`` so analytics can distinguish
    "no-one has promoted a prompt yet" from "this is our promoted
    active". Without this distinction we'd silently attribute all
    pre-promotion traffic to whatever default happens to be bundled.
    """
    reg = _FakeRegistry()  # no set_active ⇒ fallback path
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)

    _inner, attribution = _run_sync(mw, _make_request())
    assert attribution is not None
    assert attribution["sub_agent_name"] == name
    assert attribution["prompt_source"] == "default"
    assert attribution["prompt_version_id"] == f"default::{name}"
    assert attribution["prompt_version_no"] == 0


# ---------------------------------------------------------------------------
# Attribution on repeated calls: idempotent + independent
# ---------------------------------------------------------------------------


@given(
    name=_name_strategy,
    version_id=_version_id_strategy,
    version_no=_version_no_strategy,
    repeats=st.integers(min_value=2, max_value=5),
)
@_PBT_SETTINGS
def test_attribution_is_stable_across_repeated_calls(
    name: str, version_id: str, version_no: int, repeats: int
) -> None:
    """Every call through the middleware, for a fixed registry state,
    produces the same attribution dict. No state creeps between calls.

    Additionally we verify calls don't share mutable state: mutating
    the attribution dict returned from one call must not change what
    subsequent calls observe. (We deliberately avoid asserting on
    ``id(...)`` because CPython's allocator is free to recycle
    addresses once objects are reclaimed — that check is flaky on
    garbage-collected runtimes.)
    """
    reg = _FakeRegistry()
    reg.set_active(
        _pv(
            name=name,
            version_id=version_id,
            version_no=version_no,
            source="db",
        )
    )
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)

    first_inner, first_attrib = _run_sync(mw, _make_request())
    assert first_attrib is not None
    # Keep a snapshot before we poison the returned dict so we can
    # compare subsequent calls against the pristine expected value.
    expected = dict(first_attrib)

    # Poison the first call's attribution dict + inner settings. If
    # either is shared with later calls, the mutation leaks.
    first_attrib["sub_agent_name"] = "__poisoned__"
    first_attrib["prompt_version_id"] = "__poisoned__"
    first_inner.model_settings["__probe__"] = "leaked"

    for _ in range(repeats - 1):
        inner, attrib = _run_sync(mw, _make_request())
        assert attrib == expected, (
            f"State leaked across calls: expected {expected!r}, got {attrib!r}"
        )
        assert "__probe__" not in inner.model_settings
        assert _MODEL_SETTINGS_NAMESPACE not in inner.model_settings
