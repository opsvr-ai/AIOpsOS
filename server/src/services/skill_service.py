"""Skill execution service — runs LangChain skills as sub-agents.

Skills follow progressive disclosure: the skill prompt is loaded on-demand
when the skill is called, not baked into the main agent's system prompt.

Uses a manual ReAct loop (instead of ``create_agent``) to avoid issues with
DeepSeek's ``reasoning_content`` field, which the automated ReAct loop
doesn't handle correctly.
"""

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from src.config import settings
from src.services.tool_manager import tool_manager

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 10
_SKILLS_DIR = Path(settings.kb_wiki_dir) / ".." / "skills"


async def execute_skill(
    *,
    skill_name: str,
    skill_prompt: str,
    task: str,
    tool_names: list[str] | None = None,
    temperature: float = 0.3,
) -> str:
    """Execute a skill with a manual ReAct loop.

    Args:
        skill_name: Name of the skill (for logging).
        skill_prompt: The full skill system prompt (instruction set).
        task: The user's request / task description for the skill.
        tool_names: List of tool names this skill is allowed to use.
                    If None, uses all registered tools.
        temperature: LLM temperature for the skill agent.

    Returns:
        The skill agent's final response text.
    """
    tools = {t.name: t for t in _resolve_tools(tool_names)}
    model = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model="deepseek-v4-flash",
        temperature=temperature,
        timeout=60,
    )

    logger.info("Executing skill '%s' with %d tool(s): task=%.80s", skill_name, len(tools), task)

    messages: list = [
        SystemMessage(content=skill_prompt),
        HumanMessage(content=task),
    ]

    for iteration in range(_MAX_ITERATIONS):
        response = await model.ainvoke(messages)

        # Strip reasoning_content — DeepSeek returns it in additional_kwargs
        # but its API rejects it on subsequent requests.
        if isinstance(response, AIMessage):
            response.additional_kwargs.pop("reasoning_content", None)

        content = str(response.content or "")

        # If no tool calls, this is the final answer
        if not response.tool_calls:
            if content:
                return content
            return "[skill: no output]"

        # Execute each tool call
        for tc in response.tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {})
            tool_id = tc.get("id", "")

            tool = tools.get(tool_name)
            if tool is None:
                result = f"Tool '{tool_name}' not found"
                logger.warning("Skill '%s' requested unknown tool: %s", skill_name, tool_name)
            else:
                try:
                    result = str(await tool.ainvoke(tool_args))
                except Exception as exc:
                    result = f"Tool error: {exc}"
                    logger.exception("Tool '%s' call failed in skill '%s'", tool_name, skill_name)

            # Add the AI message (with reasoning_content stripped) and tool result
            messages.append(response)
            messages.append(ToolMessage(content=result, tool_call_id=tool_id))

    return f"[skill: {skill_name}] Reached max iterations ({_MAX_ITERATIONS})"


def load_skill_prompt_from_file(path: str | Path) -> str | None:
    """Read a skill prompt from a markdown file on disk.

    The file is expected to start with frontmatter (``---`` delimited), which
    is stripped before returning the prompt body.  If no frontmatter, the
    entire file content is returned as-is.
    """
    p = Path(path)
    if not p.is_file():
        logger.warning("Skill file not found: %s", p)
        return None

    content = p.read_text(encoding="utf-8")

    # Strip optional YAML frontmatter (--- ... ---)
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2].strip()

    return content


def load_builtin_skill_prompt(skill_name: str) -> str | None:
    """Load a built-in skill prompt from ``data/skills/{skill_name}.md``."""
    path = _SKILLS_DIR / f"{skill_name}.md"
    return load_skill_prompt_from_file(path)


async def execute_db_skill(
    tool_name: str,
    skill_prompt: str | None,
    task: str,
    tool_names: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    """Execute a DB-registered skill tool.

    This is called by ``_SkillTool._arun`` when the tool's DB record has a
    ``skill_prompt`` in its config (or a ``skill_prompt_file`` pointing to a
    file on disk).
    """
    prompt = skill_prompt
    if not prompt and config:
        file_ref = config.get("skill_prompt_file")
        if file_ref:
            prompt = load_skill_prompt_from_file(file_ref)

    if not prompt:
        return f"[{tool_name}] No skill prompt configured"

    if tool_names is None and config:
        tool_names = config.get("tool_names")

    return await execute_skill(
        skill_name=tool_name,
        skill_prompt=prompt,
        task=task,
        tool_names=tool_names,
        temperature=config.get("temperature", 0.3) if config else 0.3,
    )


def _resolve_tools(tool_names: list[str] | None) -> list:
    """Resolve tool names to LangChain tool instances."""
    if tool_names is None:
        return tool_manager.get_tools()
    return tool_manager.get_tools(tool_names)
