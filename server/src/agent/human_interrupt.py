"""Human-in-the-loop interrupt tools for the DeepAgents runtime.

Provides two tools the agent can call:
- ``request_approval`` — ask the user to confirm a sensitive action
- ``request_input`` — ask the user to fill in missing parameters

Each tool saves an Interrupt via InterruptManager and returns a special
marker string. The chat_stream endpoint detects this marker in on_tool_end
events, emits an SSE interrupt event, and terminates the stream. The
frontend displays the appropriate card. When the user responds, a new
request resumes the agent with the interrupt response injected.

The agent's system prompt instructs it to stop immediately after calling
these tools and output only the tool result.
"""

import json
import logging
from typing import Any

from src.services.interrupt_manager import interrupt_manager

logger = logging.getLogger(__name__)

INTERRUPT_MARKER = "__AIOPOS_INTERRUPT__"


class HumanInterruptException(Exception):
    """Legacy exception for non-DeepAgents paths (kept for graph.py compatibility)."""

    def __init__(self, interrupt_id: str, interrupt_type: str, data: dict[str, Any]):
        self.interrupt_id = interrupt_id
        self.interrupt_type = interrupt_type
        self.data = data
        super().__init__(f"Human interrupt requested: {interrupt_type}")


def build_interrupt_marker(interrupt_id: str, interrupt_type: str, data: dict[str, Any]) -> str:
    return json.dumps({
        "marker": INTERRUPT_MARKER,
        "interrupt_id": interrupt_id,
        "type": interrupt_type,
        "data": data,
    }, ensure_ascii=False)


def parse_interrupt_marker(output: str) -> dict[str, Any] | None:
    """Check if a tool output contains the interrupt marker."""
    if INTERRUPT_MARKER not in output:
        return None
    try:
        # Extract the JSON object containing the marker
        start = output.index("{")
        end = output.rindex("}") + 1
        parsed = json.loads(output[start:end])
        if parsed.get("marker") == INTERRUPT_MARKER:
            return parsed
    except (ValueError, json.JSONDecodeError):
        pass
    return None


def _get_session_id() -> str:
    from src.agent.deep_agent import get_current_user

    ctx = get_current_user()
    return ctx.get("session_id", "unknown")


async def _request_approval(
    action: str,
    details: str = "",
    risk_level: str = "medium",
    code_snippet: str = "",
    impact_scope: str = "",
) -> str:
    """Request user approval before executing a sensitive or destructive operation."""
    session_id = _get_session_id()
    data = {
        "action": action,
        "details": details,
        "risk_level": risk_level,
        "code_snippet": code_snippet,
        "impact_scope": impact_scope,
    }
    interrupt = interrupt_manager.create(
        session_id=session_id,
        interrupt_type="approval",
        data=data,
    )
    logger.info("Approval interrupt created: id=%s session=%s action=%s", interrupt.id, session_id, action[:80])
    return build_interrupt_marker(interrupt.id, "approval", data)


async def _request_input(
    title: str = "",
    description: str = "",
    fields: str = "[]",
) -> str:
    """Request parameter input from the user via a dynamic form."""
    session_id = _get_session_id()
    try:
        parsed_fields = json.loads(fields) if isinstance(fields, str) else fields
    except (json.JSONDecodeError, TypeError):
        parsed_fields = []

    data = {
        "title": title,
        "description": description,
        "fields": parsed_fields,
    }
    interrupt = interrupt_manager.create(
        session_id=session_id,
        interrupt_type="form",
        data=data,
    )
    logger.info("Form interrupt created: id=%s session=%s title=%s", interrupt.id, session_id, title[:80])
    return build_interrupt_marker(interrupt.id, "form", data)
