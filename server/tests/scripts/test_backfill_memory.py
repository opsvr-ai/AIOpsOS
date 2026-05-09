"""Integration smoke test for ``scripts/backfill_memory_hash_and_embedding``.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 9.2 / R-9.2.

Inserts ten ``agent_memories`` rows without ``content_hash`` or
``embedding``, runs the backfill with a tiny ``--batch-size`` (3) so
pagination is exercised, and asserts every row ends up with both
columns populated.

The DB is real (so the HNSW index is real); only the embedding provider
is stubbed to avoid network.
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncGenerator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from scripts.backfill_memory_hash_and_embedding import run as run_backfill
from src.models.knowledge import AgentMemory
from src.models.session import Session
from src.models.user import User
from src.services.memory.embedding import EmbeddingService


# ---------------------------------------------------------------------------
# DB gate
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
        reason="PostgreSQL not available for backfill smoke test",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _ensure_schema() -> None:
    """Bootstrap the dev DB schema + required extensions (idempotent)."""
    from sqlalchemy import text as _text

    from src.config import settings as _settings

    admin_eng = create_engine(_settings.sync_database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_eng.connect() as conn:
            conn.execute(_text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
            conn.execute(_text('CREATE EXTENSION IF NOT EXISTS "vector"'))
    finally:
        admin_eng.dispose()

    import src.models  # noqa: F401
    from src.models.base import Base

    engine = create_async_engine(_settings.database_url, pool_pre_ping=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            await conn.execute(
                _text(
                    "ALTER TABLE agent_memories "
                    "ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64), "
                    "ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT false, "
                    "ADD COLUMN IF NOT EXISTS superseded_by UUID, "
                    "ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false, "
                    "ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ"
                )
            )
            await conn.execute(
                _text(
                    "ALTER TABLE sessions "
                    "ADD COLUMN IF NOT EXISTS last_consolidation_at TIMESTAMPTZ, "
                    "ADD COLUMN IF NOT EXISTS consolidation_count INT NOT NULL DEFAULT 0, "
                    "ADD COLUMN IF NOT EXISTS hot_memory_version INT NOT NULL DEFAULT 0"
                )
            )
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def async_factory() -> AsyncGenerator[async_sessionmaker, None]:
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


@pytest_asyncio.fixture
async def seeded(async_factory: async_sessionmaker):
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    memory_ids: list[uuid.UUID] = []

    async with async_factory() as session:
        session.add(
            User(
                id=user_id,
                username=f"backfill_test_{user_id.hex[:8]}",
                email=f"backfill_{user_id.hex[:8]}@test.local",
                hashed_password="x" * 64,
                is_active=True,
            )
        )
        await session.commit()

    async with async_factory() as session:
        session.add(Session(id=session_id, user_id=user_id, status="active"))
        await session.commit()

    async with async_factory() as session:
        for i in range(10):
            mid = uuid.uuid4()
            memory_ids.append(mid)
            session.add(
                AgentMemory(
                    id=mid,
                    session_id=session_id,
                    user_id=user_id,
                    memory_type="fact",
                    content=f"backfill-sample-{i} operational detail",
                    embedding=None,
                    scope="personal",
                    title=f"m{i}",
                    tags=[],
                    content_hash=None,
                    is_archived=False,
                    pinned=False,
                )
            )
        await session.commit()

    try:
        yield {
            "user_id": user_id,
            "session_id": session_id,
            "memory_ids": memory_ids,
        }
    finally:
        async with async_factory() as session:
            await session.execute(
                delete(AgentMemory).where(AgentMemory.id.in_(memory_ids))
            )
            await session.execute(delete(Session).where(Session.id == session_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_fills_hash_and_embedding(
    async_factory: async_sessionmaker, seeded: dict
) -> None:
    # Stub provider emits a stable 1536-dim vector per content.
    async def prov(texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7)] + [0.1] * 1535 for t in texts]

    svc = EmbeddingService(
        api_key="stub",
        model="stub-model",
        dim=1536,
        batch_window_ms=5,
        max_batch=16,
        cache_ttl_s=60,
        provider=prov,
        redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
    )

    report = await run_backfill(
        dry_run=False,
        batch_size=3,
        limit=None,
        resume=False,
        embedding_service=svc,
        db_factory=async_factory,
        redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    assert report.processed >= 10
    assert report.embedded >= 10
    assert report.failed == 0

    # Re-read the seeded rows and assert both columns are populated.
    async with async_factory() as session:
        rows = (
            await session.execute(
                select(AgentMemory)
                .where(AgentMemory.id.in_(seeded["memory_ids"]))
                .order_by(AgentMemory.id)
            )
        ).scalars().all()
    assert len(rows) == 10
    for r in rows:
        assert r.content_hash == hashlib.sha256(r.content.encode()).hexdigest()
        assert r.embedding is not None
        # pgvector returns a list (or numpy array); sanity check length.
        assert len(list(r.embedding)) == 1536
