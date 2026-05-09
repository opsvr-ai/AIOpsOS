"""Prometheus metrics definitions + ``/metrics`` router.

All metrics referenced by R-6.2 live here as module-level singletons so that
downstream modules can simply:

    from src.core.metrics import agent_turn_latency_ms, trajectory_emit_dropped

The ``metrics_router`` exposes a single ``GET /metrics`` endpoint that
returns the Prometheus text exposition format (``version=0.0.4``).
"""
from __future__ import annotations

from fastapi import APIRouter
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Shared bucket definitions
# ---------------------------------------------------------------------------

# Latency buckets in milliseconds — covers sub-10ms cache hits through
# multi-second tool calls and reflection/eval batches.
_LATENCY_BUCKETS_MS: tuple[float, ...] = (
    5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000,
)


# ---------------------------------------------------------------------------
# Agent runtime metrics
# ---------------------------------------------------------------------------

agent_turn_latency_ms: Histogram = Histogram(
    "agent_turn_latency_ms",
    "End-to-end latency per stage of an agent turn (milliseconds).",
    labelnames=("stage", "route"),
    buckets=_LATENCY_BUCKETS_MS,
)

agent_tokens_total: Counter = Counter(
    "agent_tokens_total",
    "LLM tokens consumed (input/output) per model and stage.",
    labelnames=("direction", "model", "stage"),
)


# ---------------------------------------------------------------------------
# Memory metrics
# ---------------------------------------------------------------------------

memory_recall_hit_ratio: Gauge = Gauge(
    "memory_recall_hit_ratio",
    "Hit ratio for memory recall per tier (hot/warm/cold).",
    labelnames=("tier",),
)

embedding_cache_hit_ratio: Gauge = Gauge(
    "embedding_cache_hit_ratio",
    "Hit ratio for the content-hash embedding cache.",
)


# ---------------------------------------------------------------------------
# Sleep scheduler / consolidation metrics
# ---------------------------------------------------------------------------

sleep_queue_depth: Gauge = Gauge(
    "sleep_queue_depth",
    "Current depth of the sleep-time consolidation queue (Redis ZSET).",
)

consolidation_failed_total: Counter = Counter(
    "consolidation_failed_total",
    "Total number of ConsolidationWorker invocations that failed terminally.",
)

consolidation_degraded_total: Counter = Counter(
    "consolidation_degraded_total",
    "Total number of consolidations that ran in degraded (summary-only) mode.",
)


# ---------------------------------------------------------------------------
# Wiki compiler metrics
# ---------------------------------------------------------------------------

wiki_compile_total: Counter = Counter(
    "wiki_compile_total",
    "WikiCompilerWorker invocations bucketed by outcome (ok / skipped / error).",
    labelnames=("status",),
)


# ---------------------------------------------------------------------------
# Router metrics
# ---------------------------------------------------------------------------

router_path_total: Counter = Counter(
    "router_path_total",
    "Count of router-LLM decisions bucketed by the path that produced them.",
    labelnames=("path",),
)

router_timeout_total: Counter = Counter(
    "router_timeout_total",
    "Count of router-LLM invocations that exceeded the soft 500ms budget.",
)


# ---------------------------------------------------------------------------
# Executor agent pool metrics
# ---------------------------------------------------------------------------

executor_pool_cache_total: Counter = Counter(
    "executor_pool_cache_total",
    "ExecutorAgentPool LRU cache outcomes (hit / miss / evicted).",
    labelnames=("result",),
)


# ---------------------------------------------------------------------------
# Tool dispatcher metrics
# ---------------------------------------------------------------------------

tool_dispatch_total: Counter = Counter(
    "tool_dispatch_total",
    "ToolDispatcher invocations bucketed by safety + outcome.",
    labelnames=("safety", "outcome"),
)


# ---------------------------------------------------------------------------
# Evolution metrics
# ---------------------------------------------------------------------------

skill_candidate_count: Gauge = Gauge(
    "skill_candidate_count",
    "Number of skill candidates currently in each lifecycle status.",
    labelnames=("status",),
)

evolution_rejected_total: Counter = Counter(
    "evolution_rejected_total",
    "Total skill/prompt/tool candidates rejected by guardrails.",
)

evolution_unsafe_prompt_total: Counter = Counter(
    "evolution_unsafe_prompt_total",
    "Total prompt patch candidates rejected by ReflectionWorker / Promoter "
    "guards (task 21.3, R-3.11 / R-3.12). Labeled by rejection reason: "
    "``forbidden_fragment`` for new_prompt containing banned phrases, "
    "``length_delta`` for new_prompt length deviating >50% from the "
    "current active prompt.",
    labelnames=("reason",),
)


# ---------------------------------------------------------------------------
# Trajectory / Kafka metrics
# ---------------------------------------------------------------------------

trajectory_emit_dropped: Counter = Counter(
    "trajectory_emit_dropped",
    "Total trajectory events dropped because the in-process queue was full.",
)

kafka_lag: Gauge = Gauge(
    "kafka_lag",
    "Current consumer-group lag per group/topic/partition.",
    labelnames=("group", "topic", "partition"),
)

kafka_dlq_rate: Gauge = Gauge(
    "kafka_dlq_rate",
    "Current DLQ growth rate (events/minute) per topic.",
    labelnames=("topic",),
)

kafka_schema_reject_total: Counter = Counter(
    "kafka_schema_reject_total",
    "Total messages rejected by KafkaSchemaRegistry validation, per topic.",
    labelnames=("topic",),
)


# ---------------------------------------------------------------------------
# /metrics HTTP surface
# ---------------------------------------------------------------------------

metrics_router = APIRouter()


@metrics_router.get("/metrics")
async def metrics_endpoint() -> Response:
    """Prometheus scrape endpoint — always returns 200 with text exposition."""
    body = generate_latest(REGISTRY)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)


__all__ = [
    "agent_turn_latency_ms",
    "agent_tokens_total",
    "memory_recall_hit_ratio",
    "embedding_cache_hit_ratio",
    "sleep_queue_depth",
    "consolidation_failed_total",
    "consolidation_degraded_total",
    "wiki_compile_total",
    "router_path_total",
    "router_timeout_total",
    "executor_pool_cache_total",
    "tool_dispatch_total",
    "skill_candidate_count",
    "evolution_rejected_total",
    "evolution_unsafe_prompt_total",
    "trajectory_emit_dropped",
    "kafka_lag",
    "kafka_dlq_rate",
    "kafka_schema_reject_total",
    "metrics_router",
]
