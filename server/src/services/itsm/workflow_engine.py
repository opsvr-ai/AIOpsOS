"""Workflow Engine — drives ITSM ticket lifecycle based on AIOps events."""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from src.models.base import async_session_factory
from src.models.itsm import ItsmTicket
from src.models.workflow import WorkflowContext
from src.services.itsm import actions
from src.services.itsm.adapters.base import ItsmAdapter, TicketData
from src.services.itsm.adapters.jira import JiraAdapter
from src.services.itsm.adapters.script import ScriptAdapter
from src.services.itsm.adapters.servicenow import ServiceNowAdapter

logger = logging.getLogger(__name__)


def _build_adapter(ds_config: dict[str, Any]) -> ItsmAdapter:
    itsm_system = (ds_config.get("itsm_system") or ds_config.get("type") or "script").lower()
    if itsm_system == "servicenow":
        return ServiceNowAdapter(ds_config)
    elif itsm_system == "jira":
        return JiraAdapter(ds_config)
    else:
        return ScriptAdapter(ds_config)


async def trigger_on_alert(
    alert_title: str,
    alert_description: str,
    severity: str,
    affected_service: str,
    alert_ids: list[str],
    datasource_config: dict[str, Any],
    space_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    adapter = _build_adapter(datasource_config)
    priority = "critical" if severity in ("critical", "P0") else "high"

    result = await actions.create_incident(
        adapter=adapter,
        title=alert_title[:160],
        description=alert_description,
        priority=priority,
        affected_service=affected_service,
        alert_ids=alert_ids,
    )

    wf = await _record_workflow(
        source_system="itsm",
        workflow_id=result.external_id,
        action_type="create",
        title=alert_title[:512],
        status="created" if result.success else "failed",
        payload={
            "alert_ids": alert_ids,
            "severity": severity,
            "ticket_type": "incident",
            "result": {"success": result.success, "external_id": result.external_id, "url": result.url, "message": result.message},
        },
        space_id=space_id,
        session_id=session_id,
    )
    return _wf_to_dict(wf)


async def trigger_on_analysis(
    analysis_title: str,
    analysis_summary: str,
    recommended_action: str,
    ticket_type: str,
    affected_service: str,
    datasource_config: dict[str, Any],
    space_id: str | None = None,
    session_id: str | None = None,
    linked_ticket_id: str | None = None,
    execute_script: str | None = None,
) -> dict[str, Any]:
    adapter = _build_adapter(datasource_config)

    if ticket_type == "change":
        result = await actions.create_change(
            adapter=adapter,
            title=analysis_title,
            description=analysis_summary,
            affected_service=affected_service,
            implementation_plan=recommended_action,
        )
    elif ticket_type == "request":
        result = await actions.create_request(
            adapter=adapter,
            title=analysis_title,
            description=f"{analysis_summary}\n\nRecommended: {recommended_action}",
        )
    else:
        result = await actions.create_task(
            adapter=adapter,
            title=analysis_title,
            description=f"{analysis_summary}\n\nAction: {recommended_action}",
            parent_ticket_id=linked_ticket_id or "",
        )

    wf = await _record_workflow(
        source_system="itsm",
        workflow_id=result.external_id,
        action_type="create",
        title=analysis_title[:512],
        status="created" if result.success else "failed",
        payload={
            "ticket_type": ticket_type,
            "analysis_summary": analysis_summary,
            "recommended_action": recommended_action,
            "linked_ticket_id": linked_ticket_id,
            "result": {"success": result.success, "external_id": result.external_id, "url": result.url, "message": result.message},
        },
        space_id=space_id,
        session_id=session_id,
        linked_ticket_id=_uuid.UUID(linked_ticket_id) if linked_ticket_id else None,
        execute_script=execute_script,
    )
    return _wf_to_dict(wf)


async def link_and_sync(
    itsm_ticket_id: str,
    datasource_config: dict[str, Any],
) -> dict[str, Any]:
    adapter = _build_adapter(datasource_config)
    result = await adapter.get_ticket(itsm_ticket_id)
    if not result.success:
        return {"success": False, "message": result.message}

    raw = result.raw_response
    async with async_session_factory() as db:
        stmt = select(ItsmTicket).where(ItsmTicket.external_id == itsm_ticket_id)
        ticket = (await db.execute(stmt)).scalar_one_or_none()
        if ticket:
            ticket.status = raw.get("status") or raw.get("state") or ticket.status
            ticket.priority = raw.get("priority") or ticket.priority
            ticket.raw_data = raw
            await db.commit()

    return {"success": True, "external_id": itsm_ticket_id, "status": raw.get("status") or raw.get("state")}


async def execute_script_for_ticket(
    workflow_id: str,
    script_content: str,
    interpreter: str = "/bin/bash",
) -> dict[str, Any]:
    async with async_session_factory() as db:
        stmt = select(WorkflowContext).where(WorkflowContext.workflow_id == workflow_id)
        wf = (await db.execute(stmt)).scalar_one_or_none()
        if not wf:
            return {"success": False, "message": "Workflow not found"}

        adapter = ScriptAdapter({
            "script": script_content,
            "interpreter": interpreter,
        })
        ticket = TicketData(external_id=workflow_id, title=wf.title or "")
        result = await adapter._run_script("execute", ticket, {
            "workflow_status": wf.status,
            "workflow_payload": wf.payload or {},
        })

        wf.execution_log = result.message or (result.raw_response and str(result.raw_response)) or ""
        wf.updated_at = datetime.now(UTC)
        await db.commit()

    return {"success": result.success, "external_id": result.external_id, "message": result.message, "url": result.url}


async def escalate_if_stale(
    workflow_id: str,
    datasource_config: dict[str, Any],
    stale_minutes: int = 60,
) -> dict[str, Any]:
    async with async_session_factory() as db:
        stmt = select(WorkflowContext).where(WorkflowContext.workflow_id == workflow_id)
        wf = (await db.execute(stmt)).scalar_one_or_none()
        if not wf:
            return {"success": False, "message": "Workflow not found"}

        age_minutes = (datetime.now(UTC) - wf.created_at).total_seconds() / 60
        if age_minutes < stale_minutes:
            return {"success": True, "message": f"Not stale ({age_minutes:.0f}m < {stale_minutes}m threshold)"}

        adapter = _build_adapter(datasource_config)
        result = await actions.escalate_ticket(
            adapter=adapter,
            external_id=workflow_id,
            reason=f"Auto-escalated: ticket open for {age_minutes:.0f} minutes",
        )

        wf.status = "escalated"
        wf.updated_at = datetime.now(UTC)
        await db.commit()

    return {"success": result.success, "message": result.message}


async def _record_workflow(
    source_system: str,
    workflow_id: str,
    action_type: str,
    title: str = "",
    status: str = "pending",
    payload: dict[str, Any] | None = None,
    space_id: str | None = None,
    session_id: str | None = None,
    linked_ticket_id: Any = None,
    execute_script: str | None = None,
) -> WorkflowContext:
    async with async_session_factory() as db:
        wf = WorkflowContext(
            source_system=source_system,
            workflow_id=workflow_id,
            action_type=action_type,
            title=title,
            status=status,
            payload=payload or {},
            space_id=_uuid.UUID(space_id) if space_id else None,
            session_id=_uuid.UUID(session_id) if session_id else None,
            linked_ticket_id=linked_ticket_id,
            execute_script=execute_script,
        )
        db.add(wf)
        await db.commit()
        await db.refresh(wf)
    return wf


async def list_workflows(
    space_id: str | None = None,
    status: str | None = None,
    action_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    async with async_session_factory() as db:
        query = select(WorkflowContext)
        if space_id:
            query = query.where(WorkflowContext.space_id == _uuid.UUID(space_id))
        if status:
            query = query.where(WorkflowContext.status == status)
        if action_type:
            query = query.where(WorkflowContext.action_type == action_type)

        count_q = select(func.count()).select_from(query.subquery())
        total = (await db.execute(count_q)).scalar() or 0

        result = await db.execute(
            query.order_by(WorkflowContext.created_at.desc()).offset(offset).limit(limit)
        )
        workflows = result.scalars().all()
        return [_wf_to_dict(w) for w in workflows], total


async def get_workflow(workflow_id: str) -> dict[str, Any] | None:
    async with async_session_factory() as db:
        try:
            uid = _uuid.UUID(workflow_id)
            stmt = select(WorkflowContext).where(WorkflowContext.id == uid)
        except ValueError:
            stmt = select(WorkflowContext).where(WorkflowContext.workflow_id == workflow_id)
        wf = (await db.execute(stmt)).scalar_one_or_none()
        if not wf:
            return None
    return _wf_to_dict(wf)


def _wf_to_dict(wf: WorkflowContext) -> dict[str, Any]:
    return {
        "id": str(wf.id),
        "source_system": wf.source_system,
        "workflow_id": wf.workflow_id,
        "action_type": wf.action_type,
        "title": wf.title,
        "status": wf.status,
        "payload": wf.payload,
        "execute_script": wf.execute_script,
        "execution_log": wf.execution_log,
        "linked_ticket_id": str(wf.linked_ticket_id) if wf.linked_ticket_id else None,
        "linked_session_id": str(wf.session_id) if wf.session_id else None,
        "space_id": str(wf.space_id) if wf.space_id else None,
        "created_at": wf.created_at.isoformat() if wf.created_at else None,
        "updated_at": wf.updated_at.isoformat() if wf.updated_at else None,
    }
