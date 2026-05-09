import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.execution.router import router as execution_router
from src.core.logging import setup_logging
from src.core.metrics import metrics_router
from src.core.tracing import init_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("AIOpsOS execution plane starting")
    # Ensure default Kafka topics exist (Phase B task 4.7 / R-5.1). Non-blocking.
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

    # Prompt hot-reload pipeline (task 20.3 / R-3.15, R-3.17).
    # Order matters:
    #   1. generate this process's instance_id (used by PromptReloader's
    #      per-process consumer group name);
    #   2. build the SubAgentPromptRegistry (load from DB);
    #   3. start PromptReloader so subsequent promote events converge;
    #   4. start the TTL reaper so abandoned consumer groups are
    #      eventually cleaned up.
    # Any individual failure here is logged but not fatal — serving
    # traffic with a stale registry is better than failing startup.
    prompt_reloader = None
    reaper = None
    try:
        from src.core.instance import get_consumer_group_reaper, instance_id
        from src.services.evolution.prompt_registry import get_prompt_registry
        from src.services.evolution.prompt_reloader import PromptReloader

        iid = instance_id()
        logger.info("execution plane instance_id=%s", iid)

        registry = await get_prompt_registry()
        prompt_reloader = PromptReloader(registry)
        await prompt_reloader.start()
        app.state.prompt_reloader = prompt_reloader
        app.state.prompt_registry = registry

        reaper = get_consumer_group_reaper()
        await reaper.start()
        app.state.consumer_group_reaper = reaper
    except Exception:
        logger.exception("prompt hot-reload startup failed (non-fatal)")

    try:
        yield
    finally:
        logger.info("AIOpsOS execution plane shutting down")
        # Symmetric teardown. Stop the reaper first so it doesn't try
        # to describe groups while Kafka is going away.
        if reaper is not None:
            try:
                await reaper.stop()
            except Exception:
                logger.exception("consumer-group reaper stop failed")
        if prompt_reloader is not None:
            try:
                await prompt_reloader.stop()
            except Exception:
                logger.exception("prompt_reloader stop failed")


app = FastAPI(
    title="AIOpsOS Execution Plane",
    description="数据平面",
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

app.include_router(execution_router)
app.include_router(metrics_router)


@app.get("/health")
async def health():
    return {"status": "ok", "plane": "execution"}


@app.get("/readyz")
async def readyz():
    """Readiness probe — 200 iff all default Kafka topics are present (R-5.1)."""
    from fastapi.responses import JSONResponse

    from src.services.kafka.ensure import default_topics_present

    ok = await default_topics_present()
    if ok:
        return {"status": "ready", "kafka_topics": "present"}
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "kafka_topics": "missing"},
    )
