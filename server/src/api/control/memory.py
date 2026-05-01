"""Two-tier memory CRUD and session summarization endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from src.api.deps import get_current_user, get_optional_space_id
from src.models.session import Session
from src.services.memory_service import memory_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/memories")
async def list_memories(
    scope: str = Query("all", description="personal, team, or all"),
    q: str = Query("", description="Search query"),
    session_id: str = Query("", description="Filter by session"),
    space_id: str | None = Depends(get_optional_space_id),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    tags: str = Query("", description="Comma-separated tag filter"),
    sort_by: str = Query("created_at", description="created_at or updated_at"),
    user=Depends(get_current_user),
):
    """List memories with scope, search, pagination, and session filters."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    try:
        results = await memory_service.retrieve(
            query=q,
            user_id=str(user.id),
            scope=scope,
            session_id=session_id,
            space_id=space_id,
            top_k=limit,
            offset=offset,
            tags=tag_list,
            sort_by=sort_by,
        )
    except Exception:
        logger.exception("Failed to retrieve memories")
        return []

    session_titles: dict[str, str] = {}
    if results:
        sids = {r["session_id"] for r in results if r.get("session_id")}
        if sids:
            try:
                from src.models.base import async_session_factory

                async with async_session_factory() as db:
                    for sid in sids:
                        row = await db.execute(select(Session).where(Session.id == sid))
                        s = row.scalar_one_or_none()
                        if s:
                            session_titles[sid] = s.title or ""
            except Exception:
                logger.exception("Failed to resolve session titles for memories")

    return [
        {
            **r,
            "session_title": session_titles.get(r.get("session_id", ""), ""),
        }
        for r in results
    ]


@router.get("/memories/tags")
async def list_tags(
    scope: str = Query("all", description="personal, team, or all"),
    user=Depends(get_current_user),
):
    """List all tags with their occurrence counts."""
    from src.models.base import async_session_factory
    from src.models.knowledge import AgentMemory
    from sqlalchemy import func

    async with async_session_factory() as db:
        query = select(
            func.jsonb_array_elements_text(AgentMemory.tags).label("tag"),
            func.count().label("count"),
        )
        if scope == "personal":
            query = query.where(
                AgentMemory.user_id == user.id,
                AgentMemory.scope == "personal",
            )
        elif scope == "team":
            query = query.where(AgentMemory.scope == "team")
        else:
            query = query.where(
                (AgentMemory.scope == "team")
                | ((AgentMemory.scope == "personal") & (AgentMemory.user_id == user.id)),
            )
        query = query.group_by("tag").order_by(func.count().desc())
        result = await db.execute(query)
        rows = result.fetchall()

    return [{"name": row.tag, "count": row.count} for row in rows if row.tag]


@router.get("/memories/graph")
async def get_memory_graph(
    scope: str = Query("all", description="personal, team, or all"),
    space_id: str | None = Depends(get_optional_space_id),
    tag: str = Query("", description="Focus on a specific tag"),
    memory_id: str = Query("", description="Focus on a specific memory (2-hop subgraph)"),
    limit: int = Query(100, ge=1, le=500),
    user=Depends(get_current_user),
):
    """Return graph data (nodes + edges) for knowledge-graph visualization."""
    from src.models.base import async_session_factory
    from src.models.knowledge import AgentMemory

    async with async_session_factory() as db:
        query = select(AgentMemory)
        if scope == "personal":
            query = query.where(AgentMemory.user_id == user.id, AgentMemory.scope == "personal")
        elif scope == "team":
            query = query.where(AgentMemory.scope == "team")
        else:
            query = query.where(
                (AgentMemory.scope == "team")
                | ((AgentMemory.scope == "personal") & (AgentMemory.user_id == user.id)),
            )
        if space_id:
            query = query.where(AgentMemory.space_id == space_id)
        query = query.order_by(AgentMemory.created_at.desc()).limit(limit)
        result = await db.execute(query)
        memories = list(result.scalars().all())

    # If focusing on a specific memory, expand to its 2-hop neighborhood
    if memory_id:
        seed = next((m for m in memories if str(m.id) == memory_id), None)
        if seed:
            seed_tags = seed.tags or []
            related = [m for m in memories if m.id != seed.id and set(m.tags or []) & set(seed_tags)]
            memories = [seed] + related

    # Build graph data
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_tag_ids: set[str] = set()
    seen_mem_ids: set[str] = set()

    target_memories = memories
    if tag:
        target_memories = [m for m in memories if tag in (m.tags or [])]

    for m in target_memories:
        mid = f"mem-{m.id}"
        if mid in seen_mem_ids:
            continue
        seen_mem_ids.add(mid)
        nodes.append({
            "id": mid,
            "type": "memory",
            "label": m.title or "未命名记忆",
            "scope": m.scope,
            "tags": m.tags or [],
            "sessionId": str(m.session_id) if m.session_id else "",
        })

        for t in m.tags or []:
            if not t.strip():
                continue
            tid = f"tag-{t}"
            edges.append({"source": mid, "target": tid})
            if tid not in seen_tag_ids:
                seen_tag_ids.add(tid)
                tag_count = sum(1 for mm in memories if t in (mm.tags or []))
                nodes.append({
                    "id": tid,
                    "type": "tag",
                    "label": t,
                    "count": tag_count,
                })

    return {"nodes": nodes, "edges": edges}


@router.get("/memories/{memory_id}")
async def get_memory(
    memory_id: str,
    user=Depends(get_current_user),
):
    """Get a single memory entry with linked session title."""
    results = await memory_service.retrieve(
        query="",
        user_id=str(user.id),
        scope="all",
        top_k=100,
    )
    match = next((r for r in results if r["id"] == memory_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Memory not found")

    session_title = ""
    if match.get("session_id"):
        from src.models.base import async_session_factory

        async with async_session_factory() as db:
            row = await db.execute(select(Session).where(Session.id == match["session_id"]))
            s = row.scalar_one_or_none()
            if s:
                session_title = s.title or ""

    return {**match, "session_title": session_title}


@router.get("/memories/{memory_id}/related")
async def get_related_memories(
    memory_id: str,
    limit: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user),
):
    """Find memories sharing tags with the given memory."""
    from src.models.base import async_session_factory
    from src.models.knowledge import AgentMemory

    async with async_session_factory() as db:
        seed_result = await db.execute(
            select(AgentMemory).where(AgentMemory.id == memory_id)
        )
        seed = seed_result.scalar_one_or_none()
        if not seed:
            raise HTTPException(status_code=404, detail="Memory not found")

        seed_tags = seed.tags or []
        if not seed_tags:
            return []

        # Find memories with intersecting tags (excluding the seed)
        stmt = (
            select(AgentMemory)
            .where(
                AgentMemory.id != memory_id,
                AgentMemory.tags.op("?|")(seed_tags),
            )
            .order_by(AgentMemory.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        related = list(result.scalars().all())

    session_titles: dict[str, str] = {}
    sids = {str(r.session_id) for r in related if r.session_id}
    if sids:
        async with async_session_factory() as db:
            for sid in sids:
                row = await db.execute(select(Session).where(Session.id == sid))
                s = row.scalar_one_or_none()
                if s:
                    session_titles[sid] = s.title or ""

    return [
        {
            "id": str(r.id),
            "title": r.title,
            "content": r.content,
            "scope": r.scope,
            "session_id": str(r.session_id) if r.session_id else "",
            "session_title": session_titles.get(str(r.session_id), ""),
            "tags": r.tags or [],
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in related
    ]


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    user=Depends(get_current_user),
):
    """Delete a memory entry."""
    deleted = await memory_service.delete(memory_id, str(user.id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"detail": "deleted"}


@router.post("/sessions/{session_id}/summarize")
async def summarize_session(
    session_id: str,
    user=Depends(get_current_user),
):
    """Trigger LLM-based session summarization into personal + team memories."""
    from src.core.model_factory import get_default_model
    llm = await get_default_model()
    result = await memory_service.summarize_session(session_id, str(user.id), llm)
    return {"ok": True, **result}


@router.post("/memories/backfill")
async def backfill_memories(
    user=Depends(get_current_user),
):
    """Process historical sessions without memories.

    Finds all sessions that have messages but no associated memories,
    then runs LLM-based dual-scope extraction on each. Returns counts
    of sessions processed and memories created.
    """
    from langchain_openai import ChatOpenAI
    from src.config import settings
    from src.models.base import async_session_factory
    from src.models.session import Session, Message

    async with async_session_factory() as db:
        # Find sessions with messages but no memories
        result = await db.execute(
            select(Session.id, Session.user_id)
            .where(Session.user_id == user.id)
            .order_by(Session.created_at.desc())
            .limit(50)
        )
        sessions = list(result.fetchall())

    if not sessions:
        return {"ok": True, "sessions_processed": 0, "personal": 0, "team": 0}

    from src.core.model_factory import get_default_model
    llm = await get_default_model()

    total_personal = 0
    total_team = 0
    processed = 0

    for row in sessions:
        sid = str(row.id)
        uid = str(row.user_id)

        # Check if session already has memories
        existing = await memory_service.retrieve(
            query="", user_id=uid, scope="all", session_id=sid, top_k=1
        )
        if existing:
            continue

        try:
            result = await memory_service.summarize_session(sid, uid, llm)
            total_personal += result.get("personal", 0)
            total_team += result.get("team", 0)
            processed += 1
        except Exception:
            logger.exception("Backfill failed for session %s", sid)

    return {
        "ok": True,
        "sessions_processed": processed,
        "personal": total_personal,
        "team": total_team,
    }
