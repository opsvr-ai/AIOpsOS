"""PBT: a flag mutation is observable through ``is_enabled`` within 2s.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 5.4 / R-7.1 / R-7.3.

The formal requirement is "within 15s" — we exercise a tighter 2s bound
here because the test service runs at a 0.5s poll interval. Going tighter
makes the test actually discriminate between a working refresh path and
one that silently stalls.

**Validates: Requirements 7.1, 7.3** — P-FF-1 (flag effective within 15s)
+ rollout_percent bucket fairness.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st
from sqlalchemy import create_engine, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.services.feature_flags import FeatureFlagService


# ---------------------------------------------------------------------------
# Skip marker
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
    pytest.mark.property,
    pytest.mark.skipif(
        not _db_available(),
        reason="PostgreSQL not available for flag propagation tests",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
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


def _sync_url() -> str:
    from src.config import settings

    return settings.sync_database_url


def _async_url() -> str:
    from src.config import settings

    return settings.database_url


# ---------------------------------------------------------------------------
# Helpers used by both PBT and fairness tests
# ---------------------------------------------------------------------------


def _sync_upsert_flag(
    key: str, enabled: bool, rollout_percent: int, data: dict | None = None
) -> None:
    """Synchronous upsert — used from hypothesis' sync body before the
    async block takes over. Uses psycopg2 to avoid entangling hypothesis
    with asyncio lifecycle.
    """
    eng = create_engine(_sync_url())
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO runtime_feature_flags (key, enabled, rollout_percent, data)
                    VALUES (:k, :e, :p, CAST(:d AS JSONB))
                    ON CONFLICT (key) DO UPDATE SET
                        enabled = EXCLUDED.enabled,
                        rollout_percent = EXCLUDED.rollout_percent,
                        data = EXCLUDED.data
                    """
                ),
                {
                    "k": key,
                    "e": enabled,
                    "p": rollout_percent,
                    "d": "{}"
                    if data is None
                    else _json_dumps(data),
                },
            )
    finally:
        eng.dispose()


def _sync_delete_flag(key: str) -> None:
    eng = create_engine(_sync_url())
    try:
        with eng.begin() as conn:
            conn.execute(
                text("DELETE FROM runtime_feature_flags WHERE key = :k"),
                {"k": key},
            )
    finally:
        eng.dispose()


def _json_dumps(value: dict) -> str:
    import json

    return json.dumps(value, default=str)


@pytest_asyncio.fixture
async def async_factory() -> AsyncGenerator[async_sessionmaker, None]:
    engine = create_async_engine(
        _async_url(),
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


async def _async_upsert(
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


async def _async_delete(factory: async_sessionmaker, key: str) -> None:
    from src.models.runtime_flag import RuntimeFeatureFlag

    async with factory() as session:
        await session.execute(
            delete(RuntimeFeatureFlag).where(RuntimeFeatureFlag.key == key)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Non-PBT fairness test (bucket distribution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollout_50_bucket_fairness_over_2000_users(
    async_factory: async_sessionmaker,
) -> None:
    """Set rollout=50%, sample 2000 random user ids, expect [900, 1100].

    That's a ±10% band around 1000 — R-7.3 requires ±5% on 24h scale,
    but a 2000-sample snapshot has natural ±3% noise so we widen the
    band here to avoid flakes while still catching a broken hash.
    """
    key = f"fairness_{uuid.uuid4().hex[:8]}"
    try:
        await _async_upsert(async_factory, key, enabled=True, rollout_percent=50)
        svc = FeatureFlagService(session_factory=async_factory, refresh_interval_s=0.5)
        await svc.start()
        try:
            hits = sum(
                1 for _ in range(2000)
                if svc.is_enabled(key, user_id=str(uuid.uuid4()))
            )
            assert 900 <= hits <= 1100, f"expected ~1000±100 hits, got {hits}"
        finally:
            await svc.stop()
    finally:
        await _async_delete(async_factory, key)


# ---------------------------------------------------------------------------
# PBT: flag mutation converges within 2s
# ---------------------------------------------------------------------------


@hsettings(
    max_examples=8,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    enabled=st.booleans(),
    rollout=st.integers(min_value=0, max_value=100),
)
def test_flag_mutation_visible_within_2s(enabled: bool, rollout: int) -> None:
    """P-FF-1: DB mutation → ``is_enabled`` reflects new value within 2s.

    **Validates: Requirements 7.1, 7.3**.
    """

    async def _run() -> None:
        engine = create_async_engine(
            _async_url(),
            echo=False,
            pool_size=2,
            max_overflow=0,
            pool_pre_ping=False,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        key = f"propagation_{uuid.uuid4().hex[:8]}"
        user = "stable-user-001"
        try:
            # Start with the flag fully OFF so we have a well-defined baseline.
            _sync_upsert_flag(key, enabled=False, rollout_percent=0)
            svc = FeatureFlagService(session_factory=factory, refresh_interval_s=0.5)
            await svc.start()
            try:
                # Baseline: False.
                assert svc.is_enabled(key, user_id=user) is False

                # Mutate to the hypothesis-chosen state.
                _sync_upsert_flag(key, enabled=enabled, rollout_percent=rollout)

                # Determine the expected decision from the canonical rules.
                expected = _expected_enabled(enabled, rollout, user, key)

                # Poll for convergence.
                t0 = time.perf_counter()
                while time.perf_counter() - t0 < 2.0:
                    if svc.is_enabled(key, user_id=user) == expected:
                        break
                    await asyncio.sleep(0.05)

                # Final assertion — must match within the budget.
                assert svc.is_enabled(key, user_id=user) == expected
            finally:
                await svc.stop()
        finally:
            _sync_delete_flag(key)
            await engine.dispose()

    asyncio.run(_run())


def _expected_enabled(enabled: bool, rollout: int, user: str, key: str) -> bool:
    """Reference implementation of the decision rules from the service."""
    if not enabled:
        return False
    if rollout >= 100:
        return True
    if rollout <= 0:
        return False
    import xxhash

    bucket = xxhash.xxh3_64(f"{user}:{key}".encode("utf-8")).intdigest() % 100
    return bucket < rollout
