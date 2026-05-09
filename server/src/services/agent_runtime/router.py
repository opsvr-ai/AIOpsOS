"""RouterLLM — 3-tier classifier with cache + soft 500 ms budget.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 13.2 /
R-1.3 / R-1.9 / R-10.2 / R-10.4 / R-10.5 / R-10.6.

Algorithm (matches design.md § "RouterLLM 详细设计"):

1. Look up ``router:decision:{sha256(message + user_id + last_asst_sha)}``
   in Redis. Hit → bump ``router_path_total{path="cache"}`` and return
   the cached decision.
2. **Tier 1 — function calling.** Bind :data:`RouterDecisionTool` with
   ``tool_choice={"type":"tool","name":"decide"}``. Parse the tool-call
   args on success → bump ``router_path_total{path="function_calling"}``.
3. **Tier 2 — JSON mode fallback** (on schema / tool-choice errors):
   ``llm.with_structured_output(RouterDecision, method="json_mode")``.
   Success → bump ``router_path_total{path="json_mode"}``.
4. **Tier 3 — fallback executor** on any further failure. Bump
   ``router_path_total{path="fallback_executor"}``.
5. A hard ``asyncio.TimeoutError`` anywhere short-circuits to the
   fallback path and bumps ``router_timeout_total``.
6. After a decision is produced we apply two post-validation rules:
   ops-keyword promotion (R-10.6) and the confidence floor (R-1.9).
7. Successful decisions are cached with TTL 30 s (R-10.5).

``classify`` never raises: callers can rely on always receiving a
valid :class:`RouterDecision` even if the model / Redis / both go
down. That's the contract that makes RouterLLM safe to put on the
request-critical path.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Callable

from src.core.metrics import router_path_total, router_timeout_total
from src.core.tracing import tracer
from src.services.agent_runtime.router_schema import (
    ROUTER_SYSTEM_PROMPT,
    RouterDecision,
    RouterDecisionTool,
    promote_if_ops_keyword,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


DEFAULT_TIMEOUT_MS: int = 500
DEFAULT_CACHE_TTL_S: int = 30
CONFIDENCE_FLOOR: float = 0.4
CACHE_KEY_PREFIX: str = "router:decision:"

# Truncation bounds used by ``_render_router_context`` (design.md).
HOT_BLOCK_MAX_CHARS: int = 400
HISTORY_TAIL_LINES: int = 4
SKILLS_INDEX_MAX_LINES: int = 40

# Skill-index memo window — avoids hammering tool_manager every request
# while still picking up newly registered skills within a minute.
SKILL_INDEX_CACHE_TTL_S: float = 60.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cache_key(message: str, user_id: str, last_assistant_sha: str) -> str:
    """Compose the Redis key documented in R-10.5.

    The key incorporates user + conversational context so that
    cross-user collisions are impossible and a new assistant reply
    invalidates the cache naturally.
    """
    payload = f"{message}\x1f{user_id}\x1f{last_assistant_sha}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"{CACHE_KEY_PREFIX}{digest}"


def _truncate(text: str, max_chars: int) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _tail_lines(lines: list[str] | None, n: int) -> list[str]:
    if not lines:
        return []
    return list(lines[-n:])


def _clamp_confidence(decision: RouterDecision) -> RouterDecision:
    """Apply the R-1.9 confidence floor.

    Decisions with ``confidence < 0.4`` are downgraded to
    ``route="executor"`` with no suggested tools so the gateway can
    assemble the full-toolset executor. We preserve the original
    ``confidence`` field so the metric still tells the true story of
    why we degraded.
    """
    if decision.confidence >= CONFIDENCE_FLOOR:
        return decision
    return decision.model_copy(
        update={
            "route": "executor",
            "direct_answer": None,
            "subagent_name": None,
            "suggested_tools": [],
        }
    )


def _inc_path(path: str) -> None:
    try:
        router_path_total.labels(path=path).inc()
    except Exception:
        # Never let metric failures surface into the request path.
        logger.debug("router: metric inc failed", exc_info=True)


def _inc_timeout() -> None:
    try:
        router_timeout_total.inc()
    except Exception:
        logger.debug("router: metric inc failed", exc_info=True)


# ---------------------------------------------------------------------------
# RouterLLM
# ---------------------------------------------------------------------------


class RouterLLM:
    """Soft-budgeted routing classifier with tiered LLM fallbacks.

    The class owns just enough state to (a) memoize the skill-index
    string and (b) hold a reference to the underlying LLM / Redis. No
    background tasks, no concurrency locks — every call is independent
    so multiple in-flight ``classify`` invocations don't interfere.
    """

    def __init__(
        self,
        llm: Any | None = None,
        *,
        cache_ttl_s: int = DEFAULT_CACHE_TTL_S,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        redis_client: Any | None = None,
        skill_index_fn: Callable[[], str] | None = None,
    ) -> None:
        self._llm = llm
        self._cache_ttl_s = int(cache_ttl_s)
        self._timeout_s = float(timeout_ms) / 1000.0
        self._redis = redis_client
        self._skill_index_fn = skill_index_fn

        self._skill_index_cache: str | None = None
        self._skill_index_cached_at: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def classify(
        self,
        message: str,
        *,
        hot_block: str = "",
        history: list[str] | None = None,
        user_id: str = "anonymous",
        last_assistant_sha: str = "",
    ) -> RouterDecision:
        """Classify ``message`` and return a valid :class:`RouterDecision`.

        This coroutine never raises — every exceptional path resolves
        to :meth:`RouterDecision.fallback_executor` so the gateway can
        proceed with full-toolset executor assembly.
        """
        t0 = time.perf_counter()
        chosen_path = "unknown"
        cache_key = _cache_key(message, user_id or "anonymous", last_assistant_sha or "")

        with tracer.start_as_current_span("router_llm.classify") as span:
            span.set_attribute("user_id", user_id or "anonymous")

            # ---- Cache lookup ---------------------------------------------------
            cached = await self._cache_get(cache_key)
            if cached is not None:
                _inc_path("cache")
                chosen_path = "cache"
                cached = promote_if_ops_keyword(cached, message)
                cached = _clamp_confidence(cached)
                span.set_attribute("chosen_path", chosen_path)
                span.set_attribute("confidence", cached.confidence)
                span.set_attribute(
                    "latency_ms", (time.perf_counter() - t0) * 1000.0
                )
                return cached

            # ---- Build messages -----------------------------------------------
            try:
                messages = self._build_messages(
                    message=message,
                    hot_block=hot_block,
                    history=history,
                )
            except Exception:
                logger.exception("router: failed to build messages")
                decision = RouterDecision.fallback_executor("prompt_build_error")
                _inc_path("fallback_executor")
                chosen_path = "fallback_executor"
                # No cache write — we'd just poison the cache with a fallback.
                span.set_attribute("chosen_path", chosen_path)
                span.set_attribute("confidence", decision.confidence)
                span.set_attribute(
                    "latency_ms", (time.perf_counter() - t0) * 1000.0
                )
                return decision

            # ---- Tier 1: function calling --------------------------------------
            timed_out = False
            decision: RouterDecision | None = None
            try:
                decision = await asyncio.wait_for(
                    self._classify_via_function_calling(messages),
                    timeout=self._timeout_s,
                )
                if decision is not None:
                    _inc_path("function_calling")
                    chosen_path = "function_calling"
            except asyncio.TimeoutError:
                _inc_timeout()
                timed_out = True
                logger.debug("router: function_calling timed out")
            except Exception:
                # Non-timeout error (ToolChoiceNotSupported, ProviderSchemaError,
                # ValidationError, parse error) — fall through to Tier 2.
                logger.debug(
                    "router: function_calling failed, trying json_mode",
                    exc_info=True,
                )

            # ---- Tier 2: JSON mode (skipped if Tier 1 timed out) ---------------
            if decision is None and not timed_out:
                try:
                    decision = await asyncio.wait_for(
                        self._classify_via_json_mode(messages),
                        timeout=self._timeout_s,
                    )
                    if decision is not None:
                        _inc_path("json_mode")
                        chosen_path = "json_mode"
                except asyncio.TimeoutError:
                    _inc_timeout()
                    logger.debug("router: json_mode timed out")
                except Exception:
                    logger.debug(
                        "router: json_mode failed, falling back", exc_info=True
                    )

            # ---- Tier 3: fallback executor -------------------------------------
            if decision is None:
                reason = "timeout" if timed_out else "parse_error"
                decision = RouterDecision.fallback_executor(reason)
                _inc_path("fallback_executor")
                chosen_path = "fallback_executor"

            # ---- Post-validation ----------------------------------------------
            decision = promote_if_ops_keyword(decision, message)
            decision = _clamp_confidence(decision)

            # ---- Cache write (non-fallback decisions only) ---------------------
            if chosen_path in ("function_calling", "json_mode"):
                await self._cache_set(cache_key, decision)

            span.set_attribute("chosen_path", chosen_path)
            span.set_attribute("confidence", decision.confidence)
            span.set_attribute(
                "latency_ms", (time.perf_counter() - t0) * 1000.0
            )
            return decision

    # ------------------------------------------------------------------
    # Tier implementations
    # ------------------------------------------------------------------

    async def _classify_via_function_calling(
        self, messages: list[Any]
    ) -> RouterDecision | None:
        """Tier 1: ``llm.bind_tools(..., tool_choice=...).ainvoke``."""
        llm = await self._get_llm()
        if llm is None:
            return None
        # ``bind_tools`` returns a Runnable; errors are caught by the caller.
        bound = llm.bind_tools(
            [RouterDecisionTool],
            tool_choice={"type": "tool", "name": "decide"},
        )
        response = await bound.ainvoke(messages)
        return _parse_tool_call(response)

    async def _classify_via_json_mode(
        self, messages: list[Any]
    ) -> RouterDecision | None:
        """Tier 2: ``llm.with_structured_output(..., method='json_mode')``."""
        llm = await self._get_llm()
        if llm is None:
            return None
        structured = llm.with_structured_output(RouterDecision, method="json_mode")
        result = await structured.ainvoke(messages)
        if isinstance(result, RouterDecision):
            return result
        if isinstance(result, dict):
            try:
                return RouterDecision(**result)
            except Exception:
                return None
        return None

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        *,
        message: str,
        hot_block: str,
        history: list[str] | None,
    ) -> list[Any]:
        """Assemble the [SystemMessage, HumanMessage] pair for the LLM."""
        from langchain_core.messages import HumanMessage, SystemMessage

        skill_index = self._get_skill_index()
        body = self._render_router_context(
            hot_block=hot_block,
            history=history,
            skills_index=skill_index,
            message=message,
        )
        return [SystemMessage(content=ROUTER_SYSTEM_PROMPT), HumanMessage(content=body)]

    def _render_router_context(
        self,
        *,
        hot_block: str,
        history: list[str] | None,
        skills_index: str,
        message: str,
    ) -> str:
        """Render the user-facing prompt body (truncation-aware)."""
        parts: list[str] = []

        if hot_block:
            parts.append("[最近摘要]")
            parts.append(_truncate(hot_block, HOT_BLOCK_MAX_CHARS))

        tail = _tail_lines(history, HISTORY_TAIL_LINES)
        if tail:
            parts.append("[最近对话]")
            parts.extend(tail)

        if skills_index:
            parts.append("[工具索引]")
            # Cap at 40 non-empty lines.
            lines = [
                ln for ln in skills_index.splitlines() if ln.strip()
            ][:SKILLS_INDEX_MAX_LINES]
            parts.append("\n".join(lines))

        parts.append("[USER]")
        parts.append(message or "")
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Skill-index cache
    # ------------------------------------------------------------------

    def _get_skill_index(self) -> str:
        """Return the compact skill index, memoised for 60 s."""
        now = time.monotonic()
        if (
            self._skill_index_cache is not None
            and (now - self._skill_index_cached_at) < SKILL_INDEX_CACHE_TTL_S
        ):
            return self._skill_index_cache
        try:
            fn = self._skill_index_fn or _default_skill_index_fn
            value = fn() or ""
        except Exception:
            logger.debug("router: skill index lookup failed", exc_info=True)
            value = ""
        self._skill_index_cache = value
        self._skill_index_cached_at = now
        return value

    # ------------------------------------------------------------------
    # Redis cache
    # ------------------------------------------------------------------

    async def _cache_get(self, key: str) -> RouterDecision | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(key)
        except Exception:
            logger.debug("router: redis GET failed", exc_info=True)
            return None
        if not raw:
            return None
        try:
            payload = raw if isinstance(raw, str) else raw.decode("utf-8")
            data = json.loads(payload)
            return RouterDecision(**data)
        except Exception:
            logger.debug("router: cache decode failed", exc_info=True)
            return None

    async def _cache_set(self, key: str, decision: RouterDecision) -> None:
        if self._redis is None:
            return
        try:
            payload = json.dumps(decision.model_dump(mode="json"))
            await self._redis.set(key, payload, ex=self._cache_ttl_s)
        except Exception:
            logger.debug("router: redis SET failed", exc_info=True)

    # ------------------------------------------------------------------
    # LLM resolution
    # ------------------------------------------------------------------

    async def _get_llm(self) -> Any | None:
        if self._llm is not None:
            return self._llm
        try:
            from src.core.model_factory import get_default_model

            self._llm = await get_default_model()
        except Exception:
            logger.debug("router: default model unavailable", exc_info=True)
            return None
        return self._llm


# ---------------------------------------------------------------------------
# Tool-call parsing
# ---------------------------------------------------------------------------


def _parse_tool_call(response: Any) -> RouterDecision | None:
    """Extract a :class:`RouterDecision` from a ``bind_tools`` response.

    LangChain exposes tool calls on :class:`~langchain_core.messages.AIMessage`
    via the ``tool_calls`` attribute (list of ``{name, args, id}``). We
    treat any of (a) missing attribute, (b) empty list, (c) wrong-shaped
    args, (d) Pydantic validation failure as a "parse error" and return
    ``None`` so the caller falls through to Tier 2.
    """
    if response is None:
        return None
    tool_calls = getattr(response, "tool_calls", None) or []
    if not tool_calls:
        return None
    call = tool_calls[0]
    args = _extract_args(call)
    if not isinstance(args, dict):
        return None
    try:
        return RouterDecision(**args)
    except Exception:
        return None


def _extract_args(call: Any) -> dict[str, Any] | None:
    """Pull the ``args`` dict out of an AIMessage tool call.

    Handles both the AIMessage ``tool_calls`` shape (``{"args": {...}}``)
    and the raw OpenAI function-call shape (``{"function": {"arguments": "..."}}``).
    """
    if isinstance(call, dict):
        if "args" in call:
            args = call["args"]
        elif "arguments" in call:
            args = call["arguments"]
        elif "function" in call and isinstance(call["function"], dict):
            args = call["function"].get("arguments", {})
        else:
            return None
    else:
        args = getattr(call, "args", None) or getattr(call, "arguments", None)

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(args, dict):
        return None
    return args


# ---------------------------------------------------------------------------
# Defaults / singleton
# ---------------------------------------------------------------------------


def _default_skill_index_fn() -> str:
    """Default skill-index source — ``tool_manager.describe_skills_compact``.

    Kept as a module-level function so tests can monkey-patch a simpler
    fake without having to construct a full :class:`ToolManager`.
    """
    from src.services.tool_manager import tool_manager

    return tool_manager.describe_skills_compact()


_SINGLETON: RouterLLM | None = None
_SINGLETON_LOCK = asyncio.Lock()


async def get_router_llm() -> RouterLLM:
    """Lazily construct a shared :class:`RouterLLM` for the default path.

    The default instance wires Redis from the shared connection pool
    and resolves the router LLM lazily via
    :func:`src.core.model_factory.get_default_model`. Tests that need
    full control should instantiate :class:`RouterLLM` directly with
    injected ``llm`` / ``redis_client`` arguments.
    """
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    async with _SINGLETON_LOCK:
        if _SINGLETON is None:
            try:
                from src.core.redis import get_redis

                redis_client = await get_redis()
            except Exception:
                logger.debug("router: redis unavailable; cache disabled", exc_info=True)
                redis_client = None
            _SINGLETON = RouterLLM(redis_client=redis_client)
    return _SINGLETON


def _reset_singleton_for_tests() -> None:
    """Test-only escape hatch to drop the cached singleton."""
    global _SINGLETON
    _SINGLETON = None


__all__ = [
    "CONFIDENCE_FLOOR",
    "DEFAULT_CACHE_TTL_S",
    "DEFAULT_TIMEOUT_MS",
    "RouterLLM",
    "get_router_llm",
    "_reset_singleton_for_tests",
]
