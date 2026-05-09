"""Integration test: ``ensure_default_topics`` against a real broker.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.10 / R-5.1.

Exercises the end-to-end create → skip → re-create flow:

1. Best-effort delete of every default topic (so the broker starts clean).
2. Call :func:`ensure_default_topics` — assert every default topic is created
   with the expected partition count.
3. Delete one topic, call again — assert only that one is in ``created``.
4. Fixture teardown deletes the 5 default topics again (best-effort).

The module is skipped when the Kafka broker is not reachable at
``localhost:9094`` so CI without a broker remains green.
"""
from __future__ import annotations

import socket
from typing import Any

import pytest
import pytest_asyncio

from src.services.kafka.admin import KafkaAdminService
from src.services.kafka.ensure import DEFAULT_TOPICS, ensure_default_topics
from src.services.kafka.schema import KafkaSchemaRegistry


# ---------------------------------------------------------------------------
# Module-level skip: Kafka must be reachable
# ---------------------------------------------------------------------------


def _broker_available(host: str = "localhost", port: int = 9094) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.kafka,
    pytest.mark.skipif(
        not _broker_available(),
        reason="Kafka broker unreachable at localhost:9094",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _best_effort_delete_defaults(admin: KafkaAdminService) -> None:
    for spec in DEFAULT_TOPICS:
        try:
            await admin.delete_topic(spec.name, confirm=True)
        except Exception:
            pass  # topic may not exist; swallow


class _RecordingRegistry:
    """Stand-in for KafkaSchemaRegistry that doesn't need a DB.

    The integration test exercises the Kafka side of :func:`ensure_default_topics`;
    the schema-registry interaction is covered separately by the schema
    registry unit tests. Pretending every schema is already present keeps the
    registry side silent without touching Postgres.
    """

    def __init__(self) -> None:
        self.register_calls: list[tuple[str, int]] = []

    async def get(self, topic: str, version: int | None = None):  # noqa: ARG002
        return object()  # truthy → "already seeded"

    async def register(self, topic: str, version: int, schema, description=None):
        self.register_calls.append((topic, version))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def clean_admin():
    # Always use the EXTERNAL listener (9094 advertised as localhost), not
    # whatever settings/.env configured (which may point at 9092 → kafka:9092,
    # unresolvable from WSL/host).
    admin = KafkaAdminService(bootstrap_servers="localhost:9094")
    await admin.start()
    try:
        await _best_effort_delete_defaults(admin)
        # Give the broker a moment to propagate deletes
        import asyncio as _asyncio

        await _asyncio.sleep(0.5)
        yield admin
    finally:
        await _best_effort_delete_defaults(admin)
        import asyncio as _asyncio

        await _asyncio.sleep(0.3)
        await admin.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_creates_all_default_topics(clean_admin: KafkaAdminService):
    registry = _RecordingRegistry()

    report = await ensure_default_topics(
        admin=clean_admin, schema_registry=registry  # type: ignore[arg-type]
    )

    assert report.ok, f"ensure reported errors: {report.errors}"
    assert set(report.created) == {spec.name for spec in DEFAULT_TOPICS}
    assert report.existing == []
    assert report.upgraded == []

    # Broker metadata is eventually consistent — poll until every default
    # topic appears (up to ~5s).
    import asyncio as _asyncio

    expected = {spec.name for spec in DEFAULT_TOPICS}
    topics: dict[str, Any] = {}
    for _ in range(25):
        topics = {t.name: t for t in await clean_admin.list_topics(include_internal=False)}
        if expected.issubset(topics.keys()):
            break
        await _asyncio.sleep(0.2)

    for spec in DEFAULT_TOPICS:
        assert spec.name in topics, f"missing {spec.name}"
        assert topics[spec.name].partitions == spec.partitions, (
            f"{spec.name} has {topics[spec.name].partitions} partitions, "
            f"expected {spec.partitions}"
        )


@pytest.mark.asyncio
async def test_ensure_is_idempotent_after_single_delete(clean_admin: KafkaAdminService):
    registry = _RecordingRegistry()

    # First run — create everything
    first = await ensure_default_topics(admin=clean_admin, schema_registry=registry)  # type: ignore[arg-type]
    assert first.ok, first.errors

    # Delete ONE topic and wait for the broker to propagate
    victim = DEFAULT_TOPICS[0].name
    await clean_admin.delete_topic(victim, confirm=True)

    import asyncio as _asyncio

    # Poll until the victim disappears (up to 5s).
    for _ in range(25):
        names = {t.name for t in await clean_admin.list_topics(include_internal=False)}
        if victim not in names:
            break
        await _asyncio.sleep(0.2)

    # Second run — only the victim should be recreated
    second = await ensure_default_topics(admin=clean_admin, schema_registry=registry)  # type: ignore[arg-type]
    assert second.ok, second.errors
    assert victim in second.created, (
        f"expected {victim} in created, got created={second.created}, existing={second.existing}"
    )
    assert len(second.created) == 1
    assert set(second.existing) == {
        spec.name for spec in DEFAULT_TOPICS if spec.name != victim
    }
