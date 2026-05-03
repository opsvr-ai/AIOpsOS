"""ServiceNow adapter — REST Table API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.services.itsm.adapters.base import ItsmAdapter, TicketData, TicketResult

logger = logging.getLogger(__name__)


class ServiceNowAdapter(ItsmAdapter):
    """Adapter for ServiceNow via REST Table API.

    Config keys:
        instance_url: https://dev12345.service-now.com
        username:     basic-auth username
        password:     basic-auth password
        table_map:    {"incident": "incident", "change": "change_request", ...}
    """

    def __init__(self, config: dict[str, Any]):
        self.instance = (config.get("instance_url") or "").rstrip("/")
        self.username = config.get("username") or ""
        self.password = config.get("password") or ""
        self.table_map = config.get("table_map") or {
            "incident": "incident",
            "change": "change_request",
            "task": "sc_task",
            "request": "sc_request",
        }

    def _table(self, ticket_type: str) -> str:
        return self.table_map.get(ticket_type, "incident")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.instance}/api/now",
            auth=(self.username, self.password),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )

    async def create_ticket(self, ticket: TicketData) -> TicketResult:
        payload: dict[str, Any] = {
            "short_description": ticket.title[:160],
            "description": ticket.description or ticket.title,
            "priority": _sn_priority(ticket.priority),
            "state": "1",
        }
        if ticket.affected_service:
            payload["cmdb_ci"] = ticket.affected_service
        if ticket.assignee:
            payload["assigned_to"] = ticket.assignee
        payload.update(ticket.custom_fields)

        try:
            async with self._client() as client:
                resp = await client.post(f"/table/{self._table(ticket.ticket_type)}", json=payload)
                data = resp.json()
                sid = (data.get("result") or {}).get("sys_id", "")
                number = (data.get("result") or {}).get("number", "")
                return TicketResult(
                    success=resp.is_success,
                    external_id=sid or number,
                    url=f"{self.instance}/nav_to.do?uri={self._table(ticket.ticket_type)}.do?sys_id={sid}",
                    raw_response=data,
                )
        except httpx.RequestError as e:
            logger.error("ServiceNow create_ticket failed: %s", e)
            return TicketResult(success=False, message=str(e))

    async def update_ticket(self, external_id: str, updates: dict[str, Any]) -> TicketResult:
        try:
            async with self._client() as client:
                resp = await client.patch(
                    f"/table/{self._table(updates.get('ticket_type', 'incident'))}/{external_id}",
                    json=updates,
                )
                return TicketResult(success=resp.is_success, external_id=external_id, raw_response=resp.json())
        except httpx.RequestError as e:
            return TicketResult(success=False, external_id=external_id, message=str(e))

    async def get_ticket(self, external_id: str) -> TicketResult:
        try:
            async with self._client() as client:
                resp = await client.get(f"/table/task/{external_id}")
                data = resp.json()
                return TicketResult(success=resp.is_success, external_id=external_id, raw_response=data)
        except httpx.RequestError as e:
            return TicketResult(success=False, external_id=external_id, message=str(e))

    async def transition_state(self, external_id: str, new_status: str, comment: str = "") -> TicketResult:
        state_map = {"new": "1", "in_progress": "2", "on_hold": "3", "resolved": "6", "closed": "7"}
        payload: dict[str, Any] = {"state": state_map.get(new_status, "1")}
        if comment:
            payload["work_notes"] = comment
        return await self.update_ticket(external_id, payload)

    async def add_comment(self, external_id: str, comment: str) -> TicketResult:
        return await self.update_ticket(external_id, {"work_notes": comment})

    async def close_ticket(self, external_id: str, resolution: str = "") -> TicketResult:
        payload: dict[str, Any] = {"state": "7", "close_notes": resolution or "Closed by AIOpsOS"}
        return await self.update_ticket(external_id, payload)

    async def test_connection(self) -> TicketResult:
        try:
            async with self._client() as client:
                resp = await client.get("/table/sys_user/1")
                return TicketResult(success=resp.is_success, message="ServiceNow connection OK")
        except httpx.RequestError as e:
            return TicketResult(success=False, message=str(e))


def _sn_priority(priority: str) -> str:
    return {"critical": "1", "high": "2", "medium": "3", "low": "4"}.get(priority, "3")
