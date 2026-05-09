"""Pydantic schemas for the Kafka admin / management API.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 4.8.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TopicOut(BaseModel):
    name: str
    partitions: int
    replication_factor: int
    configs: dict[str, str] = Field(default_factory=dict)
    internal: bool = False


class CreateTopicBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    partitions: int = Field(default=3, ge=1, le=10_000)
    replication_factor: int = Field(default=1, ge=1, le=10)
    configs: dict[str, str] = Field(default_factory=dict)


class AlterTopicBody(BaseModel):
    partitions: int | None = Field(default=None, ge=1)
    configs: dict[str, str] | None = None


class PartitionLagOut(BaseModel):
    topic: str
    partition: int
    current_offset: int
    end_offset: int
    lag: int


class MemberOut(BaseModel):
    member_id: str
    client_id: str
    client_host: str | None = None
    assignments: list[str] = Field(default_factory=list)


class ConsumerGroupOut(BaseModel):
    group_id: str
    state: str | None = None
    protocol_type: str | None = None


class ConsumerGroupDetailOut(BaseModel):
    group_id: str
    state: str | None = None
    protocol: str | None = None
    protocol_type: str | None = None
    members: list[MemberOut] = Field(default_factory=list)
    lags: list[PartitionLagOut] = Field(default_factory=list)
    total_lag: int = 0


class ResetOffsetBody(BaseModel):
    topic: str = Field(..., min_length=1)
    partition: int = Field(..., ge=0)
    target: str = Field(..., description='"earliest" | "latest" | int | ISO-8601 timestamp')


class BrowserMessageOut(BaseModel):
    topic: str
    partition: int
    offset: int
    timestamp: int | None = None
    key: str | None = None
    value: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class DLQEntryOut(BaseModel):
    id: str
    original_topic: str
    original_partition: int | None = None
    original_offset: int | None = None
    original_key: str | None = None
    original_value: Any = None
    original_headers: dict[str, str] = Field(default_factory=dict)
    failure_reason: str | None = None
    failed_at: datetime | None = None
    attempt_count: int = 0
    tags: dict[str, str] = Field(default_factory=dict)
    dlq_topic: str | None = None
    dlq_partition: int | None = None
    dlq_offset: int | None = None


class DLQIdsBody(BaseModel):
    entry_ids: list[str] = Field(..., min_length=1)


class ReplayBody(DLQIdsBody):
    target_topic: str | None = None


class ReplayReportOut(BaseModel):
    replayed: int
    skipped: int
    errors: list[str] = Field(default_factory=list)


class SchemaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    topic: str
    version: int
    schema_: dict = Field(alias="schema")
    description: str | None = None
    created_at: datetime


class RegisterSchemaBody(BaseModel):
    topic: str = Field(..., min_length=1)
    version: int = Field(..., ge=1)
    schema_: dict = Field(..., alias="schema")
    description: str | None = None

    model_config = ConfigDict(populate_by_name=True)


__all__ = [
    "AlterTopicBody",
    "BrowserMessageOut",
    "ConsumerGroupDetailOut",
    "ConsumerGroupOut",
    "CreateTopicBody",
    "DLQEntryOut",
    "DLQIdsBody",
    "MemberOut",
    "PartitionLagOut",
    "RegisterSchemaBody",
    "ReplayBody",
    "ReplayReportOut",
    "ResetOffsetBody",
    "SchemaOut",
    "TopicOut",
]
