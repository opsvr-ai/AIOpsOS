"""企业微信消息发送 — 流式回复 (replyStream) 与主动发送 (sendMessage)。"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from typing import Any

import aiohttp

from .const import REPLY_SEND_TIMEOUT, STREAM_EXPIRED_ERRCODE

logger = logging.getLogger(__name__)


class StreamExpiredError(Exception):
    """流式回复过期 (errcode=846608)。"""

    def __init__(self, message: str = ""):
        super().__init__(message or f"Stream message update expired (errcode={STREAM_EXPIRED_ERRCODE})")
        self.errcode = STREAM_EXPIRED_ERRCODE


async def reply_stream(
    ws: aiohttp.ClientWebSocketResponse,
    frame: dict,
    stream_id: str,
    text: str,
    finish: bool = False,
    timeout: float = REPLY_SEND_TIMEOUT,
) -> str:
    """被动回复 — 流式消息。

    frame 是接收到的 aibot_callback 原始帧 dict。
    返回 stream_id。
    """
    if not text and not finish:
        return stream_id

    headers: dict[str, Any] = frame.get("headers", {})

    req_id = uuid.uuid4().hex
    response = {
        "cmd": "aibot_response",
        "headers": {
            "req_id": req_id,
            "orig_req_id": headers.get("req_id", ""),
        },
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "finish": finish,
                "content": text,
            },
        },
    }

    try:
        await asyncio.wait_for(
            ws.send_str(_json.dumps(response, ensure_ascii=False)),
            timeout=timeout,
        )
    except TimeoutError:
        raise StreamExpiredError(f"Reply stream send timed out (streamId={stream_id})")

    logger.info("replyStream streamId=%s finish=%s len=%d", stream_id, finish, len(text))
    return stream_id


async def reply_stream_non_blocking(
    ws: aiohttp.ClientWebSocketResponse,
    frame: dict,
    stream_id: str,
    text: str,
    finish: bool = False,
) -> str:
    """非阻塞流式回复 — 直接发送不等待 ack。"""
    if not text and not finish:
        return stream_id

    headers: dict[str, Any] = frame.get("headers", {})

    req_id = uuid.uuid4().hex
    response = {
        "cmd": "aibot_response",
        "headers": {
            "req_id": req_id,
            "orig_req_id": headers.get("req_id", ""),
        },
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "finish": finish,
                "content": text,
            },
        },
    }

    await ws.send_str(_json.dumps(response, ensure_ascii=False))
    logger.info("replyStreamNonBlocking streamId=%s finish=%s len=%d", stream_id, finish, len(text))
    return stream_id


async def send_message(
    ws: aiohttp.ClientWebSocketResponse,
    chatid: str,
    msgtype: str = "markdown",
    content: str = "",
    timeout: float = REPLY_SEND_TIMEOUT,
) -> dict:
    """主动发送消息到指定会话 (aibot_send_msg)。"""
    req_id = uuid.uuid4().hex
    body: dict[str, Any] = {
        "chatid": chatid,
        "msgtype": msgtype,
    }
    if msgtype == "text":
        body["text"] = {"content": content}
    else:
        body["markdown"] = {"content": content}

    request = {
        "cmd": "aibot_send_msg",
        "headers": {"req_id": req_id},
        "body": body,
    }

    await asyncio.wait_for(
        ws.send_str(_json.dumps(request, ensure_ascii=False)),
        timeout=timeout,
    )

    try:
        resp = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if resp.type == aiohttp.WSMsgType.TEXT:
            data = _json.loads(resp.data)
            logger.debug("sendMessage chatid=%s errcode=%s", chatid, data.get("errcode"))
            return data
        return {"errcode": -1, "errmsg": f"Unexpected response type: {resp.type}"}
    except TimeoutError:
        return {"errcode": -1, "errmsg": "sendMessage timeout"}


async def send_biz_msg(
    ws: aiohttp.ClientWebSocketResponse,
    chatid: str,
    msgtype: str = "markdown",
    content: str = "",
    timeout: float = 10,
) -> dict:
    """发送业务消息 (aibot_send_biz_msg)。"""
    req_id = uuid.uuid4().hex
    body: dict[str, Any] = {
        "chatid": chatid,
        "msgtype": msgtype,
    }
    if msgtype == "text":
        body["text"] = {"content": content}
    else:
        body["markdown"] = {"content": content}

    request = {
        "cmd": "aibot_send_biz_msg",
        "headers": {"req_id": req_id},
        "body": body,
    }

    await asyncio.wait_for(
        ws.send_str(_json.dumps(request, ensure_ascii=False)),
        timeout=timeout,
    )

    try:
        resp = await asyncio.wait_for(ws.receive(), timeout=timeout)
        if resp.type == aiohttp.WSMsgType.TEXT:
            return _json.loads(resp.data)
        return {"errcode": -1, "errmsg": f"Unexpected response type: {resp.type}"}
    except TimeoutError:
        return {"errcode": -1, "errmsg": "send_biz_msg timeout"}
