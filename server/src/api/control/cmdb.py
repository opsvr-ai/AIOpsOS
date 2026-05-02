"""CMDB control API — node/topology queries, review queue, mapping rules, sync logs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, text, func

from src.api.deps import DbSession, require_perm
from src.models.cmdb import CmdbNode, CmdbEdge, CmdbSyncLog, CmdbMappingRule, CmdbReviewItem
from src.agent.sub_agents.cmdb_ingestion_agent import CmdbIngestionAgent

cmdb_router = APIRouter(prefix="/cmdb", tags=["CMDB"])


class ReviewActionRequest(BaseModel):
    reviewer: str | None = None
    note: str | None = None


class MappingRuleUpdate(BaseModel):
    rule_content: dict[str, Any] | None = None
    status: str | None = None


# ── Node queries ────────────────────────────────────────────────────────

@cmdb_router.get("/nodes")
async def list_nodes(
    search: str | None = Query(None),
    ci_type: str | None = Query(None),
    source: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    query = select(CmdbNode)
    if search:
        query = query.where(
            CmdbNode.name.ilike(f"%{search}%") | CmdbNode.external_id.ilike(f"%{search}%")
        )
    if ci_type:
        query = query.where(CmdbNode.ci_type == ci_type)
    if source:
        query = query.where(CmdbNode.source == source)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(CmdbNode.name).offset((page - 1) * page_size).limit(page_size)
    )
    nodes = result.scalars().all()

    return {
        "items": [
            {
                "id": str(n.id), "ci_type": n.ci_type, "name": n.name,
                "external_id": n.external_id, "source": n.source,
                "properties": n.properties,
                "synced_at": n.synced_at.isoformat() if n.synced_at else None,
            }
            for n in nodes
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@cmdb_router.get("/topology")
async def get_topology(
    node_id: UUID | None = Query(None),
    ci_types: str | None = Query(None),
    depth: int = Query(3, ge=1, le=10),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    if node_id:
        cte_sql = text("""
            WITH RECURSIVE graph_walk AS (
                SELECT source_node_id, target_node_id, relation_type, 1 AS depth
                FROM cmdb_edges
                WHERE source_node_id = :start_id OR target_node_id = :start_id
                UNION
                SELECT e.source_node_id, e.target_node_id, e.relation_type, gw.depth + 1
                FROM cmdb_edges e
                JOIN graph_walk gw ON e.source_node_id = gw.target_node_id
                   OR e.target_node_id = gw.source_node_id
                WHERE gw.depth < :max_depth
            )
            SELECT DISTINCT source_node_id, target_node_id, relation_type FROM graph_walk
        """)
        result = await db.execute(cte_sql, {"start_id": node_id, "max_depth": depth})
        edges_data = result.all()
        edge_node_ids: set[UUID] = set()
        for row in edges_data:
            edge_node_ids.add(row[0])
            edge_node_ids.add(row[1])
        nodes_result = await db.execute(
            select(CmdbNode).where(CmdbNode.id.in_(edge_node_ids))
        )
        nodes = nodes_result.scalars().all()
    else:
        edges_result = await db.execute(select(CmdbEdge).limit(200))
        edges_data = edges_result.all()
        nodes_result = await db.execute(select(CmdbNode).limit(500))
        nodes = nodes_result.scalars().all()

    return {
        "nodes": [
            {
                "id": str(n.id), "ci_type": n.ci_type, "name": n.name,
                "external_id": n.external_id, "source": n.source,
                "properties": n.properties,
            }
            for n in nodes
        ],
        "edges": [
            {
                "source_node_id": str(e[0] if isinstance(e, tuple) else e.source_node_id),
                "target_node_id": str(e[1] if isinstance(e, tuple) else e.target_node_id),
                "relation_type": e[2] if isinstance(e, tuple) else e.relation_type,
            }
            for e in edges_data
        ],
    }


# ── Review queue ────────────────────────────────────────────────────────

@cmdb_router.get("/review-items")
async def list_review_items(
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    query = select(CmdbReviewItem)
    if status:
        query = query.where(CmdbReviewItem.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(CmdbReviewItem.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    items = result.scalars().all()

    return {
        "items": [
            {
                "id": str(item.id),
                "sync_log_id": str(item.sync_log_id) if item.sync_log_id else None,
                "review_type": item.review_type,
                "source_data": item.source_data,
                "transformed_data": item.transformed_data,
                "llm_confidence": item.llm_confidence,
                "llm_reason": item.llm_reason,
                "diff_summary": item.diff_summary,
                "status": item.status,
                "reviewer": item.reviewer,
                "review_note": item.review_note,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ],
        "total": total, "page": page, "page_size": page_size,
    }


@cmdb_router.post("/review-items/{item_id}/approve")
async def approve_review_item(
    item_id: UUID,
    body: ReviewActionRequest,
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "write")),
):
    result = await db.execute(select(CmdbReviewItem).where(CmdbReviewItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        return {"error": "Review item not found"}
    item.status = "approved"
    item.reviewer = body.reviewer
    item.review_note = body.note
    item.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "status": "approved"}


@cmdb_router.post("/review-items/{item_id}/reject")
async def reject_review_item(
    item_id: UUID,
    body: ReviewActionRequest,
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "write")),
):
    result = await db.execute(select(CmdbReviewItem).where(CmdbReviewItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        return {"error": "Review item not found"}
    item.status = "rejected"
    item.reviewer = body.reviewer
    item.review_note = body.note
    item.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "status": "rejected"}


# ── Mapping rules ───────────────────────────────────────────────────────

@cmdb_router.get("/mapping-rules")
async def list_mapping_rules(
    datasource_id: UUID | None = Query(None),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    query = select(CmdbMappingRule)
    if datasource_id:
        query = query.where(CmdbMappingRule.datasource_id == datasource_id)
    result = await db.execute(query.order_by(CmdbMappingRule.version.desc()))
    rules = result.scalars().all()
    return {
        "items": [
            {
                "id": str(r.id),
                "datasource_id": str(r.datasource_id) if r.datasource_id else None,
                "version": r.version,
                "rule_content": r.rule_content,
                "status": r.status,
                "approved_by": r.approved_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rules
        ],
    }


@cmdb_router.put("/mapping-rules/{rule_id}")
async def update_mapping_rule(
    rule_id: UUID,
    body: MappingRuleUpdate,
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "write")),
):
    result = await db.execute(select(CmdbMappingRule).where(CmdbMappingRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return {"error": "Mapping rule not found"}
    if body.rule_content is not None:
        rule.rule_content = body.rule_content
    if body.status is not None:
        rule.status = body.status
    await db.commit()
    return {"success": True, "id": str(rule.id), "status": rule.status}


# ── Sync logs ───────────────────────────────────────────────────────────

@cmdb_router.get("/sync-logs")
async def list_sync_logs(
    datasource_id: UUID | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: DbSession = None,
    _: Any = Depends(require_perm("cmdb", "read")),
):
    query = select(CmdbSyncLog)
    if datasource_id:
        query = query.where(CmdbSyncLog.datasource_id == datasource_id)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(CmdbSyncLog.started_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    logs = result.scalars().all()
    return {
        "items": [
            {
                "id": str(log.id),
                "datasource_id": str(log.datasource_id) if log.datasource_id else None,
                "mode": log.mode,
                "status": log.status,
                "nodes_created": log.nodes_created,
                "nodes_updated": log.nodes_updated,
                "nodes_deleted": log.nodes_deleted,
                "edges_count": log.edges_count,
                "review_count": log.review_count,
                "errors_detail": log.errors_detail,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "finished_at": log.finished_at.isoformat() if log.finished_at else None,
            }
            for log in logs
        ],
        "total": total, "page": page, "page_size": page_size,
    }
