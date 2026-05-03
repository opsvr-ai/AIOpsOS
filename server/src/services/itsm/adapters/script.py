"""Script adapter — execute user scripts for custom ITSM system integration.  # noqa: E501

This is the universal adapter for any ITSM system that doesn't have a native adapter.
Users write scripts (bash/python/anything) that accept JSON on stdin and return JSON on
stdout. The platform handles invocation, timeout, and result parsing.

=== SCRIPT CONTRACT ===

Your script receives a JSON object on stdin with this structure:

    {
      "action": "create_ticket | update_ticket | get_ticket | transition_state | add_comment | close_ticket",
      "config": { ... },           // your datasource config (api keys, endpoints, etc.)
      "ticket": {
        "external_id": "",         // empty for create, filled for update/transition/close
        "ticket_type": "incident | change | task | request",
        "title": "",
        "description": "",
        "status": "",
        "priority": "",
        "affected_service": "",
        "assignee": "",
        "custom_fields": {}
      },
      "params": {}                 // action-specific params (e.g. new_status, comment, resolution)
    }

Your script must write a JSON object to stdout:

    {
      "success": true | false,
      "external_id": "TICKET-123",
      "url": "https://itsm.example.com/tickets/TICKET-123",
      "message": "optional message / error description"
    }

Exit code 0 = success, non-zero = error (message taken from stderr).

=== EXAMPLE: Bash script for generic REST API ===

    #!/bin/bash
    INPUT=$(cat)
    ACTION=$(echo "$INPUT" | jq -r '.action')
    API_URL=$(echo "$INPUT" | jq -r '.config.api_url')
    API_KEY=$(echo "$INPUT" | jq -r '.config.api_key')

    case "$ACTION" in
      create_ticket)
        BODY=$(echo "$INPUT" | jq '{title: .ticket.title, description: .ticket.description, type: .ticket.ticket_type, priority: .ticket.priority}')
        RESP=$(curl -s -X POST "$API_URL/tickets" -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d "$BODY")
        ID=$(echo "$RESP" | jq -r '.id')
        echo "{\"success\": true, \"external_id\": \"$ID\", \"url\": \"$API_URL/tickets/$ID\"}"
        ;;
      update_ticket)
        EID=$(echo "$INPUT" | jq -r '.ticket.external_id')
        BODY=$(echo "$INPUT" | jq '{status: .params.new_status}')
        curl -s -X PATCH "$API_URL/tickets/$EID" -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d "$BODY" | jq '{success: true, external_id: .id}'
        ;;
      get_ticket)
        EID=$(echo "$INPUT" | jq -r '.ticket.external_id')
        curl -s "$API_URL/tickets/$EID" -H "Authorization: Bearer $API_KEY" | jq '{success: true, external_id: .id}'
        ;;
      close_ticket)
        EID=$(echo "$INPUT" | jq -r '.ticket.external_id')
        RES=$(echo "$INPUT" | jq -r '.params.resolution')
        BODY=$(jq -n --arg res "$RES" '{status: "closed", resolution: $res}')
        curl -s -X PATCH "$API_URL/tickets/$EID" -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d "$BODY" | jq '{success: true, external_id: .id}'
        ;;
    esac

=== EXAMPLE: Python script for custom ITSM ===

    #!/usr/bin/env python3
    import sys, json, requests

    data = json.load(sys.stdin)
    action = data["action"]
    config = data["config"]
    ticket = data["ticket"]
    params = data.get("params", {})

    headers = {"Authorization": f"Bearer {config['api_key']}"}
    base = config["api_url"].rstrip("/")

    if action == "create_ticket":
        r = requests.post(f"{base}/tickets", json={
            "title": ticket["title"],
            "description": ticket.get("description", ""),
            "type": ticket["ticket_type"],
            "priority": ticket["priority"],
        }, headers=headers)
        resp = r.json()
        print(json.dumps({"success": True, "external_id": resp["id"], "url": f"{base}/tickets/{resp['id']}"}))

    elif action == "update_ticket":
        r = requests.patch(f"{base}/tickets/{ticket['external_id']}", json=params, headers=headers)
        print(json.dumps({"success": True, "external_id": ticket["external_id"]}))

    elif action == "close_ticket":
        r = requests.patch(f"{base}/tickets/{ticket['external_id']}", json={
            "status": "closed",
            "resolution": params.get("resolution", "Closed by AIOpsOS"),
        }, headers=headers)
        print(json.dumps({"success": True, "external_id": ticket["external_id"]}))

=== TESTING YOUR SCRIPT LOCALLY ===

    echo '{"action":"create_ticket","config":{"api_url":"http://localhost:8080","api_key":"test"},"ticket":{"title":"Test","ticket_type":"incident","priority":"high"},"params":{}}' | bash your_script.sh
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any

from src.services.itsm.adapters.base import ItsmAdapter, TicketData, TicketResult

logger = logging.getLogger(__name__)

SCRIPT_TIMEOUT_SECONDS = 30
MAX_OUTPUT_BYTES = 256 * 1024


class ScriptAdapter(ItsmAdapter):
    """Universal adapter using a user-provided script.

    Config keys:
        script:        inline script content (string) — mutually exclusive with script_path
        script_path:   absolute path to script file on server
        interpreter:   "/bin/bash" (default), "/usr/bin/python3", or path to binary
        env:           dict of extra environment variables passed to the script
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.script_content = config.get("script") or ""
        self.script_path = config.get("script_path") or ""
        self.interpreter = config.get("interpreter") or "/bin/bash"
        self.env = config.get("env") or {}

    def _script_file(self) -> str:
        if self.script_path:
            return self.script_path
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, prefix="itsm_script_")
        tmp.write(self.script_content)
        tmp.close()
        os.chmod(tmp.name, 0o700)
        return tmp.name

    async def _run_script(self, action: str, ticket: TicketData, params: dict[str, Any] | None = None) -> TicketResult:
        script_file = self._script_file()
        stdin_payload = json.dumps({
            "action": action,
            "config": self.config,
            "ticket": {
                "external_id": ticket.external_id,
                "ticket_type": ticket.ticket_type,
                "title": ticket.title,
                "description": ticket.description,
                "status": ticket.status,
                "priority": ticket.priority,
                "affected_service": ticket.affected_service,
                "assignee": ticket.assignee,
                "custom_fields": ticket.custom_fields,
            },
            "params": params or {},
        })

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.interpreter, script_file,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **self.env},
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin_payload.encode()),
                timeout=SCRIPT_TIMEOUT_SECONDS,
            )

            if proc.returncode != 0:
                err = stderr.decode(errors="replace")[:500]
                logger.error("Script exited %d: %s", proc.returncode, err)
                return TicketResult(success=False, message=err or f"Script exit code {proc.returncode}")

            output = stdout.decode(errors="replace")[:MAX_OUTPUT_BYTES].strip()
            if not output:
                return TicketResult(success=False, message="Script produced no output")

            result = json.loads(output)
            return TicketResult(
                success=bool(result.get("success", True)),
                external_id=str(result.get("external_id") or ""),
                url=str(result.get("url") or ""),
                message=str(result.get("message") or ""),
                raw_response=result,
            )
        except TimeoutError:
            if proc is not None:
                proc.kill()
            logger.error("Script timed out after %ds", SCRIPT_TIMEOUT_SECONDS)
            return TicketResult(success=False, message=f"Script timed out after {SCRIPT_TIMEOUT_SECONDS}s")
        except json.JSONDecodeError as e:
            logger.error("Script output not valid JSON: %s", e)
            return TicketResult(success=False, message=f"Invalid JSON output: {e}")
        except Exception as e:
            logger.error("Script execution failed: %s", e)
            return TicketResult(success=False, message=str(e))
        finally:
            if not self.script_path and os.path.exists(script_file):
                os.unlink(script_file)

    async def create_ticket(self, ticket: TicketData) -> TicketResult:
        return await self._run_script("create_ticket", ticket)

    async def update_ticket(self, external_id: str, updates: dict[str, Any]) -> TicketResult:
        ticket = TicketData(external_id=external_id)
        return await self._run_script("update_ticket", ticket, {"updates": updates})

    async def get_ticket(self, external_id: str) -> TicketResult:
        ticket = TicketData(external_id=external_id)
        return await self._run_script("get_ticket", ticket)

    async def transition_state(self, external_id: str, new_status: str, comment: str = "") -> TicketResult:
        ticket = TicketData(external_id=external_id)
        return await self._run_script("transition_state", ticket, {"new_status": new_status, "comment": comment})

    async def add_comment(self, external_id: str, comment: str) -> TicketResult:
        ticket = TicketData(external_id=external_id)
        return await self._run_script("add_comment", ticket, {"comment": comment})

    async def close_ticket(self, external_id: str, resolution: str = "") -> TicketResult:
        ticket = TicketData(external_id=external_id)
        return await self._run_script("close_ticket", ticket, {"resolution": resolution})

    async def test_connection(self) -> TicketResult:
        ticket = TicketData(title="__connection_test__")
        return await self._run_script("test_connection", ticket)

    @staticmethod
    def help_text() -> str:
        """Return the full help documentation for the script contract."""
        return __doc__ or ""
