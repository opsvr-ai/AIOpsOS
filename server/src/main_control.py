import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.control.router import router as control_router
from src.core.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("AIOpsOS control plane starting")
    yield
    logger.info("AIOpsOS control plane shutting down")


app = FastAPI(
    title="AIOpsOS Control Plane",
    description="管控平面",
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


@app.get("/health")
async def health():
    return {"status": "ok", "plane": "control"}
