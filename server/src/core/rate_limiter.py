"""Redis-based fixed-window rate limiter for API endpoints."""

import time

from fastapi import HTTPException, Request

from src.core.redis import get_redis


async def _check_rate_limit(
    key: str, max_requests: int, window_sec: int
) -> tuple[bool, int]:
    """Return (allowed, remaining_requests)."""
    redis = await get_redis()
    now = int(time.time())
    window_key = f"{key}:{now // window_sec}"

    count = await redis.incr(window_key)
    if count == 1:
        await redis.expire(window_key, window_sec + 1)

    remaining = max(0, max_requests - count)
    return count <= max_requests, remaining


def rate_limit(max_requests: int = 60, window_sec: int = 60):
    """FastAPI dependency factory for rate limiting.

    Uses fixed-window counters in Redis. Each window key auto-expires
    after the window elapses.
    """

    async def limiter(request: Request):
        identifier = request.client.host if request.client else "unknown"
        route_key = f"ratelimit:{request.url.path}:{identifier}"
        allowed, remaining = await _check_rate_limit(route_key, max_requests, window_sec)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again in {window_sec}s.",
                headers={"Retry-After": str(window_sec)},
            )
        return remaining

    return limiter


webhook_limiter = rate_limit(max_requests=30, window_sec=60)
chat_limiter = rate_limit(max_requests=20, window_sec=60)
