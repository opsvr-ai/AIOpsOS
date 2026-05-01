from datetime import datetime
from typing import Literal
import uuid
from pydantic import BaseModel, field_validator, Field


class KafkaConfig(BaseModel):
    topic: str = "ops-events"
    bootstrap_servers: str = "localhost:9092"
    consumer_group: str = "aiopsos"
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password: str | None = None


class WebhookConfig(BaseModel):
    endpoint_id: str = ""
    secret: str = ""
    allowed_ips: list[str] = []
    rate_limit_per_min: int = 60
    signature_header: str = "X-Hub-Signature-256"


class ApiAuth(BaseModel):
    type: Literal["none", "basic", "bearer", "oauth2", "api_key"] = "none"
    basic: dict | None = None
    bearer: dict | None = None
    oauth2: dict | None = None
    api_key: dict | None = None


class ApiRequestStep(BaseModel):
    step: int
    name: str = ""
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str = ""
    headers: dict = {}
    body: dict | None = None
    query_params: dict | None = None
    extract: dict | None = None
    store_as: str | None = None
    data_path: str | None = None


class ApiConfig(BaseModel):
    base_url: str = ""
    poll_interval_seconds: int = 60
    timeout_seconds: int = 30
    auth: ApiAuth = Field(default_factory=ApiAuth)
    request_chain: list[ApiRequestStep] = []
    retry_count: int = 3
    retry_delay_seconds: int = 5


class LogConfig(BaseModel):
    """Configuration for log-type DataSource."""
    source: Literal["filebeat", "kafka", "vector"] = "filebeat"
    filebeat_input: str | None = None
    kafka_topic: str | None = None
    kafka_bootstrap_servers: str | None = None
    batch_size: int = 500
    batch_flush_ms: int = 500
    retention_minutes: int = 30
    partition_interval: Literal["hourly", "daily"] = "hourly"
    index_mappings: dict[str, str] | None = None


class ItsmConfig(BaseModel):
    """Configuration for itsm-type DataSource."""
    itsm_system: Literal["servicenow", "jira", "zendesk", "custom"] = "custom"
    ticket_types: list[str] = ["incident", "change", "problem", "request"]
    request_chain: list[dict] | None = None
    poll_interval_seconds: int = 300
    webhook_secret: str | None = None
    alert_link_window_minutes: int = 30
    field_mapping: dict[str, str] | None = None


class CmdbConfig(BaseModel):
    """Configuration for cmdb-type DataSource."""
    cmdb_system: Literal["itop", "servicenow", "custom"] = "custom"
    api_base_url: str = ""
    sync_schedule: str = "0 * * * *"
    topology_sync_interval_hours: int = 1
    host_sync_interval_hours: int = 24
    mapping_rule_path: str | None = None
    default_mode: Literal["discover", "incremental", "full"] = "incremental"
    validation_sample_rate: float = 0.1


class DataSourceCreate(BaseModel):
    name: str
    description: str | None = None
    source_type: Literal["kafka", "webhook", "api", "log", "itsm", "cmdb"]
    config: dict = {}
    normalization_rules: dict = {}
    table_mapping: dict | None = None


class DataSourceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_enabled: bool | None = None
    config: dict | None = None
    normalization_rules: dict | None = None
    table_mapping: dict | None = None


class DataSourceOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    source_type: str
    is_enabled: bool
    config: dict
    normalization_rules: dict
    table_mapping: dict | None = None
    last_ingested_at: datetime | None = None
    total_ingested: int = 0
    status: str = "active"
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    @field_validator('id', mode='before')
    @classmethod
    def coerce_id(cls, v: object) -> str:
        if isinstance(v, uuid.UUID):
            return str(v)
        return v  # type: ignore[return-value]


    model_config = {"from_attributes": True}


class DataSourceTestResult(BaseModel):
    success: bool
    message: str
    events_found: int = 0
    sample_event: dict | None = None
