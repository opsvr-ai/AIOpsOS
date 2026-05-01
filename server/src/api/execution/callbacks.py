"""Unified callback endpoint for notification channel callbacks (no JWT)."""

import hashlib
import hmac
import json
import logging
from fastapi import APIRouter, Request, HTTPException
from src.services.interrupt_manager import interrupt_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.post("/callbacks/{channel_type}")
async def receive_callback(channel_type: str, request: Request):
    """Receive callback from notification channels.

    DingTalk/WeCom interactive card callbacks resume interrupted sessions.
    The callback payload must include a session_id to identify the interrupt.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    logger.info("Callback received: channel=%s body=%s", channel_type, str(body)[:200])

    # Extract session_id from callback payload
    session_id = body.get("session_id") or ""
    if not session_id:
        # Try to find from out_trade_no or similar fields
        session_id = (
            body.get("out_trade_no")
            or body.get("attach", {}).get("session_id", "")
            if isinstance(body.get("attach"), dict)
            else ""
        )

    if not session_id:
        return {"ok": False, "message": "No session_id in callback"}

    # Resolve pending interrupt
    pending = interrupt_manager.get_pending_for_session(session_id)
    if pending is None:
        return {"ok": True, "message": "No pending interrupt for session"}

    # Parse user response from callback
    response_data = {"approved": True, "message": json.dumps(body, ensure_ascii=False)}
    interrupt_manager.resolve(pending.id, response_data)

    logger.info("Interrupt resolved via callback: %s", pending.id)
    return {"ok": True, "message": "Interrupt resolved"}
