"""Unit tests for :class:`FeatureFlagService`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 5.1 / R-7.1 – R-7.3.

Uses the dev Postgres — the service reads the ``runtime_feature_flags``
table directly; mocking it would rob the test of its value. The
``_db_available`` gate makes the module a no-op when PG is unreachable,
matching the pattern in ``tests/db/test_migrations_roundtrip.py``.

Each test uses a uuid-suffixed flag ``key`` so parallel runs can't
collide, and the module-scoped fixture tears down every row it created
even when a test fails.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.services.feature_flags import FeatureFlagService


# ---------------------------------------------------------------------------
# Skip marker — module is a no-op without PG
# ---------------------------------------------------------------------------


def _db_available() -> bool:
    from src.config import settings

    try:
        eng = create_engine(settings.sync_database_url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not _db_available(),
        reason="PostgreSQL not available for feature flag tests",
    ),
]


# ---------------------------------------------------------------------------
# Bootstrap + fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _ensure_table() -> None:
    from src.config import settings
    from src.models.runtime_flag import RuntimeFeatureFlag

    eng = create_engine(settings.sync_database_url)
    try:
        RuntimeFeatureFlag.__table__.create(bind=eng, checkfirst=True)
    finally:
        eng.dispose()


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker, None]:
    """Per-test async engine tied to the current event loop."""
    from src.config import settings

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=False,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _unique_key(prefix: str = "test_flag") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def _upsert_flag(
    factory: async_sessionmaker,
    key: str,
    *,
    enabled: bool,
    rollout_percent: int,
    data: dict | None = None,
) -> None:
    from src.models.runtime_flag import RuntimeFeatureFlag

    async with factory() as session:
        stmt = (
            pg_insert(RuntimeFeatureFlag)
            .values(
                key=key,
                enabled=enabled,
                rollout_percent=rollout_percent,
                data=dict(data or {}),
            )
            .on_conflict_do_update(
                index_elements=[RuntimeFeatureFlag.key],
                set_={
                    "enabled": enabled,
                    "rollout_percent": rollout_percent,
                    "data": dict(data or {}),
                },
            )
        )
        await session.execute(stmt)
        await session.commit()


async def _delete_flag(factory: async_sessionmaker, key: str) -> None:
    from src.models.runtime_flag import RuntimeFeatureFlag

    async with factory() as session:
        await session.execute(
            delete(RuntimeFeatureFlag).where(RuntimeFeatureFlag.key == key)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_flag_returns_false(session_factory: async_sessionmaker) -> None:
    svc = FeatureFlagService(session_factory=session_factory, refresh_interval_s=0.5)
    await svc.start()
    try:
        assert svc.is_enabled(_unique_key()) is False
        assert svc.is_enabled(_unique_key(), user_id="u1") is False
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_disabled_flag_is_always_false(session_factory: async_sessionmaker) -> None:
    key = _unique_key()
    try:
        await _upsert_flag(session_factory, key, enabled=False, rollout_percent=100)
        svc = FeatureFlagService(session_factory=session_factory, refresh_interval_s=0.5)
        await svc.start()
        try:
            assert svc.is_enabled(key) is False
            assert svc.is_enabled(key, user_id="whoever") is False
        finally:
            await svc.stop()
    finally:
        await _delete_flag(session_factory, key)


@pytest.mark.asyncio
async def test_rollout_100_is_true_for_every_user(
    session_factory: async_sessionmaker,
) -> None:
    key = _unique_key()
    try:
        await _upsert_flag(session_factory, key, enabled=True, rollout_percent=100)
        svc = FeatureFlagService(session_factory=session_factory, refresh_interval_s=0.5)
        await svc.start()
        try:
            assert svc.is_enabled(key) is True
            for uid in ("u1", "u2", "", "あいうえお", str(uuid.uuid4())):
                assert svc.is_enabled(key, user_id=uid) is True
        finally:
            await svc.stop()
    finally:
        await _delete_flag(session_factory, key)


@pytest.mark.asyncio
async def test_rollout_0_is_false_for_every_user(
    session_factory: async_sessionmaker,
) -> None:
    key = _unique_key()
    try:
        await _upsert_flag(session_factory, key, enabled=True, rollout_percent=0)
        svc = FeatureFlagService(session_factory=session_factory, refresh_interval_s=0.5)
        await svc.start()
        try:
            for uid in ("u1", "u2", str(uuid.uuid4())):
                assert svc.is_enabled(key, user_id=uid) is False
            # Anonymous also False
            assert svc.is_enabled(key) is False
        finally:
            await svc.stop()
    finally:
        await _delete_flag(session_factory, key)


@pytest.mark.asyncio
async def test_rollout_50_splits_roughly_evenly(
    session_factory: async_sessionmaker,
) -> None:
    """1000 distinct user ids at 50% — target 500 ± 100 (±10% tolerance)."""
    key = _unique_key()
    try:
        await _upsert_flag(session_factory, key, enabled=True, rollout_percent=50)
        svc = FeatureFlagService(session_factory=session_factory, refresh_interval_s=0.5)
        await svc.start()
        try:
            hits = sum(1 for i in range(1000) if svc.is_enabled(key, user_id=f"u{i}"))
            assert 400 <= hits <= 600, f"expected 50%±10% split, got {hits}/1000"
        finally:
            await svc.stop()
    finally:
        await _delete_flag(session_factory, key)


@pytest.mark.asyncio
async def test_rollout_is_stable_per_user_across_refreshes(
    session_factory: async_sessionmaker,
) -> None:
    """Same (user_id, key) must always map to the same bucket."""
    key = _unique_key()
    try:
        await _upsert_flag(session_factory, key, enabled=True, rollout_percent=37)
        svc = FeatureFlagService(session_factory=session_factory, refresh_interval_s=0.5)
        await svc.start()
        try:
            uids = [f"user_{i}" for i in range(200)]
            baseline = {u: svc.is_enabled(key, user_id=u) for u in uids}
            # Force a refresh; stability of hashing must survive it.
            await svc.refresh()
            again = {u: svc.is_enabled(key, user_id=u) for u in uids}
            assert baseline == again
        finally:
            await svc.stop()
    finally:
        await _delete_flag(session_factory, key)


@pytest.mark.asyncio
async def test_get_returns_snapshot_data(session_factory: async_sessionmaker) -> None:
    key = _unique_key()
    try:
        await _upsert_flag(
            session_factory,
            key,
            enabled=True,
            rollout_percent=42,
            data={"description": "x"},
        )
        svc = FeatureFlagService(session_factory=session_factory, refresh_interval_s=0.5)
        await svc.start()
        try:
            snap = svc.get(key)
            assert snap is not None
            assert snap.key == key
            assert snap.enabled is True
            assert snap.rollout_percent == 42
            assert snap.data == {"description": "x"}
        finally:
            await svc.stop()
    finally:
        await _delete_flag(session_factory, key)


@pytest.mark.asyncio
async def test_background_refresh_picks_up_updates(
    session_factory: async_sessionmaker,
) -> None:
    """Flip the DB row → service converges within a few poll cycles."""
    key = _unique_key()
    try:
        await _upsert_flag(session_factory, key, enabled=False, rollout_percent=0)
        svc = FeatureFlagService(session_factory=session_factory, refresh_interval_s=0.3)
        await svc.start()
        try:
            assert svc.is_enabled(key, user_id="u1") is False
            # Flip to 100% enabled.
            await _upsert_flag(session_factory, key, enabled=True, rollout_percent=100)
            # Poll for up to 2s (much less than the 15s requirement).
            t_budget = 2.0
            tick = 0.05
            elapsed = 0.0
            while elapsed < t_budget and not svc.is_enabled(key, user_id="u1"):
                await asyncio.sleep(tick)
                elapsed += tick
            assert svc.is_enabled(key, user_id="u1") is True
        finally:
            await svc.stop()
    finally:
        await _delete_flag(session_factory, key)
