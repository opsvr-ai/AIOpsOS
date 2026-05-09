"""Tests for :mod:`src.core.instance` — instance id + TTL reaper.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 20.2 / R-3.17.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.instance import (
    ConsumerGroupTTLReaper,
    _reset_reaper_for_tests,
    instance_id,
    reset_instance_id_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    reset_instance_id_for_tests()
    _reset_reaper_for_tests()
    yield
    reset_instance_id_for_tests()
    _reset_reaper_for_tests()


def test_instance_id_is_stable_within_process() -> None:
    """Two calls in the same process return the same id."""
    first = instance_id()
    second = instance_id()
    assert first == second
    # UUIDv7 format: the 13th hex char after hyphens is "7".
    hex_only = first.replace("-", "")
    assert hex_only[12] == "7", f"expected UUIDv7, got {first}"


def test_instance_id_regenerates_after_reset() -> None:
    """``reset_instance_id_for_tests`` produces a fresh id."""
    first = instance_id()
    reset_instance_id_for_tests()
    second = instance_id()
    assert first != second


@pytest.mark.asyncio
async def test_reaper_skips_groups_with_members() -> None:
    """Groups with live members or committed offsets must not be deleted."""
    from src.services.kafka.admin import (
        ConsumerGroupDetail,
        ConsumerGroupInfo,
        MemberInfo,
        PartitionLag,
    )

    admin = AsyncMock()
    admin.list_consumer_groups.return_value = [
        ConsumerGroupInfo(group_id=f"prompt-reloader-{instance_id()}", state="Empty"),
        ConsumerGroupInfo(group_id="prompt-reloader-abandoned", state="Empty"),
        ConsumerGroupInfo(group_id="prompt-reloader-alive", state="Stable"),
        ConsumerGroupInfo(group_id="prompt-reloader-has-offsets", state="Empty"),
        ConsumerGroupInfo(group_id="other-group", state="Stable"),
    ]

    def _describe(group_id: str) -> ConsumerGroupDetail:
        if group_id == "prompt-reloader-alive":
            return ConsumerGroupDetail(
                group_id=group_id,
                state="Stable",
                members=[
                    MemberInfo(member_id="m1", client_id="c1")
                ],
            )
        if group_id == "prompt-reloader-has-offsets":
            return ConsumerGroupDetail(
                group_id=group_id,
                state="Empty",
                members=[],
                lags=[
                    PartitionLag(
                        topic="ops.agent.promotion",
                        partition=0,
                        current_offset=42,
                        end_offset=42,
                        lag=0,
                    )
                ],
            )
        # abandoned — empty in every sense
        return ConsumerGroupDetail(
            group_id=group_id, state="Empty", members=[], lags=[]
        )

    admin.describe_group.side_effect = _describe

    sync_client = MagicMock()
    sync_client.delete_consumer_groups.return_value = [
        ("prompt-reloader-abandoned", 0)
    ]

    reaper = ConsumerGroupTTLReaper(
        admin_service=admin,
        sync_admin_factory=lambda _bootstrap: sync_client,
    )

    deleted = await reaper.run_once()
    assert deleted == 1
    sync_client.delete_consumer_groups.assert_called_once_with(
        ["prompt-reloader-abandoned"]
    )


@pytest.mark.asyncio
async def test_reaper_never_deletes_its_own_group() -> None:
    """The reaper must never delete the current instance's group."""
    from src.services.kafka.admin import (
        ConsumerGroupDetail,
        ConsumerGroupInfo,
    )

    admin = AsyncMock()
    my_group = f"prompt-reloader-{instance_id()}"
    admin.list_consumer_groups.return_value = [
        ConsumerGroupInfo(group_id=my_group, state="Empty"),
    ]
    admin.describe_group.return_value = ConsumerGroupDetail(
        group_id=my_group, state="Empty", members=[], lags=[]
    )

    sync_client = MagicMock()
    reaper = ConsumerGroupTTLReaper(
        admin_service=admin,
        sync_admin_factory=lambda _bootstrap: sync_client,
    )

    deleted = await reaper.run_once()
    assert deleted == 0
    sync_client.delete_consumer_groups.assert_not_called()


@pytest.mark.asyncio
async def test_reaper_start_stop_is_idempotent() -> None:
    """Start/stop lifecycle is safe to call multiple times."""
    reaper = ConsumerGroupTTLReaper(
        interval_s=60.0,
        admin_service=AsyncMock(list_consumer_groups=AsyncMock(return_value=[])),
        sync_admin_factory=lambda _bootstrap: MagicMock(),
    )
    await reaper.start()
    await reaper.start()  # no-op
    await reaper.stop()
    await reaper.stop()  # no-op
