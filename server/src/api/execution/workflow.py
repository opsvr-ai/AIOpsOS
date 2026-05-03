"""Workflow API — ITSM ticket linkage, script execution, and analysis."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.deps import get_current_user
from src.services.itsm import workflow_engine as wf_engine
from src.services.itsm.adapters.script import ScriptAdapter

workflow_router = APIRouter(prefix="/api/v1/workflow", tags=["ITSM Workflow"])


# ── Request Schemas ──

class TriggerRequest(BaseModel):
    ticket_type: str = Field(..., description="incident / change / task / request")
    title: str
    description: str = ""
    priority: str = "medium"
    affected_service: str = ""
    datasource_config: dict = Field(default_factory=dict)
    linked_ticket_id: str | None = None
    execute_script: str | None = None


class TransitionRequest(BaseModel):
    new_status: str
    comment: str = ""
    datasource_config: dict = Field(default_factory=dict)


class ExecuteScriptRequest(BaseModel):
    script_content: str
    interpreter: str = "/bin/bash"


class AnalyzeScriptRequest(BaseModel):
    script_content: str
    itsm_system_name: str = ""


# ── Routes ──

@workflow_router.get("/script-help")
async def get_script_help():
    """Return the full script contract documentation for custom ITSM adapters."""
    return {"help": ScriptAdapter.help_text()}


@workflow_router.post("/analyze-script")
async def analyze_script(body: AnalyzeScriptRequest):
    """Analyze a user-submitted script and suggest the target message format.

    Returns detected input/output patterns and recommended field mapping config.
    """
    script = body.script_content.strip()
    if not script:
        raise HTTPException(status_code=400, detail="Script content is empty")
    return _inspect_script(script, body.itsm_system_name)


@workflow_router.post("/trigger")
async def trigger_workflow(
    body: TriggerRequest,
    user=Depends(get_current_user),
):
    """Manually trigger creation of an ITSM ticket via a linked datasource."""
    result = await wf_engine.trigger_on_analysis(
        analysis_title=body.title,
        analysis_summary=body.description,
        recommended_action="",
        ticket_type=body.ticket_type,
        affected_service=body.affected_service,
        datasource_config=body.datasource_config,
        linked_ticket_id=body.linked_ticket_id,
        execute_script=body.execute_script,
    )
    if not result.get("success") and result.get("status") == "failed":
        detail = result.get("payload", {}).get("result", {}).get("message", "Creation failed")
        raise HTTPException(status_code=502, detail=detail)
    return result


@workflow_router.post("/{workflow_id}/transition")
async def transition_workflow(
    workflow_id: str,
    body: TransitionRequest,
    user=Depends(get_current_user),
):
    """Transition a linked ticket to a new status."""
    wf = await wf_engine.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    adapter = wf_engine._build_adapter(body.datasource_config)
    result = await adapter.transition_state(wf["workflow_id"], body.new_status, body.comment)

    if not result.success:
        raise HTTPException(status_code=502, detail=result.message)
    return {"success": True, "external_id": result.external_id, "new_status": body.new_status}


@workflow_router.post("/{workflow_id}/execute")
async def execute_script(
    workflow_id: str,
    body: ExecuteScriptRequest,
    user=Depends(get_current_user),
):
    """Execute a custom script against an existing workflow ticket."""
    result = await wf_engine.execute_script_for_ticket(
        workflow_id=workflow_id,
        script_content=body.script_content,
        interpreter=body.interpreter,
    )
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("message", "Script execution failed"))
    return result


@workflow_router.get("/{workflow_id}")
async def get_workflow_detail(workflow_id: str, user=Depends(get_current_user)):
    """Get a single workflow record with full details."""
    wf = await wf_engine.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf


@workflow_router.get("")
async def list_workflows(
    status: str | None = Query(None),
    action_type: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List workflow records with filtering and pagination."""
    items, total = await wf_engine.list_workflows(
        status=status,
        action_type=action_type,
        offset=offset,
        limit=limit,
    )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


# ── Script Analysis ──

def _inspect_script(script: str, itsm_name: str = "") -> dict:
    """Inspect a user script and produce field mapping guidance."""
    lines = script.split("\n")
    input_fields: set[str] = set()
    output_fields: set[str] = set()
    uses_curl = False
    uses_python_requests = False
    actions_found: list[str] = []

    for line in lines:
        if "curl" in line:
            uses_curl = True
        if "requests" in line or "requests." in line:
            uses_python_requests = True

        for action in ("create_ticket", "update_ticket", "get_ticket", "close_ticket", "transition_state"):
            if action in line and action not in actions_found:
                actions_found.append(action)

        for field in ("title", "description", "ticket_type", "priority", "status", "external_id", "affected_service", "assignee"):
            if f'.ticket.{field}' in line or f'ticket["{field}"]' in line or f"ticket['{field}']" in line:
                input_fields.add(field)

        for field in ("api_url", "api_key", "api_token", "instance_url", "endpoint", "token", "secret"):
            if f'.config.{field}' in line or f'config["{field}"]' in line or f"config['{field}']" in line:
                input_fields.add(f"config.{field}")

        for field in ("external_id", "url", "message", "success"):
            if f'"{field}"' in line or f"'{field}'" in line:
                output_fields.add(field)

    suggested_config: dict[str, str] = {}
    for f in sorted(input_fields):
        if f.startswith("config."):
            suggested_config[f] = f"<your {f.split('.')[1]} value>"
        else:
            suggested_config[f] = f"mapped to target system field for '{f}'"

    guessed_system = itsm_name or ""
    if not guessed_system:
        script_lower = script.lower()
        if "servicenow" in script_lower or "service-now" in script_lower or "sys_id" in script_lower:
            guessed_system = "servicenow"
        elif "jira" in script_lower or "atlassian" in script_lower or "issuetype" in script_lower:
            guessed_system = "jira"

    return {
        "actions_detected": actions_found or ["create_ticket"],
        "input_fields": sorted(input_fields) or ["title", "description", "ticket_type", "priority"],
        "output_fields": sorted(output_fields) or ["success", "external_id"],
        "script_type": "curl" if uses_curl else "python" if uses_python_requests else "generic",
        "guessed_itsm_system": guessed_system or "custom",
        "suggested_config": suggested_config,
        "test_command": _generate_test_command(script, uses_curl, uses_python_requests),
    }


def _generate_test_command(script: str, uses_curl: bool, uses_python: bool) -> str:
    stdin_json = (
        '{"action":"create_ticket","config":{"api_url":"http://localhost:8080","api_key":"test"},'
        '"ticket":{"title":"Test Ticket","ticket_type":"incident","priority":"high"},"params":{}}'
    )
    if uses_python:
        return f"echo '{stdin_json}' | python3 -c '...'"
    return f"echo '{stdin_json}' | bash your_script.sh"
