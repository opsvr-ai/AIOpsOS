"""Seed database with default roles, permissions, admin user, tools, and agents."""

import asyncio
import logging

from sqlalchemy import select

from src.models.agent import Agent, Tool
from src.models.base import async_session_factory
from src.models.user import Permission, Role, User
from src.core.security import hash_password
from src.services.skill_sync import sync_tool_to_filesystem

logger = logging.getLogger(__name__)


async def seed():
    async with async_session_factory() as db:
        existing = await db.scalar(select(User.id).limit(1))
        if existing is not None:
            logger.info("DB already seeded, skipping")
            return

        # Permissions
        resources = ["agents", "tools", "scenarios", "schedules", "triggers",
                     "alerts", "channels", "agent_profiles", "users", "roles", "admin"]
        actions = ["view", "create", "update", "delete"]

        all_perms: list[Permission] = []
        for res in resources:
            for act in actions:
                p = Permission(resource=res, action=act)
                db.add(p)
                all_perms.append(p)
        await db.flush()

        # Admin role — all permissions
        admin_role = Role(name="admin", description="Super admin")
        admin_role.permissions = all_perms
        db.add(admin_role)

        # Viewer role — view only
        viewer_role = Role(name="viewer", description="Read-only access")
        viewer_role.permissions = [p for p in all_perms if p.action == "view"]
        db.add(viewer_role)

        # Admin user
        admin = User(
            username="admin",
            email="admin@aiopsos.local",
            hashed_password=hash_password("admin123"),
            is_active=True,
        )
        admin.roles = [admin_role]
        db.add(admin)

        # Default tools
        tools = [
            Tool(
                name="system_info",
                type="skill",
                description="Query system information: version, uptime, health status",
                config={"params": {}},
            ),
            Tool(
                name="alert_query",
                type="skill",
                description="Query and filter active alerts by severity, status, or source",
                config={"params": {"severity": "str", "limit": "int"}},
            ),
            Tool(
                name="execute_script",
                type="skill",
                description="Execute a predefined operation script by name",
                config={"params": {"script": "str", "args": "str"}},
            ),
            Tool(
                name="knowledge",
                type="skill",
                description="Search, manage and maintain the LLM-Wiki knowledge base. Supports query (search & answer), ingest (save & organize), and lint (health check) operations.",
                config={"params": {"query": "str"}},
            ),
            Tool(
                name="llm-wiki",
                type="skill",
                description="LLM-Wiki personal knowledge base tool. Follows Karpathy's LLM-Wiki pattern: NOT RAG but accumulation. Three operations: Ingest (save & organize content), Query (search & synthesize), Lint (health check). Use for saving notes, organizing knowledge, searching the wiki, and maintaining cross-references.",
                config={
                    "params": {"task": "str"},
                    "skill_prompt_file": "data/skills/llm-wiki.md",
                    "tool_names": ["grep_kb", "read_wiki", "list_wiki", "write_wiki", "write_raw"],
                },
            ),
        ]
        for tool in tools:
            db.add(tool)

        # Default agent
        main_agent = Agent(
            name="AIOps主智能体",
            type="main",
            model_name="deepseek-v4-flash",
            agent_type="orchestrator",
            system_prompt=(
                "You are the main AIOpsOS intelligent agent. "
                "You coordinate sub-agents (monitor, ops, analysis) "
                "and use registered tools to fulfill user requests. "
                "Always respond in Chinese."
            ),
        )
        db.add(main_agent)

        await db.commit()
        # Sync skill-type tools to filesystem
        for tool in tools:
            if tool.type == "skill":
                try:
                    sync_tool_to_filesystem(tool)
                except Exception as exc:
                    logger.warning("Failed to sync seed tool %s to filesystem: %s", tool.name, exc)
        logger.info(
            "Seed complete: admin user (admin/admin123), %d permissions, "
            "roles (admin, viewer), %d tools, 1 agent",
            len(all_perms), len(tools),
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(seed())
