"""
企业微信 WebSocket 监控器 — 持久连接 + 消息收发 + 自动重连。

职责：
- 建立并维持 WebSocket 长连接
- 订阅认证 (aibot_subscribe)
- 接收消息回调 (aibot_callback / aibot_event_callback)
- 调用 message_parser 解析入站消息
- 通过 message_sender 发送流式/主动回复
- 断线自动重连（指数退避）
- 集成 AIOpsOS agent 路由系统
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from typing import Any, Callable, Awaitable

import aiohttp

from .const import (
    CLOUD_WS_BASE,
    EVENT_AUTH_CHANGE,
    EVENT_CHECK_UPDATE,
    EVENT_TEMPLATE_CARD,
    WECOM_CMD_CALLBACK,
    WECOM_CMD_EVENT_CALLBACK,
    WECOM_CMD_SUBSCRIBE,
    WS_HEARTBEAT_INTERVAL,
    WS_MAX_AUTH_FAILURES,
    WS_RECONNECT_DELAY_MIN,
    WS_RECONNECT_DELAY_MAX,
)
from .message_parser import ParsedMessage, parse_message
from .message_sender import reply_stream, send_message

logger = logging.getLogger(__name__)

# 消息处理回调: (parsed_msg, ws, frame) -> None
MessageHandler = Callable[
    [ParsedMessage, aiohttp.ClientWebSocketResponse, dict], Awaitable[None]
]


class WeComMonitor:
    """企业微信 WebSocket 长连接监控器。"""

    def __init__(
        self,
        bot_id: str,
        bot_secret: str,
        ws_url: str = CLOUD_WS_BASE,
        account_id: str = "default",
        on_message: MessageHandler | None = None,
    ):
        self.bot_id = bot_id
        self.bot_secret = bot_secret
        self.ws_url = ws_url
        self.account_id = account_id
        self._on_message = on_message

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._connected = False
        self._auth_failures = 0
        self._task: asyncio.Task | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None and not self._ws.closed

    @property
    def ws(self) -> aiohttp.ClientWebSocketResponse | None:
        return self._ws if self.is_connected else None

    def set_message_handler(self, handler: MessageHandler | None):
        self._on_message = handler

    async def start(self):
        """启动监控器（阻塞当前协程直到 stop）。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.current_task()
        await self._run_loop()

    async def stop(self):
        """停止监控器并清理资源。"""
        self._running = False
        await self._disconnect()
        if self._task and not self._task.done():
            self._task.cancel()

    async def _disconnect(self):
        self._connected = False
        ws = self._ws
        self._ws = None
        if ws and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        session = self._session
        self._session = None
        if session and not session.closed:
            try:
                await session.close()
            except Exception:
                pass

    async def _connect(self) -> bool:
        await self._disconnect()

        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                self.ws_url,
                timeout=aiohttp.ClientTimeout(total=30),
                heartbeat=WS_HEARTBEAT_INTERVAL,
            )
        except Exception as exc:
            logger.error("[wecom:%s] WS connect failed: %s", self.account_id, exc)
            return False

        req_id = uuid.uuid4().hex
        sub = {
            "cmd": WECOM_CMD_SUBSCRIBE,
            "headers": {"req_id": req_id},
            "body": {"bot_id": self.bot_id, "secret": self.bot_secret},
        }
        try:
            await self._ws.send_str(_json.dumps(sub, ensure_ascii=False))
            resp = await asyncio.wait_for(self._ws.receive(), timeout=10)
            if resp.type != aiohttp.WSMsgType.TEXT:
                logger.error("[wecom:%s] Subscribe unexpected type %s", self.account_id, resp.type)
                return False
            data = _json.loads(resp.data)
            if data.get("errcode") != 0:
                logger.error("[wecom:%s] Subscribe failed: %s", self.account_id, data)
                self._auth_failures += 1
                return False
        except Exception as exc:
            logger.error("[wecom:%s] Subscribe error: %s", self.account_id, exc)
            return False

        self._auth_failures = 0
        self._connected = True
        logger.info("[wecom:%s] Connected and authenticated", self.account_id)
        return True

    async def _run_loop(self):
        reconnect_delay = WS_RECONNECT_DELAY_MIN

        while self._running:
            if await self._connect():
                reconnect_delay = WS_RECONNECT_DELAY_MIN
                try:
                    await self._receive_loop()
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.error("[wecom:%s] Receive loop: %s", self.account_id, exc)
                finally:
                    self._connected = False
            else:
                if self._auth_failures >= WS_MAX_AUTH_FAILURES:
                    logger.error("[wecom:%s] Auth failures exhausted, stopping", self.account_id)
                    self._running = False
                    return
                delay = min(reconnect_delay, WS_RECONNECT_DELAY_MAX)
                logger.info("[wecom:%s] Reconnecting in %ds...", self.account_id, delay)
                await asyncio.sleep(delay)
                reconnect_delay = min(reconnect_delay * 2, WS_RECONNECT_DELAY_MAX)

    async def _receive_loop(self):
        assert self._ws is not None
        async for msg in self._ws:
            if not self._running:
                break
            if msg.type == aiohttp.WSMsgType.CLOSED:
                logger.info("[wecom:%s] WS closed by server", self.account_id)
                break
            if msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("[wecom:%s] WS error", self.account_id)
                break
            if msg.type in (aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG):
                continue
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue

            try:
                data = _json.loads(msg.data)
            except _json.JSONDecodeError:
                continue

            cmd = data.get("cmd", "")
            if cmd == WECOM_CMD_CALLBACK:
                asyncio.create_task(self._handle_callback(data))
            elif cmd == WECOM_CMD_EVENT_CALLBACK:
                asyncio.create_task(self._handle_event(data))

    async def _handle_callback(self, frame: dict):
        body = frame.get("body", {})
        parsed = parse_message(body)
        parsed.req_id = frame.get("headers", {}).get("req_id", "")

        logger.debug(
            "[wecom:%s] callback msgtype=%s chatid=%s sender=%s text_len=%d",
            self.account_id, parsed.msgtype, parsed.chatid, parsed.sender_userid, len(parsed.text),
        )

        if not parsed.text and not parsed.image_urls and not parsed.file_urls:
            return

        if self._on_message and self._ws:
            try:
                await self._on_message(parsed, self._ws, frame)
            except Exception as exc:
                logger.exception("[wecom:%s] Handler error: %s", self.account_id, exc)

    async def _handle_event(self, frame: dict):
        body = frame.get("body", {})
        event = body.get("event", {})
        event_type = event.get("eventtype", "")

        logger.info("[wecom:%s] event: %s", self.account_id, event_type)

        if event_type == EVENT_CHECK_UPDATE:
            return

        if event_type in (EVENT_TEMPLATE_CARD, EVENT_AUTH_CHANGE):
            parsed = parse_message(body)
            parsed.req_id = frame.get("headers", {}).get("req_id", "")
            if self._on_message and self._ws and parsed.text:
                try:
                    await self._on_message(parsed, self._ws, frame)
                except Exception as exc:
                    logger.exception("[wecom:%s] Event handler error: %s", self.account_id, exc)


# ── 全局监控器注册表 ────────────────────────────────────────────────────────

_monitors: dict[str, WeComMonitor] = {}


def get_monitor(account_id: str = "default") -> WeComMonitor | None:
    return _monitors.get(account_id)


def get_ws(account_id: str = "default") -> aiohttp.ClientWebSocketResponse | None:
    m = _monitors.get(account_id)
    return m.ws if m else None


async def start_monitor(
    bot_id: str,
    bot_secret: str,
    ws_url: str = CLOUD_WS_BASE,
    account_id: str = "default",
    on_message: MessageHandler | None = None,
) -> WeComMonitor:
    existing = _monitors.get(account_id)
    if existing and existing._running:
        await existing.stop()

    monitor = WeComMonitor(
        bot_id=bot_id,
        bot_secret=bot_secret,
        ws_url=ws_url,
        account_id=account_id,
        on_message=on_message,
    )
    _monitors[account_id] = monitor
    asyncio.create_task(_monitor_runner(monitor))
    return monitor


async def _monitor_runner(monitor: WeComMonitor):
    try:
        await monitor.start()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.exception("[wecom:%s] Monitor crashed: %s", monitor.account_id, exc)
    finally:
        if _monitors.get(monitor.account_id) is monitor:
            del _monitors[monitor.account_id]


async def stop_monitor(account_id: str = "default"):
    monitor = _monitors.pop(account_id, None)
    if monitor:
        await monitor.stop()


async def stop_all_monitors():
    for account_id in list(_monitors.keys()):
        await stop_monitor(account_id)
