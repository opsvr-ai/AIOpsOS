import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DbSession, get_current_user, require_perm
from src.models.agent import (
    Agent, AgentVersion, Scenario, Tool,
    agent_channels, agent_sub_agents, agent_tools,
)
from src.models.channel import NotificationChannel
from src.schemas.agent import (
    AgentCreate, AgentOut, AgentRollbackRequest, AgentUpdate, AgentVersionOut,
    ScenarioCreate, ScenarioOut,
)
from src.services.agent_sync import create_agent_version_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()


async def _load_agent_with_rels(db, agent_id: str) -> Agent | None:
    result = await db.execute(
        select(Agent)
        .where(Agent.id == agent_id)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.sub_agents),
            selectinload(Agent.channels),
        )
    )
    return result.scalar_one_or_none()


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(
        select(Agent)
        .options(
            selectinload(Agent.tools),
            selectinload(Agent.sub_agents),
            selectinload(Agent.channels),
        )
        .order_by(Agent.created_at.desc())
    )
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
    _=Depends(require_perm("agents", "update"))
):
    agent = await _load_agent_with_rels(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    await create_agent_version_snapshot(db, agent)

    data = body.model_dump(exclude_unset=True)
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
async def seed_agents(db: DbSession, _=Depends(require_perm("agents", "create"))):
    from src.agent.deep_agent import (
        AI_OPS_SYSTEM_PROMPT,
        KNOWLEDGE_SYSTEM_PROMPT, MONITOR_SYSTEM_PROMPT,
        OPS_SYSTEM_PROMPT, ANALYSIS_SYSTEM_PROMPT,
        SUBAGENTS, KNOWLEDGE_TOOLS,
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
            updated += 1
        else:
            agent = Agent(
                name=name, type=atype, system_prompt=prompt,
                model_name=model, agent_type=agent_type, is_active=True,
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
                is_active=True, is_approved=True,
            )
            db.add(tool)
            await db.flush()
        return tool

    main = await _upsert_agent("AIOpsOS 主智能体", "main", AI_OPS_SYSTEM_PROMPT)

    sub_map: dict[str, Agent] = {}
    prompt_map = {
        "knowledge": KNOWLEDGE_SYSTEM_PROMPT,
        "monitor": MONITOR_SYSTEM_PROMPT,
        "ops": OPS_SYSTEM_PROMPT,
        "analysis": ANALYSIS_SYSTEM_PROMPT,
    }
    for sa in SUBAGENTS:
        sub = await _upsert_agent(
            f"{sa['name']} 子智能体", "sub",
            prompt_map.get(sa['name'], sa.get('system_prompt', '') or ""),
        )
        sub_map[sa['name']] = sub

    for kt in KNOWLEDGE_TOOLS:
        tool = await _upsert_tool(kt.name, kt.description or "")
        try:
            await db.execute(
                agent_tools.insert().values(agent_id=main.id, tool_id=tool.id)
            )
        except Exception:
            pass

    for _sa_name, sub in sub_map.items():
        try:
            await db.execute(
                agent_sub_agents.insert().values(
                    main_agent_id=main.id, sub_agent_id=sub.id,
                )
            )
        except Exception:
            pass

    await db.commit()
    return {"created": created, "updated": updated, "agents": agent_ids}


# ── Scenarios ─────────────────────────────────────────────────────────

@router.get("/scenarios", response_model=list[ScenarioOut])
async def list_scenarios(db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(Scenario).order_by(Scenario.created_at.desc()))
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
