"""Kafka management surface — admin, metrics, browser, DLQ, schema registry.

Spec: .kiro/specs/agent-runtime-optimization-evolution, Phase B tasks 4.2-4.10.

Public facade re-exports the service classes so callers can write:

    from src.services.kafka import KafkaAdminService, KafkaBrowser, ...

Each service is an async class that accepts an optional ``bootstrap_servers``
parameter so tests can point at an ephemeral broker (or use a mocked admin
client) without monkey-patching global state.
"""
from src.services.kafka.admin import (
    ConsumerGroupDetail,
    ConsumerGroupInfo,
    KafkaAdminService,
    MemberInfo,
    PartitionLag,
    TopicInfo,
)
from src.services.kafka.browser import BrowserMessage, KafkaBrowser
from src.services.kafka.dlq import DLQEntry, KafkaDLQManager, ReplayReport
from src.services.kafka.ensure import EnsureReport, ensure_default_topics
from src.services.kafka.metrics import KafkaMetricsCollector
from src.services.kafka.schema import KafkaSchemaRegistry

__all__ = [
    "BrowserMessage",
    "ConsumerGroupDetail",
    "ConsumerGroupInfo",
    "DLQEntry",
    "EnsureReport",
    "KafkaAdminService",
    "KafkaBrowser",
    "KafkaDLQManager",
    "KafkaMetricsCollector",
    "KafkaSchemaRegistry",
    "MemberInfo",
    "PartitionLag",
    "ReplayReport",
    "TopicInfo",
    "ensure_default_topics",
]
