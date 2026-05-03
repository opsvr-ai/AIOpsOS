"""Dynamic event table query API — read mapped event data from auto-created tables."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from src.api.deps import DbSession, get_current_user, get_optional_space_id
from src.models.datasource import DataSource
from src.services.event_mapper import ensure_event_table

router = APIRouter()


@router.get("/events/datasources")
async def list_mapped_datasources(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    """List datasources that have table_mapping configured."""
    from sqlalchemy import select

    q = select(DataSource).where(DataSource.table_mapping.isnot(None))
    if space_id:
        q = q.where(DataSource.space_id == space_id)
    result = await db.execute(q.order_by(DataSource.name))
    datasources = result.scalars().all()
    return [
        {
            "id": str(ds.id),
            "name": ds.name,
            "source_type": ds.source_type,
            "table_mapping": ds.table_mapping,
            "last_ingested_at": ds.last_ingested_at.isoformat() if ds.last_ingested_at else None,
            "total_ingested": ds.total_ingested,
        }
        for ds in datasources
    ]


@router.get("/events/{datasource_id}")
async def query_event_table(
    datasource_id: str,
    db: DbSession,
    _=Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort_by: str | None = Query(None),
    sort_dir: str = Query("desc"),
):
    """Query the dynamic event table for a datasource with pagination."""
    from sqlalchemy import select

    result = await db.execute(
        select(DataSource).where(DataSource.id == datasource_id)
    )
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(status_code=404, detail="DataSource not found")
    if not ds.table_mapping:
        raise HTTPException(status_code=400, detail="DataSource has no table_mapping configured")

    tbl_name = await ensure_event_table(datasource_id, ds.table_mapping)

    # Count total
    count_result = await db.execute(text(f"SELECT COUNT(*) FROM {tbl_name}"))
    total = count_result.scalar() or 0

    # Build query
    sort_col = "ingested_at"
    if sort_by:
        known_cols = {c.get("name", "") for c in ds.table_mapping.get("columns", [])}
        if sort_by in known_cols:
            sort_col = sort_by

    order_clause = f"ORDER BY {sort_col} {'DESC' if sort_dir.lower() == 'desc' else 'ASC'}"
    offset = (page - 1) * page_size

    rows_result = await db.execute(
        text(
            f"SELECT * FROM {tbl_name} {order_clause} "
            f"LIMIT :limit OFFSET :offset"
        ),
        {"limit": page_size, "offset": offset},
    )
    rows = []
    for row in rows_result:
        row_dict = dict(row._mapping)
        for k, v in row_dict.items():
            if hasattr(v, "isoformat"):
                row_dict[k] = v.isoformat()
        rows.append(row_dict)

    # Column metadata from mapping
    columns = [
        {"name": "id", "type": "string"},
        {"name": "ingested_at", "type": "datetime"},
        {"name": "raw_event", "type": "json"},
        *ds.table_mapping.get("columns", []),
    ]

    return {
        "datasource_id": datasource_id,
        "datasource_name": ds.name,
        "table_name": tbl_name,
        "columns": columns,
        "rows": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
