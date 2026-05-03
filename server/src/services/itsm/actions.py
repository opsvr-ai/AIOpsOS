"""Predefined ITSM workflow actions — compose adapter calls into business operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.services.itsm.adapters.base import ItsmAdapter, TicketData

logger = logging.getLogger(__name__)


@dataclass
class ActionResult:
    success: bool
    external_id: str = ""
    url: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


async def create_incident(
    adapter: ItsmAdapter,
    title: str,
    description: str,
    priority: str = "high",
    affected_service: str = "",
    alert_ids: list[str] | None = None,
) -> ActionResult:
    """Create an incident ticket from an alert or manual trigger."""
    ticket = TicketData(
        ticket_type="incident",
        title=title,
        description=description,
        priority=priority,
        affected_service=affected_service,
        custom_fields={"source": "AIOpsOS", "linked_alert_ids": alert_ids or []},
    )
    result = await adapter.create_ticket(ticket)
    return ActionResult(
        success=result.success,
        external_id=result.external_id,
        url=result.url,
        message=result.message or f"Incident {result.external_id} created",
    )


async def create_change(
    adapter: ItsmAdapter,
    title: str,
    description: str,
    risk_level: str = "medium",
    affected_service: str = "",
    implementation_plan: str = "",
) -> ActionResult:
    """Create a change request ticket."""
    ticket = TicketData(
        ticket_type="change",
        title=title,
        description=f"{description}\n\nImplementation Plan:\n{implementation_plan}",
        priority=risk_level,
        affected_service=affected_service,
        custom_fields={"source": "AIOpsOS", "risk_level": risk_level},
    )
    result = await adapter.create_ticket(ticket)
    return ActionResult(
        success=result.success,
        external_id=result.external_id,
        url=result.url,
        message=result.message or f"Change {result.external_id} created",
    )


async def create_task(
    adapter: ItsmAdapter,
    title: str,
    description: str,
    assignee: str = "",
    due_date: str = "",
    parent_ticket_id: str = "",
) -> ActionResult:
    """Create a task ticket, optionally linked to a parent incident/change."""
    ticket = TicketData(
        ticket_type="task",
        title=title,
        description=description,
        assignee=assignee,
        custom_fields={
            "source": "AIOpsOS",
            "due_date": due_date,
            "parent_ticket": parent_ticket_id,
        },
    )
    result = await adapter.create_ticket(ticket)
    return ActionResult(
        success=result.success,
        external_id=result.external_id,
        url=result.url,
        message=result.message or f"Task {result.external_id} created",
    )


async def create_request(
    adapter: ItsmAdapter,
    title: str,
    description: str,
    request_type: str = "service",
    requester: str = "",
) -> ActionResult:
    """Create a service request ticket."""
    ticket = TicketData(
        ticket_type="request",
        title=title,
        description=description,
        custom_fields={
            "source": "AIOpsOS",
            "request_type": request_type,
            "requester": requester,
        },
    )
    result = await adapter.create_ticket(ticket)
    return ActionResult(
        success=result.success,
        external_id=result.external_id,
        url=result.url,
        message=result.message or f"Request {result.external_id} created",
    )


async def escalate_ticket(
    adapter: ItsmAdapter,
    external_id: str,
    reason: str = "",
    new_priority: str = "critical",
) -> ActionResult:
    """Escalate a ticket to higher priority and add escalation note."""
    comment = f"[AIOpsOS ESCALATION] {reason}" if reason else "[AIOpsOS ESCALATION] Auto-escalated due to SLA breach"
    await adapter.add_comment(external_id, comment)
    result = await adapter.update_ticket(external_id, {"priority": new_priority})
    return ActionResult(
        success=result.success,
        external_id=external_id,
        message=f"Ticket {external_id} escalated to {new_priority}",
    )


async def resolve_and_close(
    adapter: ItsmAdapter,
    external_id: str,
    resolution: str,
) -> ActionResult:
    """Resolve a ticket with resolution notes and close it."""
    await adapter.transition_state(external_id, "resolved", resolution)
    result = await adapter.close_ticket(external_id, resolution)
    return ActionResult(
        success=result.success,
        external_id=external_id,
        message=f"Ticket {external_id} resolved and closed",
    )
