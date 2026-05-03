"""Jira adapter — Jira REST API v3."""

from __future__ import annotations

import logging
from base64 import b64encode
from typing import Any

import httpx

from src.services.itsm.adapters.base import ItsmAdapter, TicketData, TicketResult

logger = logging.getLogger(__name__)


class JiraAdapter(ItsmAdapter):
    """Adapter for Jira via REST API v3.

    Config keys:
        base_url:      https://your-domain.atlassian.net
        email:         user@example.com
        api_token:     Jira API token
        project_key:   default project key (e.g. "OPS")
        issue_type_map: {"incident": "Incident", "change": "Change", ...}
    """

    def __init__(self, config: dict[str, Any]):
        self.base_url = (config.get("base_url") or "").rstrip("/")
        self.email = config.get("email") or ""
        self.api_token = config.get("api_token") or ""
        self.project_key = config.get("project_key") or "OPS"
        self.issue_type_map = config.get("issue_type_map") or {
            "incident": "Incident",
            "change": "Change",
            "task": "Task",
            "request": "Service Request",
        }

    def _client(self) -> httpx.AsyncClient:
        token = b64encode(f"{self.email}:{self.api_token}".encode()).decode()
        return httpx.AsyncClient(
            base_url=f"{self.base_url}/rest/api/3",
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    def _issue_type(self, ticket_type: str) -> str:
        return self.issue_type_map.get(ticket_type, "Task")

    async def create_ticket(self, ticket: TicketData) -> TicketResult:
        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": ticket.title[:255],
                "description": ticket.description or ticket.title,
                "issuetype": {"name": self._issue_type(ticket.ticket_type)},
                "priority": {"name": ticket.priority.title()},
            }
        }
        if ticket.assignee:
            payload["fields"]["assignee"] = {"name": ticket.assignee}
        if ticket.custom_fields:
            payload["fields"].update(ticket.custom_fields)

        try:
            async with self._client() as client:
                resp = await client.post("/issue", json=payload)
                data = resp.json()
                key = data.get("key", "")
                return TicketResult(
                    success=resp.is_success,
                    external_id=key or data.get("id", ""),
                    url=f"{self.base_url}/browse/{key}" if key else "",
                    message=data.get("errors", {}).get("summary", "") if not resp.is_success else "",
                    raw_response=data,
                )
        except httpx.RequestError as e:
            logger.error("Jira create_ticket failed: %s", e)
            return TicketResult(success=False, message=str(e))

    async def update_ticket(self, external_id: str, updates: dict[str, Any]) -> TicketResult:
        payload: dict[str, Any] = {"fields": {}}
        field_map = {"title": "summary", "priority": "priority", "description": "description"}
        for k, v in updates.items():
            if k in ("ticket_type", "external_id"):
                continue
            jira_key = field_map.get(k, k)
            if jira_key == "priority":
                payload["fields"][jira_key] = {"name": str(v).title()}
            else:
                payload["fields"][jira_key] = v

        try:
            async with self._client() as client:
                resp = await client.put(f"/issue/{external_id}", json=payload)
                return TicketResult(success=resp.is_success, external_id=external_id, raw_response=resp.json())
        except httpx.RequestError as e:
            return TicketResult(success=False, external_id=external_id, message=str(e))

    async def get_ticket(self, external_id: str) -> TicketResult:
        try:
            async with self._client() as client:
                resp = await client.get(f"/issue/{external_id}")
                data = resp.json()
                return TicketResult(success=resp.is_success, external_id=external_id, raw_response=data)
        except httpx.RequestError as e:
            return TicketResult(success=False, external_id=external_id, message=str(e))

    async def transition_state(self, external_id: str, new_status: str, comment: str = "") -> TicketResult:
        transition_name = {
            "in_progress": "In Progress",
            "resolved": "Done",
            "closed": "Close",
        }.get(new_status, new_status.replace("_", " ").title())

        try:
            async with self._client() as client:
                transitions_resp = await client.get(f"/issue/{external_id}/transitions")
                transitions = (transitions_resp.json() or {}).get("transitions", [])
                transition_id = None
                for t in transitions:
                    if t.get("name", "").lower() == transition_name.lower():
                        transition_id = t["id"]
                        break
                if not transition_id and transitions:
                    transition_id = transitions[0]["id"]
                if not transition_id:
                    return TicketResult(success=False, external_id=external_id, message=f"No transition to '{transition_name}'")

                payload: dict[str, Any] = {"transition": {"id": transition_id}}
                if comment:
                    payload["update"] = {"comment": [{"add": {"body": comment}}]}
                resp = await client.post(f"/issue/{external_id}/transitions", json=payload)
                return TicketResult(success=resp.is_success, external_id=external_id)
        except httpx.RequestError as e:
            return TicketResult(success=False, external_id=external_id, message=str(e))

    async def add_comment(self, external_id: str, comment: str) -> TicketResult:
        try:
            async with self._client() as client:
                resp = await client.post(f"/issue/{external_id}/comment", json={"body": comment})
                return TicketResult(success=resp.is_success, external_id=external_id, raw_response=resp.json())
        except httpx.RequestError as e:
            return TicketResult(success=False, external_id=external_id, message=str(e))

    async def close_ticket(self, external_id: str, resolution: str = "") -> TicketResult:
        return await self.transition_state(external_id, "closed", resolution or "Closed by AIOpsOS")

    async def test_connection(self) -> TicketResult:
        try:
            async with self._client() as client:
                resp = await client.get("/myself")
                user = (resp.json() or {}).get("displayName", "")
                return TicketResult(success=resp.is_success, message=f"Jira connection OK ({user})")
        except httpx.RequestError as e:
            return TicketResult(success=False, message=str(e))
