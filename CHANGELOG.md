# Changelog

## v2026.5.9 (2026-05-09) — Observability & Evolution

### New Features

- **Analytics Dashboard**: Admin analytics at `/control/analytics` with overview stat cards, trend charts (day-range selector), and top users/spaces tables.
- **LLM-Driven PDF Report Export**: Multi-step analytics report generation with preview and WeasyPrint-based PPT-style PDF export. Report model extended with `report_type`, `date_range_start`, `date_range_end` fields.
- **Evolution Pipeline**: Full lifecycle version management for tools and skills — Draft → Review → Active → Archived, with promotion, rollback, and diff comparison. Replaces the legacy `SkillVersion` snapshot table.
- **Kafka Management API**: Admin endpoints for topic CRUD, schema registry, consumer group monitoring, message browsing, and health checks (aiokafka-based).
- **Runtime Feature Flags**: Dynamic toggle system for gradual rollout and safe rollback of experimental features.
- **OpenTelemetry Observability**: Distributed tracing via OTLP exporter, Prometheus metrics endpoint on configurable port. Instrumentation for FastAPI and SQLAlchemy.
- **Celery Worker Service**: Dedicated `worker` container consuming `memory`, `wiki`, `evolution` queues. Reuses the server image with a separate entrypoint.
- **A2UI Generator Agent**: New sub-agent for Agent-to-UI interface generation, seeded alongside existing agents.
- **Report Generator Agent**: New sub-agent for automated report creation, integrated into the analytics report workflow.
- **Development Startup Scripts**: `scripts/start-all.ps1` / `scripts/start-all.sh` for one-command backend + frontend startup on Windows and Linux. Separate `start-backend` and `start-frontend` scripts also provided.

### Performance

- **Redis Caching**: Dashboard summary cached with 30s TTL. Space list cached with 120s TTL and invalidated on membership changes. Model cache invalidation now properly awaited (async).
- **Tool Manager Cache**: Automatic cache invalidation after tool/Skill/MCP server create, update, or delete operations.

### Infrastructure

- **Docker Compose**: Named `db_data` volume for persistent PostgreSQL storage. Entrypoint script (`entrypoint-server.sh`) runs `alembic upgrade head` before uvicorn. Kafka controller quorum explicitly configured. Health checks for all services with proper `depends_on` ordering.
- **Worker Startup Order**: Worker service `depends_on` server health — ensures schema migrations complete before the worker starts, preventing duplicate-column race conditions.
- **New Dependencies**: `weasyprint` (PDF generation), `opentelemetry-api/sdk/instrumentation-fastapi/instrumentation-sqlalchemy/exporter-otlp` (observability), `prometheus-client` (metrics), `aiokafka` (Kafka admin), `jsonschema` (schema validation).

### Refactoring

- **Sleep Detector Removed**: `server/src/services/sleep_detector.py` deleted. Wake and sleep operations refactored to direct SQLAlchemy queries in the API layer.
- **Skill Versioning Migration**: Legacy `SkillVersion`-based history endpoints return empty results or 503; rollback deferred to the evolution pipeline. Evolution pipeline (`src/api/control/evolution.py`) is the new authority for version history.
- **Worker Package**: New `server/src/workers/` package with Celery app (`app.py`) and embedded worker launcher (`embedded.py`).

### Scripts & Tooling

- `server/scripts/audit_schema_coverage.py` — Schema coverage audit against ORM models
- `server/scripts/backfill_memory_hash_and_embedding.py` — Backfill memory hash and embedding for existing records
- `server/scripts/evo_ctl.py` — Evolution pipeline CLI (promote, rollback, list versions)
- `server/scripts/rollout_router_llm.py` — LLM router rollout management
- `server/scripts/wiki_precompute_summaries.py` — Wiki article summary precomputation
- `server/scripts/cleanup_chat_msgs_keys.py` — Orphaned chat message key cleanup
- `server/scripts/eval_*.py` — Agent evaluation framework (annotate, cold_start, run)
- `server/scripts/recover_reports.py` — Report recovery from orphaned files
- `server/scripts/seed.py` — Seed data generation

---

## v2026.05.03.22 (2026-05-03)

- Data ingestion pipeline: Log, ITSM, CMDB ingestion with processors, agents, and UI pages
- Report publishing with three-level visibility (private/space/public)
- WeCom channel inbound message routing and app API
- MCP server management (CRUD)
- Space-level data isolation with `space_id` filtering across all modules
- Performance: MemoryManager cache, batch sync, space cache, pagination

## v2026.05.01 (2026-05-01)

- Core platform: LangGraph agent, tool market, knowledge base, alert center
- Data ingestion (webhook/API/Kafka), event hub, scenarios, automation
- CMDB, log search, ITSM, reports, notification channels
- Two-tier memory system, sleep management, model provider management
- Multi-space management, JWT + RBAC, audit logging
- Docker Compose deployment, dark/light theme
