"""OpenTelemetry tracing bootstrap.

Idempotent initializer for OTel tracing. Safe to call multiple times —
only the first call installs providers/exporters. Subsequent calls are
no-ops.

Behavior summary:

- In test mode (``TESTING=1`` or ``settings.service_type == "testing"``) a
  no-op ``TracerProvider`` (no processors) is installed so that tests stay
  quiet and never produce console output.
- When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, an HTTP OTLP span exporter
  is wired through a ``BatchSpanProcessor``.
- When no OTLP endpoint is configured and we're not in test mode, a
  ``ConsoleSpanExporter`` is installed via ``BatchSpanProcessor`` so that
  local dev still sees spans.
- FastAPI is instrumented via ``FastAPIInstrumentor.instrument_app``.
- SQLAlchemy is globally instrumented via
  ``SQLAlchemyInstrumentor().instrument()`` (no specific engine needed —
  the global event listener picks up engines created later in the process).

Callers should use the module-level ``tracer`` for manual spans.
"""
from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from src.config import settings

logger = logging.getLogger(__name__)

_TRACER_NAME = "aiopsos"
_initialized: bool = False

# Exposed singleton tracer. Points at the global provider, which is swapped
# in on the first ``init_tracing`` call. Safe to import before init.
tracer = trace.get_tracer(_TRACER_NAME)


def _is_testing() -> bool:
    if os.environ.get("TESTING", "").strip() == "1":
        return True
    return getattr(settings, "service_type", "") == "testing"


def _build_sampler() -> ParentBased:
    try:
        ratio = float(os.environ.get("OTEL_TRACES_SAMPLER_ARG", "1.0"))
    except ValueError:
        ratio = 1.0
    ratio = max(0.0, min(1.0, ratio))
    return ParentBased(TraceIdRatioBased(ratio))


def init_tracing(app) -> None:
    """Idempotently initialize OpenTelemetry for the given FastAPI ``app``.

    Reads the following environment variables:

    - ``OTEL_EXPORTER_OTLP_ENDPOINT``: e.g. ``http://otel-collector:4318``.
      When present we push spans to the collector via HTTP OTLP.
    - ``OTEL_SERVICE_NAME`` (default ``aiopsos-server``): resource service
      name attached to every span.
    - ``OTEL_TRACES_SAMPLER_ARG`` (default ``1.0``): trace id ratio sampler
      argument in the ``[0, 1]`` range.

    When ``TESTING=1`` or ``settings.service_type == "testing"`` a no-op
    provider (no processors) is installed so tests don't see stray span
    output.
    """
    global _initialized
    if _initialized:
        return

    service_name = os.environ.get("OTEL_SERVICE_NAME", "aiopsos-server")
    resource = Resource.create({"service.name": service_name})

    testing = _is_testing()
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()

    provider = TracerProvider(resource=resource, sampler=_build_sampler())

    if testing:
        # No processors attached — spans are dropped silently.
        pass
    elif otlp_endpoint:
        try:
            # Import lazily so OTLP exporter is only required when used.
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("OTLP tracing enabled → %s", otlp_endpoint)
        except Exception:
            logger.exception(
                "Failed to configure OTLP exporter; falling back to console exporter"
            )
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    else:
        # Local dev default: stdout console exporter.
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    # Refresh the module-level tracer to bind to the new provider.
    global tracer
    tracer = trace.get_tracer(_TRACER_NAME)

    # Instrument FastAPI + SQLAlchemy (best-effort — never raise).
    try:
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    except Exception:
        logger.exception("FastAPIInstrumentor.instrument_app failed (non-fatal)")

    try:
        sqla = SQLAlchemyInstrumentor()
        # ``is_instrumented_by_opentelemetry`` is not part of the public API,
        # so we guard with try/except to stay idempotent.
        sqla.instrument(tracer_provider=provider)
    except Exception:
        # Most common reason: already instrumented (re-entry from another
        # app in the same process). Safe to ignore.
        logger.debug("SQLAlchemyInstrumentor.instrument skipped", exc_info=True)

    _initialized = True
