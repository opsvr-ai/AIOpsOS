"""Shared in-memory DB stub for ConsolidationWorker PBTs.

Mirrors the small subset of ``agent_memories`` / ``sessions`` / ``messages``
schema that :func:`src.services.memory.consolidation_logic.run_consolidation`
reads and writes. Enough to exercise:

* session lookup
* pending-turn filter by ``created_at > last_consolidation_at``
* baseline memory listing
* ``INSERT ON CONFLICT (content_hash) DO NOTHING``
* archive-on-supersede
* ``hot_memory_version`` bump

Not a real SQL engine — we pattern-match on the SQL text to decide which
fixture method to dispatch. This keeps the fake in the test tree (not
vendored in the service) and lets us evolve it as tests demand.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class _SessionRow:
    id: uuid.UUID
    user_id: uuid.UUID
    space_id: uuid.UUID | None = None
    last_consolidation_at: datetime | None = None
    hot_memory_version: int = 0
    consolidation_count: int = 0
    memory_status: str = "unconsolidated"


@dataclass
class _MessageRow:
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    created_at: datetime


@dataclass
class _MemoryRow:
    id: uuid.UUID
    session_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    content: str
    scope: str
    tags: list[str]
    content_hash: str | None
    space_id: uuid.UUID | None
    is_archived: bool = False
    superseded_by: uuid.UUID | None = None
    pinned: bool = False
    embedding: list[float] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime | None = None


# ---------------------------------------------------------------------------
# Fake row accessor — mimics SQLAlchemy's Row interface
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Result:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    def first(self) -> _Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[_Row]:
        return list(self._rows)

    def scalar(self) -> Any:
        if not self._rows:
            return None
        first = self._rows[0]
        # Return the first attribute value (mirrors SQLAlchemy scalar() which
        # returns the first column of the first row).
        keys = [k for k in first.__dict__.keys() if not k.startswith("_")]
        return first.__dict__.get(keys[0]) if keys else None


# ---------------------------------------------------------------------------
# FakeDB — global store + session context manager
# ---------------------------------------------------------------------------


class FakeDB:
    """Minimal in-memory fixture that backs consolidation tests."""

    def __init__(self) -> None:
        self.sessions: dict[uuid.UUID, _SessionRow] = {}
        self.messages: list[_MessageRow] = []
        self.memories: dict[uuid.UUID, _MemoryRow] = {}
        # content_hash -> memory id (for ON CONFLICT DO NOTHING)
        self._hash_index: dict[str, uuid.UUID] = {}

    # -- seeding API --------------------------------------------------

    def add_session(
        self,
        *,
        session_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        space_id: uuid.UUID | None = None,
        last_consolidation_at: datetime | None = None,
    ) -> _SessionRow:
        sid = session_id or uuid.uuid4()
        uid = user_id or uuid.uuid4()
        row = _SessionRow(
            id=sid,
            user_id=uid,
            space_id=space_id,
            last_consolidation_at=last_consolidation_at,
        )
        self.sessions[sid] = row
        return row

    def add_message(
        self,
        *,
        session_id: uuid.UUID,
        role: str,
        content: str,
        created_at: datetime | None = None,
    ) -> _MessageRow:
        row = _MessageRow(
            id=uuid.uuid4(),
            session_id=session_id,
            role=role,
            content=content,
            created_at=created_at or datetime.now(UTC),
        )
        self.messages.append(row)
        return row

    def add_memory(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        content: str,
        title: str = "",
        scope: str = "personal",
        content_hash: str | None = None,
    ) -> _MemoryRow:
        row = _MemoryRow(
            id=uuid.uuid4(),
            session_id=session_id,
            user_id=user_id,
            title=title or content[:30],
            content=content,
            scope=scope,
            tags=[],
            content_hash=content_hash,
            space_id=None,
        )
        self.memories[row.id] = row
        if content_hash:
            self._hash_index[content_hash] = row.id
        return row

    # -- factory -----------------------------------------------------

    def factory(self):
        db = self

        @asynccontextmanager
        async def _factory():
            yield _FakeSession(db)

        return _factory


# ---------------------------------------------------------------------------
# _FakeSession — routes execute() + commit() to the store
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, db: FakeDB) -> None:
        self._db = db

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt).strip()
        params = params or {}
        # Strategy: pattern-match on the first meaningful line so we don't
        # need a real parser.
        head = " ".join(sql.split())[:400].lower()

        # --- mutations first (more specific) ---
        if head.startswith("insert into agent_memories"):
            return self._insert_memory(params, "cast(:embedding as vector)" in head)
        if head.startswith("update agent_memories") and "is_archived" in head and "set is_archived" in head:
            return self._archive_memories(params)
        if head.startswith("update agent_memories") and "last_used_at" in head:
            # MemoryTier last_used_at touch — no-op
            return _Result([])
        if head.startswith("update sessions") and "hot_memory_version" in head:
            return self._bump_session(params)

        # --- selects on sessions ---
        if "from sessions" in head and "where id" in head:
            # Differentiate: full session read vs hot_version read
            if "user_id" in head and "last_consolidation_at" in head:
                return self._select_session(params)
            if "hot_memory_version" in head:
                return self._select_hot_version(params)
            return _Result([])

        # --- selects on messages ---
        if "from messages" in head:
            return self._select_messages(params, head)

        # --- selects on agent_memories ---
        if "from agent_memories" in head:
            if "pinned = true" in head:
                return self._select_pinned_team(params)
            if "scope = 'personal'" in head:
                return self._select_personal(params)
            if "content_hash" in head:
                return self._select_baseline(params)
            return _Result([])

        # Fallback
        return _Result([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:  # pragma: no cover - not exercised
        return None

    # -- query dispatch ------------------------------------------------

    def _select_session(self, params: dict) -> _Result:
        sid = uuid.UUID(str(params["sid"]))
        row = self._db.sessions.get(sid)
        if row is None:
            return _Result([])
        return _Result(
            [
                _Row(
                    id=row.id,
                    user_id=row.user_id,
                    space_id=row.space_id,
                    last_consolidation_at=row.last_consolidation_at,
                    hot_memory_version=row.hot_memory_version,
                    consolidation_count=row.consolidation_count,
                )
            ]
        )

    def _select_messages(self, params: dict, head: str) -> _Result:
        sid = uuid.UUID(str(params["sid"]))
        msgs = [m for m in self._db.messages if m.session_id == sid]
        if "created_at > :last_at" in head:
            last_at = params.get("last_at")
            if last_at is not None:
                msgs = [m for m in msgs if m.created_at > last_at]
        msgs.sort(key=lambda m: m.created_at)
        msgs = msgs[:100]
        return _Result(
            [
                _Row(
                    id=m.id,
                    role=m.role,
                    content=m.content,
                    created_at=m.created_at,
                )
                for m in msgs
            ]
        )

    def _select_baseline(self, params: dict) -> _Result:
        uid = uuid.UUID(str(params["uid"]))
        rows = [
            m
            for m in self._db.memories.values()
            if m.user_id == uid and not m.is_archived
        ]
        rows.sort(key=lambda m: m.created_at, reverse=True)
        rows = rows[:50]
        return _Result(
            [
                _Row(
                    id=m.id,
                    title=m.title,
                    content=m.content,
                    scope=m.scope,
                    tags=m.tags,
                    content_hash=m.content_hash,
                )
                for m in rows
            ]
        )

    def _insert_memory(self, params: dict, with_embedding: bool) -> _Result:
        chash = params["chash"]
        if chash in self._db._hash_index:
            # ON CONFLICT DO NOTHING → no row returned.
            return _Result([])
        sid = params["id"]
        if isinstance(sid, str):
            sid = uuid.UUID(sid)
        uid = params["uid"]
        if isinstance(uid, str):
            uid = uuid.UUID(uid)
        sess_id = params["sid"]
        if isinstance(sess_id, str):
            sess_id = uuid.UUID(sess_id)
        space_id = params.get("space_id")
        if isinstance(space_id, str):
            space_id = uuid.UUID(space_id)
        import json as _json

        tags_raw = params.get("tags") or "[]"
        tags = _json.loads(tags_raw) if isinstance(tags_raw, str) else list(tags_raw)

        row = _MemoryRow(
            id=sid,
            session_id=sess_id,
            user_id=uid,
            title=params["title"],
            content=params["content"],
            scope=params["scope"],
            tags=tags,
            content_hash=chash,
            space_id=space_id,
            embedding=None,  # embedding string not parsed
        )
        self._db.memories[row.id] = row
        self._db._hash_index[chash] = row.id
        return _Result([_Row(id=row.id)])

    def _archive_memories(self, params: dict) -> _Result:
        uid = uuid.UUID(str(params["uid"]))
        ids_raw = params.get("ids") or []
        target_ids = {uuid.UUID(str(x)) for x in ids_raw}
        n = 0
        for m in self._db.memories.values():
            if m.user_id != uid:
                continue
            if m.id in target_ids:
                m.is_archived = True
                n += 1
        result = _Result([])
        result.rowcount = n
        return result

    def _bump_session(self, params: dict) -> _Result:
        sid = uuid.UUID(str(params["sid"]))
        row = self._db.sessions.get(sid)
        if row is None:
            return _Result([])
        row.consolidation_count += 1
        row.hot_memory_version += 1
        row.last_consolidation_at = datetime.now(UTC)
        row.memory_status = "consolidated"
        result = _Result([_Row(hot_memory_version=row.hot_memory_version)])
        result.rowcount = 1
        return result

    def _select_hot_version(self, params: dict) -> _Result:
        sid = uuid.UUID(str(params["sid"]))
        row = self._db.sessions.get(sid)
        if row is None:
            return _Result([])
        return _Result([_Row(hot_memory_version=row.hot_memory_version)])

    def _select_personal(self, params: dict) -> _Result:
        uid = uuid.UUID(str(params["uid"]))
        rows = [
            m
            for m in self._db.memories.values()
            if m.user_id == uid and m.scope == "personal" and not m.is_archived
        ]
        rows.sort(key=lambda m: m.created_at, reverse=True)
        rows = rows[:5]
        return _Result(
            [
                _Row(
                    id=m.id,
                    title=m.title,
                    content=m.content,
                    scope=m.scope,
                    tags=m.tags,
                    pinned=m.pinned,
                    created_at=m.created_at,
                    last_used_at=m.last_used_at,
                )
                for m in rows
            ]
        )

    def _select_pinned_team(self, params: dict) -> _Result:
        # For consolidation tests we never seed pinned team memories.
        return _Result([])
