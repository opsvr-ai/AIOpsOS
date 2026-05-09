"""Smoke test for the ``/metrics`` Prometheus endpoint (R-6.4)."""
from __future__ import annotations

import os

# Force tracing/metrics into test mode *before* importing the app so that
# ``init_tracing`` installs a no-op provider and doesn't spam stdout.
os.environ.setdefault("TESTING", "1")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from src.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_text(client: AsyncClient) -> None:
    res = await client.get("/metrics")

    assert res.status_code == 200

    content_type = res.headers.get("content-type", "")
    assert content_type.startswith("text/plain"), content_type
    assert "version=0.0.4" in content_type, content_type

    body = res.text
    # At least one of the metrics defined in src.core.metrics must appear in
    # the exposition (a HELP/TYPE line is emitted even when the metric has
    # never been observed).
    assert "agent_turn_latency_ms" in body, body[:500]
