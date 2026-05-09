"""Round-trip test for Alembic revision 202605041830 (tool safety column).

Phase H / Task 16.1 of the agent-runtime-optimization-evolution spec.

Mirrors the bootstrap/teardown strategy from
``tests/db/test_migrations_roundtrip.py`` — creates a disposable PostgreSQL
database, strips the tables/columns owned by the new Phase A→H revisions
from ``Base.metadata.create_all``'s output, stamps the chain at the
parent of the trajectory revision, and then walks the full chain up to
202605041830 to exercise the new safety column.

Assertions:

* After ``alembic upgrade 202605041830`` the ``tools`` table carries a
  ``safety`` VARCHAR(16) column.
* Inserting a ``Tool`` ORM row with ``safety='destructive'`` round-trips
  cleanly.
* Inserting a ``Tool`` ORM row that omits ``safety`` lands on
  ``'sequential'`` via the ORM's ``server_default``. (The DDL-level
  server_default was dropped in ``upgrade()``, so raw SQL inserts are
  intentionally *not* covered by a DB default.)
* Inserting a row with ``safety='bogus'`` via raw SQL raises
  ``IntegrityError`` due to the check constraint.
* ``alembic downgrade 202605041820`` drops the column (and the check
  constraint) without collateral damage.

Requirements: R-1.7.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


# ---------------------------------------------------------------------------
# Constants (kept in sync with tests/db/test_migrations_roundtrip.py)
# ---------------------------------------------------------------------------

_ADMIN_DB = "postgres"
_TEST_DB_NAME = "aiopsos_migration_test_16_1"
_PG_USER = "aiopsos"
_PG_PASSWORD = "aiopsos123"
_PG_HOST = "localhost"
_PG_PORT = 5432

_ADMIN_SYNC_URL = (
    f"postgresql+psycopg2://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_ADMIN_DB}"
)
_TEST_SYNC_URL = (
    f"postgresql+psycopg2://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_TEST_DB_NAME}"
)
_TEST_ASYNC_URL = (
    f"postgresql+asyncpg://{_PG_USER}:{_PG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_TEST_DB_NAME}"
)

_REV_BEFORE_TRAJECTORY = "0d5bb1cbc6a7"
_REV_TRAJECTORY = "202605041800"
_REV_MEMORY_EXTEND = "202605041810"
_REV_WIKI_COMPILE_LOG = "202605041820"
_REV_TOOL_SAFETY = "202605041830"

_PHASE_A_TABLES = (
    "kafka_topic_schemas",
    "sub_agent_prompt_versions",
    "skill_versions",
    "runtime_feature_flags",
    "eval_set_items",
    "skill_evaluations",
    "skill_candidates",
    "agent_trajectories",
)

_PHASE_F_TABLES = ("wiki_compile_log",)

_MEMORY_COLUMNS = (
    "content_hash",
    "is_archived",
    "superseded_by",
    "pinned",
    "last_used_at",
)

_SESSION_COLUMNS = (
    "last_consolidation_at",
    "consolidation_count",
    "hot_memory_version",
)


# ---------------------------------------------------------------------------
# Availability probe + module-level marks
# ---------------------------------------------------------------------------


def _db_available() -> bool:
    try:
        eng = create_engine(_ADMIN_SYNC_URL)
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
        reason="PostgreSQL not available at localhost:5432",
    ),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_db(monkeypatch):
    """Fresh PostgreSQL database, monkeypatched into ``settings``, dropped on teardown."""
    admin_eng = create_engine(_ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")

    try:
        with admin_eng.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": _TEST_DB_NAME},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"'))
            conn.execute(text(f'CREATE DATABASE "{_TEST_DB_NAME}"'))

        test_eng = create_engine(_TEST_SYNC_URL, isolation_level="AUTOCOMMIT")
        try:
            with test_eng.connect() as conn:
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector"'))
        finally:
            test_eng.dispose()

        from src.config import settings

        monkeypatch.setattr(settings, "database_url", _TEST_ASYNC_URL)
        monkeypatch.setattr(settings, "sync_database_url", _TEST_SYNC_URL)

        yield _TEST_SYNC_URL
    finally:
        try:
            with admin_eng.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": _TEST_DB_NAME},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}"'))
        finally:
            admin_eng.dispose()


@pytest.fixture
def alembic_cfg(test_db):
    from alembic.config import Config

    server_root = Path(__file__).resolve().parents[2]
    ini_path = server_root / "alembic.ini"
    scripts_path = server_root / "migrations"

    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(scripts_path))
    cfg.set_main_option("sqlalchemy.url", _TEST_ASYNC_URL)
    return cfg


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------


def _bootstrap_at_parent(sync_url: str, alembic_cfg) -> None:
    """Stamp the DB at 0d5bb1cbc6a7 so alembic can walk the full chain.

    ``Base.metadata.create_all`` emits every model-declared table; we then
    strip the pieces that revisions 202605041800 / 202605041810 /
    202605041820 / 202605041830 own. That leaves a clean canvas for
    ``alembic upgrade`` to rebuild them via the migration chain.
    """
    from alembic import command

    import src.models  # noqa: F401
    from src.models.base import Base

    eng = create_engine(sync_url)
    try:
        with eng.begin() as conn:
            Base.metadata.create_all(conn)

        with eng.begin() as conn:
            # Drop revision-owned tables.
            for t in _PHASE_A_TABLES:
                conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))
            for t in _PHASE_F_TABLES:
                conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))

            # Undo 202605041810 extensions.
            conn.execute(
                text(
                    "ALTER TABLE agent_memories "
                    "DROP CONSTRAINT IF EXISTS fk_agent_memories_superseded_by"
                )
            )
            for col in _MEMORY_COLUMNS:
                conn.execute(
                    text(f"ALTER TABLE agent_memories DROP COLUMN IF EXISTS {col}")
                )
            for col in _SESSION_COLUMNS:
                conn.execute(text(f"ALTER TABLE sessions DROP COLUMN IF EXISTS {col}"))

            # Undo the Phase H safety column so alembic can re-add it.
            conn.execute(
                text(
                    "ALTER TABLE tools DROP CONSTRAINT IF EXISTS ck_tools_safety"
                )
            )
            conn.execute(text("ALTER TABLE tools DROP COLUMN IF EXISTS safety"))
    finally:
        eng.dispose()

    command.stamp(alembic_cfg, _REV_BEFORE_TRAJECTORY)


def _get_column_names(sync_url: str, table: str) -> set[str]:
    eng = create_engine(sync_url)
    try:
        with eng.connect() as conn:
            return {c["name"] for c in inspect(conn).get_columns(table)}
    finally:
        eng.dispose()


def _get_column_types(sync_url: str, table: str) -> dict[str, str]:
    eng = create_engine(sync_url)
    try:
        with eng.connect() as conn:
            return {
                c["name"]: str(c["type"]).upper()
                for c in sa.inspect(conn).get_columns(table)
            }
    finally:
        eng.dispose()


def _get_check_constraint_names(sync_url: str, table: str) -> set[str]:
    """Return the names of CHECK constraints on a table (pg-specific)."""
    eng = create_engine(sync_url)
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT con.conname
                      FROM pg_constraint con
                      JOIN pg_class rel ON rel.oid = con.conrelid
                     WHERE rel.relname = :t AND con.contype = 'c'
                    """
                ),
                {"t": table},
            ).fetchall()
            return {r[0] for r in rows}
    finally:
        eng.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_upgrade_adds_safety_column(test_db, alembic_cfg):
    """After ``alembic upgrade 202605041830`` the column + constraint exist."""
    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)

    command.upgrade(alembic_cfg, _REV_TOOL_SAFETY)

    cols = _get_column_names(test_db, "tools")
    assert "safety" in cols, "safety column missing after upgrade"

    types = _get_column_types(test_db, "tools")
    assert "VARCHAR(16)" in types["safety"]

    checks = _get_check_constraint_names(test_db, "tools")
    assert "ck_tools_safety" in checks, (
        f"ck_tools_safety constraint missing; present: {sorted(checks)}"
    )


def test_orm_default_lands_on_sequential(test_db, alembic_cfg):
    """Inserting via the ORM without specifying ``safety`` uses the ORM default."""
    import uuid

    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)
    command.upgrade(alembic_cfg, _REV_TOOL_SAFETY)

    from src.models.agent import Tool

    eng = create_engine(test_db)
    try:
        with eng.begin() as conn:
            tool = Tool(
                id=uuid.uuid4(),
                name="test_default_safety_tool",
                type="skill",
                description="round-trip default",
                config={},
            )
            # Flush via SQLAlchemy's Core insert so we exercise the
            # ORM-level server_default on the 'safety' column.
            conn.execute(
                sa.insert(Tool.__table__).values(
                    id=tool.id,
                    name=tool.name,
                    type=tool.type,
                    description=tool.description,
                    config=tool.config,
                )
            )

        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT safety FROM tools WHERE name = :n"),
                {"n": "test_default_safety_tool"},
            ).fetchone()
            assert row is not None, "ORM-inserted row not found"
            assert row[0] == "sequential", (
                f"expected ORM default 'sequential', got {row[0]!r}"
            )
    finally:
        eng.dispose()


def test_destructive_roundtrip(test_db, alembic_cfg):
    """Inserting with ``safety='destructive'`` round-trips verbatim."""
    import uuid

    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)
    command.upgrade(alembic_cfg, _REV_TOOL_SAFETY)

    from src.models.agent import Tool

    eng = create_engine(test_db)
    try:
        tool_id = uuid.uuid4()
        with eng.begin() as conn:
            conn.execute(
                sa.insert(Tool.__table__).values(
                    id=tool_id,
                    name="test_destructive_tool",
                    type="skill",
                    description="destructive round-trip",
                    config={},
                    safety="destructive",
                )
            )

        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT safety FROM tools WHERE id = :i"),
                {"i": str(tool_id)},
            ).fetchone()
            assert row is not None
            assert row[0] == "destructive"
    finally:
        eng.dispose()


def test_check_constraint_rejects_bogus_value(test_db, alembic_cfg):
    """Raw SQL insert with an unknown safety value hits the CHECK constraint."""
    import uuid

    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)
    command.upgrade(alembic_cfg, _REV_TOOL_SAFETY)

    eng = create_engine(test_db)
    try:
        with pytest.raises(IntegrityError):
            with eng.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO tools (id, name, type, description, "
                        "config, is_approved, is_active, safety) "
                        "VALUES (:id, :n, 'skill', '', '{}'::jsonb, false, true, 'bogus')"
                    ),
                    {"id": str(uuid.uuid4()), "n": "test_bogus_safety_tool"},
                )
    finally:
        eng.dispose()


def test_downgrade_removes_safety_column(test_db, alembic_cfg):
    """``alembic downgrade 202605041820`` removes column + check constraint."""
    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)
    command.upgrade(alembic_cfg, _REV_TOOL_SAFETY)

    # Sanity — column + constraint present.
    assert "safety" in _get_column_names(test_db, "tools")
    assert "ck_tools_safety" in _get_check_constraint_names(test_db, "tools")

    command.downgrade(alembic_cfg, _REV_WIKI_COMPILE_LOG)

    cols = _get_column_names(test_db, "tools")
    assert "safety" not in cols, f"safety column leaked after downgrade: {cols}"

    checks = _get_check_constraint_names(test_db, "tools")
    assert "ck_tools_safety" not in checks, (
        f"ck_tools_safety leaked after downgrade: {checks}"
    )

    # Round-trip: upgrade again should restore everything.
    command.upgrade(alembic_cfg, _REV_TOOL_SAFETY)
    assert "safety" in _get_column_names(test_db, "tools")
    assert "ck_tools_safety" in _get_check_constraint_names(test_db, "tools")
