# Changelog

## v2026.5.11 (2026-05-11) — Scenario Operations & Emergency Collaboration (Complete)

### Summary

This release completes the Scenario Operations Optimization and Emergency Collaboration feature set, adding comprehensive integration between scenario execution, collaboration sessions, message synchronization, progress analysis, and intelligent recommendations.

### New Features

- **Scenario Execution & Collaboration Integration**: Scenarios with `enable_collaboration=True` now automatically create collaboration sessions when triggered. The `ScenarioExecutionEngine` integrates with `CollaborationService` to execute initialization actions (group chat creation, email notifications).

- **Message Sync Integration**: `MessageSyncService` fully integrated with `GroupChatManager` for bidirectional real-time message synchronization between collaboration sessions and WeCom group chats.

- **Progress Analysis & Recommendation Integration**: `ProgressAnalyzer` now automatically triggers `RecommendationEngine` after analysis phase changes, generating intelligent next-step suggestions based on current progress.

- **Collaboration Session API**: Complete REST API for collaboration session management:
  - List sessions with pagination and filtering (status, time range, scenario)
  - Session details with messages, progress analysis, and recommendation history
  - Status management with state transition validation
  - Report export with duration, statistics, and key events
  - Keyword search across message content
  - Manual progress analysis trigger
  - Recommendation generation and feedback submission

### Integration Points

- `ScenarioExecutionEngine._create_collaboration_session()` — Auto-creates collaboration session for scenarios with collaboration enabled
- `ProgressAnalyzer._auto_generate_recommendations()` — Auto-generates recommendations after phase changes
- `MessageSyncService` ↔ `GroupChatManager` — Bidirectional message sync with format conversion
- `CollaborationService.execute_initialization_actions()` — Group chat creation and email notifications

### API Endpoints (New)

- `GET /api/v1/collaboration-sessions` — List sessions with filters
- `GET /api/v1/collaboration-sessions/count` — Session count by status
- `GET /api/v1/collaboration-sessions/{id}` — Session details
- `GET /api/v1/collaboration-sessions/{id}/messages` — Session messages
- `GET /api/v1/collaboration-sessions/{id}/recommendations` — Session recommendations
- `PUT /api/v1/collaboration-sessions/{id}/status` — Update session status
- `GET /api/v1/collaboration-sessions/{id}/report` — Export session report
- `GET /api/v1/collaboration-sessions/search/messages` — Search messages by keyword
- `POST /api/v1/collaboration-sessions/{id}/analyze` — Trigger progress analysis
- `POST /api/v1/collaboration-sessions/{id}/generate-recommendations` — Generate recommendations
- `PUT /api/v1/collaboration-sessions/recommendations/{id}/feedback` — Submit recommendation feedback

### Files Changed

- `server/src/api/control/collaboration.py` — New collaboration session API (944 lines)
- `server/src/api/control/router.py` — Registered collaboration router
- `server/src/services/scenario_execution.py` — Added collaboration integration
- `server/src/services/progress_analyzer.py` — Added auto-recommendation generation

---

## v2026.5.10 (2026-05-10) — Scenario Operations & Emergency Collaboration

### New Features

- **Scenario Type System**: Support for three scenario types — `command` (trigger command), `natural_language` (NL prompt to agent), and `hybrid` (both). Type-specific field validation on create/update.
- **Scenario Templates**: Built-in templates for common operations — `fault_isolation`, `health_inspection`, `capacity_prediction`, `alert_analysis`. Auto-fill configuration with customization support.
- **Enhanced Trigger Rules**: Alert count threshold, alert type/severity conditions, performance trend detection (rising/falling/volatile), composite conditions (AND/OR/NOT), frequency limiting with Redis, time window constraints.
- **Scenario Resource Association**: Many-to-many relationships with Skills, Agents, KnowledgeDocuments, and NotificationChannels. Resource loading at execution time.
- **Scenario Execution Engine**: Execution record management, type-specific execution strategies, detailed logging, structured results, configurable timeout (default 300s).
- **Emergency Collaboration Workflow**: Auto-create collaboration session on scenario trigger, status lifecycle (created → active → resolved → closed), summary report generation on close.
- **Group Chat Manager**: WeCom Work API integration for auto group creation, name template with variable substitution, member management, text/markdown message send/receive, message sync to collaboration session.
- **Email Notification Service**: SMTP integration, recipient list from scenario config and user groups, template rendering with variable substitution, status update emails, retry mechanism.
- **Message Sync Service**: Bidirectional sync between collaboration session and group chat, format conversion, source tracking, deduplication by message ID.
- **Progress Analyzer**: LLM-powered analysis of collaboration messages, key event identification (problem confirmation, solution discussion, operation execution, result verification), progress summary generation, configurable auto-analysis interval.
- **Recommendation Engine**: Next-step suggestions based on progress analysis, knowledge base integration, scenario-type-specific recommendations, priority and impact estimation, user feedback (adopt/ignore/modify), feedback learning.
- **Collaboration Session Management**: List API with pagination, filtering by status/time/scenario, detail query with messages/progress/recommendations, report export, keyword search in messages.

### Database Changes

- Extended `scenarios` table: `scenario_type`, `nl_prompt`, `template_id`, `execution_timeout`, `enable_collaboration`, `collaboration_config`
- New `scenario_executions` table: execution records with status, params, result, logs
- New `collaboration_sessions` table: session lifecycle with group chat info, progress summary
- New `collaboration_messages` table: message records with source channel, sync status
- New `collaboration_recommendations` table: recommendations with priority, feedback status
- New association tables: `scenario_knowledge_docs`, `scenario_channels`
- Extended `scene_triggers` table: `description`, `last_triggered_at`, `trigger_count`

### API Endpoints

- `POST/GET/PUT/DELETE /api/control/scenarios` — Scenario CRUD with type validation
- `GET /api/control/scenario-templates` — List available templates
- `POST /api/control/scenarios/from-template` — Create scenario from template
- `POST/DELETE /api/control/scenarios/{id}/resources` — Resource association management
- `POST /api/control/scenarios/{id}/execute` — Manual trigger execution
- `GET /api/control/scenario-executions` — Query execution records
- `GET /api/control/collaboration-sessions` — List sessions with filters
- `GET /api/control/collaboration-sessions/{id}` — Session detail with messages
- `PUT /api/control/collaboration-sessions/{id}/status` — Update session status
- `POST /api/control/collaboration-sessions/{id}/analyze` — Trigger progress analysis
- `GET /api/control/collaboration-sessions/{id}/recommendations` — Get recommendations
- `POST /api/control/collaboration-sessions/{id}/recommendations/{rid}/feedback` — Submit feedback
- `GET /api/control/collaboration-sessions/{id}/export` — Export session report
- `GET /api/control/collaboration-sessions/search` — Search messages by keyword

### New Services

- `server/src/services/template_service.py` — Scenario template management
- `server/src/services/scenario_execution.py` — Scenario execution engine
- `server/src/services/collaboration_service.py` — Collaboration session management
- `server/src/services/group_chat_manager.py` — WeCom group chat integration
- `server/src/services/email_notification.py` — Email notification service
- `server/src/services/message_sync.py` — Message synchronization service
- `server/src/services/progress_analyzer.py` — LLM-powered progress analysis
- `server/src/services/recommendation_engine.py` — Intelligent recommendation generation

### New Models

- `server/src/models/scenario.py` — ScenarioExecution model
- `server/src/models/collaboration.py` — CollaborationSession, CollaborationMessage, CollaborationRecommendation models

### New Schemas

- `server/src/schemas/scenario.py` — Scenario and execution schemas with type validation
- `server/src/schemas/collaboration.py` — Collaboration session, message, recommendation schemas

---

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
