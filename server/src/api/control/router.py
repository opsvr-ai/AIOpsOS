from fastapi import APIRouter

from src.api.control.agents import router as agents_router
from src.api.control.analytics import router as analytics_router
from src.api.control.analytics_report import router as analytics_report_router
from src.api.control.assistant import router as assistant_router
from src.api.control.branding import router as branding_router
from src.api.control.channels import router as channels_router
from src.api.control.collaboration import router as collaboration_router
from src.api.control.cmdb import cmdb_router
from src.api.control.cron import router as cron_router
from src.api.control.dashboard import router as dashboard_router
from src.api.control.docs import router as docs_router
from src.api.control.events import router as events_router
from src.api.control.evolution import router as evolution_router
from src.api.control.feedback import router as feedback_router
from src.api.control.knowledge import router as knowledge_router
from src.api.control.kafka import router as kafka_router
from src.api.control.ldap import router as ldap_router
from src.api.control.logs import router as logs_router
from src.api.control.memory import router as memory_router
from src.api.control.model_providers import router as model_providers_router
from src.api.control.permissions import router as permissions_router
from src.api.control.reports import router as reports_router
from src.api.control.runtime_flags import router as runtime_flags_router
from src.api.control.scenario import router as scenario_router
from src.api.control.schedules import router as schedules_router
from src.api.control.sleep_management import router as sleep_router
from src.api.control.spaces import router as spaces_router
from src.api.control.tools import router as tools_router
from src.api.control.users import router as users_router

router = APIRouter(prefix="/api/v1")
router.include_router(dashboard_router)
router.include_router(cmdb_router)
router.include_router(users_router)
router.include_router(reports_router)
router.include_router(events_router)
router.include_router(agents_router)
router.include_router(tools_router)
router.include_router(spaces_router)
router.include_router(schedules_router)
router.include_router(cron_router)
router.include_router(channels_router)
router.include_router(knowledge_router)
router.include_router(docs_router)
router.include_router(memory_router)
router.include_router(sleep_router)
router.include_router(assistant_router)
router.include_router(permissions_router)
router.include_router(branding_router)
router.include_router(model_providers_router)
router.include_router(logs_router)
router.include_router(ldap_router)
router.include_router(feedback_router)
router.include_router(analytics_router)
router.include_router(analytics_report_router)
router.include_router(kafka_router)
router.include_router(runtime_flags_router)
router.include_router(evolution_router)
router.include_router(scenario_router)
router.include_router(collaboration_router)
