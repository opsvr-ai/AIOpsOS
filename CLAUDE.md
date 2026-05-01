# CLAUDE.md

## Project: AIOpsOS — AI运维智能操作系统

AI-powered IT operations platform. Monorepo: Python/FastAPI backend + React/TypeScript frontend.

## Tech Stack

**Backend** (`server/`):
- Python 3.11, FastAPI, SQLAlchemy 2.0 (async + PostgreSQL), Alembic
- LangChain + LangGraph for agent orchestration
- Celery + Redis for async tasks, Kafka for event streaming
- pgvector for vector search, Poetry for dependency management
- Model layer: supports OpenAI-compatible + Anthropic providers via `model_factory`

**Frontend** (`web/`):
- React 18 + TypeScript, Vite, Ant Design 5
- State: Zustand stores (`chatStore`, `memoryStore`, etc.)
- CSS: Tailwind-like utility + custom variables, `@fontsource/*` for offline fonts
- Routing: React Router v6

## Project Structure

```
server/src/
├── agent/          LangGraph agent graph, sub-agents, state definitions
├── api/control/    Management API (agents, tools, memory, sleep, logs, model providers)
├── api/execution/  Chat/execution API (router, datasources, webhooks, callbacks)
├── core/           Logging, model_factory, config
├── models/         SQLAlchemy ORM models (base, agent, session, knowledge, model_provider)
├── schemas/        Pydantic models (CRUD schemas for each entity)
├── services/       Business logic (memory_service, sleep_detector, cron, kb_monitor, skill_service)
└── main.py         FastAPI app, lifespan (DB init + background services)

web/src/
├── features/       Feature pages (chat, agents, memory, sleep, logs, model-providers, knowledge)
├── components/     Shared UI (layout/Sidebar, etc.)
├── stores/         Zustand state stores
├── api/            API client layer
└── styles/         Global CSS, font imports
```

## tmp/ Directory

- `tmp/` contains reference implementations for development guidance only — not part of the project. Never scan, search, or include it in project documentation.

## Key Patterns

- **Model Factory**: Always use `src.core.model_factory.get_default_model()` or `get_model_for_agent()` — never hardcode `ChatOpenAI(model="deepseek-v4-flash")`
- **Async DB**: Use `async_session_factory` context manager, never sync sessions
- **Idempotent inserts**: Use `pg_insert(table).values(...).on_conflict_do_nothing()` — never bare inserts with try/except
- **Lazy model init**: Agents needing LLM in `__init__` should accept optional `model` param and lazy-init via `async _get_llm()` method
- **Lifespan**: DB init in `_init_database()`, background services in `_start_background_services()` — independent error handling, don't let one failure cascade

## Gotchas

- **Agent tools creating DB records**: Use `get_current_space()` from `src.agent.context` to inherit the active `space_id` — otherwise records get `space_id=NULL` and won't appear when the frontend sends `X-Space-Id` header.
- **FastAPI route ordering**: Literal paths must be defined before parameterized paths (e.g. `/sessions/recommendations` before `/sessions/{session_id}`).
- **Backend server restart**: The running server process caches bytecode. After editing server code, kill and restart `run_server.py` for changes to take effect.
- **Ant Design Segmented**: `Segmented` option labels must be plain strings — `Tooltip`/`QuestionCircleOutlined` wrappers break rendering. Use the `title` attribute for descriptions.
- **Multiple Vite instances**: Only one Vite dev server should run at a time. Duplicate instances cause "Failed to fetch dynamically imported module" errors.
- **Memory retrieval needs `system_prompt_block()` called**: `MemoryManager.system_prompt_block()` fetches recent memories for agent context but is NOT called automatically — both `/chat` and `/chat/stream` endpoints must call it and inject the result into agent messages.
- **`_fetch_memories` defaults to session-scoped**: `DatabaseMemoryProvider._fetch_memories()` previously passed `session_id=self._session_id`, limiting retrieval to the current session only. Cross-session retrieval (system_prompt_block, prefetch) must omit the session_id filter.

## Common Commands

```bash
# Backend
cd server
poetry run python run_server.py          # Start API server
poetry run alembic upgrade head           # Run migrations
poetry run alembic revision --autogenerate -m "desc"  # Create migration
poetry run pytest                         # Run tests
poetry run ruff check .                   # Lint

# Frontend
cd web
pnpm dev                                  # Dev server (port 5173)
pnpm build                                # Production build
pnpm tsc --noEmit                         # Type check
```

## Behavioral Guidelines

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

### 5. Offline-First (Frontend)

**No external CDN references.** All fonts, icons, and assets must be bundled locally.
- Fonts: use `@fontsource/*` npm packages
- Icons: use bundled icon libraries (lucide-react, @ant-design/icons)
- Never add `https://fonts.googleapis.com` or CDN URLs to CSS/HTML

### 6. No Generic AI UI

**Avoid cliché AI-generated design patterns.** No purple-to-blue gradients, glass morphism cards, gratuitous animations, or emoji-as-icons. Use the `ui-ux-pro-max` and `frontend-design` skills for design work.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
