"""Agent runtime services (gateway / router / trajectory / dispatcher).

Spec: .kiro/specs/agent-runtime-optimization-evolution.

The package is intentionally minimal on import — each submodule is
loaded on demand so tests can mock pieces independently and startup
doesn't pay for Kafka/aiokafka imports until the sink is actually
instantiated.
"""
from __future__ import annotations

__all__: list[str] = []
