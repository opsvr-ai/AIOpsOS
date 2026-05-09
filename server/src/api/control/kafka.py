"""Admin-facing Kafka management REST API.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.8 / R-5.2 ~ R-5.6.

All mutating endpoints require :func:`src.api.deps.require_admin`. Each
mutating call emits an audit log line; once the ``audit_logs`` table exists
(future migration) we'll route through a structured audit service.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from src.api.deps import get_current_user, require_admin
from src.schemas.kafka import (
    AlterTopicBody,
    BrowserMessageOut,
    ConsumerGroupDetailOut,
    ConsumerGroupOut,
    CreateTopicBody,
    DLQEntryOut,
    DLQIdsBody,
    MemberOut,
    PartitionLagOut,
    RegisterSchemaBody,
    ReplayBody,
    ReplayReportOut,
    ResetOffsetBody,
    SchemaOut,
    TopicOut,
)
from src.services.kafka import (
    KafkaAdminService,
    KafkaBrowser,
    KafkaDLQManager,
    KafkaSchemaRegistry,
)
from src.services.kafka.admin import ConsumerGroupDetail, TopicInfo
from src.services.kafka.browser import BrowserMessage
from src.services.kafka.dlq import DLQEntry

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/kafka", tags=["kafka"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _audit(user: Any, action: str, **fields: Any) -> None:
    """Structured audit log stand-in.

    TODO: emit structured audit event once an ``audit_logs`` table / service
    is provisioned (see R-5.8). For now we write to the application log with
    a recognisable prefix so ops can scrape it.
    """
    actor = getattr(user, "username", None) or getattr(user, "id", "<unknown>")
    payload = ", ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("AUDIT kafka action=%s actor=%s %s", action, actor, payload)


def _topic_to_out(t: TopicInfo) -> TopicOut:
    return TopicOut(
        name=t.name,
        partitions=t.partitions,
        replication_factor=t.replication_factor,
        configs=t.configs,
        internal=t.internal,
    )


def _group_detail_to_out(d: ConsumerGroupDetail) -> ConsumerGroupDetailOut:
    return ConsumerGroupDetailOut(
        group_id=d.group_id,
        state=d.state,
        protocol=d.protocol,
        protocol_type=d.protocol_type,
        members=[
            MemberOut(
                member_id=m.member_id,
                client_id=m.client_id,
                client_host=m.client_host,
                assignments=list(m.assignments),
            )
            for m in d.members
        ],
        lags=[
            PartitionLagOut(
                topic=p.topic,
                partition=p.partition,
                current_offset=p.current_offset,
                end_offset=p.end_offset,
                lag=p.lag,
            )
            for p in d.lags
        ],
        total_lag=d.total_lag,
    )


def _browser_to_out(m: BrowserMessage) -> BrowserMessageOut:
    return BrowserMessageOut(
        topic=m.topic,
        partition=m.partition,
        offset=m.offset,
        timestamp=m.timestamp,
        key=m.key,
        value=m.value,
        headers=dict(m.headers),
    )


def _dlq_to_out(e: DLQEntry) -> DLQEntryOut:
    return DLQEntryOut(
        id=e.id,
        original_topic=e.original_topic,
        original_partition=e.original_partition,
        original_offset=e.original_offset,
        original_key=e.original_key,
        original_value=e.original_value,
        original_headers=dict(e.original_headers),
        failure_reason=e.failure_reason,
        failed_at=e.failed_at,
        attempt_count=e.attempt_count,
        tags=dict(e.tags),
        dlq_topic=e.dlq_topic,
        dlq_partition=e.dlq_partition,
        dlq_offset=e.dlq_offset,
    )


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------


@router.get("/topics", response_model=list[TopicOut])
async def list_topics(
    include_internal: bool = Query(False),
    _=Depends(get_current_user),
) -> list[TopicOut]:
    async with KafkaAdminService() as admin:
        topics = await admin.list_topics(include_internal=include_internal)
    return [_topic_to_out(t) for t in topics]


@router.post("/topics", response_model=TopicOut, status_code=201)
async def create_topic(
    body: CreateTopicBody,
    user=Depends(require_admin),
) -> TopicOut:
    async with KafkaAdminService() as admin:
        await admin.create_topic(
            body.name,
            partitions=body.partitions,
            replication_factor=body.replication_factor,
            configs=body.configs or None,
        )
        info = await admin.describe_topic(body.name)
    _audit(user, "create_topic", topic=body.name, partitions=body.partitions)
    return _topic_to_out(info)


@router.get("/topics/{name}", response_model=TopicOut)
async def describe_topic(name: str, _=Depends(get_current_user)) -> TopicOut:
    async with KafkaAdminService() as admin:
        try:
            info = await admin.describe_topic(name)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _topic_to_out(info)


@router.put("/topics/{name}", response_model=TopicOut)
async def alter_topic(
    name: str,
    body: AlterTopicBody,
    user=Depends(require_admin),
) -> TopicOut:
    if body.partitions is None and not body.configs:
        raise HTTPException(
            status_code=400, detail="at least one of partitions or configs required"
        )
    async with KafkaAdminService() as admin:
        await admin.alter_topic(
            name, partitions=body.partitions, configs=body.configs or None
        )
        info = await admin.describe_topic(name)
    _audit(
        user,
        "alter_topic",
        topic=name,
        partitions=body.partitions,
        configs=list((body.configs or {}).keys()),
    )
    return _topic_to_out(info)


@router.delete("/topics/{name}", status_code=204, response_model=None)
async def delete_topic(
    name: str,
    confirm: bool = Query(False),
    user=Depends(require_admin),
) -> None:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true query param required")
    async with KafkaAdminService() as admin:
        try:
            await admin.delete_topic(name, confirm=True)
        except ValueError as exc:  # pragma: no cover - confirm already enforced
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(user, "delete_topic", topic=name)
    return None


# ---------------------------------------------------------------------------
# Consumer groups
# ---------------------------------------------------------------------------


@router.get("/consumer-groups", response_model=list[ConsumerGroupOut])
async def list_consumer_groups(_=Depends(get_current_user)) -> list[ConsumerGroupOut]:
    async with KafkaAdminService() as admin:
        groups = await admin.list_consumer_groups()
    return [
        ConsumerGroupOut(
            group_id=g.group_id, state=g.state, protocol_type=g.protocol_type
        )
        for g in groups
    ]


@router.get("/consumer-groups/{group_id}", response_model=ConsumerGroupDetailOut)
async def describe_consumer_group(
    group_id: str, _=Depends(get_current_user)
) -> ConsumerGroupDetailOut:
    async with KafkaAdminService() as admin:
        try:
            detail = await admin.describe_group(group_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _group_detail_to_out(detail)


@router.post("/consumer-groups/{group_id}/reset-offset")
async def reset_offset(
    group_id: str,
    body: ResetOffsetBody,
    x_confirm: str | None = Header(default=None, alias="X-Confirm"),
    user=Depends(require_admin),
) -> dict[str, Any]:
    if (x_confirm or "").lower() != "true":
        raise HTTPException(
            status_code=400,
            detail="X-Confirm: true header required for offset reset",
        )
    async with KafkaAdminService() as admin:
        new_offset = await admin.reset_offset(
            group_id=group_id,
            topic=body.topic,
            partition=body.partition,
            target=body.target,
        )
    _audit(
        user,
        "reset_offset",
        group_id=group_id,
        topic=body.topic,
        partition=body.partition,
        target=body.target,
        new_offset=new_offset,
    )
    return {"group_id": group_id, "topic": body.topic, "partition": body.partition, "new_offset": new_offset}


# ---------------------------------------------------------------------------
# Message browser
# ---------------------------------------------------------------------------


@router.get("/browser", response_model=list[BrowserMessageOut])
async def browse_messages(
    topic: str = Query(..., min_length=1),
    partition: int | None = Query(None, ge=0),
    start_offset: str = Query("latest"),
    limit: int = Query(50, ge=1, le=500),
    key_regex: str | None = Query(None),
    value_regex: str | None = Query(None),
    header_regex: str | None = Query(None),
    _=Depends(get_current_user),
) -> list[BrowserMessageOut]:
    browser = KafkaBrowser()
    # Permit negative / int start_offset via numeric parse fallthrough.
    so: int | str
    try:
        so = int(start_offset)
    except ValueError:
        if start_offset not in ("earliest", "latest"):
            raise HTTPException(
                status_code=400,
                detail="start_offset must be 'earliest', 'latest', or an int",
            ) from None
        so = start_offset
    try:
        msgs = await browser.fetch(
            topic,
            partition=partition,
            start_offset=so,
            limit=limit,
            key_regex=key_regex,
            value_regex=value_regex,
            header_regex=header_regex,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [_browser_to_out(m) for m in msgs]


# ---------------------------------------------------------------------------
# DLQ
# ---------------------------------------------------------------------------


@router.get("/dlq", response_model=list[DLQEntryOut])
async def list_dlq_entries(
    topic: str | None = Query(None),
    since: datetime | None = Query(None),
    tag_filter: str | None = Query(
        None, description='Comma-separated k=v pairs, e.g. "cause=schema,env=prod"'
    ),
    limit: int = Query(100, ge=1, le=1000),
    _=Depends(get_current_user),
) -> list[DLQEntryOut]:
    tags: dict[str, str] | None = None
    if tag_filter:
        tags = {}
        for pair in tag_filter.split(","):
            if not pair.strip():
                continue
            if "=" not in pair:
                raise HTTPException(
                    status_code=400, detail=f"invalid tag filter segment: {pair!r}"
                )
            k, v = pair.split("=", 1)
            tags[k.strip()] = v.strip()
    mgr = KafkaDLQManager()
    entries = await mgr.list_entries(topic=topic, since=since, tag_filter=tags, limit=limit)
    return [_dlq_to_out(e) for e in entries]


@router.post("/dlq/replay", response_model=ReplayReportOut)
async def replay_dlq(
    body: ReplayBody,
    user=Depends(require_admin),
) -> ReplayReportOut:
    mgr = KafkaDLQManager()
    report = await mgr.replay(body.entry_ids, target_topic=body.target_topic)
    _audit(
        user,
        "dlq_replay",
        count=len(body.entry_ids),
        replayed=report.replayed,
        skipped=report.skipped,
        target_topic=body.target_topic,
    )
    return ReplayReportOut(**report.as_dict())


@router.post("/dlq/discard")
async def discard_dlq(
    body: DLQIdsBody,
    user=Depends(require_admin),
) -> dict[str, int]:
    mgr = KafkaDLQManager()
    n = await mgr.discard(body.entry_ids)
    _audit(user, "dlq_discard", count=n)
    return {"discarded": n}


@router.post("/dlq/mark-handled")
async def mark_dlq_handled(
    body: DLQIdsBody,
    user=Depends(require_admin),
) -> dict[str, int]:
    mgr = KafkaDLQManager()
    n = await mgr.mark_handled(body.entry_ids)
    _audit(user, "dlq_mark_handled", count=n)
    return {"handled": n}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


@router.get("/schemas", response_model=list[SchemaOut])
async def list_schemas(
    topic: str | None = Query(None),
    _=Depends(get_current_user),
) -> list[SchemaOut]:
    registry = KafkaSchemaRegistry()
    rows = await registry.list(topic=topic)
    return [SchemaOut.model_validate(r) for r in rows]


@router.post("/schemas", response_model=SchemaOut, status_code=201)
async def register_schema(
    body: RegisterSchemaBody,
    user=Depends(require_admin),
) -> SchemaOut:
    registry = KafkaSchemaRegistry()
    try:
        await registry.register(
            topic=body.topic,
            version=body.version,
            schema=body.schema_,
            description=body.description,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await registry.get(body.topic, body.version)
    _audit(
        user, "register_schema", topic=body.topic, version=body.version
    )
    assert row is not None
    return SchemaOut.model_validate(row)


@router.delete("/schemas/{topic}/{version}", status_code=204, response_model=None)
async def delete_schema(
    topic: str,
    version: int,
    user=Depends(require_admin),
) -> None:
    registry = KafkaSchemaRegistry()
    await registry.delete(topic, version)
    _audit(user, "delete_schema", topic=topic, version=version)
    return None
