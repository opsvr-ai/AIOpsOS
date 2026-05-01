import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.execution.router import router as execution_router
from src.core.logging import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("AIOpsOS execution plane starting")
    yield
    logger.info("AIOpsOS execution plane shutting down")


app = FastAPI(
    title="AIOpsOS Execution Plane",
    description="数据平面",
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

app.include_router(execution_router)


@app.get("/health")
async def health():
    return {"status": "ok", "plane": "execution"}
