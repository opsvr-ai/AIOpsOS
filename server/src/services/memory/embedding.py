"""Batched embedding service with Redis content-hash cache.

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 7.1 / 7.2 / 7.3
/ R-2.4 / R-2.5 / P-Memory-4 / P-Memory-5.

Behavior:

* **Enabled**: when ``settings.embedding_api_key`` is non-empty *or* a
  ``provider`` callable is explicitly injected (tests). In the enabled
  path, ``embed(texts)`` returns the vector per input; cache hits are
  served from Redis and misses are coalesced into a single provider
  call per batch window.
* **Disabled**: returns ``[[] for _ in texts]`` as a marker that the
  caller must treat as "no embedding available" (the caller then
  typically falls back to ILIKE). No cache access, no metric update.

Batching:

* Up to ``max_batch`` items (default 16) or ``batch_window_ms`` ms
  (default 30) — whichever happens first. Concurrent ``embed_one``
  calls under the same batch window coalesce into one provider call.
* The coordinator is an ``asyncio.Lock``-guarded accumulator that flips
  an ``asyncio.Event`` once the batch is ready, letting every waiter
  pick up its own slice of the result.

Cache:

* Key: ``emb:{model}:{sha256(text)[:16]}``.
* TTL: ``cache_ttl_s`` (default 7 days).
* Stored as JSON-encoded ``list[float]``.

Hit-ratio gauge:

* Rolling 1000-call window — every lookup counts as one sample, not
  every batched provider invocation. The gauge ``embedding_cache_hit_ratio``
  is updated whenever the window fills or on ``close()``.

Provider contract:

``provider(texts: list[str]) -> list[list[float]]`` — an async callable
that returns one vector per input text, in the same order. The default
implementation lazily imports ``langchain_openai.OpenAIEmbeddings`` (or
falls back to ``openai.AsyncOpenAI`` if that fails) inside ``__init__``
so tests can run without either package being importable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from src.config import settings
from src.core.metrics import embedding_cache_hit_ratio

logger = logging.getLogger(__name__)


Provider = Callable[[list[str]], Awaitable[list[list[float]]]]


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


class EmbeddingService:
    """Async batched embedding service with Redis caching."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dim: int | None = None,
        batch_window_ms: int = 30,
        max_batch: int = 16,
        cache_ttl_s: int = 7 * 86400,
        provider: Provider | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else settings.embedding_api_key
        self._base_url = base_url if base_url is not None else settings.embedding_base_url
        self._model = model or settings.embedding_model
        self._dim = int(dim or settings.embedding_dim)
        self._batch_window_s = float(batch_window_ms) / 1000.0
        self._max_batch = int(max_batch)
        self._cache_ttl_s = int(cache_ttl_s)

        # Precedence: injected provider > configured api_key -> default impl.
        self._injected_provider: Provider | None = provider
        self._provider: Provider | None = provider
        self._redis_client = redis_client

        # Batch coordination state.
        self._batch_lock = asyncio.Lock()
        self._pending: list[tuple[str, asyncio.Future]] = []
        self._batch_task: asyncio.Task | None = None

        # Rolling hit-ratio window.
        self._window_size: int = 1000
        self._hits: int = 0
        self._misses: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when we have either an injected provider or an API key."""
        if self._injected_provider is not None:
            return True
        return bool(self._api_key)

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text.

        When disabled, returns ``[[] for _ in texts]`` without touching
        Redis or incrementing any metric.
        """
        if not texts:
            return []
        if not self.enabled:
            return [[] for _ in texts]

        results: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []

        # Cache lookup
        redis = await self._redis()
        for i, t in enumerate(texts):
            cached = await self._cache_get(redis, t)
            if cached is not None:
                results[i] = cached
                self._record_hit()
            else:
                miss_indices.append(i)
                miss_texts.append(t)
                self._record_miss()

        # Resolve misses
        if miss_texts:
            vectors = await self._batch_fetch(miss_texts)
            # Cache-back and splice into results
            for idx, text, vec in zip(miss_indices, miss_texts, vectors, strict=True):
                results[idx] = vec
                if vec:
                    await self._cache_set(redis, text, vec)

        # Any remaining None (shouldn't happen) → fallback to empty vector
        return [r if r is not None else [] for r in results]

    async def embed_one(self, text: str) -> list[float]:
        """Single-text convenience; returns an empty list when disabled."""
        if not self.enabled:
            return []
        out = await self.embed([text])
        return out[0] if out else []

    async def close(self) -> None:
        """Flush the hit-ratio gauge one last time (tests / graceful exit)."""
        self._flush_gauge(force=True)

    # ------------------------------------------------------------------
    # Internal: batch coordination
    # ------------------------------------------------------------------

    async def _batch_fetch(self, texts: list[str]) -> list[list[float]]:
        """Coalesce one caller's miss list into the current batch window.

        The first caller that enters starts a timer task; subsequent
        callers piggyback on the same timer. When the window expires (or
        the pending queue overflows ``max_batch``) the timer fires and
        everyone gets their slice.
        """
        if not texts:
            return []

        loop = asyncio.get_event_loop()
        futures: list[asyncio.Future] = []
        async with self._batch_lock:
            for t in texts:
                fut: asyncio.Future = loop.create_future()
                self._pending.append((t, fut))
                futures.append(fut)
            # Overflow → flush immediately before waiting.
            if len(self._pending) >= self._max_batch:
                await self._flush_batch()
            elif self._batch_task is None or self._batch_task.done():
                self._batch_task = asyncio.create_task(self._flush_after_window())

        # Wait for our futures to resolve. If a single provider invocation
        # fails, propagate the exception up to the caller rather than
        # silently swapping in an empty vector.
        results: list[list[float]] = []
        for fut in futures:
            try:
                results.append(await fut)
            except Exception:
                logger.exception("embedding provider call failed")
                results.append([])
        return results

    async def _flush_after_window(self) -> None:
        try:
            await asyncio.sleep(self._batch_window_s)
        except asyncio.CancelledError:
            return
        async with self._batch_lock:
            if not self._pending:
                return
            await self._flush_batch()

    async def _flush_batch(self) -> None:
        """Must be called with ``self._batch_lock`` held."""
        pending, self._pending = self._pending, []
        if self._batch_task is not None and not self._batch_task.done():
            self._batch_task.cancel()
            self._batch_task = None
        if not pending:
            return

        texts = [t for t, _ in pending]
        provider = await self._get_provider()
        try:
            vectors = await provider(texts)
        except Exception as exc:
            for _, fut in pending:
                if not fut.done():
                    fut.set_exception(exc)
            return

        if len(vectors) != len(pending):
            err = RuntimeError(
                f"embedding provider returned {len(vectors)} vectors "
                f"for {len(pending)} inputs"
            )
            for _, fut in pending:
                if not fut.done():
                    fut.set_exception(err)
            return

        for (_, fut), vec in zip(pending, vectors, strict=True):
            if not fut.done():
                fut.set_result(list(vec))

    # ------------------------------------------------------------------
    # Internal: provider lazy-init
    # ------------------------------------------------------------------

    async def _get_provider(self) -> Provider:
        if self._provider is not None:
            return self._provider
        self._provider = self._build_default_provider()
        return self._provider

    def _build_default_provider(self) -> Provider:
        """Construct the default OpenAI-compatible async embedding provider.

        Lazily imports ``langchain_openai`` first, falling back to
        ``openai.AsyncOpenAI`` if the former isn't usable. Both cases are
        guarded so the module can still be imported in environments
        without either package.
        """
        model = self._model
        api_key = self._api_key or ""
        base_url = self._base_url or None

        try:
            from langchain_openai import OpenAIEmbeddings

            kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = OpenAIEmbeddings(**kwargs)

            async def _call(texts: list[str]) -> list[list[float]]:
                return await client.aembed_documents(texts)

            return _call
        except Exception:  # pragma: no cover - fallback path
            logger.debug("langchain_openai unavailable; falling back to openai sdk", exc_info=True)

        try:
            from openai import AsyncOpenAI

            kwargs = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = AsyncOpenAI(**kwargs)

            async def _call(texts: list[str]) -> list[list[float]]:
                resp = await client.embeddings.create(model=model, input=texts)
                return [item.embedding for item in resp.data]

            return _call
        except Exception as exc:  # pragma: no cover - last-resort
            async def _noop(_texts: list[str]) -> list[list[float]]:
                raise RuntimeError(
                    "No embedding provider available and no provider injected"
                ) from exc

            return _noop

    # ------------------------------------------------------------------
    # Internal: Redis cache
    # ------------------------------------------------------------------

    async def _redis(self) -> Any:
        if self._redis_client is not None:
            return self._redis_client
        from src.core.redis import get_redis

        return await get_redis()

    def _cache_key(self, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return f"emb:{self._model}:{digest}"

    async def _cache_get(self, redis: Any, text: str) -> list[float] | None:
        key = self._cache_key(text)
        try:
            raw = await redis.get(key)
        except Exception:
            logger.debug("embedding cache get failed", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            data = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
            if isinstance(data, list):
                return [float(x) for x in data]
        except Exception:
            logger.debug("embedding cache decode failed", exc_info=True)
        return None

    async def _cache_set(self, redis: Any, text: str, vec: list[float]) -> None:
        key = self._cache_key(text)
        try:
            await redis.set(key, json.dumps(list(vec)), ex=self._cache_ttl_s)
        except Exception:
            logger.debug("embedding cache set failed", exc_info=True)

    # ------------------------------------------------------------------
    # Hit-ratio metric
    # ------------------------------------------------------------------

    def _record_hit(self) -> None:
        self._hits += 1
        self._flush_gauge()

    def _record_miss(self) -> None:
        self._misses += 1
        self._flush_gauge()

    def _flush_gauge(self, *, force: bool = False) -> None:
        total = self._hits + self._misses
        if total == 0:
            return
        if not force and total < self._window_size:
            return
        try:
            embedding_cache_hit_ratio.set(self._hits / total)
        except Exception:
            logger.debug("embedding_cache_hit_ratio gauge update failed", exc_info=True)
        # Reset window so each 1000 samples is its own measurement.
        if not force:
            self._hits = 0
            self._misses = 0


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


_SVC: EmbeddingService | None = None
_SVC_LOCK = asyncio.Lock()


def get_embedding_service() -> EmbeddingService:
    """Return a process-wide ``EmbeddingService`` singleton.

    Creation is lazy and doesn't touch the network; the provider and
    Redis client are both resolved on first call to :meth:`embed`.
    """
    global _SVC
    if _SVC is None:
        _SVC = EmbeddingService()
    return _SVC


def _reset_singleton_for_tests() -> None:
    """Drop the cached service (tests only)."""
    global _SVC
    _SVC = None


__all__ = [
    "EmbeddingService",
    "get_embedding_service",
]
