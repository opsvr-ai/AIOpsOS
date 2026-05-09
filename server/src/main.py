import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.control.router import router as control_router
from src.api.execution.callbacks import router as callback_router
from src.api.execution.datasources import router as datasource_router
from src.api.execution.itsm_search import itsm_search_router
from src.api.execution.log_search import log_search_router
from src.api.execution.notifications import router as notification_router
from src.api.execution.router import router as execution_router
from src.api.execution.tasks import router as tasks_router
from src.api.execution.webhooks import router as webhook_router
from src.api.execution.workflow import workflow_router
from src.api.public import router as public_router
from src.config import settings
from src.core.logging import setup_logging
from src.core.metrics import metrics_router
from src.core.tracing import init_tracing
from src.models.base import Base, engine

logger = logging.getLogger(__name__)


async def _auto_seed_agents() -> None:
    """Ensure main agent + sub-agents + tool associations exist."""
    from sqlalchemy import delete, select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from src.agent.deep_agent import (
        A2UI_GENERATOR_SYSTEM_PROMPT,
        AI_OPS_SYSTEM_PROMPT,
        ANALYSIS_SYSTEM_PROMPT,
        CMDB_SYSTEM_PROMPT,
        KNOWLEDGE_SYSTEM_PROMPT,
        KNOWLEDGE_TOOLS,
        MEMORY_SYSTEM_PROMPT,
        MEMORY_TOOLS,
        MONITOR_SYSTEM_PROMPT,
        OPS_SYSTEM_PROMPT,
        SUBAGENTS,
    )
    from src.models.agent import Agent, Tool, agent_sub_agents, agent_tools
    from src.models.base import async_session_factory

    async with async_session_factory() as db:
        stale = (await db.execute(
            select(Agent).where(Agent.agent_type == "orchestrator")
        )).scalars().all()
        for s in stale:
            await db.execute(delete(agent_tools).where(agent_tools.c.agent_id == s.id))
            await db.execute(delete(agent_sub_agents).where(agent_sub_agents.c.main_agent_id == s.id))
            await db.execute(delete(agent_sub_agents).where(agent_sub_agents.c.sub_agent_id == s.id))
            await db.delete(s)
            logger.info("Removed stale agent: %s", s.name)

        main_rows = (await db.execute(
            select(Agent).where(Agent.name == "AIOpsOS 主智能体").order_by(Agent.created_at.desc())
        )).scalars().all()
        # Deduplicate: keep the newest, remove older duplicates
        for dup in main_rows[1:]:
            await db.execute(delete(agent_tools).where(agent_tools.c.agent_id == dup.id))
            await db.execute(delete(agent_sub_agents).where(agent_sub_agents.c.main_agent_id == dup.id))
            await db.execute(delete(agent_sub_agents).where(agent_sub_agents.c.sub_agent_id == dup.id))
            await db.delete(dup)
            logger.info("Removed duplicate main agent: %s", dup.id)
        main = main_rows[0] if main_rows else None
        if main is None:
            main = Agent(name="AIOpsOS 主智能体", type="main",
                         system_prompt=AI_OPS_SYSTEM_PROMPT,
                         model_name="deepseek-v4-flash", agent_type="deep_agent", is_active=True,
                         is_builtin=True, space_id=None)
            db.add(main)
            await db.flush()
            logger.info("Created main agent: AIOpsOS 主智能体")
        else:
            main.agent_type = "deep_agent"
            main.is_active = True
            main.is_builtin = True
            main.space_id = None
            # Refresh system_prompt from code (handles updates to existing main agent)
            if AI_OPS_SYSTEM_PROMPT != main.system_prompt:
                main.system_prompt = AI_OPS_SYSTEM_PROMPT
                logger.info("Updated system_prompt for main agent")
            await db.flush()

        prompt_map = {
            "knowledge": KNOWLEDGE_SYSTEM_PROMPT, "monitor": MONITOR_SYSTEM_PROMPT,
            "ops": OPS_SYSTEM_PROMPT, "analysis": ANALYSIS_SYSTEM_PROMPT,
            "memory": MEMORY_SYSTEM_PROMPT, "cmdb_ingestion": CMDB_SYSTEM_PROMPT,
            "a2ui_generator": A2UI_GENERATOR_SYSTEM_PROMPT,
        }
        sub_map: dict[str, Agent] = {}
        for sa in SUBAGENTS:
            sub_name = f"{sa['name']} 子智能体"
            sub_rows = (await db.execute(
                select(Agent).where(Agent.name == sub_name).order_by(Agent.created_at.desc())
            )).scalars().all()
            for dup in sub_rows[1:]:
                await db.execute(delete(agent_tools).where(agent_tools.c.agent_id == dup.id))
                await db.execute(delete(agent_sub_agents).where(agent_sub_agents.c.main_agent_id == dup.id))
                await db.execute(delete(agent_sub_agents).where(agent_sub_agents.c.sub_agent_id == dup.id))
                await db.delete(dup)
                logger.info("Removed duplicate sub-agent: %s %s", sub_name, dup.id)
            sub = sub_rows[0] if sub_rows else None
            if sub is None:
                sub = Agent(name=sub_name, type="sub",
                            system_prompt=prompt_map.get(sa['name'], ""),
                            model_name="deepseek-v4-flash", agent_type="deep_agent", is_active=True,
                            is_builtin=True, space_id=None)
                db.add(sub)
                await db.flush()
                logger.info("Created sub-agent: %s", sub_name)
            else:
                sub.agent_type = "deep_agent"
                sub.is_active = True
                sub.is_builtin = True
                sub.space_id = None
                # Refresh system_prompt from code (handles updates to existing sub-agents)
                refreshed = prompt_map.get(sa['name'])
                if refreshed and refreshed != sub.system_prompt:
                    sub.system_prompt = refreshed
                    logger.info("Updated system_prompt for sub-agent: %s", sub_name)
                await db.flush()
            sub_map[sa['name']] = sub

        for kt in KNOWLEDGE_TOOLS:
            result = await db.execute(select(Tool).where(Tool.name == kt.name))
            tool = result.scalar_one_or_none()
            if tool is None:
                tool = Tool(name=kt.name, type="builtin", description=kt.description or "",
                            is_active=True, is_approved=True, is_builtin=True)
                db.add(tool)
                await db.flush()
            else:
                tool.is_builtin = True
            await db.execute(
                pg_insert(agent_tools)
                .values(agent_id=main.id, tool_id=tool.id)
                .on_conflict_do_nothing()
            )

        memory_sub = sub_map.get("memory")
        if memory_sub:
            for mt in MEMORY_TOOLS:
                result = await db.execute(select(Tool).where(Tool.name == mt.name))
                tool = result.scalar_one_or_none()
                if tool is None:
                    tool = Tool(name=mt.name, type="builtin", description=mt.description or "",
                                is_active=True, is_approved=True, is_builtin=True)
                    db.add(tool)
                    await db.flush()
                else:
                    tool.is_builtin = True
                await db.execute(
                    pg_insert(agent_tools)
                    .values(agent_id=memory_sub.id, tool_id=tool.id)
                    .on_conflict_do_nothing()
                )

        for _sa_name, sub in sub_map.items():
            await db.execute(
                pg_insert(agent_sub_agents)
                .values(main_agent_id=main.id, sub_agent_id=sub.id)
                .on_conflict_do_nothing()
            )

        await db.commit()
        logger.info("Agent auto-seed complete: 1 main + %d sub-agents", len(sub_map))


async def _init_database(app: FastAPI) -> bool:
    """Ensure schema is present and run seeds. Returns True on success.

    Schema ownership rules (deploy fix):

    * ``Base.metadata.create_all`` is **always** invoked. SQLAlchemy emits
      ``CREATE TABLE IF NOT EXISTS`` under the hood, so it's safe to
      re-run. This keeps the association tables that never landed in the
      Alembic chain (``agent_tools`` / ``agent_sub_agents`` /
      ``agent_channels`` / ``agent_versions`` / several cmdb_* tables /
      log_events / itsm_tickets / tasks / …) creatable on every boot.
      The alternative — authoring ``create_table`` migrations for every
      gap — is a separate backlog item; for now, ``create_all`` is the
      ground truth for those.
    * If the Alembic bookkeeping table ``alembic_version`` is missing,
      the DB has never been stamped. ``create_all`` above has already
      produced a schema at ``head``, so we **stamp head** programmatically
      so the Dockerfile's ``alembic upgrade head`` on next boot is a
      no-op instead of re-running the chain against tables that are
      already present.
    * If ``alembic_version`` is present, we leave it alone and trust the
      Dockerfile entrypoint's ``alembic upgrade head`` (run before this
      function executes) to have reconciled schema additions from newer
      revisions. The migrations in this chain are now idempotent where
      they risk duplicate DDL with ``create_all`` (space_id / is_builtin
      / perf indexes — see the guards in those files).

    Only DB-creation and seed are essential. Everything else (tool
    reload, skill sync, agent pre-warm) happens in _init_optional() so a
    single non-essential failure does not block background services.
    """
    import src.models  # noqa: F401 — ensure all ORM models are registered with Base.metadata
    from sqlalchemy import inspect as sa_inspect

    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.wiki_path, exist_ok=True)
    for sub in ("wiki", "raw", "meta"):
        os.makedirs(os.path.join(settings.wiki_path, sub), exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

    async with engine.begin() as conn:
        has_alembic = await conn.run_sync(
            lambda sync_conn: "alembic_version" in sa_inspect(sync_conn).get_table_names()
        )
        # Always run create_all — it's idempotent and covers the ORM
        # tables that have no dedicated Alembic migration.
        await conn.run_sync(Base.metadata.create_all)
        logger.info(
            "DB init: Base.metadata.create_all complete (alembic_version present=%s)",
            has_alembic,
        )

    # Stamp outside the engine transaction — Alembic opens its own
    # connection. Only stamp on a truly fresh volume; re-stamping an
    # existing alembic_version row would overwrite whatever revision
    # the Dockerfile's ``alembic upgrade`` just reached.
    if not has_alembic:
        try:
            await asyncio.to_thread(_alembic_stamp_head)
        except Exception:
            logger.exception(
                "DB init: alembic stamp head failed (non-fatal, schema "
                "is already at head via create_all; next upgrade will reconcile)"
            )

    from scripts.seed import seed as run_seed
    await run_seed()

    return True


def _alembic_stamp_head() -> None:
    """Stamp the DB at ``head`` after a ``create_all`` bootstrap.

    Called from :func:`_init_database` when the DB has no ``alembic_version``
    row. Running Alembic programmatically here means a fresh deploy doesn't
    try to re-apply every migration on top of a schema ``create_all`` just
    built (which would hit the same duplicate-column errors we patched in
    the migration files).
    """
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    ini_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini_path))
    # Point script_location at the absolute migrations/ dir so stamping
    # works regardless of the CWD uvicorn is started from.
    cfg.set_main_option(
        "script_location",
        str(ini_path.parent / "migrations"),
    )
    command.stamp(cfg, "head")
    logger.info("DB init: stamped alembic at head")


async def _init_optional() -> None:
    """Non-essential init: tool reload, skill sync, agent seeding, pre-warm.

    Each step is independently try/except'd so one failure does not cascade.
    """
    from src.services.tool_manager import tool_manager
    await tool_manager.reload()

    from src.services.skill_sync import auto_register_filesystem_skills
    try:
        registered = await auto_register_filesystem_skills()
        if registered:
            logger.info("Auto-registered %d filesystem skills as DB tools", registered)
            await tool_manager.reload()
    except Exception:
        logger.exception("Skill sync failed (non-fatal)")

    try:
        await _auto_seed_agents()
    except Exception:
        logger.exception("Agent auto-seed failed (non-fatal)")

    try:
        from src.agent.deep_agent import get_deep_agent
        _agent = await get_deep_agent()
        logger.info("Agent pre-warmed successfully")
    except Exception:
        logger.exception("Agent pre-warm failed (non-fatal)")


async def _start_background_services(app: FastAPI):
    """Start all background services with individual error handling."""
    if settings.kb_monitor_enabled:
        try:
            from src.services.kb_monitor import kb_monitor
            await kb_monitor.start(poll_interval=settings.kb_monitor_poll_interval)
        except Exception:
            logger.exception("KB monitor failed to start")

    # Auto-start WeCom WebSocket monitors for active bot_websocket channels
    try:
        from sqlalchemy import select

        from src.models.base import async_session_factory
        from src.models.channel import NotificationChannel
        from src.services.channels.wecom.agent_bridge import handle_wecom_message
        from src.services.channels.wecom.monitor import start_monitor

        async with async_session_factory() as db:
            result = await db.execute(
                select(NotificationChannel).where(
                    NotificationChannel.is_active,
                    NotificationChannel.channel_type == "wecom",
                )
            )
            channels = result.scalars().all()

        started = 0
        for ch in channels:
            config = ch.config or {}
            if config.get("wecom_sub_type") != "bot_websocket":
                continue
            bot_id = config.get("bot_id", "")
            bot_secret = config.get("bot_secret", "")
            if not bot_id or not bot_secret:
                logger.warning("WeCom auto-start: channel %s missing bot_id/bot_secret", ch.id)
                continue

            async def _make_callback(cfg):
                async def cb(parsed_msg, ws, frame):
                    await handle_wecom_message(parsed_msg, cfg, ws, frame)
                return cb

            await start_monitor(
                bot_id=bot_id,
                bot_secret=bot_secret,
                ws_url=config.get("ws_api_base") or "",
                account_id="default",
                on_message=await _make_callback(config),
            )
            started += 1
            logger.info("WeCom bot monitor auto-started for channel %s (%s)", ch.name, ch.id)

        if started > 0:
            logger.info("Auto-started %d WeCom bot monitor(s)", started)
    except Exception:
        logger.exception("WeCom monitor auto-start failed")

    # Auto-register WeCom webhook callback routes for channels with callback config
    try:
        from src.services.channels.wecom.agent_bridge import handle_wecom_message
        from src.services.channels.wecom.webhook_handler import create_webhook_router

        async with async_session_factory() as db:
            result = await db.execute(
                select(NotificationChannel).where(
                    NotificationChannel.is_active,
                    NotificationChannel.channel_type == "wecom",
                )
            )
            channels = result.scalars().all()

        for ch in channels:
            config = ch.config or {}
            callback_token = config.get("callback_token", "")
            callback_aes_key = config.get("callback_encoding_aes_key", "")
            if not callback_token or not callback_aes_key:
                continue

            receive_id = config.get("callback_receive_id", config.get("corp_id", ""))

            async def _webhook_handler(parsed_msg, message, _cfg=dict(config)):
                try:
                    reply_text = await asyncio.wait_for(
                        handle_wecom_message(parsed_msg, _cfg),
                        timeout=_cfg.get("webhook_timeout", 4.0),
                    )
                    if reply_text:
                        return {"msgtype": "markdown", "markdown": {"content": reply_text}}
                except TimeoutError:
                    logger.warning(
                        "[wecom-webhook] agent reply timed out for chatid=%s (limit=%.1fs)",
                        parsed_msg.chatid, _cfg.get("webhook_timeout", 4.0),
                    )
                except Exception:
                    logger.exception("[wecom-webhook] agent error for chatid=%s", parsed_msg.chatid)
                return None

            webhook_router = create_webhook_router(
                token=callback_token,
                encoding_aes_key=callback_aes_key,
                receive_id=receive_id,
                on_message=_webhook_handler,
            )
            app.include_router(webhook_router)
            logger.info(
                "WeCom webhook callback router registered for channel %s (%s)", ch.name, ch.id
            )
            break  # Only one webhook router — callback URL is per-server
    except Exception:
        logger.exception("WeCom webhook router registration failed")

    try:
        from src.services.cron_scheduler import cron_scheduler
        await cron_scheduler.start()
    except Exception:
        logger.exception("Cron scheduler failed to start")

    # Task 25.1 — legacy sleep_detector removed; SleepScheduler is the
    # sole consolidation dispatcher.
    try:
        from src.services.sleep_scheduler import start_sleep_scheduler
        await start_sleep_scheduler()
    except Exception:
        logger.exception("SleepScheduler failed to start")

    try:
        from src.services.api_poller import api_poller
        await api_poller.start()
    except Exception:
        logger.exception("API poller failed to start")

    try:
        from src.services.kafka_source_manager import kafka_source_manager
        await kafka_source_manager.start()
    except Exception:
        logger.exception("Kafka source manager failed to start")

    # Ensure default Kafka topics exist (Phase B task 4.7 / R-5.1).
    # Non-blocking: log on failure and carry on; /readyz will reflect status.
    try:
        from src.services.kafka.ensure import ensure_default_topics
        report = await ensure_default_topics()
        if report.errors:
            logger.warning("kafka ensure completed with errors: %s", report.errors)
        else:
            logger.info(
                "kafka topics ensured: created=%s existing=%s upgraded=%s",
                report.created, report.existing, report.upgraded,
            )
    except Exception:
        logger.exception("kafka ensure failed (non-fatal)")


async def _stop_background_services():
    """Stop all background services with individual error handling."""
    try:
        from src.services.kafka_source_manager import kafka_source_manager
        await kafka_source_manager.stop()
    except Exception:
        pass

    try:
        from src.services.api_poller import api_poller
        await api_poller.stop()
    except Exception:
        pass

    try:
        from src.services.sleep_scheduler import stop_sleep_scheduler
        await stop_sleep_scheduler()
    except Exception:
        pass

    try:
        from src.services.cron_scheduler import cron_scheduler
        await cron_scheduler.stop()
    except Exception:
        pass

    try:
        from src.services.kb_monitor import kb_monitor
        await kb_monitor.stop()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("AIOpsOS server starting")

    # DB init — retry once with a short delay if the first attempt fails
    db_ok = False
    for attempt in (1, 2):
        try:
            db_ok = await _init_database(app)
            break
        except Exception:
            logger.exception("DB init attempt %d failed", attempt)
            if attempt == 1:
                await __import__("asyncio").sleep(2)

    if not db_ok:
        logger.warning("DB init failed after retries; background services will not start")

    # Non-essential init runs independently — failures do not block services
    if db_ok:
        try:
            await _init_optional()
        except Exception:
            logger.exception("Optional init failed (non-fatal)")

    # Phase C — seed default feature flags, start flag service + trajectory sink
    # BEFORE background services so downstream services can call
    # ``feature_flags.is_enabled(...)`` during their own startup.
    if db_ok:
        try:
            from src.services.feature_flags_bootstrap import seed_default_flags
            await seed_default_flags()
        except Exception:
            logger.exception("Feature flag seed failed (non-fatal)")

        try:
            from src.services.feature_flags import get_feature_flags
            await get_feature_flags()
        except Exception:
            logger.exception("Feature flag service start failed (non-fatal)")

        # Register real schemas over the placeholder rows seeded by ensure.py.
        try:
            from src.services.kafka.schemas_seed import register_trajectory_schema
            await register_trajectory_schema()
        except Exception:
            logger.exception("Trajectory schema register failed (non-fatal)")

        # TrajectorySink spin-up is best-effort — Kafka broker outages must not
        # block the app; events will be dropped + counted until the broker is
        # back.
        try:
            from src.services.agent_runtime.trajectory import get_trajectory_sink
            await get_trajectory_sink()
        except Exception:
            logger.exception("TrajectorySink start failed (non-fatal)")

    # Start background services if DB is available
    if db_ok:
        await _start_background_services(app)

    # Embedded Celery worker for allinone deployments. Skipped under TESTING=1
    # so the test suite does not spawn a background worker thread.
    if settings.service_type == "allinone" and os.environ.get("TESTING") != "1":
        try:
            from src.workers.embedded import start_embedded_worker
            start_embedded_worker()
        except Exception:
            logger.exception("Embedded Celery worker failed to start")

    yield

    try:
        from src.workers.embedded import stop_embedded_worker
        stop_embedded_worker()
    except Exception:
        pass

    # Phase C — tear down trajectory sink + flag service in reverse order.
    try:
        from src.services.agent_runtime.trajectory import shutdown_trajectory_sink
        await shutdown_trajectory_sink()
    except Exception:
        logger.exception("TrajectorySink shutdown failed (non-fatal)")

    try:
        from src.services.feature_flags import shutdown_feature_flags
        await shutdown_feature_flags()
    except Exception:
        logger.exception("Feature flag service shutdown failed (non-fatal)")

    await _stop_background_services()

    try:
        from src.core.redis import close_redis
        await close_redis()
    except Exception:
        pass

    logger.info("AIOpsOS server shutting down")


app = FastAPI(
    title="AIOpsOS",
    description="AI运维智能操作系统",
    version="0.1.0",
    lifespan=lifespan,
)

init_tracing(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000
    logger.info(
        "%s %s %d %.1fms",
        request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


app.include_router(control_router)
app.include_router(execution_router)
app.include_router(datasource_router)
app.include_router(webhook_router)
app.include_router(callback_router)
app.include_router(notification_router)
app.include_router(tasks_router)
app.include_router(log_search_router)
app.include_router(itsm_search_router)
app.include_router(workflow_router)
app.include_router(public_router)
app.include_router(metrics_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness probe — 200 iff all default Kafka topics are present (R-5.1).

    Used by Kubernetes / compose healthchecks to detect when the platform's
    managed Kafka state is fully converged after startup. Returns 503 until
    :func:`ensure_default_topics` has created every default topic.
    """
    from fastapi.responses import JSONResponse

    from src.services.kafka.ensure import default_topics_present

    ok = await default_topics_present()
    if ok:
        return {"status": "ready", "kafka_topics": "present"}
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "kafka_topics": "missing"},
    )
