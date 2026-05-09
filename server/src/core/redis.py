"""Redis client singleton with connection pool and cache helpers."""

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from src.config import settings

logger = logging.getLogger(__name__)

_pool: aioredis.ConnectionPool | None = None
_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _pool, _client
    if _client is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.redis_url, max_connections=20, decode_responses=True
        )
        _client = aioredis.Redis(connection_pool=_pool)
        await _client.ping()
        logger.info("Redis connected: %s", settings.redis_url)
    return _client


async def close_redis() -> None:
    global _pool, _client
    if _client:
        await _client.close()
        _client = None
    if _pool:
        await _pool.disconnect()
        _pool = None
    logger.info("Redis disconnected")


# ── Cache helpers ──────────────────────────────────────────────


async def cache_get(key: str) -> Any | None:
    """Get and deserialize a cached value. Returns None on miss."""
    r = await get_redis()
    raw = await r.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """Serialize and cache a value with TTL in seconds."""
    r = await get_redis()
    await r.set(key, json.dumps(value, default=str), ex=ttl)


async def cache_delete(key: str) -> None:
    r = await get_redis()
    await r.delete(key)


async def cache_delete_pattern(pattern: str) -> int:
    """Delete all keys matching a pattern. Returns count deleted."""
    r = await get_redis()
    keys = await r.keys(pattern)
    if keys:
        return await r.delete(*keys)
    return 0
