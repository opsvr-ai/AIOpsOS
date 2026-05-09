"""Integration test for :meth:`MemoryTier.warm_recall`.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 8.6 / R-2.8
/ R-2.5.

Flow:

1. Seed 30 ``agent_memories`` rows. Their embedding is a deterministic
   1536-dim vector derived from the first character of the content —
   matching seeds (same first char) produce near-identical vectors
   while different seeds are close-to-orthogonal.
2. Call :meth:`MemoryTier.warm_recall` with an injected stub
   :class:`EmbeddingService` that emits the same vector shape, then
   assert the top-K rows all share the query's seed character.
3. Flip embeddings off and assert the fallback path still returns
   non-empty results via the legacy ILIKE path.

The module gates itself on ``_db_available`` so the CI can skip
cleanly without PG.
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

from src.models.knowledge import AgentMemory
from src.models.session import Message, Session
from src.models.user import User
from src.services.memory.embedding import EmbeddingService
from src.services.memory.tier import HotContext, MemoryTier


# ---------------------------------------------------------------------------
# Gate
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
        reason="PostgreSQL not available for warm_recall integration test",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", autouse=True)
async def _ensure_schema() -> None:
    """Bootstrap the dev DB schema + required extensions.

    The default dev DB may be near-empty (e.g. only ``runtime_feature_flags``
    seeded). We create the full model set via ``Base.metadata.create_all``
    so the fixture rows below have somewhere to land. We deliberately do
    NOT drop tables on teardown — they're idempotent across re-runs.
    """
    from sqlalchemy import text as _text

    from src.config import settings as _settings

    # 1) Ensure pgvector + pgcrypto extensions exist.
    admin_eng = create_engine(_settings.sync_database_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_eng.connect() as conn:
            conn.execute(_text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
            conn.execute(_text('CREATE EXTENSION IF NOT EXISTS "vector"'))
    finally:
        admin_eng.dispose()

    # 2) Create every model-declared table, idempotently.
    import src.models  # noqa: F401  (registers every table on Base.metadata)
    from src.models.base import Base

    engine = create_async_engine(_settings.database_url, pool_pre_ping=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # 3) If the warm-recall columns we rely on don't exist yet (fresh DB
        # that never ran the 202605041810 migration), add them via raw DDL.
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


# Seed characters — a, b, c chosen so each cluster is well-separated.
_SEED_CHARS = ["a", "b", "c"]


def _seed_vector(seed_char: str, dim: int = 1536) -> list[float]:
    """Build a deterministic 1536-dim vector keyed by the seed char.

    Same seed → same vector. Different seeds share very little overlap
    since each perturbs a different contiguous slice of the 1536-dim
    space.
    """
    base = [0.0] * dim
    # Map seed char to a slice index (stable, reproducible).
    slot = (ord(seed_char) - ord("a")) % 8
    slice_len = dim // 8  # 192
    start = slot * slice_len
    for i in range(slice_len):
        # Small deterministic signal so similarity is well-defined.
        base[start + i] = 1.0 / (slice_len ** 0.5)
    return base


async def _ensure_user_session(
    factory: async_sessionmaker, user_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    async with factory() as session:
        existing_user = await session.execute(
            select(User).where(User.id == user_id)
        )
        if existing_user.scalar_one_or_none() is None:
            session.add(
                User(
                    id=user_id,
                    username=f"warm_recall_test_{user_id.hex[:8]}",
                    email=f"warm_recall_{user_id.hex[:8]}@test.local",
                    hashed_password="x" * 64,
                    is_active=True,
                )
            )
        existing_sess = await session.execute(
            select(Session).where(Session.id == session_id)
        )
        if existing_sess.scalar_one_or_none() is None:
            session.add(Session(id=session_id, user_id=user_id, status="active"))
        await session.commit()


async def _cleanup(
    factory: async_sessionmaker,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    memory_ids: list[uuid.UUID],
) -> None:
    async with factory() as session:
        if memory_ids:
            await session.execute(
                delete(AgentMemory).where(AgentMemory.id.in_(memory_ids))
            )
        await session.execute(delete(Message).where(Message.session_id == session_id))
        await session.execute(delete(Session).where(Session.id == session_id))
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


@pytest_asyncio.fixture
async def seeded(async_factory: async_sessionmaker):
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    await _ensure_user_session(async_factory, user_id, session_id)

    memory_ids: list[uuid.UUID] = []
    # 30 rows — 10 per seed char, all personal scope for simplicity.
    async with async_factory() as session:
        for i in range(30):
            seed = _SEED_CHARS[i % 3]
            vec = _seed_vector(seed)
            content = f"{seed}-memory-{i} important operational detail"
            mid = uuid.uuid4()
            memory_ids.append(mid)
            # Use ORM insert so column mapping handles pgvector.
            mem = AgentMemory(
                id=mid,
                session_id=session_id,
                user_id=user_id,
                memory_type="fact",
                content=content,
                embedding=vec,
                scope="personal",
                title=f"mem-{i}",
                tags=[seed],
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                is_archived=False,
                pinned=False,
            )
            session.add(mem)
        await session.commit()

    try:
        yield {
            "user_id": user_id,
            "session_id": session_id,
            "memory_ids": memory_ids,
        }
    finally:
        await _cleanup(async_factory, user_id, session_id, memory_ids)


# ---------------------------------------------------------------------------
# Stub embedding service
# ---------------------------------------------------------------------------


def _build_stub_embedding() -> EmbeddingService:
    """Stub provider whose vector is the same ``_seed_vector`` logic."""

    async def prov(texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            seed = (t.strip() or "a")[0].lower()
            if not seed.isalpha():
                seed = "a"
            out.append(_seed_vector(seed))
        return out

    return EmbeddingService(
        api_key="stub",
        model="stub-model",
        dim=1536,
        batch_window_ms=5,
        max_batch=8,
        cache_ttl_s=60,
        provider=prov,
        redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warm_recall_returns_top_k_ordered_by_score(
    async_factory: async_sessionmaker, seeded: dict
) -> None:
    tier = MemoryTier(
        embedding=_build_stub_embedding(),
        redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
        db_factory=async_factory,
    )
    ctx = HotContext(
        session_id=str(seeded["session_id"]),
        user_id=str(seeded["user_id"]),
        space_id=None,
    )

    items = await tier.warm_recall(ctx, "a-query-about-something", k=3)

    assert items, "warm_recall returned nothing for the 'a' seed"
    # The top-K rows must all belong to the "a" cluster. The stub embeds
    # "a-query-..." to the same slice-vector as any "a-memory-*" row.
    for m in items[:3]:
        assert m.content.startswith("a-"), f"unexpected row: {m.content}"
    # Scores are sorted desc.
    scores = [m.score for m in items]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_warm_recall_falls_back_to_ilike_when_embedding_disabled(
    async_factory: async_sessionmaker, seeded: dict
) -> None:
    # Disabled service → MemoryTier must use the legacy retrieve path.
    disabled = EmbeddingService(
        api_key="",
        redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
    )
    assert disabled.enabled is False

    tier = MemoryTier(
        embedding=disabled,
        redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True),
        db_factory=async_factory,
    )
    ctx = HotContext(
        session_id=str(seeded["session_id"]),
        user_id=str(seeded["user_id"]),
        space_id=None,
    )

    items = await tier.warm_recall(ctx, "b-memory", k=5)
    # ILIKE on "b-memory" matches content of every b-* row.
    assert items, "fallback ILIKE path returned nothing"
    assert all("b-" in m.content for m in items)
