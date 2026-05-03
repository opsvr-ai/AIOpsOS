"""
企业微信渠道主模块 — 双向通信。

对外接口：
- send()            — 通知发送
- test()            — 渠道测试
- validate_config() — 配置校验
- start_bot_monitor() — 启动 Bot WS 持久连接
- get_bot_ws()      — 获取当前已连接的 WebSocket
- create_webhook_router() — 创建 HTTP webhook FastAPI 路由
"""

from __future__ import annotations

import json as _json
import logging
import time
import uuid
from typing import Any, Callable, Awaitable

import aiohttp

from src.services.channels.base import NotificationChannelBase, NotificationPayload

from .const import CLOUD_API_BASE, CLOUD_WS_BASE, WECOM_CMD_SUBSCRIBE
from .message_parser import ParsedMessage
from .message_sender import send_message
from .monitor import (
    WeComMonitor,
    get_ws,
    start_monitor,
    stop_monitor,
    stop_all_monitors,
)
from .webhook_handler import create_webhook_router
from .agent_bridge import handle_wecom_message

logger = logging.getLogger(__name__)


def _get_api_base(config: dict) -> str:
    if config.get("deployment_mode") == "private" and config.get("api_base_url"):
        return str(config["api_base_url"]).rstrip("/")
    return CLOUD_API_BASE


def _get_ws_base(config: dict) -> str:
    if config.get("deployment_mode") == "private" and config.get("ws_api_base"):
        return str(config["ws_api_base"]).rstrip("/")
    return CLOUD_WS_BASE


class WeComChannel(NotificationChannelBase):
    """企业微信渠道 — Bot Webhook / Bot WebSocket / App 三种子模式。"""

    channel_type = "wecom"

    def __init__(self):
        self._message_callback: Callable[
            [ParsedMessage, aiohttp.ClientWebSocketResponse, dict], Awaitable[None]
        ] | None = None

    def set_message_callback(self, cb):
        self._message_callback = cb

    # ── 通知发送 ──────────────────────────────────────────────────────────

    async def send(self, config: dict, payload: NotificationPayload) -> bool:
        sub_type = config.get("wecom_sub_type", "bot_webhook")
        if sub_type == "app":
            return await self._send_app(config, payload)
        elif sub_type == "bot_websocket":
            return await self._send_websocket(config, payload)
        else:
            return await self._send_webhook(config, payload)

    async def _send_webhook(self, config: dict, payload: NotificationPayload) -> bool:
        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            logger.warning("WeCom webhook: missing webhook_url")
            return False
        body = self._build_bot_body(payload)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url, json=body, timeout=aiohttp.ClientTimeout(10)
                ) as resp:
                    result = await resp.json()
                    if result.get("errcode") == 0:
                        return True
                    logger.error("WeCom webhook send failed: %s", result)
                    return False
        except Exception as exc:
            logger.exception("WeCom webhook send error: %s", exc)
            return False

    async def _send_app(self, config: dict, payload: NotificationPayload) -> bool:
        corp_id = config.get("corp_id", "")
        corp_secret = config.get("corp_secret", "")
        agent_id = config.get("agent_id", 0)
        meta = payload.metadata or {}
        to_user = meta.get("to_user") or config.get("to_user", "@all")
        to_party = meta.get("to_party") or config.get("to_party", "")
        to_tag = meta.get("to_tag") or config.get("to_tag", "")
        msg_type = config.get("msg_type", "markdown")

        if not corp_id or not corp_secret or not agent_id:
            logger.warning("WeCom app: missing corp_id/corp_secret/agent_id")
            return False

        api_base = _get_api_base(config)
        try:
            async with aiohttp.ClientSession() as session:
                token_url = f"{api_base}/cgi-bin/gettoken"
                async with session.get(
                    token_url,
                    params={"corpid": corp_id, "corpsecret": corp_secret},
                    timeout=aiohttp.ClientTimeout(10),
                ) as resp:
                    token_data = await resp.json()
                    if token_data.get("errcode") != 0:
                        logger.error("WeCom app gettoken: %s", token_data)
                        return False
                    access_token = token_data["access_token"]
        except Exception as exc:
            logger.exception("WeCom app gettoken error: %s", exc)
            return False

        receiver: dict = {}
        if to_user:
            receiver["touser"] = to_user
        if to_party:
            receiver["toparty"] = to_party
        if to_tag:
            receiver["totag"] = to_tag

        body = {**receiver, "msgtype": msg_type, "agentid": int(agent_id)}
        if msg_type == "text":
            body["text"] = {"content": self._build_text_content(payload)}
        else:
            body["markdown"] = {"content": self._build_markdown_content(payload)}

        try:
            async with aiohttp.ClientSession() as session:
                send_url = f"{api_base}/cgi-bin/message/send?access_token={access_token}"
                async with session.post(
                    send_url, json=body, timeout=aiohttp.ClientTimeout(10)
                ) as resp:
                    result = await resp.json()
                    if result.get("errcode") == 0:
                        return True
                    logger.error("WeCom app send failed: %s", result)
                    return False
        except Exception as exc:
            logger.exception("WeCom app send error: %s", exc)
            return False

    async def _send_websocket(self, config: dict, payload: NotificationPayload) -> bool:
        chatid = config.get("chatid", "") or (payload.metadata or {}).get("chatid", "")
        bot_id = config.get("bot_id", "")
        bot_secret = config.get("bot_secret", "")
        ws_base = _get_ws_base(config)

        # 优先使用持久连接
        ws = get_ws("default")
        if ws:
            try:
                result = await send_message(
                    ws, chatid, "markdown", self._build_markdown_content(payload)
                )
                return result.get("errcode") == 0
            except Exception as exc:
                logger.exception("WeCom WS (persistent) send error: %s", exc)

        # 回退到临时连接
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    ws_base, timeout=aiohttp.ClientTimeout(total=30)
                ) as ws:
                    req_id = uuid.uuid4().hex
                    await ws.send_str(_json.dumps({
                        "cmd": WECOM_CMD_SUBSCRIBE,
                        "headers": {"req_id": req_id},
                        "body": {"bot_id": bot_id, "secret": bot_secret},
                    }, ensure_ascii=False))
                    sub_resp = await ws.receive(timeout=10)
                    if sub_resp.type != aiohttp.WSMsgType.TEXT:
                        return False
                    if _json.loads(sub_resp.data).get("errcode") != 0:
                        return False
                    result = await send_message(
                        ws, chatid, "markdown", self._build_markdown_content(payload)
                    )
                    return result.get("errcode") == 0
        except Exception as exc:
            logger.exception("WeCom WS (fallback) send error: %s", exc)
            return False

    # ── Bot 监控器 ────────────────────────────────────────────────────────

    async def start_bot_monitor(
        self,
        bot_id: str,
        bot_secret: str,
        ws_url: str | None = None,
        account_id: str = "default",
        channel_config: dict | None = None,
    ) -> WeComMonitor:
        # Build agent bridge callback with config closure
        cfg = channel_config or {}
        async def _agent_callback(parsed_msg, ws, frame):
            await handle_wecom_message(parsed_msg, cfg, ws, frame)
        callback = self._message_callback or _agent_callback
        if self._message_callback and cfg:
            # Use both: user callback + agent bridge
            original = self._message_callback
            async def _combined(parsed_msg, ws, frame):
                await original(parsed_msg, ws, frame)
                await handle_wecom_message(parsed_msg, cfg, ws, frame)
            callback = _combined

        monitor = await start_monitor(
            bot_id=bot_id,
            bot_secret=bot_secret,
            ws_url=ws_url or CLOUD_WS_BASE,
            account_id=account_id,
            on_message=callback,
        )
        logger.info("WeCom bot monitor started: account=%s", account_id)
        return monitor

    async def stop_bot_monitor(self, account_id: str = "default"):
        await stop_monitor(account_id)

    @staticmethod
    async def stop_all_monitors():
        await stop_all_monitors()

    @staticmethod
    def get_bot_ws(account_id: str = "default"):
        return get_ws(account_id)

    @staticmethod
    def create_webhook_router(
        token: str,
        encoding_aes_key: str,
        receive_id: str = "",
        on_message=None,
    ):
        return create_webhook_router(
            token=token,
            encoding_aes_key=encoding_aes_key,
            receive_id=receive_id,
            on_message=on_message,
        )

    # ── WS 订阅验证 ──────────────────────────────────────────────────────

    async def _ws_subscribe(self, config: dict) -> tuple[bool, str]:
        bot_id = config.get("bot_id", "")
        bot_secret = config.get("bot_secret", "")
        ws_base = _get_ws_base(config)

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                ws_base, timeout=aiohttp.ClientTimeout(total=30)
            ) as ws:
                req_id = uuid.uuid4().hex
                sub = {
                    "cmd": WECOM_CMD_SUBSCRIBE,
                    "headers": {"req_id": req_id},
                    "body": {"bot_id": bot_id, "secret": bot_secret},
                }
                await ws.send_str(_json.dumps(sub, ensure_ascii=False))
                sub_resp = await ws.receive(timeout=10)
                if sub_resp.type != aiohttp.WSMsgType.TEXT:
                    return False, f"Unexpected WS message type: {sub_resp.type}"
                sub_data = _json.loads(sub_resp.data)
                if sub_data.get("errcode") != 0:
                    return False, f"Subscribe failed: {sub_data.get('errmsg', sub_data)}"
                return True, ""

    # ── Content builders ──────────────────────────────────────────────────

    @staticmethod
    def _build_text_content(payload: NotificationPayload) -> str:
        return (
            f"{payload.title}\n{payload.message}\n"
            f"Severity: {payload.severity} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    @staticmethod
    def _build_markdown_content(payload: NotificationPayload) -> str:
        severity_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵", "error": "🔴"}
        return (
            f"## {severity_emoji.get(payload.severity, '📢')} {payload.title}\n"
            f"{payload.message}\n"
            f">严重程度: <font color=\"warning\">{payload.severity}</font>\n"
            f">时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f">来源: AIOpsOS"
        )

    @staticmethod
    def _build_bot_body(payload: NotificationPayload) -> dict:
        return {
            "msgtype": "markdown",
            "markdown": {"content": WeComChannel._build_markdown_content(payload)},
        }

    # ── Validate ──────────────────────────────────────────────────────────

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        sub_type = config.get("wecom_sub_type", "bot_webhook")
        deployment_mode = config.get("deployment_mode", "cloud")

        if deployment_mode not in ("cloud", "private"):
            return False, "deployment_mode must be 'cloud' or 'private'"
        if deployment_mode == "private":
            if sub_type in ("app", "bot_webhook"):
                api_base = config.get("api_base_url", "")
                if not api_base:
                    return False, "api_base_url is required for private deployment"
                if not str(api_base).startswith(("http://", "https://")):
                    return False, "api_base_url must start with http:// or https://"

        if sub_type == "app":
            for key in ["corp_id", "corp_secret", "agent_id"]:
                if not config.get(key):
                    return False, f"Missing required field: {key}"
            msg_type = config.get("msg_type", "markdown")
            if msg_type not in ("text", "markdown"):
                return False, "msg_type must be 'text' or 'markdown'"
            return True, ""

        if sub_type == "bot_websocket":
            if not config.get("bot_id"):
                return False, "bot_id is required"
            if not config.get("bot_secret"):
                return False, "bot_secret is required"
            if deployment_mode == "private":
                ws_base = config.get("ws_api_base", "")
                if not ws_base:
                    return False, "ws_api_base is required for private deployment"
                if not str(ws_base).startswith(("ws://", "wss://")):
                    return False, "ws_api_base must start with ws:// or wss://"
            return True, ""

        # bot_webhook
        url = config.get("webhook_url", "")
        if not url:
            return False, "webhook_url is required"
        if "cgi-bin/webhook/send" not in url:
            return False, "URL must contain cgi-bin/webhook/send"
        return True, ""

    async def test(self, config: dict) -> tuple[bool, str]:
        valid, err = await self.validate_config(config)
        if not valid:
            return False, err

        sub_type = config.get("wecom_sub_type", "bot_webhook")

        if sub_type == "bot_websocket":
            try:
                ok, detail = await self._ws_subscribe(config)
                if ok:
                    return True, "WebSocket 连接成功，订阅已确认"
                return False, f"WebSocket 订阅失败: {detail}"
            except Exception as exc:
                logger.exception("WeCom WS test error")
                return False, f"WebSocket 测试异常: {exc}"

        test_payload = NotificationPayload(
            title="AIOpsOS 测试通知",
            message="这是一条来自 AIOpsOS 的测试消息，渠道配置正确。",
            severity="info",
            metadata={"type": "test"},
        )
        try:
            ok = await self.send(config, test_payload)
            if ok:
                return True, "测试消息发送成功"
            return False, "测试消息发送失败，请检查配置"
        except Exception as exc:
            logger.exception("WeCom test error")
            return False, f"测试异常: {exc}"
