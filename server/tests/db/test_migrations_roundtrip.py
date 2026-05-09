"""Round-trip tests for the agent-runtime-optimization-evolution Alembic migrations.

These tests exercise the two new revisions in isolation against a disposable
PostgreSQL database (``aiopsos_migration_test``). They verify:

* ``alembic upgrade head`` applies revisions ``202605041800`` and ``202605041810``
  cleanly against a DB stamped at their parent.
* Revision ``202605041800`` (8 new tables) is reversible.
* Revision ``202605041810`` (new ``agent_memories``/``sessions`` columns +
  HNSW vector index) is reversible.

When PostgreSQL is unreachable, the whole module is skipped gracefully —
mirroring the ``_db_available()`` pattern in ``tests/test_api_auth.py``.

Bootstrap strategy
------------------
The Alembic chain mid-points (e.g. ``012_spaces``) reference tables
(``datasources``, ``cron_jobs``) whose ``create_table`` migrations do not
exist — the production bootstrap path uses ``Base.metadata.create_all``
instead. To produce a realistic "state at the parent of our new revisions"
without re-implementing the upstream chain, each test:

1. Creates the full schema via ``Base.metadata.create_all``.
2. Drops the 8 new tables and the extended columns that our two revisions
   own (they would otherwise already exist from ``create_all``).
3. Stamps Alembic at ``0d5bb1cbc6a7`` (parent of ``202605041800``).
4. Exercises ``alembic upgrade/downgrade`` against the two revisions.

Requirements traceability: R-9.1 (migrations reversible).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ADMIN_DB = "postgres"
_TEST_DB_NAME = "aiopsos_migration_test"
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

# Revision identifiers (must match the migration files on disk).
_REV_BEFORE_TRAJECTORY = "0d5bb1cbc6a7"
_REV_TRAJECTORY = "202605041800"
_REV_MEMORY_EXTEND = "202605041810"
_REV_WIKI_COMPILE_LOG = "202605041820"
_REV_TOOL_SAFETY = "202605041830"

_NEW_TABLES_202605041800 = (
    # Order matters for teardown: FK children first.
    "kafka_topic_schemas",
    "sub_agent_prompt_versions",
    "skill_versions",
    "runtime_feature_flags",
    "eval_set_items",
    "skill_evaluations",
    "skill_candidates",
    "agent_trajectories",
)

_NEW_TABLES_202605041820 = ("wiki_compile_log",)

# Columns owned by revision 202605041830 (tool safety). Stripped during
# bootstrap so the full alembic chain can re-create them from scratch.
_NEW_TOOL_COLUMNS_202605041830 = ("safety",)

_NEW_MEMORY_COLUMNS = (
    "content_hash",
    "is_archived",
    "superseded_by",
    "pinned",
    "last_used_at",
)

_NEW_SESSION_COLUMNS = (
    "last_consolidation_at",
    "consolidation_count",
    "hot_memory_version",
)


# ---------------------------------------------------------------------------
# Availability probe + module-level marks
# ---------------------------------------------------------------------------


def _db_available() -> bool:
    """Return True when the dev PostgreSQL on localhost:5432 answers ``SELECT 1``."""
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
# Test-DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def test_db(monkeypatch):
    """Create a fresh ``aiopsos_migration_test`` DB, monkeypatch settings, drop on teardown.

    The fixture:
      1. Connects to the ``postgres`` administrative DB (autocommit) and drops
         any stale copy of the target DB before recreating it.
      2. Installs the ``pgcrypto`` and ``vector`` extensions (required by
         ``gen_random_uuid()`` and the HNSW index).
      3. Monkeypatches ``settings.database_url`` (asyncpg) and
         ``settings.sync_database_url`` (psycopg2). ``migrations/env.py``
         reads the former to build its engine.
      4. Yields the sync URL of the test DB.
      5. On teardown, terminates stragglers and drops the test DB.
    """
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
    """Build an Alembic ``Config`` targeting the test DB, with absolute paths.

    Resolves ``script_location`` + ``alembic.ini`` to absolute paths so the
    test is independent of the pytest invocation CWD, and overrides
    ``sqlalchemy.url`` in-memory (no file edits to ``alembic.ini``).
    """
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
    """Set up a DB that looks like revision ``0d5bb1cbc6a7`` — ready for our migrations.

    Steps:
      1. ``Base.metadata.create_all`` — pulls in every table/column declared by
         the model layer (this is the same path ``main.py`` uses on startup).
      2. Strip the pieces owned by revisions 202605041800/202605041810 so the
         upgrade path has a clean canvas:
           * drop 8 new tables (FK-safe via ``CASCADE``);
           * drop the self-referential FK + 5 new columns on ``agent_memories``;
           * drop 3 new columns on ``sessions``.
         (The two new indexes are declared only in the migrations, never in
         the models, so ``create_all`` never produced them.)
      3. ``alembic stamp 0d5bb1cbc6a7`` — marks the state so ``upgrade`` picks
         up from the exact parent of our first new revision.
    """
    from alembic import command

    # Import models so every table is registered on Base.metadata.
    import src.models  # noqa: F401
    from src.models.base import Base

    eng = create_engine(sync_url)
    try:
        with eng.begin() as conn:
            Base.metadata.create_all(conn)

        with eng.begin() as conn:
            # (a) Drop the 8 new tables — they come first because our migration
            # owns them outright.
            for t in _NEW_TABLES_202605041800:
                conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))

            # (a2) Drop tables owned by the Phase F revision (202605041820).
            for t in _NEW_TABLES_202605041820:
                conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))

            # (a3) Strip the Phase H (202605041830) tool-safety extensions so
            # the full upgrade chain can re-add them from scratch.
            conn.execute(
                text(
                    "ALTER TABLE tools DROP CONSTRAINT IF EXISTS ck_tools_safety"
                )
            )
            for col in _NEW_TOOL_COLUMNS_202605041830:
                conn.execute(
                    text(f"ALTER TABLE tools DROP COLUMN IF EXISTS {col}")
                )

            # (b) Undo the agent_memories extensions from 202605041810.
            conn.execute(
                text(
                    "ALTER TABLE agent_memories "
                    "DROP CONSTRAINT IF EXISTS fk_agent_memories_superseded_by"
                )
            )
            for col in _NEW_MEMORY_COLUMNS:
                conn.execute(
                    text(f"ALTER TABLE agent_memories DROP COLUMN IF EXISTS {col}")
                )

            # (c) Undo the sessions extensions from 202605041810.
            for col in _NEW_SESSION_COLUMNS:
                conn.execute(text(f"ALTER TABLE sessions DROP COLUMN IF EXISTS {col}"))
    finally:
        eng.dispose()

    # Stamp so alembic thinks the DB sits at the parent of 202605041800.
    command.stamp(alembic_cfg, _REV_BEFORE_TRAJECTORY)


# ---------------------------------------------------------------------------
# Inspection helpers
# ---------------------------------------------------------------------------


def _get_table_names(sync_url: str) -> set[str]:
    eng = create_engine(sync_url)
    try:
        with eng.connect() as conn:
            return set(inspect(conn).get_table_names())
    finally:
        eng.dispose()


def _get_column_names(sync_url: str, table: str) -> set[str]:
    eng = create_engine(sync_url)
    try:
        with eng.connect() as conn:
            return {c["name"] for c in inspect(conn).get_columns(table)}
    finally:
        eng.dispose()


def _get_index_names(sync_url: str, table: str) -> set[str]:
    """Union of ``sa.inspect().get_indexes()`` names with a raw ``pg_indexes`` fallback.

    ``get_indexes`` on SQLAlchemy 2.x may not surface HNSW / vector indexes
    because the dialect doesn't know the ``hnsw`` access method. We merge
    with a direct ``pg_indexes`` read to cover both cases (tasks.md §1.5
    index-introspection caveat).
    """
    eng = create_engine(sync_url)
    try:
        with eng.connect() as conn:
            via_inspector = {i["name"] for i in inspect(conn).get_indexes(table)}
            via_pg_indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = :t"),
                    {"t": table},
                ).fetchall()
            }
        return via_inspector | via_pg_indexes
    finally:
        eng.dispose()


def _get_column_types(sync_url: str, table: str) -> dict[str, str]:
    """Return ``{column_name: sqlalchemy_type_str}`` for sanity-checking column types."""
    eng = create_engine(sync_url)
    try:
        with eng.connect() as conn:
            return {
                c["name"]: str(c["type"]).upper()
                for c in sa.inspect(conn).get_columns(table)
            }
    finally:
        eng.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_full_upgrade_head(test_db, alembic_cfg):
    """From a parent-stamped DB, ``alembic upgrade head`` runs without error.

    Covers R-9.1 end-state: after the two new revisions land, all expected
    artifacts are present.
    """
    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)

    command.upgrade(alembic_cfg, "head")

    tables = _get_table_names(test_db)
    missing_tables = set(_NEW_TABLES_202605041800) - tables
    assert not missing_tables, f"head should create new tables; missing: {missing_tables}"

    missing_phase_f = set(_NEW_TABLES_202605041820) - tables
    assert not missing_phase_f, (
        f"head should create Phase F wiki_compile_log; missing: {missing_phase_f}"
    )

    # Phase H (202605041830) — the ``tools.safety`` column.
    tools_cols = _get_column_names(test_db, "tools")
    assert set(_NEW_TOOL_COLUMNS_202605041830).issubset(tools_cols), (
        f"head should create Phase H tools.safety; tools cols: {sorted(tools_cols)}"
    )

    mem_cols = _get_column_names(test_db, "agent_memories")
    assert set(_NEW_MEMORY_COLUMNS).issubset(mem_cols)

    session_cols = _get_column_names(test_db, "sessions")
    assert set(_NEW_SESSION_COLUMNS).issubset(session_cols)

    # Spot-check column types on agent_memories — content_hash should be
    # VARCHAR(64), is_archived/pinned BOOLEAN, last_used_at TIMESTAMP.
    mem_types = _get_column_types(test_db, "agent_memories")
    assert "VARCHAR(64)" in mem_types["content_hash"]
    assert "BOOLEAN" in mem_types["is_archived"]
    assert "BOOLEAN" in mem_types["pinned"]
    assert "TIMESTAMP" in mem_types["last_used_at"]

    mem_indexes = _get_index_names(test_db, "agent_memories")
    assert "agent_memories_active_idx" in mem_indexes
    assert "agent_memories_embed_idx" in mem_indexes


def test_roundtrip_add_trajectory_and_evolution(test_db, alembic_cfg):
    """Revision 202605041800 is reversible (up → down → up)."""
    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)

    # 1) Upgrade to the revision that adds the 8 new tables.
    command.upgrade(alembic_cfg, _REV_TRAJECTORY)

    tables_up = _get_table_names(test_db)
    missing = set(_NEW_TABLES_202605041800) - tables_up
    assert not missing, (
        f"upgrade to {_REV_TRAJECTORY} should create all 8 tables; missing: {missing}"
    )

    # 2) Downgrade to the parent — tables must vanish.
    command.downgrade(alembic_cfg, _REV_BEFORE_TRAJECTORY)

    tables_down = _get_table_names(test_db)
    leftover = set(_NEW_TABLES_202605041800) & tables_down
    assert not leftover, f"downgrade should drop new tables; found: {leftover}"

    # 3) Upgrade again — round-trip must be idempotent.
    command.upgrade(alembic_cfg, _REV_TRAJECTORY)

    tables_up_again = _get_table_names(test_db)
    missing_again = set(_NEW_TABLES_202605041800) - tables_up_again
    assert not missing_again, (
        f"re-upgrade should restore all 8 tables; missing: {missing_again}"
    )


def test_roundtrip_extend_memories_and_sessions(test_db, alembic_cfg):
    """Revision 202605041810 (new columns + indexes) is reversible."""
    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)

    # 1) Upgrade all the way to the memory-extend revision.
    command.upgrade(alembic_cfg, _REV_MEMORY_EXTEND)

    mem_cols = _get_column_names(test_db, "agent_memories")
    assert set(_NEW_MEMORY_COLUMNS).issubset(mem_cols), (
        f"expected {_NEW_MEMORY_COLUMNS} on agent_memories; "
        f"missing: {set(_NEW_MEMORY_COLUMNS) - mem_cols}"
    )

    session_cols = _get_column_names(test_db, "sessions")
    assert set(_NEW_SESSION_COLUMNS).issubset(session_cols), (
        f"expected {_NEW_SESSION_COLUMNS} on sessions; "
        f"missing: {set(_NEW_SESSION_COLUMNS) - session_cols}"
    )

    mem_indexes = _get_index_names(test_db, "agent_memories")
    assert "agent_memories_active_idx" in mem_indexes
    assert "agent_memories_embed_idx" in mem_indexes, (
        "HNSW vector index missing; present indexes: "
        f"{sorted(mem_indexes)}"
    )

    # 2) Downgrade to 202605041800 — extensions gone, base structure intact.
    command.downgrade(alembic_cfg, _REV_TRAJECTORY)

    mem_cols_down = _get_column_names(test_db, "agent_memories")
    leftover_mem = set(_NEW_MEMORY_COLUMNS) & mem_cols_down
    assert not leftover_mem, f"downgrade left columns behind: {leftover_mem}"

    session_cols_down = _get_column_names(test_db, "sessions")
    leftover_session = set(_NEW_SESSION_COLUMNS) & session_cols_down
    assert not leftover_session, f"downgrade left columns behind: {leftover_session}"

    mem_indexes_down = _get_index_names(test_db, "agent_memories")
    assert "agent_memories_active_idx" not in mem_indexes_down
    assert "agent_memories_embed_idx" not in mem_indexes_down

    # 3) Upgrade again — idempotent.
    command.upgrade(alembic_cfg, _REV_MEMORY_EXTEND)

    mem_cols_up = _get_column_names(test_db, "agent_memories")
    assert set(_NEW_MEMORY_COLUMNS).issubset(mem_cols_up)
    session_cols_up = _get_column_names(test_db, "sessions")
    assert set(_NEW_SESSION_COLUMNS).issubset(session_cols_up)
    mem_indexes_up = _get_index_names(test_db, "agent_memories")
    assert "agent_memories_active_idx" in mem_indexes_up
    assert "agent_memories_embed_idx" in mem_indexes_up



def test_roundtrip_add_wiki_compile_log(test_db, alembic_cfg):
    """Revision 202605041820 (``wiki_compile_log``) is reversible."""
    from alembic import command

    _bootstrap_at_parent(test_db, alembic_cfg)

    # 1) Upgrade all the way to the Phase F revision.
    command.upgrade(alembic_cfg, _REV_WIKI_COMPILE_LOG)

    tables_up = _get_table_names(test_db)
    assert "wiki_compile_log" in tables_up

    cols = _get_column_names(test_db, "wiki_compile_log")
    assert {"raw_path", "raw_sha256", "last_compiled_at", "wiki_path", "created_at"}.issubset(cols)

    types = _get_column_types(test_db, "wiki_compile_log")
    assert "TEXT" in types["raw_path"]
    assert "VARCHAR(64)" in types["raw_sha256"]
    assert "TIMESTAMP" in types["last_compiled_at"]
    assert "TEXT" in types["wiki_path"]

    # 2) Downgrade to 202605041810 — the Phase F table vanishes, rest intact.
    command.downgrade(alembic_cfg, _REV_MEMORY_EXTEND)

    tables_down = _get_table_names(test_db)
    assert "wiki_compile_log" not in tables_down
    # The memory-extension columns must survive the Phase F rollback.
    mem_cols_down = _get_column_names(test_db, "agent_memories")
    assert set(_NEW_MEMORY_COLUMNS).issubset(mem_cols_down)

    # 3) Upgrade again — idempotent.
    command.upgrade(alembic_cfg, _REV_WIKI_COMPILE_LOG)
    tables_up_again = _get_table_names(test_db)
    assert "wiki_compile_log" in tables_up_again
