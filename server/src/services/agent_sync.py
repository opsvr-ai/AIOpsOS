"""Agent version snapshot helper — saves current agent state before each modification."""

from src.models.agent import Agent, AgentVersion


async def create_agent_version_snapshot(db, agent: Agent) -> AgentVersion:
    version = AgentVersion(
        agent_id=agent.id,
        name=agent.name,
        system_prompt=agent.system_prompt,
        user_prompt=agent.user_prompt,
        model_name=agent.model_name,
        agent_type=agent.agent_type,
        config=dict(agent.config or {}),
    )
    db.add(version)
    return version
