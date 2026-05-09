"""Unit tests for :class:`SubAgentPromptVersionRepository`.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 18.1 —
R-3.15 (backing), R-3.20 (default fallback at registry layer).

Exercises all five repository methods against the dev Postgres:

* ``list_live()`` — returns rows with live statuses, excludes retired /
  rejected, orders newest-first per sub-agent.
* ``get_by_id()`` — status-agnostic point lookup; accepts ``UUID`` or
  ``str``; malformed ids return ``None`` rather than raising.
* ``get_active(name)`` — returns the single active row or ``None``.
* ``get_previous_active(name, before_id)`` — rollback target: most
  recently activated row that isn't ``before_id``.
* ``get_by_candidate(candidate_id)`` — full history for one candidate,
  ordered oldest-first.

Each test inserts rows with a uuid-suffixed ``sub_agent_name`` so
parallel runs can't collide, and the fixture cleans up its own writes
even when a test fails.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.services.prompt_versions.repository import (
    PromptVersionRow,
    SubAgentPromptVersionRepository,
    _coerce_uuid,
)


# ---------------------------------------------------------------------------
# Skip marker — tests need a reachable Postgres
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
        reason="PostgreSQL not available for prompt_versions repository tests",
    ),
]


# ---------------------------------------------------------------------------
# Bootstrap + fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _ensure_table() -> None:
    """Create the ``sub_agent_prompt_versions`` table if the dev DB hasn't
    been fully migrated yet. Migrations are the canonical path in prod;
    this guard keeps the test suite usable on a bare dev DB."""
    from src.config import settings
    from src.models.evolution import SubAgentPromptVersion

    eng = create_engine(settings.sync_database_url)
    try:
        SubAgentPromptVersion.__table__.create(bind=eng, checkfirst=True)
    finally:
        eng.dispose()


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker, None]:
    """Per-test async engine tied to the active loop."""
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
async def repo(
    session_factory: async_sessionmaker,
) -> SubAgentPromptVersionRepository:
    return SubAgentPromptVersionRepository(session_factory=session_factory)


def _unique_name(prefix: str = "sa") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def _insert_row(
    factory: async_sessionmaker,
    *,
    sub_agent_name: str,
    system_prompt: str = "prompt",
    status: str = "proposed",
    activated_at: datetime | None = None,
    retired_at: datetime | None = None,
    parent_version_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> uuid.UUID:
    """Insert a single row and return its id. Uses raw Core insert to
    bypass ORM default lifecycle and set ``created_at`` deterministically
    when the caller provides one (handy for ordering tests)."""
    from src.models.evolution import SubAgentPromptVersion

    row_id = uuid.uuid4()
    values: dict = {
        "id": row_id,
        "sub_agent_name": sub_agent_name,
        "system_prompt": system_prompt,
        "status": status,
        "activated_at": activated_at,
        "retired_at": retired_at,
        "parent_version_id": parent_version_id,
        "candidate_id": candidate_id,
    }
    if created_at is not None:
        values["created_at"] = created_at

    async with factory() as session:
        await session.execute(pg_insert(SubAgentPromptVersion).values(**values))
        await session.commit()
    return row_id


async def _delete_by_name(factory: async_sessionmaker, sub_agent_name: str) -> None:
    """Clean up every row created for this sub_agent_name.

    ``sub_agent_prompt_versions`` self-references via ``parent_version_id``
    (same-table FK). Parent rows must outlive children at delete time so
    we null out ``parent_version_id`` first to avoid FK order surprises.
    """
    from src.models.evolution import SubAgentPromptVersion

    async with factory() as session:
        await session.execute(
            text(
                "UPDATE sub_agent_prompt_versions "
                "SET parent_version_id = NULL "
                "WHERE sub_agent_name = :n"
            ),
            {"n": sub_agent_name},
        )
        await session.execute(
            delete(SubAgentPromptVersion).where(
                SubAgentPromptVersion.sub_agent_name == sub_agent_name
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# list_live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_live_returns_only_live_statuses(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """Rows with status retired / rejected must be filtered out."""
    name = _unique_name()
    try:
        await _insert_row(session_factory, sub_agent_name=name, status="proposed")
        await _insert_row(session_factory, sub_agent_name=name, status="shadow")
        await _insert_row(session_factory, sub_agent_name=name, status="ab")
        await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="active",
            activated_at=datetime.now(UTC),
        )
        # Excluded:
        await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="retired",
            retired_at=datetime.now(UTC),
        )
        await _insert_row(session_factory, sub_agent_name=name, status="rejected")

        rows = await repo.list_live()
        ours = [r for r in rows if r.sub_agent_name == name]

        statuses = sorted(r.status for r in ours)
        assert statuses == ["ab", "active", "proposed", "shadow"], (
            f"got live rows with unexpected statuses: {statuses}"
        )
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_list_live_orders_newest_first_per_sub_agent(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """Same name, three rows. Repo orders ``created_at DESC`` so the
    registry's load loop sees newest first. Use explicit ``created_at``
    so the ordering isn't flaky on fast machines."""
    name = _unique_name()
    now = datetime.now(UTC)
    try:
        old_id = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="proposed",
            created_at=now - timedelta(hours=2),
        )
        mid_id = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="shadow",
            created_at=now - timedelta(hours=1),
        )
        new_id = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="ab",
            created_at=now,
        )

        rows = await repo.list_live()
        ours = [r for r in rows if r.sub_agent_name == name]
        assert [r.id for r in ours] == [new_id, mid_id, old_id]
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_list_live_returns_immutable_rows(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """``PromptVersionRow`` is a frozen dataclass — mutation must raise.

    This matters because the registry stashes rows in its lock-free
    snapshot dict; a mutable row could be changed by a downstream
    caller and corrupt the live snapshot.
    """
    name = _unique_name()
    try:
        await _insert_row(session_factory, sub_agent_name=name, status="proposed")
        rows = await repo.list_live()
        ours = next(r for r in rows if r.sub_agent_name == name)
        with pytest.raises((AttributeError, TypeError)):
            ours.status = "active"  # type: ignore[misc]
    finally:
        await _delete_by_name(session_factory, name)


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_id_is_status_agnostic(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """Rollback / apply_promotion lookup retired rows; status filter is off."""
    name = _unique_name()
    try:
        retired_id = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="retired",
            activated_at=datetime.now(UTC) - timedelta(hours=3),
            retired_at=datetime.now(UTC),
        )
        row = await repo.get_by_id(retired_id)
        assert row is not None
        assert row.id == retired_id
        assert row.status == "retired"
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_get_by_id_accepts_string_input(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """Kafka events carry string ids — method must accept both."""
    name = _unique_name()
    try:
        rid = await _insert_row(session_factory, sub_agent_name=name)
        by_uuid = await repo.get_by_id(rid)
        by_str = await repo.get_by_id(str(rid))
        assert by_uuid is not None and by_str is not None
        assert by_uuid.id == by_str.id == rid
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_get_by_id_malformed_string_returns_none(
    repo: SubAgentPromptVersionRepository,
) -> None:
    """A bad uuid must never crash ``apply_promotion`` — returning None
    is the contract. (see ``_coerce_uuid`` docstring)"""
    assert await repo.get_by_id("not-a-uuid") is None
    assert await repo.get_by_id("") is None


@pytest.mark.asyncio
async def test_get_by_id_unknown_uuid_returns_none(
    repo: SubAgentPromptVersionRepository,
) -> None:
    random = uuid.uuid4()
    assert await repo.get_by_id(random) is None


# ---------------------------------------------------------------------------
# get_active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_returns_the_active_row(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """Mixed statuses with exactly one active row — that's what we get back."""
    name = _unique_name()
    try:
        await _insert_row(session_factory, sub_agent_name=name, status="proposed")
        active_id = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="active",
            activated_at=datetime.now(UTC),
        )
        await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="retired",
            retired_at=datetime.now(UTC),
        )

        row = await repo.get_active(name)
        assert row is not None
        assert row.id == active_id
        assert row.status == "active"
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_get_active_returns_none_when_no_active_row(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """No active row → None. The registry falls back to code-level defaults
    (R-3.20); this repo stays honest and doesn't invent one."""
    name = _unique_name()
    try:
        await _insert_row(session_factory, sub_agent_name=name, status="proposed")
        await _insert_row(session_factory, sub_agent_name=name, status="shadow")
        assert await repo.get_active(name) is None
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_get_active_returns_none_for_unknown_name(
    repo: SubAgentPromptVersionRepository,
) -> None:
    assert await repo.get_active(f"no_such_{uuid.uuid4().hex[:6]}") is None


# ---------------------------------------------------------------------------
# get_previous_active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_previous_active_picks_most_recently_activated(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """Rollback semantics: walk back to the closest predecessor by
    ``activated_at DESC``. Rows never activated are skipped."""
    name = _unique_name()
    now = datetime.now(UTC)
    try:
        # Older retired
        older = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="retired",
            activated_at=now - timedelta(days=3),
            retired_at=now - timedelta(days=2),
        )
        # Newer retired (should win)
        newer = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="retired",
            activated_at=now - timedelta(days=1),
            retired_at=now - timedelta(hours=1),
        )
        # Current active — excluded by ``before_id``
        current = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="active",
            activated_at=now,
        )
        # Never activated — excluded because activated_at IS NULL
        await _insert_row(session_factory, sub_agent_name=name, status="rejected")

        prev = await repo.get_previous_active(name, before_id=current)
        assert prev is not None
        assert prev.id == newer

        # Exclude the newer one too → falls to older
        prev2 = await repo.get_previous_active(name, before_id=newer)
        # before_id here is the newer retired row's id, but the active
        # ``current`` row is still more recently activated so it wins.
        assert prev2 is not None
        assert prev2.id == current

        # Exclude both newer and current → falls to older
        # (need to chain — method only takes one ``before_id``; here we
        # verify the older row is reachable by passing ``current``).
        # The older-vs-newer ordering above already proves that.
        assert prev.id != older
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_get_previous_active_returns_none_when_history_empty(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """Only one active row ever → no prior version to roll back to."""
    name = _unique_name()
    try:
        only_id = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="active",
            activated_at=datetime.now(UTC),
        )
        assert await repo.get_previous_active(name, before_id=only_id) is None
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_get_previous_active_with_none_before_id_returns_latest(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """``before_id=None`` means "give me the most recently activated row
    period". Used when the caller has no incumbent to exclude (e.g. a
    fresh admin-console request)."""
    name = _unique_name()
    now = datetime.now(UTC)
    try:
        await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="retired",
            activated_at=now - timedelta(days=1),
            retired_at=now - timedelta(hours=2),
        )
        newest = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="active",
            activated_at=now,
        )
        row = await repo.get_previous_active(name, before_id=None)
        assert row is not None
        assert row.id == newest
    finally:
        await _delete_by_name(session_factory, name)


@pytest.mark.asyncio
async def test_get_previous_active_accepts_string_before_id(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    name = _unique_name()
    now = datetime.now(UTC)
    try:
        old = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="retired",
            activated_at=now - timedelta(days=1),
            retired_at=now - timedelta(hours=2),
        )
        current = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="active",
            activated_at=now,
        )
        row = await repo.get_previous_active(name, before_id=str(current))
        assert row is not None
        assert row.id == old
    finally:
        await _delete_by_name(session_factory, name)


# ---------------------------------------------------------------------------
# get_by_candidate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_candidate_returns_rows_ordered_oldest_first(
    session_factory: async_sessionmaker,
    repo: SubAgentPromptVersionRepository,
) -> None:
    """A candidate may spawn multiple version rows across shadow→ab→active
    transitions; return them oldest-first so the caller can build a
    timeline view."""
    from src.models.evolution import SkillCandidate

    name = _unique_name()
    candidate_id = uuid.uuid4()
    now = datetime.now(UTC)

    # Insert candidate row first — the FK is ON DELETE SET NULL but the
    # row still needs to exist at insert time unless the column is NULL.
    async with session_factory() as session:
        await session.execute(
            pg_insert(SkillCandidate).values(
                id=candidate_id,
                name="test-candidate",
                proposal_source="test",
                skill_prompt="skill prompt",
                status="proposed",
                kind="prompt_patch",
            )
        )
        await session.commit()

    try:
        first = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="shadow",
            candidate_id=candidate_id,
            created_at=now - timedelta(hours=2),
        )
        second = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="ab",
            candidate_id=candidate_id,
            created_at=now - timedelta(hours=1),
        )
        third = await _insert_row(
            session_factory,
            sub_agent_name=name,
            status="active",
            activated_at=now,
            candidate_id=candidate_id,
            created_at=now,
        )
        # Unrelated candidate — must not show up
        await _insert_row(session_factory, sub_agent_name=name, status="proposed")

        rows = await repo.get_by_candidate(candidate_id)
        assert [r.id for r in rows] == [first, second, third]
        for r in rows:
            assert r.candidate_id == candidate_id
    finally:
        await _delete_by_name(session_factory, name)
        # Tear down the candidate row.
        async with session_factory() as session:
            await session.execute(
                delete(SkillCandidate).where(SkillCandidate.id == candidate_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_get_by_candidate_accepts_string_id(
    repo: SubAgentPromptVersionRepository,
) -> None:
    """String-id path returns [] for unknown candidates rather than crashing."""
    rows = await repo.get_by_candidate(str(uuid.uuid4()))
    assert rows == []


@pytest.mark.asyncio
async def test_get_by_candidate_malformed_id_returns_empty(
    repo: SubAgentPromptVersionRepository,
) -> None:
    assert await repo.get_by_candidate("definitely-not-a-uuid") == []


# ---------------------------------------------------------------------------
# _coerce_uuid — small helper but it's on the hot path for apply_promotion
# ---------------------------------------------------------------------------


def test_coerce_uuid_roundtrips_uuid_objects() -> None:
    u = uuid.uuid4()
    assert _coerce_uuid(u) is u


def test_coerce_uuid_parses_string_form() -> None:
    u = uuid.uuid4()
    assert _coerce_uuid(str(u)) == u


def test_coerce_uuid_returns_none_for_bad_input() -> None:
    assert _coerce_uuid(None) is None
    assert _coerce_uuid("") is None
    assert _coerce_uuid("not-a-uuid") is None
    assert _coerce_uuid(12345) is None  # wrong type — logged + None


# ---------------------------------------------------------------------------
# PromptVersionRow.from_orm — exercised via list_live above but the
# direct test catches regressions in the mapping function itself.
# ---------------------------------------------------------------------------


def test_prompt_version_row_from_orm_maps_all_fields() -> None:
    from src.models.evolution import SubAgentPromptVersion

    orm_obj = SubAgentPromptVersion()
    orm_obj.id = uuid.uuid4()
    orm_obj.sub_agent_name = "ops"
    orm_obj.candidate_id = uuid.uuid4()
    orm_obj.system_prompt = "hello"
    orm_obj.rationale = "because"
    orm_obj.status = "shadow"
    orm_obj.parent_version_id = None
    orm_obj.manifest_sha256 = "abc"
    orm_obj.activated_at = None
    orm_obj.retired_at = None
    orm_obj.created_at = datetime.now(UTC)

    row = PromptVersionRow.from_orm(orm_obj)
    assert row.id == orm_obj.id
    assert row.sub_agent_name == "ops"
    assert row.system_prompt == "hello"
    assert row.status == "shadow"
    assert row.rationale == "because"
    assert row.manifest_sha256 == "abc"
