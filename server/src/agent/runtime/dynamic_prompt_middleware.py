"""Runtime-time replacement of the subagent system prompt.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 19.1,
requirements R-3.21, R-3.22, R-3.24, R-3.25, correctness properties
P-HotReload-6 / 7 / 8.

The middleware lives at **position 0** of every :class:`CompiledSubAgent`
stack (see :mod:`src.agent.runtime.compiled_subagent_factory`). On each
model call it replaces ``request.system_message`` with the latest
prompt snapshot resolved from :class:`SubAgentPromptRegistry`, leaving
any suffix that later middleware appended on top of our sentinel
intact (R-3.25).

Design contract recap:

1. Registry ownership. The middleware does **not** hold DB state; it
   only reads from an injected :class:`SubAgentPromptRegistry`. The
   registry is hot-swappable at runtime, so the same compiled runnable
   picks up new prompt versions without rebuilding LangGraph (R-3.23).
2. Suffix preservation. ``create_agent(system_prompt=_SENTINEL_PROMPT)``
   installs the sentinel as the base SystemMessage. Any outer
   middleware that appends extra content — Skills / Summarization —
   runs *after* us for ``before_model`` hooks but *wraps* us via its
   own ``wrap_model_call``. When we're first in the list we're
   outermost ⇒ the sentinel is pristine. But: LangChain composes
   ``wrap_model_call`` with the first middleware as the **outermost**
   layer, meaning later middleware can still have mutated the request
   further inside. We defensively detect either case: if the incoming
   text starts with the sentinel, whatever follows is the "suffix" to
   keep; otherwise we replace wholesale (defensive fallback that
   shouldn't fire under a correct middleware order).
3. Metadata tagging (R-3.24). ``ModelRequest.override`` does not
   accept a free-form ``metadata`` kwarg — the upstream dataclass only
   whitelists a fixed set of fields. Before LangChain 1.0 we piggy-
   backed on ``model_settings['aiopsos']``, but 1.0 spreads
   ``**request.model_settings`` directly into ``model.bind_tools(...)``
   (which in turn lands in the OpenAI SDK's ``create(**payload)`` →
   ``TypeError: unexpected keyword argument 'aiopsos'``). We instead
   publish the attribution dict through a module-level
   :class:`~contextvars.ContextVar`
   (:data:`_CURRENT_PROMPT_ATTRIBUTION`) that TrajectorySink and tests
   can snapshot via :func:`get_current_prompt_attribution`.

Ordering contract (R-3.22): the shim factory puts this middleware at
index 0 of the middleware list passed to ``create_agent``. For the
``wrap_model_call`` chain that means we are the **outermost** layer —
handler(request) runs every other middleware's wrap *and* the model
itself. For ``before_model`` / ``after_model`` hooks the order is the
natural list order too (first → earliest). Placing us first satisfies
both the "sees sentinel + suffix only" and "runs before inner hooks"
requirements in R-3.25.

**P-HotReload-6 (sentinel always replaced).** After our
``_swap_prompt`` runs, ``new_text`` is derived from
``registry.prompt + suffix``; it never starts with the sentinel string
unless the registry itself returned the sentinel (defended against
with an explicit assertion).

**P-HotReload-7 (suffix preserved).** If the pre-swap text is
``_SENTINEL_PROMPT + X``, the post-swap text is ``registry.prompt + X``
— the sentinel is swapped out, X is kept.

**P-HotReload-8 (metadata annotation).** Every call publishes a fresh
attribution dict via :data:`_CURRENT_PROMPT_ATTRIBUTION` (a
:class:`~contextvars.ContextVar`) carrying ``sub_agent_name /
prompt_version_id / prompt_version_no / prompt_source`` so downstream
observers (TrajectorySink, ``/metrics``) can attribute the LLM call.
The dict is **not** forwarded via ``request.model_settings`` because
LangChain 1.0 spreads that map straight into the underlying model's
``create(**payload)`` call.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import SystemMessage

if TYPE_CHECKING:
    from src.services.evolution.prompt_registry import (
        PromptVersion,
        SubAgentPromptRegistry,
    )

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------

_SENTINEL_PROMPT = "<<AIOPS_DYNAMIC_PROMPT_SENTINEL>>"
"""Placeholder system prompt passed to ``create_agent()`` at compile time.

The sentinel is an arbitrary but unique string that:

* LangChain won't interpret specially (it's just a ``SystemMessage``
  body).
* We can reliably detect and strip at runtime.
* Is never appended by outer middleware (it carries no semantic
  meaning — outer middleware only append instructions).

``create_agent(system_prompt=None)`` would also work, but the upstream
implementation sets ``request.system_message = None`` in that case,
which forces every consumer to handle ``None``. A sentinel string
gives us a stable, non-None anchor.
"""


# ---------------------------------------------------------------------------
# Observability key
# ---------------------------------------------------------------------------

_MODEL_SETTINGS_NAMESPACE = "aiopsos"
"""Historic sub-dict key name. Kept only for backward-compat imports
(tests still reference it). The middleware no longer writes this key
into ``request.model_settings`` — see :func:`_swap_prompt` for why.
"""


_CURRENT_PROMPT_ATTRIBUTION: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("aiopsos_prompt_attribution", default=None)
)
"""Live attribution metadata for the model call currently in flight.

Written by :class:`DynamicSystemPromptMiddleware` just before it calls
``handler(request)``; read by :class:`TrajectorySink` (or tests) to
attribute the call to a specific sub-agent + prompt version. The value
is a shallow copy — callers must treat it as read-only.
"""


def get_current_prompt_attribution() -> dict[str, Any] | None:
    """Return a snapshot of the current LLM call's prompt attribution.

    Returns ``None`` outside of a
    :class:`DynamicSystemPromptMiddleware`-wrapped model call.
    """
    current = _CURRENT_PROMPT_ATTRIBUTION.get()
    return dict(current) if current else None


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class DynamicSystemPromptMiddleware(AgentMiddleware[Any, ContextT, ResponseT]):
    """Replace ``request.system_message`` with the live registry snapshot.

    This middleware **must** be the first entry in the subagent's
    middleware list (R-3.22). It supports both sync and async code paths
    so the same compiled sub-agent works whether the caller uses
    ``invoke()`` or ``ainvoke()``.

    Per-request variant resolution (future: ShadowABRouter, task 23.2)
    is already supported: if the :class:`Runtime` context carries a
    dict under ``ctx_variant_key`` of the shape
    ``{sub_agent_name: version_id}``, that specific version is loaded
    via :meth:`SubAgentPromptRegistry.get_by_id`. Otherwise we fall
    back to the lane's current active.
    """

    def __init__(
        self,
        *,
        name: str,
        registry: SubAgentPromptRegistry,
        ctx_variant_key: str = "prompt_variant",
    ) -> None:
        """
        Args:
            name: sub_agent_name (e.g. ``"knowledge"``, ``"ops"``).
                Must match the key under which the registry stores the
                active version for this sub-agent.
            registry: process-wide :class:`SubAgentPromptRegistry`.
                Borrowed, not owned.
            ctx_variant_key: optional name of the attribute on
                ``request.runtime.context`` that :class:`ShadowABRouter`
                uses to pin a variant for the whole turn. Defaulting
                this keeps the middleware usable before the router
                lands.
        """
        super().__init__()
        self._name = name
        self._registry = registry
        self._ctx_variant_key = ctx_variant_key

    # ------------------------------------------------------------------
    # wrap_model_call / awrap_model_call
    # ------------------------------------------------------------------

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        new_request, attribution = self._swap_prompt(request)
        token = _CURRENT_PROMPT_ATTRIBUTION.set(attribution)
        try:
            return handler(new_request)
        finally:
            _CURRENT_PROMPT_ATTRIBUTION.reset(token)

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[
            [ModelRequest[ContextT]], Awaitable[ModelResponse[ResponseT]]
        ],
    ) -> ModelResponse[ResponseT]:
        new_request, attribution = self._swap_prompt(request)
        token = _CURRENT_PROMPT_ATTRIBUTION.set(attribution)
        try:
            return await handler(new_request)
        finally:
            _CURRENT_PROMPT_ATTRIBUTION.reset(token)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _swap_prompt(
        self, request: ModelRequest[ContextT]
    ) -> tuple[ModelRequest[ContextT], dict[str, Any]]:
        """Return the rewritten request plus the attribution dict.

        The transformation is a single ``request.override(...)`` so the
        original request object is never mutated (``ModelRequest`` is a
        frozen-ish dataclass that emits deprecation warnings on
        in-place writes).

        The attribution dict is returned separately so the caller can
        scope its :class:`~contextvars.ContextVar` lifetime to exactly
        the inner ``handler(new_request)`` call; see
        :meth:`wrap_model_call` / :meth:`awrap_model_call` for the
        token-based reset.
        """
        pv = self._pick_version(request)
        base_text = pv.system_prompt or ""
        suffix = _extract_suffix(request.system_message)

        new_text = base_text + suffix
        # Belt-and-braces: if the registry ever hands back the sentinel
        # we'd recreate it; strip so P-HotReload-6 still holds.
        if new_text.startswith(_SENTINEL_PROMPT):
            logger.warning(
                "DynamicSystemPromptMiddleware[%s]: registry returned the "
                "sentinel as system_prompt; stripping to honour P-HotReload-6",
                self._name,
            )
            new_text = new_text[len(_SENTINEL_PROMPT):]

        new_msg = SystemMessage(content=new_text)
        # NB: we deliberately do NOT stash attribution metadata into
        # ``request.model_settings``. As of LangChain 1.0 the factory
        # spreads ``**request.model_settings`` directly into
        # ``model.bind_tools(...)`` → OpenAI SDK ``create(**payload)``,
        # so any key there becomes a literal API kwarg (the OpenAI SDK
        # then raises ``TypeError: got an unexpected keyword argument
        # 'aiopsos'``). The attribution dict is instead passed back to
        # the caller which scopes it to the inner handler via a
        # :class:`~contextvars.ContextVar`.
        attribution = _build_prompt_attribution(self._name, pv)
        new_request = request.override(system_message=new_msg)
        return new_request, attribution

    def _pick_version(
        self, request: ModelRequest[ContextT]
    ) -> PromptVersion:
        """Resolve which :class:`PromptVersion` applies to this call.

        Resolution order:

        1. ShadowABRouter pin: ``request.runtime.context[ctx_variant_key]
           [sub_agent_name]`` ⇒ ``registry.get_by_id(version_id)``.
           If the pin points at an unknown id we fall through (don't
           crash on stale pins).
        2. Registry active lane for this sub_agent_name.

        Return is guaranteed non-``None`` because
        :meth:`SubAgentPromptRegistry.get_active` synthesises a
        default fallback for unknown names (R-3.20).
        """
        rt = getattr(request, "runtime", None)
        ctx = getattr(rt, "context", None)
        if ctx is not None:
            variants: Any = None
            try:
                variants = getattr(ctx, self._ctx_variant_key, None)
                if variants is None and isinstance(ctx, dict):
                    variants = ctx.get(self._ctx_variant_key)
            except Exception:  # pragma: no cover - exotic context types
                variants = None
            if isinstance(variants, dict):
                chosen_id = variants.get(self._name)
                if chosen_id:
                    pv = self._registry.get_by_id(chosen_id)
                    if pv is not None:
                        return pv
                    logger.debug(
                        "DynamicSystemPromptMiddleware[%s]: pinned variant "
                        "id %s not loaded; falling back to active",
                        self._name,
                        chosen_id,
                    )
        return self._registry.get_active(self._name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_suffix(system_message: SystemMessage | None) -> str:
    """Return text that outer middleware appended after the sentinel.

    If the sentinel prefix is intact, the suffix is the trailing
    portion. If the sentinel is gone (outer middleware replaced the
    whole message), return ``""`` — we can't safely preserve content
    that might already carry a prior prompt version's base text.
    """
    if system_message is None:
        return ""
    # ``SystemMessage.text`` returns a ``TextAccessor`` that is a
    # true ``str`` subclass, so string operations work directly.
    text = str(system_message.text) if system_message.text is not None else ""
    if text.startswith(_SENTINEL_PROMPT):
        return text[len(_SENTINEL_PROMPT):]
    return ""


def _build_prompt_attribution(
    sub_agent_name: str,
    pv: PromptVersion,
) -> dict[str, Any]:
    """Return a fresh attribution dict for the current model call.

    Data layout::

        {
            'sub_agent_name': 'knowledge',
            'prompt_version_id': '...uuid-or-default::name...',
            'prompt_version_no': 7,
            'prompt_source': 'db',   # or 'default'
        }

    The dict is freshly allocated on every call so downstream readers
    can keep a reference without risk of cross-call mutation.
    """
    return {
        "sub_agent_name": sub_agent_name,
        "prompt_version_id": pv.id,
        "prompt_version_no": pv.version_no,
        "prompt_source": pv.source,
    }


__all__ = [
    "DynamicSystemPromptMiddleware",
    "_MODEL_SETTINGS_NAMESPACE",
    "_SENTINEL_PROMPT",
    "get_current_prompt_attribution",
]
