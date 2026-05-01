"""Autonomous skill management tool for the AIOpsOS agent.

Lets the agent create, update, and list skills during conversation.
Follows the Hermes pattern: agent offers to create a skill after
completing a complex task (5+ tool calls), and can fix outdated
skills via updates.
"""

from __future__ import annotations

import json as _json
import logging
import uuid

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.agent.context import get_current_space
from src.models.agent import Tool
from src.models.base import async_session_factory
from src.services.skill_sync import (
    _SKILL_NAME_RE,
    create_default_skill_dirs,
    create_version_snapshot,
    write_skill_file,
)

logger = logging.getLogger(__name__)

# ── request schemas ──────────────────────────────────────────────────

_SKILL_MANAGE_DESCRIPTION = (
    "Manage AIOpsOS skills autonomously. Use this tool to create, update, or list skills. "
    "**When to use:** "
    "(1) After completing a complex multi-step task (5+ tool calls), proactively offer to save the workflow as a skill. "
    "(2) When you notice an existing skill is outdated or wrong, update it. "
    "(3) Before creating a skill, list existing skills to avoid duplicates. "
    "Actions: "
    "- `create`: name (required, lowercase alphanumeric with hyphens), description (required, 1-2 sentences about what the skill does), "
    "  skill_prompt (required, the full system prompt for the skill), tool_names (optional list of tool names this skill needs), "
    "  category (optional, e.g. 'devops'/'monitoring'/'troubleshooting'), tags (optional list of tags). "
    "- `update`: name (required), description (optional new description), skill_prompt (optional new prompt), "
    "  tool_names (optional new tool list). Creates a version snapshot before updating. "
    "- `list_for_review`: returns compact list of all existing skills (name + short description) for duplicate checking."
)


class SkillManageInput(BaseModel):
    action: str = Field(description="One of: create, update, list_for_review")
    name: str = Field(default="", description="Skill name (lowercase, alphanumeric + hyphens)")
    description: str = Field(default="", description="1-2 sentence description of what the skill does")
    skill_prompt: str = Field(default="", description="Full system prompt for the skill")
    tool_names: list[str] = Field(default_factory=list, description="Tool names this skill needs access to")
    category: str = Field(default="", description="Category (e.g. devops, monitoring, troubleshooting)")
    tags: list[str] = Field(default_factory=list, description="Tags for discovery (e.g. docker, postgresql, deployment)")


# ── helpers ──────────────────────────────────────────────────────────


def _validate_skill_name(name: str) -> str | None:
    """Return error message if name is invalid, None if valid."""
    if not name:
        return "Skill name is required"
    if not _SKILL_NAME_RE.match(name):
        return f"Invalid skill name '{name}': must be lowercase alphanumeric with single hyphens"
    return None


def _build_skill_prompt(name: str, description: str, skill_prompt: str) -> str:
    """Build the full skill prompt with metadata header."""
    return (
        f"# {name}\n\n"
        f"{description}\n\n"
        f"---\n\n"
        f"{skill_prompt}"
    )


# ── action handlers ──────────────────────────────────────────────────


async def _create_skill(
    name: str,
    description: str,
    skill_prompt: str,
    tool_names: list[str] | None = None,
    category: str = "",
    tags: list[str] | None = None,
) -> str:
    """Create a new skill: write SKILL.md, register DB record, create version, reload."""
    err = _validate_skill_name(name)
    if err:
        return _json.dumps({"ok": False, "error": err}, ensure_ascii=False)

    if not description:
        return _json.dumps({"ok": False, "error": "description is required"}, ensure_ascii=False)
    if not skill_prompt:
        return _json.dumps({"ok": False, "error": "skill_prompt is required"}, ensure_ascii=False)

    tool_names = tool_names or []
    tags = tags or []

    async with async_session_factory() as db:
        from sqlalchemy import select

        # Check for duplicate name
        existing = await db.scalar(select(Tool).where(Tool.name == name))
        if existing is not None:
            return _json.dumps({
                "ok": False,
                "error": f"Skill '{name}' already exists. Use action='update' to modify it, or choose a different name.",
            }, ensure_ascii=False)

        # Create directory structure and SKILL.md
        create_default_skill_dirs(name)

        # Build config
        config = {
            "skill_prompt": _build_skill_prompt(name, description, skill_prompt),
            "tool_names": tool_names,
            "tags": tags,
            "version": "1.0.0",
        }

        # Scope to current space
        space_ctx = get_current_space()
        current_space_id: str = space_ctx.get("space_id", "")
        try:
            space_uuid = uuid.UUID(current_space_id) if current_space_id else None
        except (ValueError, AttributeError):
            space_uuid = None

        # Create DB record
        tool = Tool(
            id=uuid.uuid4(),
            name=name,
            type="skill",
            description=description,
            category=category or None,
            config=config,
            is_active=True,
            is_approved=True,
            space_id=space_uuid,
        )
        db.add(tool)
        await db.flush()

        # Write SKILL.md to filesystem
        write_skill_file(tool)

        # Create initial version snapshot
        await create_version_snapshot(db, tool)

        await db.commit()

        # Hot-reload tool manager
        from src.services.tool_manager import tool_manager

        await tool_manager.reload()

        logger.info("Agent created skill '%s' via skill_manage tool", name)
        return _json.dumps({
            "ok": True,
            "action": "create",
            "name": name,
            "id": str(tool.id),
            "message": f"Skill '{name}' created and activated.",
        }, ensure_ascii=False)


async def _update_skill(
    name: str,
    description: str = "",
    skill_prompt: str = "",
    tool_names: list[str] | None = None,
    tags: list[str] | None = None,
) -> str:
    """Update an existing skill: create version snapshot, update SKILL.md + DB, reload."""
    err = _validate_skill_name(name)
    if err:
        return _json.dumps({"ok": False, "error": err}, ensure_ascii=False)

    async with async_session_factory() as db:
        from sqlalchemy import or_, select

        space_ctx = get_current_space()
        current_space_id: str = space_ctx.get("space_id", "")

        query = select(Tool).where(Tool.name == name, Tool.type == "skill")
        if current_space_id:
            query = query.where(
                or_(Tool.space_id == current_space_id, Tool.space_id.is_(None))
            )
        tool = await db.scalar(query)
        if tool is None:
            return _json.dumps({
                "ok": False,
                "error": f"Skill '{name}' not found. Use action='list_for_review' to see existing skills.",
            }, ensure_ascii=False)

        # Create version snapshot before modifying
        await create_version_snapshot(db, tool)

        # Update fields
        if description:
            tool.description = description
        cfg = dict(tool.config or {})

        if skill_prompt:
            cfg["skill_prompt"] = _build_skill_prompt(name, description or tool.description or name, skill_prompt)
        if tool_names is not None:
            cfg["tool_names"] = tool_names
        if tags is not None:
            cfg["tags"] = tags

        tool.config = cfg
        db.add(tool)

        # Rewrite SKILL.md
        write_skill_file(tool)

        await db.commit()

        # Hot-reload tool manager
        from src.services.tool_manager import tool_manager

        await tool_manager.reload()

        logger.info("Agent updated skill '%s' via skill_manage tool", name)
        return _json.dumps({
            "ok": True,
            "action": "update",
            "name": name,
            "id": str(tool.id),
            "message": f"Skill '{name}' updated (version snapshot created).",
        }, ensure_ascii=False)


async def _list_skills() -> str:
    """Return compact skill index for duplicate checking — scoped to current space."""
    from sqlalchemy import or_, select

    space_ctx = get_current_space()
    current_space_id: str = space_ctx.get("space_id", "")

    async with async_session_factory() as db:
        query = select(Tool.name, Tool.description, Tool.category).where(Tool.type == "skill")
        if current_space_id:
            query = query.where(
                or_(Tool.space_id == current_space_id, Tool.space_id.is_(None))
            )
        result = await db.execute(query.order_by(Tool.name))
        skills = []
        for row in result.all():
            skills.append({
                "name": row.name,
                "description": (row.description or "")[:80],
                "category": row.category or "",
            })

    if not skills:
        return _json.dumps({"skills": [], "count": 0, "hint": "No skills exist yet. Create one with action='create'."}, ensure_ascii=False)

    return _json.dumps({
        "skills": skills,
        "count": len(skills),
    }, ensure_ascii=False)


# ── main entry point ─────────────────────────────────────────────────


async def _skill_manage(
    action: str,
    name: str = "",
    description: str = "",
    skill_prompt: str = "",
    tool_names: list[str] | None = None,
    category: str = "",
    tags: list[str] | None = None,
) -> str:
    """Route to the appropriate action handler."""
    action = action.strip().lower()

    if action == "create":
        return await _create_skill(
            name=name,
            description=description,
            skill_prompt=skill_prompt,
            tool_names=tool_names,
            category=category,
            tags=tags,
        )
    elif action == "update":
        return await _update_skill(
            name=name,
            description=description,
            skill_prompt=skill_prompt,
            tool_names=tool_names,
            tags=tags,
        )
    elif action in ("list_for_review", "list"):
        return await _list_skills()
    else:
        return _json.dumps({
            "ok": False,
            "error": f"Unknown action '{action}'. Use: create, update, or list_for_review.",
        }, ensure_ascii=False)


# ── LangChain tool ───────────────────────────────────────────────────

skill_manage_tool = StructuredTool.from_function(
    name="skill_manage",
    description=_SKILL_MANAGE_DESCRIPTION,
    coroutine=_skill_manage,
    args_schema=SkillManageInput,
)
