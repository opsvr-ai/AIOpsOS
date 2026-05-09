import json
import logging
from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, create_model
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.redis import cache_delete, cache_get, cache_set
from src.models.agent import Tool
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)


class _SkillTool(BaseTool):
    """LangChain wrapper around a DB Tool record of type 'skill'.

    Two execution modes:
    1. Simple function call — if ``_fn`` is set, calls it directly.
    2. LangChain skill — if ``_config`` contains ``skill_prompt`` or
       ``skill_prompt_file``, creates a ReAct sub-agent via SkillService.
    """

    name: str
    description: str
    _fn: Callable | None = None
    _config: dict[str, Any] | None = None

    def __init__(self, /, **kwargs: Any):
        # Pydantic v2 strips underscore-prefixed fields
        fn = kwargs.pop("_fn", None)
        cfg = kwargs.pop("_config", None)
        super().__init__(**kwargs)
        object.__setattr__(self, "_fn", fn)
        object.__setattr__(self, "_config", cfg)

    def _run(self, **kwargs: Any) -> str:
        # Synchronous fallback — try simple function call
        if self._fn:
            return str(self._fn(**kwargs))
        return f"[Skill {self.name}] executed with: {kwargs}"

    async def _arun(self, **kwargs: Any) -> str:
        # Inject space_id from request context so DB-record tools inherit it automatically
        from src.agent.context import get_current_space

        space = get_current_space()
        if space.get("space_id"):
            kwargs["space_id"] = space["space_id"]

        # Mode 1: simple function call
        if self._fn:
            return str(self._fn(**kwargs))

        # Mode 2: LangChain skill via SkillService
        task = self._resolve_task(kwargs)
        if self._config and (
            self._config.get("skill_prompt") or self._config.get("skill_prompt_file")
        ):
            from src.services.skill_service import execute_db_skill

            return await execute_db_skill(
                tool_name=self.name,
                skill_prompt=self._config.get("skill_prompt"),
                task=task,
                tool_names=self._config.get("tool_names"),
                config=self._config,
            )

        return f"[Skill {self.name}] executed with: {kwargs}"

    @staticmethod
    def _resolve_task(kwargs: dict[str, Any]) -> str:
        """Extract a human-readable task string from tool arguments."""
        return (
            kwargs.get("query")
            or kwargs.get("task")
            or kwargs.get("input")
            or json.dumps(kwargs, ensure_ascii=False)
        )


class _BuiltinTool(BaseTool):
    """LangChain tool wrapping a built-in async callable."""

    name: str
    description: str
    _afn: Callable | None = None

    def __init__(self, /, **kwargs: Any):
        # Pydantic v2 strips underscore-prefixed fields, so pop _afn
        # before Pydantic init and set it directly after.
        afn = kwargs.pop("_afn", None)
        super().__init__(**kwargs)
        self._afn = afn

    def _run(self, **kwargs: Any) -> str:
        return f"[{self.name}] use async execution"

    async def _arun(self, **kwargs: Any) -> str:
        if self._afn:
            return await self._afn(**kwargs)
        return f"[{self.name}] executed with: {kwargs}"


SAFE_PARALLEL = "parallel-safe"
SEQUENTIAL = "sequential"
DESTRUCTIVE = "destructive"
DEFAULT_SAFETY = SEQUENTIAL


# Boot-time safety seed for built-in tools (design.md § ToolDispatcher).
# Names absent from this dict default to SEQUENTIAL via get_safety(...).
# Keep in sync with migration 202605041830_add_tool_safety_column.py.
_BUILTIN_SAFETY_SEED: dict[str, str] = {
    # parallel-safe — pure-read, idempotent, no external mutation.
    "grep_kb": SAFE_PARALLEL,
    "read_wiki": SAFE_PARALLEL,
    "list_wiki": SAFE_PARALLEL,
    "memory_retrieve": SAFE_PARALLEL,
    "list_cron_jobs": SAFE_PARALLEL,
    "get_config": SAFE_PARALLEL,
    "list_datasources": SAFE_PARALLEL,
    "query_cmdb_nodes": SAFE_PARALLEL,
    "search_logs": SAFE_PARALLEL,
    "count_logs": SAFE_PARALLEL,
    "search_tickets": SAFE_PARALLEL,
    "get_ticket_detail": SAFE_PARALLEL,
    # destructive — require HumanInterrupt approval before dispatch.
    "execute": DESTRUCTIVE,
    "write_wiki": DESTRUCTIVE,
    "write_raw": DESTRUCTIVE,
    "cron_create": DESTRUCTIVE,
    "sync_datasource": DESTRUCTIVE,
}


# Cache key for DB-backed tool list snapshot. Bumped to v2 once the
# snapshot schema gained a 'safety' field — avoids deserialising stale
# v1 payloads left over in Redis from before migration 202605041830.
_TOOLS_LIST_CACHE_KEY = "tools:list:v2"


class ToolManager:
    """Registry that loads tools/Skills from DB and wraps them as LangChain tools.

    Singleton per process. Call ``ToolManager.reload()`` to hot-refresh after
    tool/MCP-server rows change.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._builtin: dict[str, _BuiltinTool] = {}
        self._safety: dict[str, str] = {}
        self._output_budgets: dict[str, int] = {}
        self._default_output_budget: int = 100000

    # ── public API ──────────────────────────────────────────────

    def get_tools(self, names: list[str] | None = None) -> list[BaseTool]:
        """Return all registered LangChain tools, or a filtered subset."""
        if names is None:
            return list(self._tools.values())
        return [t for name in names if (t := self._tools.get(name))]

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def list_skills(self) -> list[str]:
        """Return names of DB-registered skill tools (not built-in)."""
        return [n for n in self._tools if n not in self._builtin]

    def describe_tools(self) -> str:
        """Return a formatted string of ALL tools (built-in + skills)."""
        builtin_lines: list[str] = []
        skill_lines: list[str] = []
        for name in sorted(self._tools):
            tool = self._tools[name]
            desc = tool.description or ""
            line = f"    {name}: {desc}"
            if name in self._builtin:
                builtin_lines.append(line)
            else:
                skill_lines.append(line)
        parts = ["[内置工具]:"] + builtin_lines
        if skill_lines:
            parts.append("\n[已注册技能]:")
            parts.extend(skill_lines)
        return "\n".join(parts)

    def describe_skills(self) -> str:
        """Return formatted string of only DB-registered skills."""
        lines: list[str] = []
        for name in sorted(self._tools):
            if name in self._builtin:
                continue
            tool = self._tools[name]
            desc = tool.description or ""
            lines.append(f"    {name}: {desc}")
        if not lines:
            return "(no skills registered)"
        return "[已注册技能]:\n" + "\n".join(lines)

    def describe_skills_compact(self) -> str:
        """Return compact one-line-per-skill index for the system prompt.

        Each line: ``name: description (truncated to 80 chars)``.
        Use for progressive disclosure (Tier 1) to save tokens.
        """
        lines: list[str] = []
        for name in sorted(self._tools):
            if name in self._builtin:
                continue
            tool = self._tools[name]
            desc = (tool.description or "").replace("\n", " ").strip()
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append(f"    {name}: {desc}")
        if not lines:
            return "(no skills registered)"
        return "[已注册技能]:\n" + "\n".join(lines)

    def register_builtin(self, name: str, description: str, afn: Callable) -> None:
        """Register a built-in tool (not from DB) with an async callable."""
        async def _wrapper(**kwargs: Any) -> str:
            task = kwargs.get("query") or kwargs.get("task") or json.dumps(kwargs, ensure_ascii=False)
            return await afn(task=task)
        tool = _BuiltinTool(name=name, description=description, _afn=_wrapper)
        self._tools[name] = tool
        self._builtin[name] = tool

    async def reload(self) -> None:
        """Hot-reload: clear cache, re-read tools & MCP servers from DB."""
        # Preserve built-in tools, reload only DB-backed tools
        builtins = dict(self._builtin)
        self._tools.clear()
        self._tools.update(builtins)

        # Seed safety defaults BEFORE any cache/DB hydration so the in-memory
        # dict is deterministic even on a Redis cache hit. DB rows (or
        # explicit runtime overrides) may later refine individual entries.
        self.seed_safety_from_defaults()

        cache_key = _TOOLS_LIST_CACHE_KEY
        try:
            cached = await cache_get(cache_key)
        except Exception:
            cached = None

        if cached:
            self._load_tools_from_cache(cached)
            return

        async with async_session_factory() as db:
            # load skill tools (skip names that are already built-in)
            result = await db.execute(
                select(Tool).where(Tool.is_active, Tool.type == "skill")
            )
            raw = {"skills": [], "mcps": []}
            for tool in result.scalars().all():
                if tool.name not in self._builtin:
                    self._register_skill(tool)
                    raw["skills"].append({
                        "name": tool.name,
                        "description": tool.description or "",
                        "return_direct": bool(tool.return_direct),
                        "source_file": tool.source_file,
                        "parameters_schema": tool.parameters_schema,
                        "safety": tool.safety,
                    })

            # load MCP-backed tools (skip names that are already built-in)
            mcp_tools_result = await db.execute(
                select(Tool)
                .where(Tool.is_active, Tool.type == "mcp")
                .options(selectinload(Tool.mcp_server))
            )
            for tool in mcp_tools_result.scalars().all():
                if tool.name not in self._builtin:
                    self._register_skill(tool)
                    mcp = tool.mcp_server
                    raw["mcps"].append({
                        "name": tool.name,
                        "description": tool.description or "",
                        "return_direct": bool(tool.return_direct),
                        "safety": tool.safety,
                        "mcp_server": {
                            "name": mcp.name if mcp else "",
                            "transport": mcp.transport if mcp else "stdio",
                            "command": mcp.command if mcp else None,
                            "args": mcp.args if mcp else None,
                            "url": mcp.url if mcp else None,
                        } if mcp else None,
                    })

            try:
                await cache_set(cache_key, raw, ttl=300)
            except Exception:
                pass

    def _load_tools_from_cache(self, raw: dict) -> None:
        """Reconstruct tool objects from cached metadata."""
        from src.models.agent import MCPServer

        for s in raw.get("skills", []):
            tool = Tool(
                name=s["name"], type="skill", description=s["description"],
                return_direct=s.get("return_direct"), source_file=s.get("source_file"),
                parameters_schema=s.get("parameters_schema"),
                safety=s.get("safety") or DEFAULT_SAFETY,
            )
            self._register_skill(tool)

        for m in raw.get("mcps", []):
            mcp_data = m.get("mcp_server")
            mcp_server = None
            if mcp_data:
                mcp_server = MCPServer(
                    name=mcp_data.get("name", ""),
                    transport=mcp_data.get("transport", "stdio"),
                    command=mcp_data.get("command"),
                    args=mcp_data.get("args"),
                    url=mcp_data.get("url"),
                )
            tool = Tool(
                name=m["name"], type="mcp", description=m["description"],
                return_direct=m.get("return_direct"), mcp_server=mcp_server,
                safety=m.get("safety") or DEFAULT_SAFETY,
            )
            self._register_skill(tool)

    # ── safety classification ──────────────────────────────────

    def set_safety(self, tool_name: str, classification: str) -> None:
        """Mark a tool as parallel-safe, sequential, or destructive."""
        if classification not in (SAFE_PARALLEL, SEQUENTIAL, DESTRUCTIVE):
            raise ValueError(f"Invalid safety classification: {classification}")
        self._safety[tool_name] = classification

    def get_safety(self, tool_name: str) -> str:
        return self._safety.get(tool_name, DEFAULT_SAFETY)

    def get_parallel_safe_tools(self) -> list[str]:
        return [n for n, s in self._safety.items() if s == SAFE_PARALLEL]

    def is_destructive(self, tool_name: str) -> bool:
        return self._safety.get(tool_name) == DESTRUCTIVE

    def seed_safety_from_defaults(self) -> None:
        """Seed the in-memory safety map from the built-in classification table.

        Idempotent on reload: only fills entries that are not already set.
        Runtime overrides (``set_safety`` calls from user code, or DB-backed
        rows hydrated in ``_register_skill``) are preserved verbatim. This is
        the inverse of clobbering user intent every time ``reload()`` runs.
        """
        for name, classification in _BUILTIN_SAFETY_SEED.items():
            if name not in self._safety:
                self.set_safety(name, classification)

    # ── output budget ──────────────────────────────────────────

    def set_output_budget(self, tool_name: str, max_chars: int) -> None:
        """Set per-tool output budget in characters."""
        self._output_budgets[tool_name] = max_chars

    def get_output_budget(self, tool_name: str) -> int:
        return self._output_budgets.get(tool_name, self._default_output_budget)

    def apply_output_budget(self, output: str, tool_name: str) -> str:
        """Truncate tool output to its budget, appending a truncation marker."""
        budget = self.get_output_budget(tool_name)
        if len(output) <= budget:
            return output
        marker = f"\n\n[... output truncated at {budget} chars, original length: {len(output)}]"
        return output[:budget - len(marker)] + marker

    # ── deregistration ─────────────────────────────────────────

    def deregister(self, tool_name: str) -> bool:
        """Remove a tool from the registry. Returns True if found."""
        self._safety.pop(tool_name, None)
        self._output_budgets.pop(tool_name, None)
        if tool_name in self._tools:
            del self._tools[tool_name]
            self._builtin.pop(tool_name, None)
            return True
        return False

    def deregister_mcp_tools(self, server_id: str) -> int:
        """Remove all tools from a specific MCP server. Returns count."""
        to_remove = [
            name for name, tool in self._tools.items()
            if hasattr(tool, "_config") and tool._config
            and tool._config.get("mcp_server_id") == server_id
        ]
        for name in to_remove:
            self.deregister(name)
        return len(to_remove)

    # ── internal ────────────────────────────────────────────────

    def _register_skill(self, tool: Tool) -> None:
        wrapped = _SkillTool(
            name=tool.name,
            description=tool.description or tool.name,
            args_schema=_build_schema(tool.config.get("params", {})),
            _config=tool.config,
        )
        self._tools[tool.name] = wrapped
        # Hydrate the safety dict from the DB-backed row so user-defined
        # skills and MCP tools carry their classification into dispatch
        # decisions. Falls back silently when the model doesn't expose a
        # safety attr (e.g. reduced-schema stubs used in unit tests).
        classification = getattr(tool, "safety", None)
        if classification in (SAFE_PARALLEL, SEQUENTIAL, DESTRUCTIVE):
            self.set_safety(tool.name, classification)

def _build_schema(params: dict) -> type[BaseModel]:
    """Turn a flat {name: type} dict into a Pydantic args schema."""
    if not params:
        return type("_EmptyArgs", (BaseModel,), {"model_config": {"arbitrary_types_allowed": True}})

    type_map = {"str": str, "int": int, "float": float, "bool": bool}
    fields: dict[str, Any] = {}
    for param_name, param_type in params.items():
        py_type = type_map.get(param_type, str)
        fields[param_name] = (py_type, Field(...))
    return create_model("_DynamicArgs", **fields)


    async def invalidate_cache(self) -> None:
        """Invalidate the shared tool cache (call after CRUD on tools/MCP servers)."""
        try:
            await cache_delete(_TOOLS_LIST_CACHE_KEY)
        except Exception:
            pass


# module-level singleton
tool_manager = ToolManager()
