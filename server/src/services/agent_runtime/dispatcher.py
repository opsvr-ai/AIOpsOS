"""ToolDispatcher — parallel-safe tool execution with result cache + approval.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 16.2 /
Requirements R-1.7, R-1.8, R-8.3.

Responsibilities (see design.md § "ToolDispatcher 并行策略"):

1. Partition a batch of :class:`ToolCall` objects by their
   :mod:`tool_manager` safety class (``parallel-safe`` / ``sequential``
   / ``destructive``) while preserving input ordering within each
   partition so the returned :class:`ToolCallResult` list lines up
   positionally with the input.
2. **Destructive** calls are gated through
   :data:`interrupt_manager.interrupt_manager` — no session ⇒ immediate
   ``REJECTED``; explicit rejection / timeout also ⇒ ``REJECTED``.
3. **Parallel-safe** calls hit a Redis result cache keyed by
   ``tool:result:{name}:{sha256(canonical_args)}`` with a 60 s TTL.
   Cache misses fan out via :func:`asyncio.gather` and only successful
   outputs are written back to Redis.
4. **Sequential** calls run serially; the cache is never consulted.
5. Every invocation emits a ``tool.{name}`` OpenTelemetry span and
   observes :data:`agent_turn_latency_ms` under ``stage="tool"`` so
   downstream dashboards get the expected histogram shape.

Public API:

* :class:`ToolCall` / :class:`ToolCallResult` / :class:`ToolCallStatus`
* :class:`ToolDispatcher` (single `dispatch_batch` entrypoint).
* :func:`cache_key_for` / :func:`canonical_args_sha` — exposed because
  other subsystems (PBTs, the executor wrapper) need to reason about
  cache keys without reaching into the dispatcher's internals.

``dispatch_batch`` never raises: per-call failures are reported as
``ToolCallStatus.ERROR`` results so the executor loop can continue
processing the remaining calls in the batch.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.core.metrics import agent_turn_latency_ms, tool_dispatch_total
from src.core.redis import cache_get, cache_set
from src.core.tracing import tracer
from src.services.interrupt_manager import interrupt_manager as _default_interrupt_manager
from src.services.tool_manager import (
    DESTRUCTIVE,
    SAFE_PARALLEL,
    SEQUENTIAL,
    tool_manager as _default_tool_manager,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

DEFAULT_RESULT_CACHE_TTL_S: int = 60
DEFAULT_APPROVAL_TIMEOUT_S: float = 300.0


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


class ToolCallStatus(str, Enum):
    """Outcome states for a single dispatched tool call."""

    OK = "ok"
    CACHED = "cached"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass(slots=True)
class ToolCall:
    """One tool invocation request from the executor.

    ``call_id`` is supplied by the caller (e.g. the LangChain tool_call
    id) and round-tripped through :class:`ToolCallResult` so callers
    can correlate results regardless of partition ordering.
    """

    name: str
    args: dict[str, Any]
    call_id: str


@dataclass(slots=True)
class ToolCallResult:
    """Result of dispatching a single :class:`ToolCall`.

    ``output`` is always a string and has been passed through
    ``tool_manager.apply_output_budget`` so downstream consumers can
    log / include it in prompts without re-budgeting.
    """

    call_id: str
    name: str
    status: ToolCallStatus
    output: str
    cache_hit: bool = False
    error: str | None = None
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Cache key helpers
# ---------------------------------------------------------------------------


def canonical_args_sha(args: dict[str, Any] | None) -> str:
    """Return a deterministic sha256 hex digest of ``args``.

    ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` gives a
    stable representation across insertion orders and whitespace
    variations. When ``args`` contains values that aren't
    JSON-serialisable (e.g. :class:`set`, arbitrary objects) we fall
    back to ``repr(args)`` so the function never raises — the cache
    simply won't share entries across equivalent-but-non-JSON dicts,
    which is the safer tradeoff than poisoning Redis with stale data.
    """
    if args is None:
        payload = "null"
    else:
        try:
            payload = json.dumps(
                args,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                default=_json_default,
            )
        except (TypeError, ValueError):
            payload = repr(args)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(obj: Any) -> Any:
    """Coerce common non-JSON scalars to stable JSON representations."""
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=repr)
    if isinstance(obj, tuple):
        return list(obj)
    return repr(obj)


def cache_key_for(name: str, args: dict[str, Any] | None) -> str:
    """Compose the Redis cache key documented in design.md § ToolDispatcher."""
    return f"tool:result:{name}:{canonical_args_sha(args)}"


# ---------------------------------------------------------------------------
# Helper — metric increment safety net
# ---------------------------------------------------------------------------


def _inc_dispatch(safety: str, outcome: str) -> None:
    try:
        tool_dispatch_total.labels(safety=safety, outcome=outcome).inc()
    except Exception:  # pragma: no cover — never surface metrics errors
        logger.debug("dispatcher: metric inc failed", exc_info=True)


def _observe_latency_ms(ms: float) -> None:
    try:
        agent_turn_latency_ms.labels(stage="tool", route="dispatcher").observe(ms)
    except Exception:  # pragma: no cover — defensive
        logger.debug("dispatcher: histogram observe failed", exc_info=True)


def _safe_set_attr(span: Any, key: str, value: Any) -> None:
    """Attribute-set that never raises even on no-op spans."""
    try:
        span.set_attribute(key, value)
    except Exception:  # pragma: no cover - defensive
        logger.debug("dispatcher: span attr set failed", exc_info=True)


def _coerce_output(raw: Any) -> str:
    """Normalise a tool's raw return value to a string."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (dict, list, tuple)):
        try:
            return json.dumps(raw, ensure_ascii=False, default=_json_default)
        except (TypeError, ValueError):
            return str(raw)
    return str(raw)


# ---------------------------------------------------------------------------
# ToolDispatcher
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Indexed:
    """Internal pair used to preserve input ordering across partitions."""

    idx: int
    call: ToolCall
    safety: str


class ToolDispatcher:
    """Execute a batch of tool calls with safety-aware parallelism.

    The class is intentionally stateless apart from the references it
    holds to the shared ``tool_manager`` / ``interrupt_manager``
    singletons and its Redis cache toggle. Callers are expected to
    instantiate one per process (via :func:`get_tool_dispatcher`) or
    inject test doubles directly in unit tests.
    """

    def __init__(
        self,
        *,
        tool_manager_: Any = None,
        interrupt_manager_: Any = None,
        redis_enabled: bool = True,
        approval_session_id: str | None = None,
        approval_timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S,
        result_cache_ttl_s: int = DEFAULT_RESULT_CACHE_TTL_S,
    ) -> None:
        self._tm = tool_manager_ or _default_tool_manager
        self._im = interrupt_manager_ or _default_interrupt_manager
        self._redis_enabled = bool(redis_enabled)
        self._approval_session_id = approval_session_id
        self._approval_timeout_s = float(approval_timeout_s)
        self._cache_ttl_s = int(result_cache_ttl_s)

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    async def dispatch_batch(
        self,
        calls: list[ToolCall],
        *,
        session_id: str | None = None,
    ) -> list[ToolCallResult]:
        """Dispatch ``calls`` and return results in input order.

        The algorithm:

        1. Classify each call by safety and stash ``(idx, call, safety)``
           tuples so we can scatter / gather without losing the
           positional invariant
           ``results[i].call_id == calls[i].call_id``.
        2. Destructive → approval-gated sequentially.
        3. Parallel-safe → cache lookup; misses fan out via
           :func:`asyncio.gather`; successful outputs written back.
        4. Sequential → serialised via ``await invoke`` calls.
        5. Return a single list sorted back into input order.
        """
        n = len(calls)
        results: list[ToolCallResult | None] = [None] * n
        if n == 0:
            return []

        destructive: list[_Indexed] = []
        parallel: list[_Indexed] = []
        sequential: list[_Indexed] = []

        for idx, call in enumerate(calls):
            safety = self._tm.get_safety(call.name)
            entry = _Indexed(idx=idx, call=call, safety=safety)
            if safety == DESTRUCTIVE:
                destructive.append(entry)
            elif safety == SAFE_PARALLEL:
                parallel.append(entry)
            else:
                sequential.append(entry)

        # 1. Destructive phase — serialised by design (human-in-the-loop).
        effective_session = session_id or self._approval_session_id
        for entry in destructive:
            results[entry.idx] = await self._dispatch_destructive(
                entry.call, session_id=effective_session
            )

        # 2. Parallel-safe phase.
        if parallel:
            parallel_results = await self._dispatch_parallel(parallel)
            for entry, res in zip(parallel, parallel_results, strict=True):
                results[entry.idx] = res

        # 3. Sequential phase.
        for entry in sequential:
            res = await self._invoke_one(entry.call, safety=SEQUENTIAL)
            if res.status == ToolCallStatus.ERROR:
                _inc_dispatch(SEQUENTIAL, "error")
            else:
                _inc_dispatch(SEQUENTIAL, "ok")
            results[entry.idx] = res

        # ``None`` slots would indicate a bug — fill defensively.
        for i in range(n):
            if results[i] is None:  # pragma: no cover - defensive
                results[i] = ToolCallResult(
                    call_id=calls[i].call_id,
                    name=calls[i].name,
                    status=ToolCallStatus.ERROR,
                    output="",
                    error="dispatcher_internal_skipped",
                )
        return [r for r in results if r is not None]

    # ------------------------------------------------------------------
    # Destructive path
    # ------------------------------------------------------------------

    async def _dispatch_destructive(
        self, call: ToolCall, *, session_id: str | None
    ) -> ToolCallResult:
        if session_id is None:
            _inc_dispatch(DESTRUCTIVE, "rejected")
            return ToolCallResult(
                call_id=call.call_id,
                name=call.name,
                status=ToolCallStatus.REJECTED,
                output="",
                error="no_session_for_approval",
            )

        try:
            interrupt = self._im.create(
                session_id,
                "approval",
                {
                    "action": f"call {call.name}",
                    "args": call.args,
                    "risk_level": "high",
                },
            )
        except Exception as exc:
            logger.debug("dispatcher: interrupt_manager.create failed", exc_info=True)
            _inc_dispatch(DESTRUCTIVE, "error")
            return ToolCallResult(
                call_id=call.call_id,
                name=call.name,
                status=ToolCallStatus.ERROR,
                output="",
                error=f"approval_setup_error: {exc}",
            )

        try:
            response = await interrupt.wait(timeout=self._approval_timeout_s)
        except Exception as exc:
            logger.debug("dispatcher: interrupt.wait raised", exc_info=True)
            _inc_dispatch(DESTRUCTIVE, "error")
            return ToolCallResult(
                call_id=call.call_id,
                name=call.name,
                status=ToolCallStatus.ERROR,
                output="",
                error=f"approval_wait_error: {exc}",
            )

        if response is None:
            _inc_dispatch(DESTRUCTIVE, "rejected")
            return ToolCallResult(
                call_id=call.call_id,
                name=call.name,
                status=ToolCallStatus.REJECTED,
                output="",
                error="approval_timeout",
            )

        if _is_rejection(response):
            _inc_dispatch(DESTRUCTIVE, "rejected")
            return ToolCallResult(
                call_id=call.call_id,
                name=call.name,
                status=ToolCallStatus.REJECTED,
                output="",
                error="approval_rejected",
            )

        # Approved — invoke the tool.
        result = await self._invoke_one(call, safety=DESTRUCTIVE)
        if result.status == ToolCallStatus.ERROR:
            _inc_dispatch(DESTRUCTIVE, "error")
        else:
            _inc_dispatch(DESTRUCTIVE, "ok")
        return result

    # ------------------------------------------------------------------
    # Parallel-safe path
    # ------------------------------------------------------------------

    async def _dispatch_parallel(
        self, entries: list[_Indexed]
    ) -> list[ToolCallResult]:
        """Run parallel-safe calls, consulting + populating Redis cache."""
        results: list[ToolCallResult | None] = [None] * len(entries)
        pending: list[tuple[int, _Indexed, str]] = []  # (local_idx, entry, key)

        for local_idx, entry in enumerate(entries):
            key = cache_key_for(entry.call.name, entry.call.args)
            cached = await self._cache_lookup(key)
            if cached is not None:
                _inc_dispatch(SAFE_PARALLEL, "cached")
                results[local_idx] = ToolCallResult(
                    call_id=entry.call.call_id,
                    name=entry.call.name,
                    status=ToolCallStatus.CACHED,
                    output=str(cached.get("output", "")),
                    cache_hit=True,
                )
            else:
                pending.append((local_idx, entry, key))

        if pending:
            coroutines = [
                self._invoke_one(entry.call, safety=SAFE_PARALLEL)
                for (_, entry, _) in pending
            ]
            invocations = await asyncio.gather(*coroutines, return_exceptions=False)

            for (local_idx, entry, key), res in zip(pending, invocations, strict=True):
                results[local_idx] = res
                if res.status == ToolCallStatus.ERROR:
                    _inc_dispatch(SAFE_PARALLEL, "error")
                    continue
                _inc_dispatch(SAFE_PARALLEL, "ok")
                await self._cache_store(key, res)

        # defensive — should always be filled
        return [r for r in results if r is not None]

    async def _cache_lookup(self, key: str) -> dict[str, Any] | None:
        if not self._redis_enabled:
            return None
        try:
            raw = await cache_get(key)
        except Exception:
            logger.debug("dispatcher: cache_get failed", exc_info=True)
            return None
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        # Non-dict payload is unexpected — treat as miss rather than crash.
        return None

    async def _cache_store(self, key: str, result: ToolCallResult) -> None:
        if not self._redis_enabled:
            return
        try:
            await cache_set(key, {"output": result.output}, ttl=self._cache_ttl_s)
        except Exception:
            logger.debug("dispatcher: cache_set failed", exc_info=True)

    # ------------------------------------------------------------------
    # Single-call invoke
    # ------------------------------------------------------------------

    async def _invoke_one(
        self,
        call: ToolCall,
        *,
        safety: str,
    ) -> ToolCallResult:
        """Invoke one tool and package the result.

        Instruments every invocation with an OTel span (``tool.{name}``)
        and observes ``agent_turn_latency_ms`` regardless of outcome
        so dashboards report real tool latency including error paths.
        """
        t0 = time.perf_counter()
        span_cm = tracer.start_as_current_span(f"tool.{call.name}")

        with span_cm as span:
            _safe_set_attr(span, "tool.name", call.name)
            _safe_set_attr(span, "safety", safety)
            _safe_set_attr(span, "cache_hit", False)
            _safe_set_attr(span, "call_id", call.call_id)

            tool = self._tm.get_tool(call.name)
            if tool is None:
                latency_ms = int((time.perf_counter() - t0) * 1000.0)
                _observe_latency_ms(latency_ms)
                _safe_set_attr(span, "latency_ms", latency_ms)
                _safe_set_attr(span, "status", "error")
                return ToolCallResult(
                    call_id=call.call_id,
                    name=call.name,
                    status=ToolCallStatus.ERROR,
                    output="",
                    error="unknown_tool",
                    latency_ms=latency_ms,
                )

            try:
                raw = await _invoke_tool(tool, call.args)
            except Exception as exc:
                latency_ms = int((time.perf_counter() - t0) * 1000.0)
                _observe_latency_ms(latency_ms)
                _safe_set_attr(span, "latency_ms", latency_ms)
                _safe_set_attr(span, "status", "error")
                logger.debug(
                    "dispatcher: tool %s raised: %s", call.name, exc, exc_info=True
                )
                return ToolCallResult(
                    call_id=call.call_id,
                    name=call.name,
                    status=ToolCallStatus.ERROR,
                    output="",
                    error=str(exc),
                    latency_ms=latency_ms,
                )

            output = _coerce_output(raw)
            try:
                output = self._tm.apply_output_budget(output, call.name)
            except Exception:
                logger.debug(
                    "dispatcher: apply_output_budget failed for %s",
                    call.name,
                    exc_info=True,
                )

            latency_ms = int((time.perf_counter() - t0) * 1000.0)
            _observe_latency_ms(latency_ms)
            _safe_set_attr(span, "latency_ms", latency_ms)
            _safe_set_attr(span, "status", "ok")

            return ToolCallResult(
                call_id=call.call_id,
                name=call.name,
                status=ToolCallStatus.OK,
                output=output,
                latency_ms=latency_ms,
            )


# ---------------------------------------------------------------------------
# Helpers — tool invocation + rejection parsing
# ---------------------------------------------------------------------------


async def _invoke_tool(tool: Any, args: dict[str, Any]) -> Any:
    """Invoke a LangChain-style ``BaseTool``.

    Prefers ``ainvoke(args)`` — the canonical LangChain async entrypoint.
    Falls back to ``_arun(**args)`` when the tool either lacks
    ``ainvoke`` or raises ``NotImplementedError`` (older BaseTool
    subclasses in the codebase define ``_arun`` directly).
    """
    ainvoke = getattr(tool, "ainvoke", None)
    if callable(ainvoke):
        try:
            return await ainvoke(args)
        except NotImplementedError:
            pass

    arun = getattr(tool, "_arun", None)
    if callable(arun):
        return await arun(**(args or {}))

    # Last resort: synchronous invocation wrapped for compatibility.
    invoke = getattr(tool, "invoke", None)
    if callable(invoke):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: invoke(args))

    raise RuntimeError(f"tool {getattr(tool, 'name', '?')!r} has no invocation surface")


def _is_rejection(response: dict[str, Any]) -> bool:
    """Treat a response as rejection if it explicitly opts out.

    Matches the two shapes the interrupt UI may emit — either an
    ``approved=False`` boolean or an ``action=="reject"`` verb. Any
    other shape (including unexpected keys) is treated as approval so
    we don't accidentally block on malformed payloads once a user has
    clicked approve.
    """
    if not isinstance(response, dict):
        return False
    if response.get("approved") is False:
        return True
    if response.get("action") == "reject":
        return True
    return False


# ---------------------------------------------------------------------------
# Singleton plumbing
# ---------------------------------------------------------------------------


_DISPATCHER: ToolDispatcher | None = None


def get_tool_dispatcher() -> ToolDispatcher:
    """Return the shared :class:`ToolDispatcher` instance."""
    global _DISPATCHER
    if _DISPATCHER is None:
        _DISPATCHER = ToolDispatcher()
    return _DISPATCHER


def _reset_singleton_for_tests() -> None:
    """Drop the cached singleton — test-only escape hatch."""
    global _DISPATCHER
    _DISPATCHER = None


__all__ = [
    "DEFAULT_APPROVAL_TIMEOUT_S",
    "DEFAULT_RESULT_CACHE_TTL_S",
    "ToolCall",
    "ToolCallResult",
    "ToolCallStatus",
    "ToolDispatcher",
    "cache_key_for",
    "canonical_args_sha",
    "get_tool_dispatcher",
    "_reset_singleton_for_tests",
]
