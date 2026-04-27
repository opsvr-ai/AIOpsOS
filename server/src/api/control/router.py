from fastapi import APIRouter

from src.api.control.agents import router as agents_router
from src.api.control.channels import router as channels_router
from src.api.control.cron import router as cron_router
from src.api.control.knowledge import router as knowledge_router
from src.api.control.docs import router as docs_router
from src.api.control.memory import router as memory_router
from src.api.control.schedules import router as schedules_router
from src.api.control.sleep_management import router as sleep_router
from src.api.control.tools import router as tools_router
from src.api.control.users import router as users_router

router = APIRouter(prefix="/api/v1")
router.include_router(users_router)
router.include_router(agents_router)
router.include_router(tools_router)
router.include_router(schedules_router)
router.include_router(cron_router)
router.include_router(channels_router)
router.include_router(knowledge_router)
router.include_router(docs_router)
router.include_router(memory_router)
router.include_router(sleep_router)
