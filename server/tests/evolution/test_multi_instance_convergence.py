"""Integration test: P-HotReload-3 (multi-instance convergence).

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 20.4 /
R-3.15, R-3.17.

Property under test
-------------------

    ∀ 3 instances subscribed to ``ops.agent.promotion``:
        after a prompt_patch promotion event is published,
        within 5s every registry's ``get_active(sub_agent_name).id``
        equals the new version id.

Implementation (per task 20.4 deviation note)
---------------------------------------------

The spec text describes "spawn 3 uvicorn subprocesses". That shape is
brittle in the dev environment (WSL + docker-compose + asyncpg pool
warm-up). The fixture ``multi_instance_cluster`` defined in
``tests/conftest.py`` supplies a lighter-weight alternative: three
``PromptReloader`` instances in-process, each with its own UUIDv7-like
``group_id`` and its own ``SubAgentPromptRegistry``, all subscribed to
the same fresh Kafka topic on the real broker at ``localhost:9094``.

This exercises exactly the Kafka fan-out semantics the spec requires
(each instance has a distinct consumer group → every instance gets
every message) while keeping the test deterministic and fast.

If a team needs the full subprocess version, the fixture can be
swapped for a subprocess factory without changing the body below.

The test is automatically skipped when Kafka is not reachable so CI
without a broker stays green.
"""

from __future__ import annotations

import pytest

from tests.conftest import MultiInstanceCluster, kafka_available


pytestmark = [
    pytest.mark.kafka,
    pytest.mark.skipif(
        not kafka_available(),
        reason="Kafka broker unreachable at localhost:9094",
    ),
]


@pytest.mark.asyncio
async def test_three_instances_converge_after_promotion(
    multi_instance_cluster: MultiInstanceCluster,
) -> None:
    """All 3 reloaders must see the new active version within 5s (R-3.15)."""
    cluster = multi_instance_cluster

    # Seed baseline active + a candidate BEFORE starting reloaders so
    # initial ``registry.load()`` has a deterministic starting point.
    baseline = cluster.seed_initial_active("ops", "BASELINE-PROMPT")
    candidate = cluster.stage_candidate("ops", "CANDIDATE-PROMPT")

    # Load each registry from the shared repo and start the reloader.
    # Every reloader gets a distinct consumer group (fixture-assigned
    # group_id=`prompt-reloader-<instance_id>`), and consumes from
    # ``latest`` — no historical replay.
    for inst in cluster.instances:
        await inst.registry.load()
        assert inst.registry.get_active("ops").id == str(baseline.id)
        await inst.reloader.start()

    # Tiny settle so each AIOKafkaConsumer finishes subscribe + join.
    # A fresh consumer group with ``auto_offset_reset=latest`` and a
    # not-yet-produced topic can otherwise miss the first event.
    import asyncio as _asyncio

    await _asyncio.sleep(1.5)

    # Act: simulate Promoter.promote_to_active — flip DB state + publish event.
    await cluster.promote_to_active(candidate)

    # Assert convergence within 5s (R-3.15).
    converged = await cluster.wait_for_convergence(
        "ops", expected_version_id=str(candidate.id), timeout_s=5.0
    )
    if not converged:  # surface what diverged for easier debugging
        snapshot = cluster.divergence_snapshot("ops")
        raise AssertionError(
            f"Instances did not converge within 5s. "
            f"Expected {candidate.id}, got {snapshot}"
        )

    # Spot-check the prompt text on every instance too, not just the id.
    for inst in cluster.instances:
        pv = inst.registry.get_active("ops")
        assert pv.id == str(candidate.id)
        assert pv.system_prompt == "CANDIDATE-PROMPT", (
            f"instance {inst.instance_id} loaded wrong prompt text: {pv.system_prompt!r}"
        )


@pytest.mark.asyncio
async def test_repeated_promotion_is_idempotent_across_cluster(
    multi_instance_cluster: MultiInstanceCluster,
) -> None:
    """R-3.18 across the cluster: re-emitting the same event is a no-op everywhere.

    Promotes once, waits for convergence, re-emits the SAME event
    (same ``event_id``), and asserts:

    * the active version id is unchanged,
    * the registry accepts the re-delivered message without raising.
    """
    cluster = multi_instance_cluster
    baseline = cluster.seed_initial_active("ops", "B")
    candidate = cluster.stage_candidate("ops", "C")

    for inst in cluster.instances:
        await inst.registry.load()
        await inst.reloader.start()

    import asyncio as _asyncio

    await _asyncio.sleep(1.5)

    # First promotion
    await cluster.promote_to_active(candidate)
    assert await cluster.wait_for_convergence(
        "ops", expected_version_id=str(candidate.id), timeout_s=5.0
    ), cluster.divergence_snapshot("ops")

    # Re-emit the same promotion payload N times. Because the
    # ``event_id`` in promote_to_active is deterministic (derived from
    # candidate id), every registry's dedupe set recognises it.
    for _ in range(5):
        await cluster.promote_to_active(candidate)

    # Convergence must still hold; no instance should have drifted back.
    await _asyncio.sleep(0.5)
    for inst in cluster.instances:
        pv = inst.registry.get_active("ops")
        assert pv.id == str(candidate.id), (
            f"instance {inst.instance_id} drifted from {candidate.id} to {pv.id}"
        )
    # Baseline is stamped retired, per Promoter.promote_to_active
    # behaviour the fixture mimics.
    baseline_row = await cluster.repo.get_by_id(baseline.id)
    assert baseline_row is not None
    assert baseline_row.status == "retired"
