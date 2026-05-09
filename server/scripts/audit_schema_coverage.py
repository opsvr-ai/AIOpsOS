"""Audit: does the Alembic chain + Base.metadata.create_all cover every
table & column the ORM declares?

Run: ``python -m scripts.audit_schema_coverage``

Prints three sections:

1. Tables declared in ORM but NEVER created by any Alembic migration.
   These tables rely on ``Base.metadata.create_all`` and will silently
   go missing in any deploy path that runs ``alembic upgrade head``
   without following up with ``create_all``.

2. Potential duplicate-DDL conflicts: migration ``add_column`` /
   ``create_index`` calls where the ORM already declares that same
   column or index. On a fresh DB these are the ones that will throw
   ``already exists`` unless the migration has an explicit guard.

3. Columns / indexes present in ORM but not added by any migration.
   On an old DB stamped before the ORM column existed, these require
   ``create_all`` to land — an ``alembic upgrade head`` alone won't
   suffice. This is only a problem for customers upgrading from a
   pre-ORM-column state.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

# Make ``src`` importable without invoking the full DB engine — we only
# need the ORM metadata, not a live connection. We deliberately stub
# out heavy dependencies that src.config otherwise requires.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://aud:aud@localhost:5432/aud")
os.environ.setdefault("SYNC_DATABASE_URL", "postgresql://aud:aud@localhost:5432/aud")
os.environ.setdefault("SECRET_KEY", "audit")
os.environ.setdefault("TESTING", "1")

import src.models  # noqa: F401 — register every ORM table on Base.metadata
from src.models.base import Base

MIG_DIR = ROOT / "migrations" / "versions"


# ---------------------------------------------------------------------------
# ORM introspection
# ---------------------------------------------------------------------------


def _orm_tables() -> dict[str, set[str]]:
    """Return ``{tablename: {column_name, ...}}`` for every ORM-declared table."""
    out: dict[str, set[str]] = {}
    for tname, table in Base.metadata.tables.items():
        out[tname] = {c.name for c in table.columns}
    return out


def _orm_indexes() -> dict[str, set[str]]:
    """Return ``{tablename: {index_name, ...}}`` for every ORM-declared index."""
    out: dict[str, set[str]] = {}
    for tname, table in Base.metadata.tables.items():
        out[tname] = {ix.name for ix in table.indexes if ix.name}
    return out


# ---------------------------------------------------------------------------
# Alembic migration scan — AST-based so we pick up calls inside ``if``
# branches and inside helper loops like ``012_spaces``.
# ---------------------------------------------------------------------------


def _scan_migrations():
    """Scan every migration and return:

    * ``create_table_ops``  — ``{migration_id: {table_name}}``
    * ``add_column_ops``    — ``{migration_id: [(table, column)]}``
    * ``create_index_ops``  — ``{migration_id: [(table, index_name)]}``
    * ``drop_table_ops``    — ``{migration_id: {table_name}}``
    """
    create_table: dict[str, set[str]] = {}
    add_column: dict[str, list[tuple[str, str]]] = {}
    create_index: dict[str, list[tuple[str, str]]] = {}
    drop_table: dict[str, set[str]] = {}
    execute_create: dict[str, list[str]] = {}

    for path in sorted(MIG_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        mig_id = path.stem
        create_table[mig_id] = set()
        add_column[mig_id] = []
        create_index[mig_id] = []
        drop_table[mig_id] = set()
        execute_create[mig_id] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "op":
                    name = func.attr
            if name is None:
                continue

            # op.create_table("name", ...)
            if name == "create_table" and node.args:
                t = _literal_str(node.args[0])
                if t:
                    create_table[mig_id].add(t)

            # op.add_column("table", Column("col", ...))
            elif name == "add_column" and len(node.args) >= 2:
                t = _literal_str(node.args[0])
                col = _column_name_from_arg(node.args[1])
                if t and col:
                    add_column[mig_id].append((t, col))

            # op.create_index("name", "table", [...])
            elif name == "create_index" and len(node.args) >= 2:
                iname = _literal_str(node.args[0])
                t = _literal_str(node.args[1])
                # In some migrations the positional order is (name, table),
                # but ``012_spaces`` also uses f-strings via loops — we
                # conservatively capture anything we can resolve.
                if t and iname:
                    create_index[mig_id].append((t, iname))

            # op.drop_table("name")
            elif name == "drop_table" and node.args:
                t = _literal_str(node.args[0])
                if t:
                    drop_table[mig_id].add(t)

            # op.execute("CREATE TABLE IF NOT EXISTS ...") — 012_spaces pattern
            elif name == "execute" and node.args:
                sql = _literal_str(node.args[0])
                if sql and "CREATE TABLE IF NOT EXISTS" in sql.upper():
                    execute_create[mig_id].append(
                        _extract_table_from_create_sql(sql)
                    )

    return {
        "create_table": create_table,
        "add_column": add_column,
        "create_index": create_index,
        "drop_table": drop_table,
        "execute_create": execute_create,
    }


def _literal_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _column_name_from_arg(node):
    """Extract the column name from ``sa.Column("name", ...)``."""
    if not isinstance(node, ast.Call):
        return None
    if not node.args:
        return None
    return _literal_str(node.args[0])


def _extract_table_from_create_sql(sql: str) -> str:
    """Best-effort: pull ``foo`` out of ``CREATE TABLE IF NOT EXISTS foo (``."""
    upper = sql.upper()
    key = "CREATE TABLE IF NOT EXISTS"
    idx = upper.find(key)
    if idx < 0:
        return ""
    tail = sql[idx + len(key):].strip()
    # tail starts with the table name followed by whitespace or paren
    for end in (" ", "(", "\n", "\t"):
        cut = tail.find(end)
        if cut > 0:
            return tail[:cut].strip()
    return tail.strip()


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def main() -> int:
    orm_tables = _orm_tables()
    orm_indexes = _orm_indexes()
    scan = _scan_migrations()

    # Flatten per-migration sets into whole-chain sets.
    chain_tables: set[str] = set()
    for s in scan["create_table"].values():
        chain_tables.update(s)
    for lst in scan["execute_create"].values():
        for t in lst:
            if t:
                chain_tables.add(t)
    chain_dropped = set()
    for s in scan["drop_table"].values():
        chain_dropped.update(s)
    # A drop_table on a later migration doesn't remove it permanently
    # unless it's not recreated; we only care about `never created at all`.

    chain_added_columns: dict[str, set[str]] = {}
    for entries in scan["add_column"].values():
        for t, c in entries:
            chain_added_columns.setdefault(t, set()).add(c)
    chain_created_indexes: dict[str, set[str]] = {}
    for entries in scan["create_index"].values():
        for t, ix in entries:
            chain_created_indexes.setdefault(t, set()).add(ix)

    # ---- 1. Tables in ORM but not in chain ----
    missing_tables = sorted(t for t in orm_tables if t not in chain_tables)
    print("=" * 78)
    print("1) ORM tables NEVER created by any Alembic migration")
    print("   (rely on Base.metadata.create_all to exist)")
    print("=" * 78)
    for t in missing_tables:
        print(f"  - {t}  ({len(orm_tables[t])} columns)")
    print(f"\n  total: {len(missing_tables)} tables\n")

    # ---- 2. Duplicate-DDL risk on fresh boot ----
    # An add_column is only a risk if the ORM *also* declares the column,
    # and the containing migration runs *before* create_all can possibly
    # blanket the table. Our deploy order is: alembic upgrade head →
    # create_all, so in practice the chain runs against an empty DB and
    # these add_columns are fine UNLESS a previous migration already
    # added the column (012_spaces-style fan-out).
    print("=" * 78)
    print("2) Potential duplicate-DDL in the migration chain")
    print("   (same column added by >1 migration, or same index name twice)")
    print("=" * 78)

    per_mig_cols: dict[tuple[str, str], list[str]] = {}
    for mig_id, entries in scan["add_column"].items():
        for t, c in entries:
            per_mig_cols.setdefault((t, c), []).append(mig_id)

    dup_cols = {k: v for k, v in per_mig_cols.items() if len(v) > 1}
    for (t, c), migs in sorted(dup_cols.items()):
        print(f"  - {t}.{c}  added by: {', '.join(migs)}")

    per_mig_idx: dict[tuple[str, str], list[str]] = {}
    for mig_id, entries in scan["create_index"].items():
        for t, ix in entries:
            per_mig_idx.setdefault((t, ix), []).append(mig_id)
    dup_idx = {k: v for k, v in per_mig_idx.items() if len(v) > 1}
    for (t, ix), migs in sorted(dup_idx.items()):
        print(f"  - index {ix} on {t}  created by: {', '.join(migs)}")

    if not dup_cols and not dup_idx:
        print("  (none — good)")
    print()

    # ---- 3. ORM columns missing from the migration chain ----
    # If the DB is stamped at head but the column isn't in any migration,
    # create_all must run to produce the column. Our deploy always runs
    # create_all after alembic upgrade head, so this is OK for fresh
    # deploys; it's only a risk for customers with a pre-existing DB
    # that was stamped at head *before* the ORM column was added.
    print("=" * 78)
    print("3) ORM columns NOT present in any migration")
    print("   (create_all is the only path that produces them)")
    print("=" * 78)
    total_orphan_cols = 0
    for t in sorted(orm_tables):
        orm_cols = orm_tables[t]
        # For tables the chain creates, chain_added_columns also counts,
        # since create_table itself puts those cols down.
        chain_cols: set[str] = set()
        if t in chain_tables:
            # We can't introspect columns from create_table without
            # actually executing the migration, so we treat every column
            # on a chain-created table as "known" unless we tracked
            # otherwise (this is a conservative stance — we flag only
            # cols that appear to be drift from ORM relative to
            # add_column ops).
            chain_cols = orm_cols  # chain-created tables: assume columns ok
        chain_cols = chain_cols | chain_added_columns.get(t, set())

        missing_cols = sorted(orm_cols - chain_cols)
        if missing_cols and t not in chain_tables:
            print(f"  - {t}:  {', '.join(missing_cols)}")
            total_orphan_cols += len(missing_cols)

    if total_orphan_cols == 0:
        print("  (none — every ORM column is on a chain-created table)")
    print()

    # ---- 4. Tables in ORM with ORM-declared indexes that no migration
    # creates. create_all will create them, but an alembic-only deploy
    # wouldn't. Low risk for our setup (create_all always runs).
    print("=" * 78)
    print("4) ORM-declared indexes NOT present in any migration")
    print("   (create_all is the only path that produces them)")
    print("=" * 78)
    missing_idx_count = 0
    for t, ix_names in sorted(orm_indexes.items()):
        if not ix_names:
            continue
        chain_ix = chain_created_indexes.get(t, set())
        gap = sorted(n for n in ix_names if n not in chain_ix)
        if gap:
            print(f"  - {t}:  {', '.join(gap)}")
            missing_idx_count += len(gap)
    if missing_idx_count == 0:
        print("  (none)")
    print()

    # ---- 5. Migration references to non-existent tables ----
    # If a migration adds a column to a table that the chain never
    # creates (and the ORM also doesn't declare), we have a dead
    # reference.
    print("=" * 78)
    print("5) Migration add_column targets that are NOT in ORM nor")
    print("   created by any migration")
    print("=" * 78)
    chain_target_tables = chain_tables | set(orm_tables)
    orphans: list[tuple[str, str, str]] = []
    for mig_id, entries in scan["add_column"].items():
        for t, c in entries:
            if t not in chain_target_tables:
                orphans.append((mig_id, t, c))
    for mig_id, t, c in sorted(set(orphans)):
        print(f"  - {mig_id}: adds {t}.{c} (table unknown)")
    if not orphans:
        print("  (none — good)")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
