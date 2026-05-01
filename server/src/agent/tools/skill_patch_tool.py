"""In-place skill patching via fuzzy text matching.

When the agent uses a skill and finds it outdated or wrong, it can
patch the SKILL.md body immediately without a full rewrite. Uses
whitespace-tolerant fuzzy matching from fuzzy_match.py.

Follows the Hermes pattern: create version snapshot before patching,
update DB + filesystem, hot-reload tool manager.
"""

from __future__ import annotations

import json as _json
import logging

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.agent.tools.fuzzy_match import fuzzy_find_and_replace
from src.models.agent import Tool
from src.models.base import async_session_factory
from src.services.skill_sync import (
    SKILLS_DIR,
    create_version_snapshot,
)

logger = logging.getLogger(__name__)

_SKILL_PATCH_DESCRIPTION = (
    "Fix or improve an existing skill's SKILL.md body text using fuzzy matching. "
    "**When to use:** You are executing a skill and notice its instructions are outdated, "
    "incorrect, or could be improved. Instead of a full rewrite, patch just the wrong part. "
    "Parameters: "
    "skill_name (required, the skill to patch), "
    "old_text (required, the text to replace — can have different whitespace/indentation), "
    "new_text (required, the replacement text), "
    "reason (required, short explanation of why this change is needed — stored in version history). "
    "The tool creates a version snapshot before patching, so changes are never destructive."
)


class SkillPatchInput(BaseModel):
    skill_name: str = Field(description="Name of the skill to patch")
    old_text: str = Field(description="Text to replace (whitespace-tolerant matching)")
    new_text: str = Field(description="Replacement text")
    reason: str = Field(description="Why this change is needed (stored in version history)")


def _parse_skill_md_parts(content: str) -> tuple[str, str]:
    """Split SKILL.md into (frontmatter_block, body)."""
    if not content.startswith("---"):
        return ("", content)

    parts = content.split("---", 2)
    if len(parts) < 3:
        return ("", content)

    return (f"---{parts[1]}---", parts[2])


async def _skill_patch(
    skill_name: str,
    old_text: str = "",
    new_text: str = "",
    reason: str = "",
) -> str:
    if not skill_name:
        return _json.dumps({"ok": False, "error": "skill_name is required"}, ensure_ascii=False)
    if not old_text:
        return _json.dumps({"ok": False, "error": "old_text is required"}, ensure_ascii=False)
    if not reason:
        return _json.dumps({"ok": False, "error": "reason is required"}, ensure_ascii=False)

    skill_md_path = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_md_path.exists():
        return _json.dumps({
            "ok": False,
            "error": f"SKILL.md not found for '{skill_name}'. Check the skill name with skill_manage list_for_review.",
        }, ensure_ascii=False)

    original_content = skill_md_path.read_text(encoding="utf-8")
    frontmatter, body = _parse_skill_md_parts(original_content)

    if not body.strip():
        return _json.dumps({
            "ok": False,
            "error": f"SKILL.md for '{skill_name}' has no body content to patch.",
        }, ensure_ascii=False)

    try:
        new_body, match_count, confidence = fuzzy_find_and_replace(
            body, old_text, new_text, threshold=0.6
        )
    except ValueError as e:
        return _json.dumps({
            "ok": False,
            "error": str(e),
            "hint": "Try a more specific old_text pattern, or use skill_manage update for a full rewrite.",
        }, ensure_ascii=False)

    new_content = f"{frontmatter}\n\n{new_body.lstrip()}" if frontmatter else new_body
    if not new_content.endswith("\n"):
        new_content += "\n"

    async with async_session_factory() as db:
        from sqlalchemy import select

        tool = await db.scalar(
            select(Tool).where(Tool.name == skill_name, Tool.type == "skill")
        )
        if tool is None:
            return _json.dumps({
                "ok": False,
                "error": f"Skill '{skill_name}' not found in database.",
            }, ensure_ascii=False)

        await create_version_snapshot(db, tool)

        skill_md_path.write_text(new_content, encoding="utf-8")

        cfg = dict(tool.config or {})
        cfg["skill_prompt"] = new_content
        cfg["_patch_reason"] = reason
        tool.config = cfg
        db.add(tool)
        await db.commit()

    from src.services.tool_manager import tool_manager
    await tool_manager.reload()

    logger.info(
        "Agent patched skill '%s' (confidence=%.2f, matches=%d, reason=%s)",
        skill_name, confidence, match_count, reason,
    )
    return _json.dumps({
        "ok": True,
        "action": "patch",
        "skill_name": skill_name,
        "confidence": round(confidence, 3),
        "matches_replaced": match_count,
        "reason": reason,
        "message": f"Skill '{skill_name}' patched (confidence: {confidence:.1%}). Version snapshot created.",
    }, ensure_ascii=False)


skill_patch_tool = StructuredTool.from_function(
    name="skill_patch",
    description=_SKILL_PATCH_DESCRIPTION,
    coroutine=_skill_patch,
    args_schema=SkillPatchInput,
)
