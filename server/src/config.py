from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://aiopsos:aiopsos123@localhost:5432/aiopsos"
    sync_database_url: str = "postgresql://aiopsos:aiopsos123@localhost:5432/aiopsos"
    redis_url: str = "redis://localhost:6379"
    # Default points at the docker-compose.dev.yml EXTERNAL listener (9094),
    # which advertises "localhost" and is reachable from WSL / host processes.
    # Container-to-container traffic should override with the PLAINTEXT
    # listener (KAFKA_BOOTSTRAP_SERVERS=kafka:9092).
    kafka_bootstrap_servers: str = "localhost:9094"
    secret_key: str = "dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    llm_api_key: str = ""
    llm_base_url: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    service_type: str = "allinone"
    upload_dir: str = "uploads"
    wiki_path: str = "data/knowledge"  # WIKI_PATH env var — knowledge base filesystem root
    kb_wiki_dir: str = "data/knowledge"  # deprecated, use wiki_path
    kb_monitor_enabled: bool = True
    kb_monitor_poll_interval: int = 30  # seconds between filesystem scans
    kb_monitor_model: str = "deepseek-v4-flash"
    log_level: str = "DEBUG"
    log_dir: str = "data/logs"
    log_format: str = "text"
    log_retention_days: int = 30

    # Public-facing base URL for generating shareable links (no trailing slash)
    public_url: str = "http://localhost:8000"

    # Progress analysis configuration
    progress_analysis_interval: int = 300  # seconds between automatic analyses (default 5 minutes)
    progress_analysis_enabled: bool = True  # whether automatic analysis is enabled by default

    model_config = SettingsConfigDict(env_file=".env", env_prefix="")


settings = Settings()
