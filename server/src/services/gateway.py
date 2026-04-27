"""Gateway pattern — multi-platform messaging adapters.

Each platform adapter handles:
- Session isolation per chat/user with LRU cache + TTL
- Message routing to the correct platform/channel
- Response delivery back to the originating platform
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

SESSION_TTL = 3600
MAX_CACHED_SESSIONS = 1000


class SessionCache:
    """LRU cache with TTL for session isolation per chat/user."""

    def __init__(self, max_size: int = MAX_CACHED_SESSIONS, ttl: int = SESSION_TTL) -> None:
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key: str) -> Any | None:
        self._evict_expired()
        entry = self._cache.get(key)
        if entry is None:
            return None
        created, value = entry
        if time.monotonic() - created > self._ttl:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._evict_expired()
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (time.monotonic(), value)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (t, _) in self._cache.items() if now - t > self._ttl]
        for k in expired:
            del self._cache[k]

    def remove(self, key: str) -> None:
        self._cache.pop(key, None)


class PlatformAdapter(ABC):
    """Base class for messaging platform adapters."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Short identifier (e.g. 'web', 'api', 'telegram')."""

    @abstractmethod
    async def send(self, chat_id: str, content: str, **kwargs: Any) -> dict[str, Any]:
        """Send a message to a specific chat."""

    async def receive(self, chat_id: str) -> list[dict[str, Any]]:
        return []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class WebAdapter(PlatformAdapter):
    """Web chat adapter — stores outbound messages in-memory keyed by chat_id."""

    def __init__(self) -> None:
        self._outbox: dict[str, list[dict[str, Any]]] = {}

    @property
    def platform_name(self) -> str:
        return "web"

    async def send(self, chat_id: str, content: str, **kwargs: Any) -> dict[str, Any]:
        msg = {"content": content, "timestamp": time.time(), "chat_id": chat_id}
        self._outbox.setdefault(chat_id, []).append(msg)
        return {"success": True, "platform": "web", "chat_id": chat_id}

    async def receive(self, chat_id: str) -> list[dict[str, Any]]:
        return self._outbox.pop(chat_id, [])

    async def stop(self) -> None:
        self._outbox.clear()


class APIAdapter(PlatformAdapter):
    """External API adapter for programmatic third-party access."""

    def __init__(self) -> None:
        self._outbox: dict[str, list[dict[str, Any]]] = {}

    @property
    def platform_name(self) -> str:
        return "api"

    async def send(self, chat_id: str, content: str, **kwargs: Any) -> dict[str, Any]:
        msg = {"content": content, "timestamp": time.time(), "chat_id": chat_id}
        self._outbox.setdefault(chat_id, []).append(msg)
        return {"success": True, "platform": "api", "chat_id": chat_id}

    async def receive(self, chat_id: str) -> list[dict[str, Any]]:
        return self._outbox.pop(chat_id, [])

    async def stop(self) -> None:
        self._outbox.clear()


class Gateway:
    """Central message gateway — routes messages across platforms."""

    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}
        self._sessions = SessionCache()

    def register(self, adapter: PlatformAdapter) -> None:
        self._adapters[adapter.platform_name] = adapter
        logger.info("Gateway: registered '%s' adapter", adapter.platform_name)

    def unregister(self, platform_name: str) -> None:
        self._adapters.pop(platform_name, None)

    def get_adapter(self, platform: str) -> PlatformAdapter | None:
        return self._adapters.get(platform)

    async def send(self, platform: str, chat_id: str, content: str, **kwargs: Any) -> dict[str, Any]:
        adapter = self._adapters.get(platform)
        if adapter is None:
            logger.warning("Gateway: no adapter for '%s', falling back to web", platform)
            adapter = self._adapters.get("web")
        if adapter is None:
            return {"success": False, "error": f"No adapter for '{platform}'"}

        try:
            result = await adapter.send(chat_id, content, **kwargs)
            logger.debug("Gateway: delivered to %s:%s", platform, chat_id)
            return result
        except Exception:
            logger.exception("Gateway: send to %s:%s failed", platform, chat_id)
            return {"success": False, "error": "send failed"}

    def get_session(self, platform: str, chat_id: str) -> Any | None:
        return self._sessions.get(f"{platform}:{chat_id}")

    def set_session(self, platform: str, chat_id: str, session: Any) -> None:
        self._sessions.set(f"{platform}:{chat_id}", session)

    def remove_session(self, platform: str, chat_id: str) -> None:
        self._sessions.remove(f"{platform}:{chat_id}")

    async def start(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.start()
            except Exception:
                logger.exception("Gateway: '%s' start failed", adapter.platform_name)

    async def stop(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.stop()
            except Exception:
                logger.exception("Gateway: '%s' stop failed", adapter.platform_name)


gateway = Gateway()
gateway.register(WebAdapter())
gateway.register(APIAdapter())
