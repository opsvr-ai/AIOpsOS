"""P-HotReload-7: suffix appended around the sentinel survives the swap.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 19.5.
**Validates Requirements R-3.25**.

Property (design.md § Correctness Properties, P-HotReload-7)::

    WHEN an outer middleware (Skills / Summarization / …) has appended
    content X onto the sentinel prompt THEN, after
    DynamicSystemPromptMiddleware runs, the final system_message.text
    the inner layers / model see MUST equal
    ``registry.prompt + X`` exactly.

There are two places "X" can come from in practice:

1. **Pre-swap suffix** — the request arrives at
   :class:`DynamicSystemPromptMiddleware` with
   ``sentinel + X`` already in ``system_message``. This happens when a
   middleware earlier in the stack (or a ``before_model`` hook) rewrote
   the system message prior to our ``wrap_model_call``.
2. **Post-swap suffix** — a middleware **inside** us (i.e. deeper in
   the list, so its ``wrap_model_call`` runs as our handler's callee)
   appends "::SUFFIX" to whatever it receives from us. We must hand
   it the clean ``registry.prompt`` so this probe observes exactly
   ``registry.prompt + "::SUFFIX"``.

Both flows are exercised below. Hypothesis varies the registry prompt
and the suffix content so we don't rely on specific marker strings to
"accidentally work".
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agent.runtime.dynamic_prompt_middleware import (
    DynamicSystemPromptMiddleware,
    _SENTINEL_PROMPT,
)
from src.services.evolution.prompt_registry import PromptVersion


pytestmark = pytest.mark.property


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


class _FakeModel:
    """Placeholder ``ModelRequest.model``."""


class _FakeRegistry:
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
        id=f"v-{name}",
        sub_agent_name=name,
        status="active",
        system_prompt=text,
        version_no=1,
        manifest_sha256="",
        parent_version_id=None,
        activated_at=None,
        source="db",
    )


def _make_request(*, system_text: str | None) -> ModelRequest:
    sys_msg = (
        None if system_text is None else SystemMessage(content=system_text)
    )
    return ModelRequest(
        model=_FakeModel(),  # type: ignore[arg-type]
        messages=[HumanMessage(content="ping")],
        system_message=sys_msg,
        model_settings={},
    )


# ---------------------------------------------------------------------------
# Probe middleware: records + appends suffix to system_message
# ---------------------------------------------------------------------------


class _SuffixAppendProbe(AgentMiddleware):
    """Appends ``suffix`` onto ``system_message`` then forwards.

    Used to simulate a downstream middleware (Skills, Summarization,
    custom extensions) that adds content *after*
    :class:`DynamicSystemPromptMiddleware` has already swapped the
    sentinel for the live prompt. The probe also records the request
    it receives so the test can assert what it saw *before* it
    appended — that captures ``registry.prompt`` on its own.
    """

    def __init__(self, *, suffix: str) -> None:
        super().__init__()
        self._suffix = suffix
        self.observed_before_append: list[str] = []

    def _append(self, request: ModelRequest) -> ModelRequest:
        old = request.system_message
        old_text = (
            str(old.text)
            if old is not None and old.text is not None
            else ""
        )
        self.observed_before_append.append(old_text)
        new_msg = SystemMessage(content=old_text + self._suffix)
        return request.override(system_message=new_msg)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._append(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._append(request))


def _compose_wrap(
    middlewares: list[AgentMiddleware],
    request: ModelRequest,
    *,
    sync: bool = True,
) -> ModelRequest:
    """Compose a wrap chain outer-first and return what the model sees.

    This mirrors how LangChain composes ``wrap_model_call`` — middleware
    at index 0 is outermost, its ``handler`` is the next middleware's
    wrap, and the deepest handler is the "model invocation". We capture
    the request at that deepest point so the test can assert the
    final, fully-processed system message.
    """
    captured: dict[str, ModelRequest] = {}

    def model_handler(inner: ModelRequest) -> ModelResponse:
        captured["request"] = inner
        return ModelResponse(result=[AIMessage(content="ok")])

    async def amodel_handler(inner: ModelRequest) -> ModelResponse:
        captured["request"] = inner
        return ModelResponse(result=[AIMessage(content="ok")])

    def _build_sync_chain(
        idx: int,
    ) -> Callable[[ModelRequest], ModelResponse]:
        if idx >= len(middlewares):
            return model_handler
        inner = _build_sync_chain(idx + 1)
        mw = middlewares[idx]
        return lambda req: mw.wrap_model_call(req, inner)

    def _build_async_chain(
        idx: int,
    ) -> Callable[[ModelRequest], Awaitable[ModelResponse]]:
        if idx >= len(middlewares):
            return amodel_handler
        inner = _build_async_chain(idx + 1)
        mw = middlewares[idx]
        return lambda req: mw.awrap_model_call(req, inner)

    if sync:
        _build_sync_chain(0)(request)
    else:
        asyncio.run(_build_async_chain(0)(request))
    return captured["request"]


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------


_PBT_SETTINGS = hsettings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)


_prompt_strategy = st.text(max_size=400).filter(
    # Exclude prompts that themselves start with the sentinel — the
    # middleware's belt-and-braces strip would then rewrite the base,
    # which is a *separate* property tested in task 19.4. Filtering
    # here keeps P-HotReload-7 focused on the equality it asserts.
    lambda s: not s.startswith(_SENTINEL_PROMPT)
)


# Suffix strategy: everything from ``""`` to short multi-line
# instructions. We include ``"::SUFFIX"`` as a fixed example to match
# the task's canonical case, and we let Hypothesis generate arbitrary
# other suffixes so the property doesn't only hold for one token.
_suffix_strategy = st.one_of(
    st.just("::SUFFIX"),
    st.just(""),
    st.text(max_size=250),
)


_name_strategy = st.sampled_from(
    ["knowledge", "ops", "research", "planning", "general-purpose"]
)


# ---------------------------------------------------------------------------
# Probe-middleware property: post-swap suffix survives unchanged
# ---------------------------------------------------------------------------


@given(
    name=_name_strategy,
    prompt_text=_prompt_strategy,
    suffix=_suffix_strategy,
)
@_PBT_SETTINGS
def test_probe_suffix_appended_after_dynamic_mw_yields_exact_concatenation(
    name: str, prompt_text: str, suffix: str
) -> None:
    """Core 19.5 scenario.

    DynamicSystemPromptMiddleware at index 0 swaps
    ``sentinel → registry.prompt``; :class:`_SuffixAppendProbe` at
    index 1 appends ``suffix``. The model sees
    ``registry.prompt + suffix`` with no artifacts.
    """
    reg = _FakeRegistry()
    reg.set_active(_make_pv(name, prompt_text))
    dyn = DynamicSystemPromptMiddleware(name=name, registry=reg)
    probe = _SuffixAppendProbe(suffix=suffix)

    req = _make_request(system_text=_SENTINEL_PROMPT)
    inner = _compose_wrap([dyn, probe], req)

    final = str(inner.system_message.text) if inner.system_message else ""
    assert final == prompt_text + suffix, (
        f"Expected {(prompt_text + suffix)!r}, got {final!r}"
    )
    # Also: the probe itself must have observed the cleanly-swapped
    # prompt (pre-append). If the sentinel had leaked, we'd have seen
    # it here.
    assert probe.observed_before_append == [prompt_text]


@given(
    name=_name_strategy,
    prompt_text=_prompt_strategy,
    suffix=_suffix_strategy,
)
@_PBT_SETTINGS
def test_probe_suffix_preserved_on_async_path(
    name: str, prompt_text: str, suffix: str
) -> None:
    """Same property, exercised through ``awrap_model_call``."""
    reg = _FakeRegistry()
    reg.set_active(_make_pv(name, prompt_text))
    dyn = DynamicSystemPromptMiddleware(name=name, registry=reg)
    probe = _SuffixAppendProbe(suffix=suffix)

    req = _make_request(system_text=_SENTINEL_PROMPT)
    inner = _compose_wrap([dyn, probe], req, sync=False)

    final = str(inner.system_message.text) if inner.system_message else ""
    assert final == prompt_text + suffix


# ---------------------------------------------------------------------------
# Pre-swap suffix property: sentinel + X arrives, registry.prompt + X leaves
# ---------------------------------------------------------------------------


@given(
    name=_name_strategy,
    prompt_text=_prompt_strategy,
    prepend_suffix=st.text(max_size=250),
)
@_PBT_SETTINGS
def test_pre_swap_suffix_is_preserved(
    name: str, prompt_text: str, prepend_suffix: str
) -> None:
    """R-3.25 in its direct form: ``sentinel + X → registry.prompt + X``.

    Whatever the caller stacks onto the sentinel before the middleware
    runs must land intact after the swap. This is the complementary
    half of the probe property: it nails down the "outer wrote ahead
    of us" path that would otherwise be invisible to a probe living
    further inside the chain.
    """
    reg = _FakeRegistry()
    reg.set_active(_make_pv(name, prompt_text))
    mw = DynamicSystemPromptMiddleware(name=name, registry=reg)

    req = _make_request(system_text=_SENTINEL_PROMPT + prepend_suffix)
    inner = _compose_wrap([mw], req)

    final = str(inner.system_message.text) if inner.system_message else ""
    assert final == prompt_text + prepend_suffix


@given(
    name=_name_strategy,
    prompt_text=_prompt_strategy,
    prepend_suffix=st.text(max_size=150),
    probe_suffix=_suffix_strategy,
)
@_PBT_SETTINGS
def test_combined_pre_and_post_swap_suffixes_both_preserved(
    name: str,
    prompt_text: str,
    prepend_suffix: str,
    probe_suffix: str,
) -> None:
    """Realistic stack: outer-appended X and inner probe-appended Y.

    Final text MUST be ``registry.prompt + X + Y``. The two suffixes
    attach in deterministic order — X survives the swap (pre-swap
    contract), Y is added by the probe after the swap (post-swap
    contract).
    """
    reg = _FakeRegistry()
    reg.set_active(_make_pv(name, prompt_text))
    dyn = DynamicSystemPromptMiddleware(name=name, registry=reg)
    probe = _SuffixAppendProbe(suffix=probe_suffix)

    req = _make_request(system_text=_SENTINEL_PROMPT + prepend_suffix)
    inner = _compose_wrap([dyn, probe], req)

    final = str(inner.system_message.text) if inner.system_message else ""
    assert final == prompt_text + prepend_suffix + probe_suffix
