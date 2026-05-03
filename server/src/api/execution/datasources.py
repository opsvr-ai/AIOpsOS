from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user, get_optional_space_id, require_perm
from src.models.datasource import DataSource
from src.models.ingestion_log import IngestionLog
from src.schemas.datasource import (
    DataSourceCreate,
    DataSourceOut,
    DataSourceTestResult,
    DataSourceUpdate,
)
from src.schemas.ingestion_log import IngestionLogOut

router = APIRouter(prefix="/api/v1")


@router.get("/datasources", response_model=list[DataSourceOut])
async def list_datasources(
    db: DbSession,
    _=Depends(get_current_user),
    source_type: str | None = Query(None),
    status: str | None = Query(None),
    search: str | None = Query(None),
    space_id: str | None = Depends(get_optional_space_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = select(DataSource)
    if source_type:
        q = q.where(DataSource.source_type == source_type)
    if status:
        q = q.where(DataSource.status == status)
    if search:
        q = q.where(DataSource.name.ilike(f"%{search}%"))
    if space_id:
        q = q.where(DataSource.space_id == space_id)
    q = q.order_by(DataSource.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/datasources", response_model=DataSourceOut)
async def create_datasource(
    body: DataSourceCreate, db: DbSession, _=Depends(require_perm("datasources", "create"))
):
    import uuid
    config = body.config
    if body.source_type == "webhook":
        config.setdefault("endpoint_id", str(uuid.uuid4()))
        config.setdefault("secret", uuid.uuid4().hex[:32])
    ds = DataSource(
        name=body.name,
        description=body.description,
        source_type=body.source_type,
        config=config,
        normalization_rules=body.normalization_rules,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


@router.get("/datasources/{datasource_id}", response_model=DataSourceOut)
async def get_datasource(datasource_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(DataSource).where(DataSource.id == datasource_id))
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="DataSource not found")
    return ds


@router.patch("/datasources/{datasource_id}", response_model=DataSourceOut)
async def update_datasource(
    datasource_id: str, body: DataSourceUpdate, db: DbSession,
    _=Depends(require_perm("datasources", "update"))
):
    result = await db.execute(select(DataSource).where(DataSource.id == datasource_id))
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="DataSource not found")
    for key, val in body.model_dump(exclude_unset=True).items():
        setattr(ds, key, val)
    await db.commit()
    await db.refresh(ds)
    return ds


@router.delete("/datasources/{datasource_id}")
async def delete_datasource(
    datasource_id: str, db: DbSession, _=Depends(require_perm("datasources", "delete"))
):
    result = await db.execute(select(DataSource).where(DataSource.id == datasource_id))
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="DataSource not found")
    await db.delete(ds)
    await db.commit()
    return {"detail": "deleted"}


@router.post("/datasources/{datasource_id}/test", response_model=DataSourceTestResult)
async def test_datasource(datasource_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(DataSource).where(DataSource.id == datasource_id))
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="DataSource not found")

    if ds.source_type == "api":
        try:
            from src.services.api_poller import _execute_request_chain
            events = await _execute_request_chain(ds)
            sample = events[0] if events else None
            return DataSourceTestResult(
                success=True,
                message=f"Connected. Found {len(events)} events.",
                events_found=len(events),
                sample_event=sample,
            )
        except Exception as e:
            return DataSourceTestResult(
                success=False,
                message=f"Connection failed: {str(e)[:500]}",
            )
    elif ds.source_type == "webhook":
        endpoint_id = (ds.config or {}).get("endpoint_id", "")
        return DataSourceTestResult(
            success=True,
            message=f"Webhook endpoint: POST /api/v1/webhook/{endpoint_id}",
        )
    elif ds.source_type == "kafka":
        return DataSourceTestResult(
            success=True,
            message=f"Kafka: {ds.config.get('topic', 'ops-events')} @ {ds.config.get('bootstrap_servers', 'localhost:9092')}",
        )
    elif ds.source_type == "log":
        config = ds.config or {}
        kafka_topic = config.get("kafka_topic")
        kafka_servers = config.get("kafka_bootstrap_servers")
        if kafka_topic and kafka_servers:
            return DataSourceTestResult(
                success=True,
                message=f"Log source configured (Kafka topic={kafka_topic}, servers={kafka_servers})",
            )
        return DataSourceTestResult(
            success=True,
            message="Log source ready (filebeat/vector/webhook mode)",
        )
    elif ds.source_type == "itsm":
        config = ds.config or {}
        base_url = config.get("api_base_url", "").rstrip("/")
        if not base_url:
            return DataSourceTestResult(
                success=False,
                message="api_base_url not configured for ITSM datasource",
            )
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{base_url}/api/health")
                if resp.status_code < 500:
                    return DataSourceTestResult(
                        success=True,
                        message=f"ITSM API reachable (status={resp.status_code})",
                    )
                return DataSourceTestResult(
                    success=False,
                    message=f"ITSM API returned status {resp.status_code}",
                )
        except Exception as e:
            return DataSourceTestResult(
                success=False,
                message=f"ITSM connection failed: {str(e)[:500]}",
            )
    elif ds.source_type == "cmdb":
        config = ds.config or {}
        base_url = config.get("api_base_url", "").rstrip("/")
        if not base_url:
            return DataSourceTestResult(
                success=False,
                message="api_base_url not configured for CMDB datasource",
            )
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{base_url}/api/health")
                if resp.status_code < 500:
                    return DataSourceTestResult(
                        success=True,
                        message=f"CMDB API reachable (status={resp.status_code})",
                    )
                return DataSourceTestResult(
                    success=False,
                    message=f"CMDB API returned status {resp.status_code}",
                )
        except Exception as e:
            return DataSourceTestResult(
                success=False,
                message=f"CMDB connection failed: {str(e)[:500]}",
            )
    return DataSourceTestResult(success=False, message=f"Unknown type: {ds.source_type}")


@router.get("/datasources/{datasource_id}/logs", response_model=list[IngestionLogOut])
async def list_ingestion_logs(
    datasource_id: str, db: DbSession, _=Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    result = await db.execute(
        select(IngestionLog)
        .where(IngestionLog.datasource_id == datasource_id)
        .order_by(IngestionLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return result.scalars().all()


class SyncTriggerRequest(BaseModel):
    mode: str = "incremental"  # discover / incremental / full


@router.post("/datasources/{datasource_id}/sync")
async def trigger_cmdb_sync(
    datasource_id: UUID,
    body: SyncTriggerRequest,
    db: DbSession,
    _=Depends(require_perm("datasources", "write")),
):
    result = await db.execute(select(DataSource).where(DataSource.id == datasource_id))
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="DataSource not found")
    if ds.source_type != "cmdb":
        raise HTTPException(status_code=400, detail="Sync only supported for cmdb-type DataSources")

    from src.agent.sub_agents.cmdb_ingestion_agent import CmdbIngestionAgent
    agent = CmdbIngestionAgent()
    sync_result = await agent.run_sync(str(datasource_id), mode=body.mode)
    return sync_result
