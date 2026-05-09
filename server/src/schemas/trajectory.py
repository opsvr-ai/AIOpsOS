"""Pydantic model + JSON-schema for ``ops.agent.trajectory`` events.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 6.1 / R-5.6 / R-6.2.

Matches the shape in design.md § TrajectorySink. The JSON-schema generated
by :meth:`TrajectoryEvent.json_schema` is registered in
``kafka_topic_schemas`` at version 1 so KafkaSchemaRegistry can validate
events before producer-emit.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


KindLiteral = Literal[
    "turn",
    "tool_call",
    "subagent",
    "router_decision",
    "reflection",
]

OutcomeLiteral = Literal["ok", "error", "timeout", "rejected"]


class TrajectoryEvent(BaseModel):
    """One row of ``agent_trajectories`` / one message on ``ops.agent.trajectory``."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    session_id: uuid.UUID
    user_id: uuid.UUID
    space_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None

    kind: KindLiteral
    ts: datetime
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    model: str | None = None

    outcome: OutcomeLiteral
    data: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # JSON-schema (used by KafkaSchemaRegistry)
    # ------------------------------------------------------------------

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Return a Draft 2020-12 JSON schema describing this event.

        We hand-author the shape (rather than returning ``cls.model_json_schema()``)
        so we can be explicit about the union / format constraints that
        KafkaSchemaRegistry enforces on the producer side. The result is a
        self-contained dict — no ``$ref`` / ``$defs`` that would require
        schema bundling.
        """
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "TrajectoryEvent",
            "type": "object",
            "required": [
                "id",
                "session_id",
                "user_id",
                "kind",
                "ts",
                "outcome",
            ],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "format": "uuid"},
                "session_id": {"type": "string", "format": "uuid"},
                "user_id": {"type": "string", "format": "uuid"},
                "space_id": {
                    "anyOf": [
                        {"type": "string", "format": "uuid"},
                        {"type": "null"},
                    ]
                },
                "parent_id": {
                    "anyOf": [
                        {"type": "string", "format": "uuid"},
                        {"type": "null"},
                    ]
                },
                "kind": {
                    "type": "string",
                    "enum": [
                        "turn",
                        "tool_call",
                        "subagent",
                        "router_decision",
                        "reflection",
                    ],
                },
                "ts": {"type": "string", "format": "date-time"},
                "latency_ms": {
                    "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]
                },
                "tokens_in": {
                    "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]
                },
                "tokens_out": {
                    "anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]
                },
                "model": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "outcome": {
                    "type": "string",
                    "enum": ["ok", "error", "timeout", "rejected"],
                },
                "data": {"type": "object"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
        }


__all__ = ["TrajectoryEvent", "KindLiteral", "OutcomeLiteral"]
