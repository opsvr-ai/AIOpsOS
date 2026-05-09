"""Memory tier + embedding services for the Agent Runtime Optimization spec.

Phase D components (tasks 7-9):

* :mod:`.embedding` — :class:`EmbeddingService` with batching + content-hash
  Redis cache + graceful fallback when no API key is configured.
* :mod:`.tier` — :class:`MemoryTier` with HOT (Redis) / WARM (pgvector) /
  COLD (wiki filesystem) read paths and hybrid scoring.

These modules sit alongside the legacy ``src.services.memory_service`` and
``src.services.memory_provider`` modules which remain untouched this
phase. Phase E will wire the new tier in.
"""

from .embedding import EmbeddingService, get_embedding_service
from .tier import HotBlock, HotContext, MemoryItem, MemoryTier

__all__ = [
    "EmbeddingService",
    "get_embedding_service",
    "HotBlock",
    "HotContext",
    "MemoryItem",
    "MemoryTier",
]
