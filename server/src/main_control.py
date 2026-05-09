import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.control.router import router as control_router
from src.core.logging import setup_logging
from src.core.metrics import metrics_router
from src.core.tracing import init_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("AIOpsOS control plane starting")
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

    # Phase C — feature flags + TrajectoryEvent schema + flag service.
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

    try:
        from src.services.kafka.schemas_seed import register_trajectory_schema
        await register_trajectory_schema()
    except Exception:
        logger.exception("Trajectory schema register failed (non-fatal)")

    yield

    # Shutdown: reverse order.
    try:
        from src.services.feature_flags import shutdown_feature_flags
        await shutdown_feature_flags()
    except Exception:
        logger.exception("Feature flag service shutdown failed (non-fatal)")

    logger.info("AIOpsOS control plane shutting down")


app = FastAPI(
    title="AIOpsOS Control Plane",
    description="管控平面",
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

app.include_router(control_router)
app.include_router(metrics_router)


@app.get("/health")
async def health():
    return {"status": "ok", "plane": "control"}


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
