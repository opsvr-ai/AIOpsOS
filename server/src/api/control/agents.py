import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from src.api.deps import (
    DbSession,
    get_current_user,
    get_optional_space_id,
    require_perm,
)
from src.models.agent import (
    Agent,
    AgentVersion,
    Scenario,
    Tool,
    agent_sub_agents,
    agent_tools,
)
from src.models.channel import NotificationChannel
from src.models.knowledge import KnowledgeDocument
from src.schemas.agent import (
    AgentCreate,
    AgentOut,
    AgentRollbackRequest,
    AgentUpdate,
    AgentVersionOut,
    ScenarioCreate,
    ScenarioOut,
)
from src.schemas.scenario import (
    ScenarioCreate as ScenarioCreateV2,
    ScenarioFromTemplateCreate,
    ScenarioResponse,
    ScenarioTemplateListResponse,
    ScenarioTemplateResponse,
)
from src.services.agent_sync import create_agent_version_snapshot
from src.services.template_service import TemplateService

logger = logging.getLogger(__name__)
router = APIRouter()

# Initialize template service singleton
_template_service: TemplateService | None = None


def get_template_service() -> TemplateService:
    """Get or create the template service singleton."""
    global _template_service
    if _template_service is None:
        _template_service = TemplateService()
    return _template_service


async def _load_agent_with_rels(db, agent_id: str) -> Agent | None:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.sub_agents).selectinload(Agent.tools),
            selectinload(Agent.sub_agents).selectinload(Agent.channels),
            selectinload(Agent.channels),
        )
    )
    return result.scalar_one_or_none()


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    query = (
        select(Agent)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.sub_agents).selectinload(Agent.tools),
            selectinload(Agent.sub_agents).selectinload(Agent.channels),
            selectinload(Agent.channels),
        )
    )
    if space_id:
        query = query.where((Agent.space_id == space_id) | (Agent.space_id.is_(None)))
    result = await db.execute(query.order_by(Agent.created_at.desc()))
    return result.scalars().all()


@router.post("/agents", response_model=AgentOut)
async def create_agent(
    body: AgentCreate, db: DbSession, _=Depends(require_perm("agents", "create"))
):
    data = body.model_dump()
    tool_ids = data.pop("tool_ids", [])
    sub_agent_ids = data.pop("sub_agent_ids", [])
    channel_ids = data.pop("channel_ids", [])

    agent = Agent(**data)
    db.add(agent)
    await db.flush()

    if tool_ids:
        result = await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))
        agent.tools = result.scalars().all()
    if sub_agent_ids:
        result = await db.execute(select(Agent).where(Agent.id.in_(sub_agent_ids)))
        agent.sub_agents = result.scalars().all()
    if channel_ids:
        result = await db.execute(
            select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))
        )
        agent.channels = result.scalars().all()

    await db.commit()
    await db.refresh(agent)
    return await _load_agent_with_rels(db, str(agent.id))


@router.get("/agents/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, db: DbSession, _=Depends(get_current_user)):
    agent = await _load_agent_with_rels(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.patch("/agents/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str, body: AgentUpdate, db: DbSession,
    user=Depends(get_current_user),
):
    # Permission check inline so we can distinguish admin vs regular
    is_admin = False
    has_perm = False
    for role in (user.roles or []):
        for perm in (role.permissions or []):
            if perm.resource == "admin":
                is_admin = True
                has_perm = True
            if perm.resource == "agents" and perm.action == "update":
                has_perm = True
    if not has_perm:
        raise HTTPException(status_code=403, detail="Permission denied")

    agent = await _load_agent_with_rels(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    await create_agent_version_snapshot(db, agent)

    data = body.model_dump(exclude_unset=True)

    # Builtin protection
    if agent.is_builtin:
        if data.get("is_active") is False:
            raise HTTPException(status_code=403, detail="Built-in agents cannot be deactivated")
        if "name" in data and not is_admin:
            raise HTTPException(status_code=403, detail="Only admins can rename built-in agents")

    # system_prompt: admin only
    if "system_prompt" in data and not is_admin:
        raise HTTPException(status_code=403, detail="Only admins can edit system prompt")

    # user_prompt: admin or matching editable_roles
    if "user_prompt" in data and not is_admin:
        user_role_names = {r.name for r in (user.roles or [])}
        agent_editable = set(agent.editable_roles or [])
        if not agent_editable or not user_role_names & agent_editable:
            raise HTTPException(status_code=403, detail="You don't have permission to edit the user prompt")

    tool_ids = data.pop("tool_ids", None)
    sub_agent_ids = data.pop("sub_agent_ids", None)
    channel_ids = data.pop("channel_ids", None)

    for key, val in data.items():
        setattr(agent, key, val)

    if tool_ids is not None:
        result = await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))
        agent.tools = result.scalars().all()
    if sub_agent_ids is not None:
        result = await db.execute(select(Agent).where(Agent.id.in_(sub_agent_ids)))
        agent.sub_agents = result.scalars().all()
    if channel_ids is not None:
        result = await db.execute(
            select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))
        )
        agent.channels = result.scalars().all()

    await db.commit()
    await db.refresh(agent)
    return await _load_agent_with_rels(db, agent_id)


@router.delete("/agents/{agent_id}")
async def delete_agent(
    agent_id: str, db: DbSession, _=Depends(require_perm("agents", "delete"))
):
    agent = await _load_agent_with_rels(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.is_builtin:
        raise HTTPException(status_code=403, detail="Built-in agents cannot be deleted")
    await db.delete(agent)
    await db.commit()
    return {"detail": "deleted"}


# ── Tool association ──────────────────────────────────────────────────

@router.get("/agents/{agent_id}/tools", response_model=list[str])
async def list_agent_tools(agent_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(agent_tools.c.tool_id).where(agent_tools.c.agent_id == agent_id)
    )
    return [str(r[0]) for r in result.all()]


@router.put("/agents/{agent_id}/tools")
async def set_agent_tools(
    agent_id: str, body: dict, db: DbSession,
    _=Depends(require_perm("agents", "update"))
):
    tool_ids = body.get("tool_ids", [])
    await db.execute(
        agent_tools.delete().where(agent_tools.c.agent_id == agent_id)
    )
    for tid in tool_ids:
        await db.execute(
            agent_tools.insert().values(agent_id=agent_id, tool_id=tid)
        )
    await db.commit()
    return {"detail": "updated"}


# ── Sub-agent association ─────────────────────────────────────────────

@router.get("/agents/{agent_id}/sub-agents", response_model=list[str])
async def list_sub_agents(agent_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(agent_sub_agents.c.sub_agent_id).where(
            agent_sub_agents.c.main_agent_id == agent_id
        )
    )
    return [str(r[0]) for r in result.all()]


@router.put("/agents/{agent_id}/sub-agents")
async def set_sub_agents(
    agent_id: str, body: dict, db: DbSession,
    _=Depends(require_perm("agents", "update"))
):
    sub_ids = body.get("sub_agent_ids", [])
    await db.execute(
        agent_sub_agents.delete().where(agent_sub_agents.c.main_agent_id == agent_id)
    )
    for sid in sub_ids:
        await db.execute(
            agent_sub_agents.insert().values(main_agent_id=agent_id, sub_agent_id=sid)
        )
    await db.commit()
    return {"detail": "updated"}


# ── Channel association ───────────────────────────────────────────────

@router.get("/agents/{agent_id}/channels")
async def list_agent_channels(
    agent_id: str, db: DbSession, _=Depends(get_current_user)
):
    agent = await _load_agent_with_rels(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return [{"id": str(c.id), "name": c.name} for c in agent.channels]


@router.put("/agents/{agent_id}/channels")
async def set_agent_channels(
    agent_id: str, body: dict, db: DbSession,
    _=Depends(require_perm("agents", "update"))
):
    channel_ids = body.get("channel_ids", [])
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))
    )
    channels = result.scalars().all()
    agent = await _load_agent_with_rels(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.channels = channels
    await db.commit()
    return {"detail": "updated"}


# ── Version management ────────────────────────────────────────────────

@router.get("/agents/{agent_id}/versions", response_model=list[AgentVersionOut])
async def list_agent_versions(
    agent_id: str, db: DbSession, _=Depends(get_current_user)
):
    result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.created_at.desc())
    )
    return result.scalars().all()


@router.post("/agents/{agent_id}/rollback", response_model=AgentOut)
async def rollback_agent(
    agent_id: str, body: AgentRollbackRequest, db: DbSession,
    _=Depends(require_perm("agents", "update"))
):
    agent = await _load_agent_with_rels(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    await create_agent_version_snapshot(db, agent)

    result = await db.execute(
        select(AgentVersion).where(AgentVersion.id == body.version_id)
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Version not found")

    agent.name = target.name
    agent.system_prompt = target.system_prompt
    agent.model_name = target.model_name
    agent.agent_type = target.agent_type
    agent.config = dict(target.config or {})

    await db.commit()
    await db.refresh(agent)
    return await _load_agent_with_rels(db, agent_id)


# ── Reload ────────────────────────────────────────────────────────────

@router.post("/agents/reload")
async def reload_agents(_=Depends(require_perm("agents", "update"))):
    from src.agent.deep_agent import reload_deep_agent
    from src.services.tool_manager import tool_manager

    await tool_manager.reload()
    await reload_deep_agent()
    logger.info("Agent reload triggered via API")
    return {"detail": "reloaded"}


# ── Seed ──────────────────────────────────────────────────────────────

@router.post("/agents/seed")
async def seed_agents(db: DbSession, _=Depends(get_current_user)):
    from src.agent.deep_agent import (
        A2UI_GENERATOR_SYSTEM_PROMPT,
        AI_OPS_SYSTEM_PROMPT,
        ANALYSIS_SYSTEM_PROMPT,
        CMDB_SYSTEM_PROMPT,
        KNOWLEDGE_SYSTEM_PROMPT,
        KNOWLEDGE_TOOLS,
        MEMORY_SYSTEM_PROMPT,
        MONITOR_SYSTEM_PROMPT,
        OPS_SYSTEM_PROMPT,
        REPORT_GENERATOR_SYSTEM_PROMPT,
        SUBAGENTS,
    )

    created = 0
    updated = 0
    agent_ids: list[str] = []

    async def _upsert_agent(name, atype, prompt, model="deepseek-v4-flash", agent_type="deep_agent"):
        nonlocal created, updated
        result = await db.execute(
            select(Agent).where(Agent.name == name, Agent.type == atype)
        )
        agent = result.scalar_one_or_none()
        if agent:
            agent.system_prompt = prompt
            agent.model_name = model
            agent.agent_type = agent_type
            agent.is_builtin = True
            updated += 1
        else:
            agent = Agent(
                name=name, type=atype, system_prompt=prompt,
                model_name=model, agent_type=agent_type, is_active=True,
                is_builtin=True,
            )
            db.add(agent)
            created += 1
        await db.flush()
        agent_ids.append(str(agent.id))
        return agent

    async def _upsert_tool(name, desc):
        result = await db.execute(select(Tool).where(Tool.name == name))
        tool = result.scalar_one_or_none()
        if tool is None:
            tool = Tool(
                name=name, type="builtin", description=desc,
                is_active=True, is_approved=True, is_builtin=True,
            )
            db.add(tool)
            await db.flush()
        else:
            tool.is_builtin = True
        return tool

    main = await _upsert_agent("AIOpsOS 主智能体", "main", AI_OPS_SYSTEM_PROMPT)

    sub_map: dict[str, Agent] = {}
    prompt_map = {
        "knowledge": KNOWLEDGE_SYSTEM_PROMPT,
        "monitor": MONITOR_SYSTEM_PROMPT,
        "ops": OPS_SYSTEM_PROMPT,
        "analysis": ANALYSIS_SYSTEM_PROMPT,
        "memory": MEMORY_SYSTEM_PROMPT,
        "cmdb_ingestion": CMDB_SYSTEM_PROMPT,
        "report_generator": REPORT_GENERATOR_SYSTEM_PROMPT,
        "a2ui_generator": A2UI_GENERATOR_SYSTEM_PROMPT,
    }
    for sa in SUBAGENTS:
        sub = await _upsert_agent(
            f"{sa['name']} 子智能体", "sub",
            prompt_map.get(sa['name'], sa.get('system_prompt', '') or ""),
        )
        sub_map[sa['name']] = sub

    for kt in KNOWLEDGE_TOOLS:
        tool = await _upsert_tool(kt.name, kt.description or "")
        await db.execute(
            pg_insert(agent_tools)
            .values(agent_id=main.id, tool_id=tool.id)
            .on_conflict_do_nothing()
        )

    for _sa_name, sub in sub_map.items():
        await db.execute(
            pg_insert(agent_sub_agents)
            .values(main_agent_id=main.id, sub_agent_id=sub.id)
            .on_conflict_do_nothing()
        )

    await db.commit()
    return {"created": created, "updated": updated, "agents": agent_ids}


# ── Scenario Templates ────────────────────────────────────────────────
# Requirements 2.1-2.6: Scenario template management APIs
# NOTE: These routes are defined BEFORE /scenarios/{scenario_id} to ensure
# proper route matching (FastAPI matches routes in order)


@router.get("/scenarios/templates", response_model=ScenarioTemplateListResponse)
async def list_scenario_templates(
    _=Depends(get_current_user),
):
    """List all available scenario templates.

    Returns all built-in scenario templates including:
    - fault_isolation: 故障定界模板
    - health_inspection: 健康巡检模板
    - capacity_prediction: 容量预测模板
    - alert_analysis: 告警分析模板

    Requirements 2.1: THE Scenario_Template_System SHALL provide built-in templates.
    """
    template_service = get_template_service()
    templates = template_service.list_templates()
    return ScenarioTemplateListResponse(
        templates=templates,
        total=len(templates),
    )


@router.get("/scenarios/templates/{template_id}", response_model=ScenarioTemplateResponse)
async def get_scenario_template(
    template_id: str,
    _=Depends(get_current_user),
):
    """Get details of a specific scenario template.

    Args:
        template_id: Template identifier (fault_isolation, health_inspection,
                    capacity_prediction, alert_analysis)

    Returns:
        Template details including default configuration, parameter schema,
        and recommended tools/agents.

    Raises:
        HTTPException 404: If template not found.

    Requirements 2.1, 2.4, 2.5: Provide template details with default params
    schema and recommended resources.
    """
    template_service = get_template_service()
    template = template_service.get_template(template_id)
    if template is None:
        valid_templates = ", ".join(template_service.get_template_ids())
        raise HTTPException(
            status_code=404,
            detail=f"Template '{template_id}' not found. Valid templates: {valid_templates}",
        )
    return template


@router.post("/scenarios/from-template", response_model=ScenarioResponse)
async def create_scenario_from_template(
    body: ScenarioFromTemplateCreate,
    db: DbSession,
    _=Depends(require_perm("scenarios", "create")),
):
    """Create a new scenario from a template with optional customization.

    This endpoint allows creating scenarios based on built-in templates.
    Template defaults are automatically applied, and users can customize
    any configuration as needed.

    Args:
        body: Template ID and optional customizations including:
            - template_id: Required template identifier
            - name: Required scenario name
            - description: Optional custom description
            - trigger_command: Optional custom trigger command
            - nl_prompt: Optional custom NL prompt
            - params_schema: Optional custom params (merged with template defaults)
            - execution_timeout: Optional custom timeout
            - enable_collaboration: Optional collaboration flag
            - collaboration_config: Optional collaboration settings
            - tool_ids: Optional tool associations
            - agent_ids: Optional agent associations
            - knowledge_doc_ids: Optional knowledge document associations
            - channel_ids: Optional notification channel associations

    Returns:
        The created scenario with template configuration applied.

    Raises:
        HTTPException 400: If template_id is invalid.
        HTTPException 422: If validation fails.

    Requirements:
        - 2.2: Auto-fill template predefined configuration items
        - 2.3: Allow user customization on top of template
        - 2.6: Record scenario's template source
    """
    template_service = get_template_service()

    # Apply template to create ScenarioCreate object
    try:
        scenario_create = template_service.apply_template(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Convert to dict for database insertion
    data = scenario_create.model_dump()
    tool_ids = data.pop("tool_ids", [])
    agent_ids = data.pop("agent_ids", [])
    knowledge_doc_ids = data.pop("knowledge_doc_ids", [])
    channel_ids = data.pop("channel_ids", [])

    # Convert collaboration_config to dict if it's a Pydantic model
    if data.get("collaboration_config") is not None:
        if hasattr(data["collaboration_config"], "model_dump"):
            data["collaboration_config"] = data["collaboration_config"].model_dump()

    # Create scenario
    scenario = Scenario(**data)

    # Associate tools
    if tool_ids:
        result = await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))
        scenario.tools = result.scalars().all()

    # Associate agents
    if agent_ids:
        result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
        scenario.agents = result.scalars().all()

    # Associate knowledge documents
    if knowledge_doc_ids:
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id.in_(knowledge_doc_ids))
        )
        scenario.knowledge_docs = result.scalars().all()

    # Associate notification channels
    if channel_ids:
        result = await db.execute(
            select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))
        )
        scenario.notification_channels = result.scalars().all()

    db.add(scenario)
    await db.commit()
    await db.refresh(scenario)

    logger.info(
        "Created scenario '%s' from template '%s' (id=%s)",
        scenario.name,
        body.template_id,
        scenario.id,
    )

    return scenario


# ── Scenarios ─────────────────────────────────────────────────────────

@router.get("/scenarios", response_model=list[ScenarioOut])
async def list_scenarios(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    query = select(Scenario)
    if space_id:
        query = query.where((Scenario.space_id == space_id) | (Scenario.space_id.is_(None)))
    result = await db.execute(query.order_by(Scenario.created_at.desc()))
    return result.scalars().all()


@router.post("/scenarios", response_model=ScenarioOut)
async def create_scenario(
    body: ScenarioCreate, db: DbSession, _=Depends(require_perm("scenarios", "create"))
):
    data = body.model_dump()
    tool_ids = data.pop("tool_ids", [])
    agent_ids = data.pop("agent_ids", [])
    scenario = Scenario(**data)
    if tool_ids:
        result = await db.execute(select(Tool).where(Tool.id.in_(tool_ids)))
        scenario.tools = result.scalars().all()
    if agent_ids:
        result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
        scenario.agents = result.scalars().all()
    db.add(scenario)
    await db.commit()
    await db.refresh(scenario)
    return scenario


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
async def get_scenario(scenario_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(Scenario).where(Scenario.id == scenario_id).options(
            selectinload(Scenario.tools), selectinload(Scenario.agents)
        )
    )
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return scenario


@router.delete("/scenarios/{scenario_id}")
async def delete_scenario(
    scenario_id: str, db: DbSession, _=Depends(require_perm("scenarios", "delete"))
):
    result = await db.execute(select(Scenario).where(Scenario.id == scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    await db.delete(scenario)
    await db.commit()
    return {"detail": "deleted"}
