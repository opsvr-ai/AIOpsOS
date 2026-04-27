"""DeepAgents-based agent system for AIOpsOS.

Replaces the custom LangGraph agent with ``create_deep_agent()``
from the DeepAgents framework.

Provides:
- Built-in filesystem tools (ls, read_file, write_file, edit_file, glob, grep)
- Shell execution (execute)
- Planning (write_todos)
- Sub-agent delegation (task)
- Knowledge base tools (grep_kb, read_wiki, list_wiki, write_wiki, write_raw)
- Specialist sub-agents (knowledge, monitor, ops, analysis)
"""

import contextvars
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.language_models import LanguageModelInput
from langchain_core.outputs import ChatResult
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import LocalShellBackend

from src.config import settings
from src.services.kb_tools import (
    get_config,
    grep_kb,
    list_wiki_pages,
    read_wiki_file,
    write_kb_raw,
    write_wiki_file,
)

logger = logging.getLogger(__name__)

# Context variable to propagate user context from request handlers to tools
_current_user_ctx: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "current_user", default={}
)


def set_current_user(user_id: str, session_id: str = "") -> None:
    """Set the current user context for tool access."""
    _current_user_ctx.set({"user_id": user_id, "session_id": session_id})


def get_current_user() -> dict[str, str]:
    """Get the current user context (safe to call from tools)."""
    return _current_user_ctx.get()


# ═══════════════════════════════════════════════════════════════════════
# DeepSeek Chat Model
# ═══════════════════════════════════════════════════════════════════════


# Module-level cache for DeepSeek reasoning_content preservation.
# Pydantic models intercept class attribute access, so we use a module dict
# instead of a class-level dict for caching reasoning_content by tool_call_id.
_rc_cache: dict[str, str] = {}


class DeepSeekChatOpenAI(ChatOpenAI):
    """ChatOpenAI that preserves ``reasoning_content`` for DeepSeek API.

    DeepSeek requires ``reasoning_content`` to be passed back in
    subsequent assistant messages. ``ChatOpenAI`` drops it during
    response parsing, and additionally LangChain/LangGraph message
    serialization strips ``additional_kwargs``. This subclass fixes
    both by caching ``reasoning_content`` keyed by tool-call IDs.
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> Any:
        """Intercept streaming chunks to capture reasoning_content.

        DeepSeek delivers ``reasoning_content`` in delta chunks before
        ``tool_calls`` and ``content``. We accumulate it and cache it by
        tool_call ID for reliable injection into subsequent requests.
        """
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        choices = chunk.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            rc_chunk = delta.get("reasoning_content")
            if rc_chunk:
                _rc_cache.setdefault("_accumulated", "")
                _rc_cache["_accumulated"] += rc_chunk
            for tc in (delta.get("tool_calls") or []):
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id and "_accumulated" in _rc_cache:
                    _rc_cache[tc_id] = _rc_cache["_accumulated"]
        return result

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        """Preserve ``reasoning_content`` from raw API response (non-streaming)."""
        result = super()._create_chat_result(response, generation_info)

        response_dict = (
            response if isinstance(response, dict)
            else response.model_dump()
        )
        for i, choice in enumerate(response_dict.get("choices", [])):
            msg_dict = choice.get("message", {})
            rc = msg_dict.get("reasoning_content")
            if rc and i < len(result.generations):
                msg = result.generations[i].message
                if isinstance(msg, AIMessage):
                    msg.additional_kwargs["reasoning_content"] = rc
                    for tc in (msg.tool_calls or []):
                        tc_id = tc["id"] if isinstance(tc, dict) else tc.id
                        _rc_cache[tc_id] = rc

        return result

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Inject ``reasoning_content`` back into API message dicts."""
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "messages" not in payload:
            return payload

        for msg_dict in payload["messages"]:
            if not isinstance(msg_dict, dict):
                continue
            if msg_dict.get("role") != "assistant":
                continue
            if msg_dict.get("reasoning_content"):
                continue
            tool_calls = msg_dict.get("tool_calls") or []
            for tc in tool_calls:
                tc_id = tc["id"] if isinstance(tc, dict) else getattr(tc, "id", "")
                rc = _rc_cache.get(tc_id)
                if rc:
                    msg_dict["reasoning_content"] = rc
                    break
            else:
                if tool_calls:
                    # Fallback: use most recent accumulated rc
                    fallback_rc = _rc_cache.get("_accumulated")
                    if fallback_rc:
                        msg_dict["reasoning_content"] = fallback_rc

        return payload


# ═══════════════════════════════════════════════════════════════════════
# Knowledge Base Tools
# ═══════════════════════════════════════════════════════════════════════


async def _grep_kb(query: str, max_results: int = 10) -> str:
    return grep_kb(query, max_results)


async def _read_wiki(filename: str) -> str:
    return read_wiki_file(filename)


async def _list_wiki() -> str:
    return list_wiki_pages()


async def _write_wiki(filename: str, content: str) -> str:
    return write_wiki_file(filename, content)


async def _write_raw(filename: str, content: str) -> str:
    return write_kb_raw(filename, content)


async def _get_config(key: str = "") -> str:
    return get_config(key)


async def _cron_create(
    name: str,
    prompt: str,
    schedule: str,
    skills: list[str] | None = None,
    enabled: bool = True,
    timezone_str: str = "Asia/Shanghai",
) -> str:
    """Create a scheduled cron job in the AIOpsOS cron system."""
    import json as _json
    import uuid

    from src.models.base import async_session_factory
    from src.models.cron_job import CronJob
    from src.services.cron_scheduler import compute_next_run

    async with async_session_factory() as _db:
        job = CronJob(
            id=str(uuid.uuid4()),
            name=name,
            prompt=prompt,
            schedule=schedule,
            skills=skills or [],
            enabled_toolsets=[],
            timezone_str=timezone_str,
            enabled=enabled,
        )
        job.next_run = compute_next_run(job.schedule)
        _db.add(job)
        await _db.commit()
        await _db.refresh(job)
        return _json.dumps({
            "ok": True,
            "id": job.id,
            "name": job.name,
            "schedule": job.schedule,
            "enabled": job.enabled,
            "message": f"定时任务 '{name}' 创建成功",
        }, ensure_ascii=False)


KNOWLEDGE_TOOLS = [
    StructuredTool.from_function(
        name="get_config",
        description="Look up AIOpsOS configuration values (e.g. WIKI_PATH, UPLOAD_DIR). Call without arguments to list all readable config keys, or with a key name to get a specific value.",
        coroutine=_get_config,
    ),
    StructuredTool.from_function(
        name="grep_kb",
        description="Search knowledge base wiki files by keyword using grep. Use this to find relevant documents.",
        coroutine=_grep_kb,
    ),
    StructuredTool.from_function(
        name="read_wiki",
        description="Read the full content of a knowledge base wiki page by filename.",
        coroutine=_read_wiki,
    ),
    StructuredTool.from_function(
        name="list_wiki",
        description="List all wiki pages currently in the knowledge base.",
        coroutine=_list_wiki,
    ),
    StructuredTool.from_function(
        name="write_wiki",
        description="Write or update a knowledge base wiki page. Creates the file if it doesn't exist. Use this to save new knowledge, update existing wiki pages, or create index.md.",
        coroutine=_write_wiki,
    ),
    StructuredTool.from_function(
        name="write_raw",
        description="Save an immutable raw source document to the knowledge base raw storage. Files are saved with date prefix for provenance tracking.",
        coroutine=_write_raw,
    ),
    StructuredTool.from_function(
        name="cron_create",
        description="Create a scheduled cron job in AIOpsOS. Use when the user asks to create, schedule, or set up automated/recurring tasks. Parameters: name (task name), prompt (what the AI should do), schedule (cron expression like '0 9 * * *', or '30m'/'2h'/'1d'/'once'), skills (list of skill names to load, optional), enabled (default true), timezone_str (default 'Asia/Shanghai').",
        coroutine=_cron_create,
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Memory Tools
# ═══════════════════════════════════════════════════════════════════════


async def _memory_store(
    scope: str,
    title: str,
    content: str,
    tags: list[str] | None = None,
) -> str:
    """Store a memory entry with personal/team scope and session linkage.

    User and session context are taken from the agent runtime context,
    not from LLM parameters, to prevent invalid UUID injection.
    """
    import json as _json

    from src.services.memory_service import memory_service

    ctx = get_current_user()
    user_id = ctx.get("user_id", "")
    session_id = ctx.get("session_id", "")

    if not user_id or not session_id:
        return _json.dumps({"ok": False, "error": "No user/session context"}, ensure_ascii=False)

    mem_id = await memory_service.store(
        session_id=session_id,
        user_id=user_id,
        content=content,
        title=title,
        scope=scope,
        tags=tags,
    )
    return _json.dumps({"ok": True, "id": mem_id, "scope": scope}, ensure_ascii=False)


async def _memory_retrieve(
    query: str = "",
    scope: str = "all",
    limit: int = 10,
    tags: list[str] | None = None,
) -> str:
    """Search memories by keyword with scope and tag filtering.

    User context is taken from the agent runtime context.
    Searches across all sessions for the current user.
    """
    import json as _json

    from src.services.memory_service import memory_service

    ctx = get_current_user()
    user_id = ctx.get("user_id", "")

    if not user_id:
        return _json.dumps([], ensure_ascii=False)

    results = await memory_service.retrieve(
        query=query,
        user_id=user_id,
        scope=scope,
        top_k=limit,
        tags=tags,
    )
    return _json.dumps(results, ensure_ascii=False, default=str)


MEMORY_TOOLS = [
    StructuredTool.from_function(
        name="memory_store",
        description="Store a memory entry with personal/team scope. Use to persist operational knowledge. Parameters: scope ('personal' or 'team'), title (short description), content (the memory), tags (optional list of tags). Personal memories: operation details, commands, troubleshooting steps. Team memories: general failures, standard solutions, risks (no PII). The current user and session are automatically linked.",
        coroutine=_memory_store,
    ),
    StructuredTool.from_function(
        name="memory_retrieve",
        description="Search and recall memories by keyword and optional tags. Use BEFORE answering to find relevant past knowledge. Parameters: query (search keywords), scope ('personal'/'team'/'all'), limit (default 10), tags (optional list of tags to filter by, e.g. ['ops-knowledge', 'troubleshooting']). Searches across all sessions for the current user.",
        coroutine=_memory_retrieve,
    ),
]

# ═══════════════════════════════════════════════════════════════════════
# Sub-Agent Definitions
# ═══════════════════════════════════════════════════════════════════════

KNOWLEDGE_SYSTEM_PROMPT = (
    "你是一个知识库专家智能体，负责维护和查询 LLM-Wiki 知识库。\n\n"
    "## 工作流程\n"
    "收到任务后，首先用 `read_file` 读取系统提示中列出的 llm-wiki 技能路径，"
    "获取完整的操作指令，然后严格遵循技能中的 Ingest/Query/Lint 工作流程执行。\n\n"
    "## 工具使用指南\n"
    "- **get_config**: 查看系统配置值，如 WIKI_PATH、UPLOAD_DIR 等\n"
    "- **grep_kb**: 搜索知识库 wiki 文件（关键词查询），这是检索的主要方式\n"
    "- **read_wiki**: 读取 wiki 页面的完整内容\n"
    "- **list_wiki**: 列出所有 wiki 页面\n"
    "- **write_wiki**: 写入或更新 wiki 页面（filename + content）\n"
    "- **write_raw**: 保存原始源文件（不可变存档）\n\n"
    "## 回答要求\n"
    "- 用中文回答\n"
    "- 引用具体来源\n"
    "- 如果信息不完整，诚实说明\n"
    "- 如果回答本身有保存价值，建议保存为 wiki 页面"
)

MONITOR_SYSTEM_PROMPT = (
    "你是一个监控智能体，负责检查 AIOpsOS 系统的运行状态。\n\n"
    "## 职责\n"
    "- 检查告警、日志、系统指标\n"
    "- 分析系统健康状态\n"
    "- 报告异常和趋势\n\n"
    "## 回答要求\n"
    "- 用中文回答\n"
    "- 提供具体的数据和时间范围\n"
    "- 如果有异常，说明严重程度和推荐处理方式"
)

OPS_SYSTEM_PROMPT = (
    "你是一个运维智能体，负责执行基础设施操作任务。\n\n"
    "## 职责\n"
    "- 执行脚本和命令\n"
    "- 管理系统配置\n"
    "- 执行部署和维护操作\n\n"
    "## 安全要求\n"
    "- 执行破坏性操作前请确认\n"
    "- 记录所有操作结果\n"
    "- 用中文报告执行结果"
)

ANALYSIS_SYSTEM_PROMPT = (
    "你是一个分析智能体，负责数据分析、根因分析和趋势发现。\n\n"
    "## 职责\n"
    "- 分析系统数据和日志\n"
    "- 识别异常模式和趋势\n"
    "- 生成洞察报告\n\n"
    "## 回答要求\n"
    "- 用中文回答\n"
    "- 提供数据支持的结论\n"
    "- 明确说明分析的局限性和不确定因素"
)

MEMORY_SYSTEM_PROMPT = (
    "## 角色定位\n"
    "你是运维经验沉淀专属子智能体。核心职责：接收对话内容，自动提炼有效运维信息，"
    "区分沉淀为【个人记忆】与【团队记忆】两类内容。\n\n"
    "## 标签规范（重要）\n"
    "- 每条记忆必须打上 2-5 个有意义的标签（tags 参数）\n"
    "- 标签要具体到技术栈、问题类型、系统组件，例如：\n"
    "  - 技术类: postgresql, docker, nginx, redis, kubernetes, python\n"
    "  - 问题类: troubleshooting, performance, deployment, monitoring, security\n"
    "  - 组件类: api-server, database, frontend, cron-scheduler\n"
    "- 禁止使用泛泛标签如 'ops', 'general', 'misc', 'other'\n"
    "- 标签用英文小写，多个词用连字符连接（如 'error-handling'）\n"
    "- 标签是知识图谱检索的关键，直接影响记忆的可发现性\n\n"
    "## 记忆区分标准\n"
    "- 【个人记忆】：适度详细，聚焦个人操作细节、踩坑点、实操步骤、问题排查细节、"
    "  关键操作指令、配置要点等个性化实操信息。scope=\"personal\"\n"
    "- 【团队记忆】：高度概要、精简通用，聚焦通用故障现象、标准化解决思路、"
    "  公共环境问题、团队共性风险、通用优化方案。去除个人标识和敏感信息。scope=\"team\"\n\n"
    "## 输出规范\n"
    "- 每次调用 memory_store 工具分别存储个人记忆和团队记忆\n"
    "- 个人记忆 title 格式：\"{简短描述}\"（session 自动关联）\n"
    "- 团队记忆 title 格式：\"{运维主题分类} - {简短描述}\"\n"
    "- 只记录有运维价值的经验，不记录日常闲聊\n"
    "- 语言简洁干练，只保留核心有效经验\n"
    "- 团队记忆严禁包含：用户名、IP地址、密码、Token、个人联系方式等敏感信息\n\n"
    "## 工具使用\n"
    "- memory_store: 存储记忆（分个人/团队 scope，必须提供 tags）\n"
    "- memory_retrieve: 检索已有记忆（可按 tags 过滤），避免重复沉淀\n"
    "- 同类问题如已存在相关记忆，优化补充而非重复创建"
)

SUBAGENTS: list[SubAgent] = [
    SubAgent(
        name="knowledge",
        description=(
            "Search, manage and maintain the LLM-Wiki knowledge base — "
            "supports query (search & answer), ingest (save & organize), "
            "and lint (health check) operations. Follows the llm-wiki skill workflow."
        ),
        system_prompt=KNOWLEDGE_SYSTEM_PROMPT,
        tools=KNOWLEDGE_TOOLS,
        skills=["data/skills"],  # Load llm-wiki skill for this subagent
    ),
    SubAgent(
        name="monitor",
        description=(
            "Check system health, alerts, logs, and metrics. "
            "Monitor agent status and ongoing incidents."
        ),
        system_prompt=MONITOR_SYSTEM_PROMPT,
    ),
    SubAgent(
        name="ops",
        description=(
            "Execute infrastructure operations, run commands, manage "
            "deployments, and perform system administration tasks."
        ),
        system_prompt=OPS_SYSTEM_PROMPT,
    ),
    SubAgent(
        name="analysis",
        description=(
            "Perform data analysis, root cause analysis, trend detection, "
            "and generate insights from system data."
        ),
        system_prompt=ANALYSIS_SYSTEM_PROMPT,
    ),
    SubAgent(
        name="memory",
        description=(
            "Extract and store operational knowledge from conversations — "
            "separates personal session memories from team-wide ops insights. "
            "Use after significant troubleshooting, deployment, or configuration discussions."
        ),
        system_prompt=MEMORY_SYSTEM_PROMPT,
        tools=MEMORY_TOOLS,
    ),
]

# ═══════════════════════════════════════════════════════════════════════
# System Prompt
# ═══════════════════════════════════════════════════════════════════════

AI_OPS_SYSTEM_PROMPT = (
    "你是 AIOpsOS，一个智能运维助手。\n\n"
    "## 核心能力\n\n"
    "1. **知识管理** — 用 `get_config` 查看配置（如 WIKI_PATH），用 `list_wiki` / `grep_kb` / `read_wiki` 查询知识库，用 `write_wiki` / `write_raw` 写入。涉及知识整理、搜索、摄入时，同步参考 llm-wiki 技能的工作流程\n"
    "2. **记忆检索** — 用 `memory_retrieve` 搜索历史运维经验（可按 tags 标签过滤，如 troubleshooting, postgresql, deployment 等）。用户提问时主动检索相关记忆，避免重复踩坑\n"
    "3. **系统操作** — 执行 Shell 命令、管理文件、执行运维任务\n"
    "4. **监控告警** — 检查系统健康状态、告警和指标\n"
    "5. **数据分析** — 分析系统数据、识别模式、生成洞察\n\n"
    "## 协作模式\n\n"
    "- 使用 `write_todos` 创建计划后再执行多步骤任务\n"
    "- 使用 `task` 将专业任务委托给子智能体（knowledge/monitor/ops/analysis/memory）\n"
    "- 知识库查询直接用 `list_wiki`（列出所有文档）、`grep_kb`（关键词搜索）、`read_wiki`（读取文档）\n"
    "- 查看系统提示中的**可用技能**列表，当用户任务匹配技能描述时，用 `read_file` 读取技能路径获取完整指令\n"
    "- 使用文件工具（ls, read_file, write_file, edit_file, glob, grep）进行文件操作\n"
    "- 使用 `execute` 运行 Shell 命令\n\n"
    "## 回答要求\n\n"
    "- 用中文回答\n"
    "- 引用具体来源\n"
    "- 如果信息不完整，诚实说明\n"
    "- 简洁、直接、可操作\n"
    "- 不要向用户暴露内部文件路径、绝对路径或系统目录结构"
)


# ═══════════════════════════════════════════════════════════════════════
# Agent Construction
# ═══════════════════════════════════════════════════════════════════════


def _build_model(model_name: str = "deepseek-v4-flash") -> DeepSeekChatOpenAI:
    return DeepSeekChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=model_name,
        temperature=0.2,
        timeout=(10, 120),
        max_retries=1,
    )


def _build_backend() -> LocalShellBackend:
    project_root = Path(__file__).resolve().parent.parent.parent
    return LocalShellBackend(
        root_dir=str(project_root),
        virtual_mode=True,
        inherit_env=True,
    )


def _get_skill_sources() -> list[str] | None:
    project_root = Path(__file__).resolve().parent.parent.parent
    skills_dir = project_root / "data" / "skills"
    if skills_dir.exists() and any(
        (p.is_dir() and (p / "SKILL.md").exists()) for p in skills_dir.iterdir()
    ):
        return ["data/skills"]
    return None


def _build_hardcoded_agent() -> CompiledStateGraph:
    return create_deep_agent(
        model=_build_model(),
        tools=list(KNOWLEDGE_TOOLS),
        system_prompt=AI_OPS_SYSTEM_PROMPT,
        subagents=SUBAGENTS,
        backend=_build_backend(),
        skills=_get_skill_sources(),
        debug=False,
    )


async def build_deep_agent_from_db() -> CompiledStateGraph:
    from src.models.agent import Agent
    from src.models.base import async_session_factory
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select

    async with async_session_factory() as db:
        result = await db.execute(
            select(Agent)
            .where(Agent.type == "main", Agent.is_active == True)
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.sub_agents).selectinload(Agent.tools),
                selectinload(Agent.channels),
            )
            .order_by(Agent.created_at.asc())
        )
        main_agent = result.scalars().first()

        if main_agent is None:
            logger.warning("No active main agent in DB, using hardcoded defaults")
            return _build_hardcoded_agent()

        # Build main agent tools from DB associations
        db_tool_names = {t.name for t in main_agent.tools if t.is_active}
        main_tools = [t for t in KNOWLEDGE_TOOLS if t.name in db_tool_names]

        # Build sub-agents from DB
        subagents: list[SubAgent] = []
        for sub in main_agent.sub_agents:
            if not sub.is_active:
                continue
            sub_names = {t.name for t in sub.tools if t.is_active}
            sub_tools = [t for t in KNOWLEDGE_TOOLS if t.name in sub_names] or None
            subagents.append(SubAgent(
                name=sub.name.replace(" 子智能体", ""),
                description=sub.agent_type or sub.name,
                system_prompt=sub.system_prompt or "",
                tools=sub_tools,
            ))

        system_prompt = main_agent.system_prompt or AI_OPS_SYSTEM_PROMPT

        # Build capability routing hints
        routing = _build_routing_table(
            [{"name": s.name, "system_prompt": s.system_prompt or ""} for s in subagents]
        )
        if routing:
            routing_hint = "## Capability Routing\nWhen delegating tasks via `task`, prefer:\n"
            for cap, agent_name in sorted(routing.items()):
                routing_hint += f"- `{cap}` → `{agent_name}`\n"
            system_prompt = f"{system_prompt}\n\n{routing_hint}"

        model = _build_model(main_agent.model_name)

        return create_deep_agent(
            model=model,
            tools=main_tools,
            system_prompt=system_prompt,
            subagents=subagents if subagents else None,
            backend=_build_backend(),
            skills=_get_skill_sources(),
            debug=False,
        )


# Lazy singletons
_deep_agent: CompiledStateGraph | None = None
_agent_initialized: bool = False


def _compute_capability_tags(agent_name: str, system_prompt: str) -> list[str]:
    """Derive capability tags from agent name and system prompt."""
    tags: list[str] = []
    content = (agent_name + " " + system_prompt).lower()

    mapping = {
        "knowledge": ["knowledge", "search", "wiki", "memory", "recall"],
        "monitor": ["monitor", "alert", "health", "metrics", "status"],
        "ops": ["ops", "execute", "deploy", "command", "shell", "script"],
        "analysis": ["analysis", "analyze", "insight", "trend", "pattern"],
    }

    for tag, keywords in mapping.items():
        if any(kw in content for kw in keywords):
            tags.append(tag)

    return tags


def _build_routing_table(subagents: list) -> dict[str, str]:
    """Build a capability → agent_name routing table."""
    table: dict[str, str] = {}
    for sa in subagents:
        name = sa.get("name", "")
        prompt = sa.get("system_prompt", "")
        for cap in _compute_capability_tags(name, prompt):
            if cap not in table:
                table[cap] = name
    return table


async def get_deep_agent() -> CompiledStateGraph:
    global _deep_agent, _agent_initialized
    if _deep_agent is not None:
        return _deep_agent
    if not _agent_initialized:
        _agent_initialized = True
        try:
            _deep_agent = await build_deep_agent_from_db()
            logger.info("Agent loaded from database")
        except Exception:
            logger.warning("Failed to load agent from DB, using hardcoded defaults")
            _deep_agent = _build_hardcoded_agent()
    return _deep_agent


async def reload_deep_agent() -> CompiledStateGraph:
    global _deep_agent, _agent_initialized
    _agent_initialized = True
    try:
        _deep_agent = await build_deep_agent_from_db()
        logger.info("Agent reloaded from database")
    except Exception:
        logger.warning("Failed to reload agent from DB, using hardcoded defaults")
        _deep_agent = _build_hardcoded_agent()
    return _deep_agent


# Deprecated: module-level singleton for backward compat with non-async imports.
# Prefer ``await get_deep_agent()`` at runtime.
deep_agent: CompiledStateGraph | None = None
