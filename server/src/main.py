import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.control.router import router as control_router
from src.api.execution.router import router as execution_router
from src.config import settings
from src.core.logging import setup_logging
from src.models.base import engine, Base

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging("DEBUG")
    logger.info("AIOpsOS server starting (debug logging)")

    # create tables and seed data on first run
    try:
        os.makedirs(settings.upload_dir, exist_ok=True)
        os.makedirs(settings.wiki_path, exist_ok=True)
        for sub in ("wiki", "raw", "meta"):
            os.makedirs(os.path.join(settings.wiki_path, sub), exist_ok=True)
        app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        from scripts.seed import seed as run_seed
        await run_seed()
        # reload tools so registered tools are available
        from src.services.tool_manager import tool_manager
        await tool_manager.reload()

        # auto-register filesystem skills as DB tools
        from src.services.skill_sync import auto_register_filesystem_skills
        registered = await auto_register_filesystem_skills()
        if registered:
            logger.info("Auto-registered %d filesystem skills as DB tools", registered)

        # auto-sync hermes agent skills (categorize + register new ones)
        from src.services.hermes_skill_scanner import sync_hermes_skills_to_db
        hermes_result = await sync_hermes_skills_to_db()
        if hermes_result["created"] or hermes_result["updated"]:
            logger.info("Hermes skill sync: created=%d, updated=%d, total=%d",
                        hermes_result["created"], hermes_result["updated"],
                        hermes_result["total_scanned"])

        if registered or hermes_result["created"] or hermes_result["updated"]:
            await tool_manager.reload()

        # auto-seed agents from hardcoded definitions if none exist
        from sqlalchemy import select, func
        from src.models.agent import Agent, agent_sub_agents, agent_tools
        from src.models.base import async_session_factory
        async with async_session_factory() as db:
            count = (await db.execute(select(func.count()).select_from(Agent))).scalar()
            if count == 0:
                from src.agent.deep_agent import (
                    AI_OPS_SYSTEM_PROMPT, KNOWLEDGE_SYSTEM_PROMPT,
                    MONITOR_SYSTEM_PROMPT, OPS_SYSTEM_PROMPT, ANALYSIS_SYSTEM_PROMPT,
                    SUBAGENTS, KNOWLEDGE_TOOLS,
                )
                from src.models.agent import Tool
                main = Agent(name="AIOpsOS 主智能体", type="main",
                             system_prompt=AI_OPS_SYSTEM_PROMPT,
                             model_name="deepseek-v4-flash", agent_type="deep_agent", is_active=True)
                db.add(main)
                await db.flush()
                prompt_map = {"knowledge": KNOWLEDGE_SYSTEM_PROMPT, "monitor": MONITOR_SYSTEM_PROMPT,
                              "ops": OPS_SYSTEM_PROMPT, "analysis": ANALYSIS_SYSTEM_PROMPT}
                sub_map = {}
                for sa in SUBAGENTS:
                    sub = Agent(name=f"{sa['name']} 子智能体", type="sub",
                                system_prompt=prompt_map.get(sa['name'], ""),
                                model_name="deepseek-v4-flash", agent_type="deep_agent", is_active=True)
                    db.add(sub)
                    await db.flush()
                    sub_map[sa['name']] = sub
                for kt in KNOWLEDGE_TOOLS:
                    result = await db.execute(select(Tool).where(Tool.name == kt.name))
                    tool = result.scalar_one_or_none()
                    if tool is None:
                        tool = Tool(name=kt.name, type="builtin", description=kt.description or "",
                                    is_active=True, is_approved=True)
                        db.add(tool)
                        await db.flush()
                    try:
                        await db.execute(agent_tools.insert().values(agent_id=main.id, tool_id=tool.id))
                    except Exception:
                        pass
                for _sa_name, sub in sub_map.items():
                    try:
                        await db.execute(agent_sub_agents.insert().values(main_agent_id=main.id, sub_agent_id=sub.id))
                    except Exception:
                        pass
                await db.commit()
                logger.info("Auto-seeded %d agents from hardcoded definitions", 1 + len(sub_map))

        # Start KB file monitor
        if settings.kb_monitor_enabled:
            from src.services.kb_monitor import kb_monitor
            await kb_monitor.start(poll_interval=settings.kb_monitor_poll_interval)

        # Start cron scheduler
        from src.services.cron_scheduler import cron_scheduler
        await cron_scheduler.start()

        # Start sleep detector
        from src.services.sleep_detector import sleep_detector
        await sleep_detector.start()
    except Exception:
        logger.warning("DB init skipped (may not be available yet)")

    yield

    # Shutdown sleep detector
    try:
        from src.services.sleep_detector import sleep_detector
        await sleep_detector.stop()
    except Exception:
        pass

    # Shutdown cron scheduler
    try:
        from src.services.cron_scheduler import cron_scheduler
        await cron_scheduler.stop()
    except Exception:
        pass

    # Shutdown KB monitor
    try:
        from src.services.kb_monitor import kb_monitor
        await kb_monitor.stop()
    except Exception:
        pass
    logger.info("AIOpsOS server shutting down")


app = FastAPI(
    title="AIOpsOS",
    description="AI运维智能操作系统",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(control_router)
app.include_router(execution_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
