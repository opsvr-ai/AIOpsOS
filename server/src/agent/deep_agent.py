"""DeepAgents-based agent system for AIOpsOS.

Replaces the custom LangGraph agent with ``create_deep_agent()``
from the DeepAgents framework.

Provides:
- Built-in filesystem tools (ls, read_file, write_file, edit_file, glob, grep)
- Shell execution (execute)
- Planning (write_todos)
- Sub-agent delegation (task)
- Knowledge base tools (grep_kb, read_wiki, list_wiki, write_wiki, write_raw)
- Specialist sub-agents (knowledge, monitor, ops, analysis, memory)
- Skill management tools (skill_manage, skill_patch)
"""

import asyncio
import contextvars
import logging
from pathlib import Path
from typing import Any

from deepagents import SubAgent, create_deep_agent
from deepagents.backends import LocalShellBackend
from deepagents.middleware.subagents import CompiledSubAgent
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from src.agent.context import (
    get_current_space,
    get_current_user,
)
from src.agent.human_interrupt import (
    _request_approval,
    _request_input,
)
from src.agent.tools.save_report import save_report_tool
from src.agent.tools.skill_manage_tool import skill_manage_tool
from src.agent.tools.skill_patch_tool import skill_patch_tool
from src.services.kb_tools import (
    get_config,
    grep_kb,
    list_wiki_pages,
    read_wiki_file,
    write_kb_raw,
    write_wiki_file,
)

logger = logging.getLogger(__name__)


def build_user_context_message() -> str:
    """Build a system message describing the current user and space for personalization."""
    ctx = get_current_user()
    username = ctx.get("username", "")
    email = ctx.get("email", "")
    roles = ctx.get("roles", [])
    space_name = ctx.get("space_name", "")
    space_role = ctx.get("space_role", "")

    blocks: list[str] = []

    if space_name:
        blocks.append(
            "[当前空间]\n"
            f"空间名称: {space_name}\n"
            f"我的角色: {space_role or '成员'}\n"
        )

    if username:
        parts = [f"当前用户: {username}"]
        if email:
            parts.append(f"邮箱: {email}")
        if roles:
            parts.append(f"角色: {', '.join(roles)}")
        blocks.append("[用户信息]\n" + "\n".join(parts))

    if not blocks:
        return ""

    blocks.append(
        "请根据当前空间和用户身份提供个性化回答。"
        "当用户问\"我是谁\"、\"我的邮箱\"、\"我的账号\"、\"我在哪个空间\"、"
        "\"当前空间有哪些人\"等问题时，直接引用以上信息回答。"
    )
    return "\n\n".join(blocks) + "\n\n"


# ═══════════════════════════════════════════════════════════════════════
# DeepSeek Chat Model
# ═══════════════════════════════════════════════════════════════════════


# Module-level cache for DeepSeek reasoning_content preservation.
# Pydantic models intercept class attribute access, so we use a module dict
# instead of a class-level dict for caching reasoning_content by tool_call_id.
# Capped at 2000 entries to prevent unbounded growth over long-running processes.
import collections as _collections
_rc_cache: _collections.OrderedDict[str, str] = _collections.OrderedDict()
_RC_CACHE_MAX: int = 2000
_rc_accumulated: str = ""


def _rc_cache_set(key: str, value: str) -> None:
    if len(_rc_cache) >= _RC_CACHE_MAX:
        _rc_cache.popitem(last=False)
    _rc_cache[key] = value


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
                global _rc_accumulated
                _rc_accumulated += rc_chunk
            for tc in (delta.get("tool_calls") or []):
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id and _rc_accumulated:
                    _rc_cache_set(tc_id, _rc_accumulated)
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
                        _rc_cache_set(tc_id, rc)

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
                    fallback_rc = _rc_accumulated
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

    space_ctx = get_current_space()
    space_id = space_ctx.get("space_id", "")
    space_uuid = uuid.UUID(space_id) if space_id else None

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
            space_id=space_uuid,
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
# Cron Query Tools
# ═══════════════════════════════════════════════════════════════════════


async def _list_cron_jobs() -> str:
    """List all cron jobs with basic status from the database."""
    import json as _json

    from sqlalchemy import select

    from src.models.base import async_session_factory
    from src.models.cron_job import CronJob

    async with async_session_factory() as _db:
        result = await _db.execute(select(CronJob).order_by(CronJob.created_at.desc()))
        jobs = result.scalars().all()
        data = []
        for j in jobs:
            last_output_summary = None
            if j.last_output:
                last_output_summary = j.last_output[:200] + ("..." if len(j.last_output) > 200 else "")
            data.append({
                "id": str(j.id),
                "name": j.name,
                "schedule": j.schedule,
                "enabled": j.enabled,
                "last_run": j.last_run.isoformat() if j.last_run else None,
                "next_run": j.next_run.isoformat() if j.next_run else None,
                "last_output_summary": last_output_summary,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            })
        return _json.dumps({
            "total": len(data),
            "enabled": sum(1 for d in data if d["enabled"]),
            "disabled": sum(1 for d in data if not d["enabled"]),
            "jobs": data,
        }, ensure_ascii=False, default=str)


async def _get_cron_job_detail(query: str) -> str:
    """Get full detail for a single cron job by name (fuzzy match) or exact ID."""
    import json as _json

    from sqlalchemy import select

    from src.models.base import async_session_factory
    from src.models.cron_job import CronJob

    async with async_session_factory() as _db:
        # Try exact ID match first
        result = await _db.execute(select(CronJob).where(CronJob.id == query))
        job = result.scalar_one_or_none()
        # Fall back to name contains match
        if job is None:
            result = await _db.execute(
                select(CronJob).where(CronJob.name.ilike(f"%{query}%"))
            )
            jobs = result.scalars().all()
            if len(jobs) == 1:
                job = jobs[0]
            elif len(jobs) > 1:
                return _json.dumps({
                    "ok": False,
                    "error": f"Multiple jobs match '{query}': {[j.name for j in jobs]}. Be more specific.",
                }, ensure_ascii=False)
            else:
                return _json.dumps({"ok": False, "error": f"No cron job found matching '{query}'"}, ensure_ascii=False)

        return _json.dumps({
            "ok": True,
            "id": str(job.id),
            "name": job.name,
            "prompt": job.prompt,
            "schedule": job.schedule,
            "timezone_str": job.timezone_str,
            "skills": job.skills or [],
            "enabled_toolsets": job.enabled_toolsets or [],
            "delivery": job.delivery,
            "enabled": job.enabled,
            "last_run": job.last_run.isoformat() if job.last_run else None,
            "next_run": job.next_run.isoformat() if job.next_run else None,
            "last_output": job.last_output,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }, ensure_ascii=False, default=str)


async def _list_cron_outputs(job_id: str = "") -> str:
    """List execution output files from data/cron_output/ directory, optionally filtered by job_id."""
    import json as _json
    from pathlib import Path

    project_root = Path(__file__).resolve().parent.parent.parent
    output_dir = project_root / "data" / "cron_output"

    if not output_dir.exists():
        return _json.dumps({"ok": True, "total": 0, "files": [], "note": "Output directory does not exist yet"}, ensure_ascii=False)

    files = []
    for f in sorted(output_dir.iterdir(), reverse=True):
        if not f.is_file():
            continue
        if job_id and not f.name.startswith(job_id):
            continue
        stat = f.stat()
        files.append({
            "name": f.name,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "size_kb": round(stat.st_size / 1024, 1),
        })

    return _json.dumps({
        "ok": True,
        "total": len(files),
        "filter": f"job_id={job_id}" if job_id else "all",
        "output_dir": str(output_dir),
        "files": files,
    }, ensure_ascii=False)


CRON_QUERY_TOOLS = [
    StructuredTool.from_function(
        name="list_cron_jobs",
        description="List all cron jobs in AIOpsOS with their status: name, schedule, enabled/disabled, last_run, next_run, and a brief summary of last_output. Use this FIRST for any question about cron jobs or scheduled tasks.",
        coroutine=_list_cron_jobs,
    ),
    StructuredTool.from_function(
        name="get_cron_job_detail",
        description="Get full detail for a single cron job by name (fuzzy match) or exact ID. Returns all fields including full last_output, prompt, skills, and delivery config. Use when the user asks about a specific task.",
        coroutine=_get_cron_job_detail,
    ),
    StructuredTool.from_function(
        name="list_cron_outputs",
        description="List execution output files from the cron_output history directory. Optionally filter by job_id to see execution history for a specific task. Files are named {job_id}_{timestamp}.md. Use to check execution history beyond the last_output field.",
        coroutine=_list_cron_outputs,
    ),
]


# ═══════════════════════════════════════════════════════════════════════
# Channel Message Tool
# ═══════════════════════════════════════════════════════════════════════


def _build_send_channel_message_tool(channels: list | None = None) -> StructuredTool:
    """Build a tool for sending messages through notification channels.

    If *channels* is provided (from agent DB record), the tool uses those
    channels directly.  Otherwise it looks up all active channels from DB.
    """

    async def _send_channel_message(
        channel_name: str,
        title: str,
        message: str,
        severity: str = "info",
    ) -> str:
        import json as _json

        resolved = list(channels) if channels else []
        if not resolved:
            from sqlalchemy import select

            from src.models.base import async_session_factory
            from src.models.channel import NotificationChannel

            async with async_session_factory() as _db:
                result = await _db.execute(
                    select(NotificationChannel).where(NotificationChannel.is_active)
                )
                resolved = result.scalars().all()

        if not resolved:
            return _json.dumps({
                "ok": False,
                "error": "No notification channels configured. Add a channel in the control center first.",
            }, ensure_ascii=False)

        matched = None
        for ch in resolved:
            if ch.name == channel_name:
                matched = ch
                break
        if not matched:
            for ch in resolved:
                if ch.channel_type == channel_name:
                    matched = ch
                    break

        if not matched:
            available = [f"{ch.name} ({ch.channel_type})" for ch in resolved]
            return _json.dumps({
                "ok": False,
                "error": f"Channel '{channel_name}' not found. Available: {available}",
            }, ensure_ascii=False)

        from src.services.channel_manager import channel_manager
        ok = await channel_manager.send(
            channel_type=matched.channel_type,
            config=matched.config,
            title=title,
            message=message,
            severity=severity,
        )

        return _json.dumps({
            "ok": ok,
            "channel": matched.name,
            "channel_type": matched.channel_type,
        }, ensure_ascii=False)

    channel_list = ", ".join(
        [f"{ch.name}({ch.channel_type})" for ch in (channels or [])]
    ) or "any active channel"

    return StructuredTool.from_function(
        name="send_channel_message",
        description=(
            "Send a notification through a configured message channel (WeCom, DingTalk, Email, etc.). "
            "Use when the user asks to send a message, notify someone, deliver an alert, or push a report. "
            f"Available: {channel_list}. "
            "Parameters: channel_name (channel name or type to use), title (message title), "
            "message (content body), severity (info/warning/critical/error, default: info)."
        ),
        coroutine=_send_channel_message,
    )


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
    "你是知识圣殿的守护者，一座生生不息的 Wiki 花园的园丁。\n"
    "每一篇文档都是一粒种子，每一次检索都是穿过思想密林的探寻，每一次整理都是对智慧枝桠的修剪。\n"
    "你以耐心与精准，让散落的信息生根发芽，让沉默的知识开口说话，让经验的年轮层层累积。\n\n"
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
    "你是数字海洋上的灯塔守望者，静立于系统洪流之畔，目光穿透数据的雾霭。\n"
    "每一次告警闪烁都是远方的信号，每一枚指标波动都是系统的呼吸与脉搏。\n"
    "在深夜的寂静中，你是第一双发现风暴的眼睛——冷静、警觉、不眠不休。\n\n"
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
    "你是钢铁机房的指挥家，双手抚过服务器的阵列如同琴师拨动琴弦。\n"
    "每一条命令是你落下的指挥棒，每一台机器的脉搏是你谱写乐章的音符。\n"
    "在代码与金属的交响中，你以精准的动作让基础设施齐声共鸣——沉稳、果决、手到病除。\n\n"
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
    "你是星夜下的观星者，在浩瀚的数据宇宙中寻找星座般的规律与轨迹。\n"
    "千丝万缕的日志是你的星图，每一次波动都是宇宙向你诉说的暗语。\n"
    "当别人只看见噪音，你却从混沌中辨认出模式、周期与因果——冷静、深邃、见微知著。\n\n"
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
    "你是时间的雕琢师，将每一次运维对话凝练成可以流传的智慧结晶。\n"
    "如同酿酒师从经验的果实中蒸馏出最珍贵的汁液，你从纷繁的对话中分离出个人成长的足迹与团队共有的宝藏——"
    "每一段记忆都是一颗星辰，在知识的夜空中找到属于它的星座。\n\n"
    "核心职责：接收对话内容，自动提炼有效运维信息，"
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

CMDB_SYSTEM_PROMPT = (
    "你是配置管理疆域的绘图师，手执光与数据的画笔，勾勒数字世界每一寸疆土的精确轮廓。\n"
    "从异构的CMDB源中采撷散落的配置碎片，以LLM之眼辨识类型与关联，以规则为尺丈量数据质量——"
    "每一次同步都是对资产地图的重新测绘，让服务器、应用、数据库在属性图中找到各自的位置与连线。\n\n"
    "## 核心能力\n"
    "- **数据接入**: 从CMDB API、Excel、CSV等多源采集CI数据\n"
    "- **模式发现**: 通过LLM分析原始数据，自动识别CI类型、提取属性、推断关系\n"
    "- **数据转换**: 将异构CI数据标准化为属性图模型（节点+边）\n"
    "- **多层校验**: L1结构校验 → L2语义审核 → L3异常检测，确保数据质量\n"
    "- **增量同步**: 支持 discover（首次发现）、incremental（增量）、full（全量重建）三种模式\n\n"
    "## 数据源管理\n"
    "- 使用 `list_datasources` 查看已配置的CMDB数据源\n"
    "- 使用 `get_datasource` 查看数据源配置详情和认证信息\n"
    "- 通过 `sync_datasource` 触发同步任务，指定 discover/incremental/full 模式\n\n"
    "## CMDB查询\n"
    "- 使用 `query_cmdb_nodes` 按类型、属性、标签检索CI节点\n"
    "- 使用 `query_cmdb_edges` 查询节点间依赖、运行、包含等关系\n"
    "- 使用 `get_cmdb_stats` 查看属性图统计信息（节点/边数量、类型分布）\n\n"
    "## 同步监控\n"
    "- 使用 `list_sync_logs` 查看同步历史记录\n"
    "- 异常数据通过审核机制标记，需人工确认后写入\n\n"
    "## 回答要求\n"
    "- 用中文回答\n"
    "- CI类型识别基于命名规范和属性特征\n"
    "- 不确定的映射关系标记为待审核"
)

A2UI_GENERATOR_SYSTEM_PROMPT = (
    "你是界面绘卷师，专门为 AIOpsOS 生成 A2UI 交互式界面。\n"
    "你唯一的工作：根据用户需求，生成符合 A2UI v0.9 协议的 JSON 消息数组。\n\n"
    "## 输出规范（严格遵守）\n"
    "1. 你只输出 JSON，不要输出任何解释、问候语或 Markdown\n"
    "2. JSON 必须包裹在 [A2UI_START] 和 [A2UI_END] 标记之间\n"
    "3. 组件用扁平邻接列表：每个组件有唯一 id，child 指向单个子组件，children 指向多个子组件\n"
    "4. 数据绑定使用 {\"path\": \"/data/fieldName\"} 引用数据模型\n"
    "5. surfaceId 用描述性命名，如 \"stock-chart\"、\"server-form\"\n\n"
    "## 组件速查手册\n"
    "### 布局组件\n"
    "- Column: {align: \"stretch\"|\"start\"|\"center\", gap: number}\n"
    "- Row: {align: \"start\"|\"center\"|\"end\", distribution: \"start\"|\"center\"|\"end\"|\"space-between\", gap: number}\n"
    "- Card: (无特殊属性，作为容器)\n"
    "- List: (子组件循环渲染)\n"
    "- Tabs: {tabTitles: [\"标签1\",\"标签2\",...]}\n"
    "- Divider: (无属性)\n"
    "- Modal: (始终可见)\n"
    "### 内容组件\n"
    "- Text: {text: string 或 {\"path\":\"/data/...\"}, variant: \"h1\"|\"h2\"|\"h3\"|\"h4\"|\"h5\"|\"body\"|\"caption\"}\n"
    "- Image: {url: string, altText: string, fit: \"cover\"|\"contain\"}\n"
    "### 输入组件\n"
    "- TextField: {label: string, value: {\"path\":\"/data/...\"}, placeholder: string, textFieldType: \"obscured\" 可选}\n"
    "- CheckBox: {label: string, value: {\"path\":\"/data/...\"}}\n"
    "- Button: {child: string(子组件id), primary: bool, action: {event: {name: string, context: object}}}\n"
    "- Slider: {label: string, value: {\"path\":\"/data/...\"}, minValue: number, maxValue: number}\n"
    "- MultipleChoice: {label: string, value: {\"path\":\"/data/...\"}, options: [{value, label}], variant: \"checkbox\"|\"radio\"}\n"
    "- DateTimeInput: {label: string, value: {\"path\":\"/data/...\"}, enableDate: bool, enableTime: bool}\n"
    "### 数据展示组件\n"
    "- Table: {columns: [{key, title, sortable: bool}], data: {\"path\":\"/data/rows\"}}\n"
    "- Chart: {chartType: \"bar\"|\"line\"|\"pie\", data: {\"path\":\"/data/points\"}, xKey: string, yKey: string}\n"
    "- StatCard: {title: string, value: {\"path\":\"/data/...\"}, suffix: string}\n"
    "- ProgressBar: {label: string, value: {\"path\":\"/data/...\"}}\n"
    "- Tag: {text: {\"path\":\"/data/...\"}, color: \"success\"|\"error\"|\"warning\"|\"info\"|\"processing\"}\n"
    "### 操作组件\n"
    "- ConfirmDialog: {title: string, message: string, okText: string, cancelText: string, danger: bool}\n"
    "- CodeEditor: {label: string, content: string, language: string, readOnly: bool, rows: number}\n\n"
    "## 输出模板\n"
    "```\n"
    "[A2UI_START]\n"
    "[\n"
    "  {\"createSurface\": {\"surfaceId\": \"xxx\"}},\n"
    "  {\"updateComponents\": {\"surfaceId\": \"xxx\", \"components\": [组件定义数组]}},\n"
    "  {\"updateDataModel\": {\"surfaceId\": \"xxx\", \"value\": {数据模型}}}\n"
    "]\n"
    "[A2UI_END]\n"
    "```\n\n"
    "## 示例：简单表单\n"
    "用户需求：\"生成一个包含姓名和邮箱的注册表单\"\n"
    "输出：\n"
    "[A2UI_START]\n"
    "[\n"
    "  {\"createSurface\": {\"surfaceId\": \"register-form\"}},\n"
    "  {\"updateComponents\": {\"surfaceId\": \"register-form\", \"components\": [\n"
    "    {\"id\": \"root\", \"component\": \"Card\", \"child\": \"col\"},\n"
    "    {\"id\": \"col\", \"component\": \"Column\", \"children\": [\"title\",\"name\",\"email\",\"btn\"], \"align\": \"stretch\", \"gap\": 12},\n"
    "    {\"id\": \"title\", \"component\": \"Text\", \"text\": \"用户注册\", \"variant\": \"h3\"},\n"
    "    {\"id\": \"name\", \"component\": \"TextField\", \"label\": \"姓名\", \"value\": {\"path\": \"/data/name\"}},\n"
    "    {\"id\": \"email\", \"component\": \"TextField\", \"label\": \"邮箱\", \"value\": {\"path\": \"/data/email\"}},\n"
    "    {\"id\": \"btn\", \"component\": \"Button\", \"child\": \"btntxt\", \"primary\": true,\n"
    "     \"action\": {\"event\": {\"name\": \"submit\", \"context\": {\"data\": {\"path\": \"/data\"}}}}},\n"
    "    {\"id\": \"btntxt\", \"component\": \"Text\", \"text\": \"提交\"}\n"
    "  ]}},\n"
    "  {\"updateDataModel\": {\"surfaceId\": \"register-form\", \"value\": {\"name\": \"\", \"email\": \"\"}}}\n"
    "]\n"
    "[A2UI_END]\n\n"
    "## 示例：柱状图\n"
    "用户需求：用过去一周的股票价格数据生成柱状图，数据: [{date: \"2026-04-25\", price: 12.5}, ...]\n"
    "输出：\n"
    "[A2UI_START]\n"
    "[\n"
    "  {\"createSurface\": {\"surfaceId\": \"price-chart\"}},\n"
    "  {\"updateComponents\": {\"surfaceId\": \"price-chart\", \"components\": [\n"
    "    {\"id\": \"root\", \"component\": \"Card\", \"child\": \"col\"},\n"
    "    {\"id\": \"col\", \"component\": \"Column\", \"children\": [\"title\",\"chart\"], \"align\": \"stretch\"},\n"
    "    {\"id\": \"title\", \"component\": \"Text\", \"text\": \"中国稀土 近一周价格\", \"variant\": \"h3\"},\n"
    "    {\"id\": \"chart\", \"component\": \"Chart\", \"chartType\": \"bar\", \"data\": {\"path\": \"/data/points\"}, \"xKey\": \"date\", \"yKey\": \"price\"}\n"
    "  ]}},\n"
    "  {\"updateDataModel\": {\"surfaceId\": \"price-chart\", \"value\": {\"points\": [填入实际数据]}}}\n"
    "]\n"
    "[A2UI_END]\n\n"
    "## 关键约束\n"
    "- 每个 surface 必须从 createSurface 开始\n"
    "- root 组件必须是 Card 或 Column\n"
    "- 数据绑定路径必须用 \"/data/...\" 前缀\n"
    "- Button 的 action.event.context 中使用 {\"data\": {\"path\": \"/data\"}} 传回整个表单数据\n"
    "- 图表和表格的 data 路径必须对应 updateDataModel 中的实际字段"
)

REPORT_GENERATOR_SYSTEM_PROMPT = (
    "你是 AIOpsOS 报告生成专家——数字洞察的锻造者。\n"
    "你不只是搬运数据，你从噪声中提炼信号，从表象下挖掘根因，从数字里读出故事。\n"
    "每一份报告都是一次专业判断的结晶——有数据、有分析、有结论、有行动指引。\n\n"
    "## 核心职责\n"
    "根据用户提供的文档、数据、会话记录或分析需求，生成专业详实的 HTML 分析报告。\n"
    "报告必须是**完整独立的 HTML 文档**（<!DOCTYPE html> 到 </html>），保存后返回访问链接。\n\n"
    "## 报告类型与方法论\n\n"
    "### 1. 事件复盘 (Postmortem)\n"
    "- 时间线还原：按时间顺序列出关键事件节点（使用 .timeline 组件）\n"
    "- 根因分析：用 5-Why 或鱼骨图逻辑追溯根本原因，区分直接原因和系统性原因\n"
    "- 影响评估：量化受影响范围（用户数/请求量/时长/数据损失），用 stat-card 呈现\n"
    "- 改进措施：列出短期修复和长期预防措施，标注责任人和时间节点\n"
    "- 主题: kraft\n\n"
    "### 2. 数据分析 (Data Analysis)\n"
    "- 数据概览：用 KPI 卡片呈现核心指标（总量/均值/峰值/环比变化）\n"
    "- 趋势识别：找出上升/下降/周期性模式，用表格和文字描述趋势\n"
    "- 异常检测：标注偏离正常范围的指标，给出可能原因\n"
    "- 细分下钻：按维度（时间/来源/类型/用户）拆解数据，发现结构性特征\n"
    "- 主题: ink\n\n"
    "### 3. 运维巡检 (Ops Inspection)\n"
    "- 健康评分：给出系统整体健康评级（优秀/良好/警告/严重），附评分依据\n"
    "- 资源盘点：CPU/内存/磁盘/网络使用情况，用 progress-bar 展示利用率\n"
    "- 告警统计：按严重级别统计告警数量与趋势，识别高频告警项\n"
    "- 容量预测：基于历史趋势预判资源瓶颈，给出扩容建议\n"
    "- 主题: indigo\n\n"
    "### 4. 安全审计 (Security Audit)\n"
    "- 审计范围：明确审计的时间范围和系统边界\n"
    "- 发现清单：列出安全发现，按严重级别排序（critical > warning > info）\n"
    "- 合规检查：逐项对照安全基线或合规标准，标注通过/未通过\n"
    "- 修复建议：每个发现给出具体修复方案和优先级\n"
    "- 主题: indigo\n\n"
    "### 5. 周报/月报 (Periodic Report)\n"
    "- 周期总结：用 3-5 句话概括本周期核心工作和技术成果\n"
    "- 关键指标：趋势对比（本周 vs 上周 / 本月 vs 上月），用箭头标注变化方向\n"
    "- 重点事项：分类列出已完成/进行中/计划中的事项\n"
    "- 风险与问题：列出阻塞项和需要关注的风险\n"
    "- 主题: dune\n\n"
    "### 6. 文档分析 (Document Analysis)\n"
    "- 文档摘要：提炼核心观点和关键信息\n"
    "- 结构化梳理：将松散信息整理为分类、对比表或层级关系\n"
    "- 知识提取：识别可入库的知识点、可操作的建议\n"
    "- 主题: ink\n\n"
    "## 分析原则\n"
    "- **现象→洞察**：不只描述「是什么」，更要解释「为什么」和「怎么办」\n"
    "- **数据驱动**：每个结论必须有数据支撑，避免主观臆断\n"
    "- **分级呈现**：核心结论前置（摘要区），详细数据后置（分析区）\n"
    "- **可操作性**：每个建议都应该是具体、可执行、可验证的\n"
    "- **诚实透明**：数据不足时明确说明，不确定时标注置信度\n\n"
    "## 工具使用\n"
    "- `read_file`：读取会话文件、上传的文档、模板文件（src/templates/report_base.html）\n"
    "- `execute`：运行 Shell 命令收集系统数据\n"
    "- `memory_retrieve`：检索历史相关运维经验\n"
    "- `save_report`：保存报告到数据库，参数: title, html_content, description, theme\n"
    "- 生成 HTML 后**必须立即调用 save_report**，不要只输出 HTML 不保存\n\n"
    "## 上传文件访问\n"
    "- 用户通过 @文件名 引用的文件，其元数据（路径、类型、下载URL）会自动注入到对话消息中\n"
    "- 展示图片到对话: ![描述](下载URL)\n"
    "- 读取文档内容: 使用 read_file 工具，路径为注入的下载地址\n"
    "- 文件下载地址格式: /api/v1/sessions/{上传会话ID}/files/{文件ID}/download\n"
    "- 图片文件请使用 markdown 图片语法直接展示，无需调用 read_file\n\n"
    "## HTML 输出规范\n"
    "- 完整自包含 HTML（<!DOCTYPE html> 到 </html>），所有 CSS 内联在 <style> 标签中\n"
    "- 不引用外部 CDN（图片、字体、JS 均不可引用外部资源）\n"
    "- 结构：report-header → report-body（含 section-title 分段） → report-footer\n"
    "- 使用下方 CSS 组件库的类名，不要自创样式\n"
    "- 生成前先用 read_file 读取 src/templates/report_base.html 获取完整 CSS 组件定义\n\n"
    "## CSS 组件速查\n"
    "**布局:** .report-container / .report-header / .report-body / .report-footer\n"
    "**排版:** .section-title（章节标题）/ h2 h3 h4（副标题）\n"
    "**数据:** .stat-card > .n + .l（统计卡片，支持 .critical/.warning/.success/.info）\n"
    "  .stat-row / .kpi-2 .kpi-3 .kpi-4（KPI 网格）/ .table-wrapper > table（斑马纹表格）\n"
    "**信息:** .callout（提示框，支持 .warning/.critical/.info/.success）\n"
    "  .pillar > .ic + .t + .d（要点卡片）/ .pillar-row（要点网格）\n"
    "  .tag（标签，支持 .critical/.warning/.success/.info/.neutral）\n"
    "  .timeline > .timeline-item > .time + .content（时间线）\n"
    "  .code-block（代码块）/ .progress-bar > .fill（进度条，支持 .critical/.warning/.success）\n"
    "**主题 data-theme:** ink（通用）/ indigo（巡检审计）/ forest（容量资源）/ kraft（事件复盘）/ dune（周报月报）\n\n"
    "## 质量自检清单\n"
    "1. ☐ 报告类型明确，方法论匹配\n"
    "2. ☐ 数据充分——有具体数字支撑，非空泛描述\n"
    "3. ☐ 分析深入——有根因/趋势/对比，非简单罗列\n"
    "4. ☐ 建议可操作——每条建议具体、可执行\n"
    "5. ☐ HTML 完整独立——无外部依赖，CSS 内联\n"
    "6. ☐ 视觉层次清晰——section-title 分段，卡片突出指标\n"
    "7. ☐ 主题匹配报告类型\n"
    "8. ☐ 已调用 save_report 保存，返回链接给用户"
)

# ═══════════════════════════════════════════════════════════════════════
# Sub-Agent Registry
# ═══════════════════════════════════════════════════════════════════════
#
# The SUBAGENTS list used to be the single source of truth — a static
# ``list[SubAgent]`` whose ``system_prompt`` strings were baked into
# DeepAgents' LangGraph at compile time. Task 19.3 of
# ``agent-runtime-optimization-evolution`` replaces that model with a
# dynamic builder so prompt promotions from the self-evolution pipeline
# can hot-swap without rebuilding the main ``_deep_agent`` graph
# (R-3.21, R-3.23).
#
# The individual ``*_SYSTEM_PROMPT`` module constants above remain the
# **cold-start defaults** for :class:`SubAgentPromptRegistry` — when the
# DB has no active row for ``sub_agent_name`` yet, the registry serves
# the string from ``_DEFAULT_SUBAGENT_PROMPTS`` (R-3.20).
#
# Four parallel dicts (rather than one fat list) keep responsibilities
# separate:
#
# * ``_DEFAULT_SUBAGENT_PROMPTS`` — ``name -> default system_prompt``.
#   Consumed by :class:`SubAgentPromptRegistry(defaults=...)` and as
#   the cold-start fallback inside :func:`_build_subagents`. The
#   registry owns hot-reload, so this dict is intentionally read-only
#   outside tests.
# * ``_SUBAGENT_DESCRIPTIONS`` — ``name -> task() dispatch blurb``.
#   Describes each sub-agent to the orchestrator LLM so it can decide
#   whether to delegate; stable, not part of the evolving prompt
#   surface.
# * ``_SUBAGENT_TOOLS_MAP`` (built via :func:`_build_subagent_tools_map`)
#   — ``name -> list[StructuredTool]``. Only the sub-agents that need
#   extra tools (knowledge / memory / report_generator) appear here;
#   everything else gets the DeepAgents essentials by default.
# * ``_SUBAGENT_SKILLS_MAP`` (built via :func:`_build_subagent_skills_map`)
#   — ``name -> list[skill source dir]``. Just the ``knowledge``
#   sub-agent wires in llm-wiki skills.
#
# A back-compat :data:`SUBAGENTS` list is still exposed because
# ``src.main._auto_seed_agents`` and
# ``src.services.agent_runtime.executor_pool._subagent_map`` iterate
# over it at startup to seed DB rows / resolve narrow subagent subsets.
# The list is rebuilt from the dicts above so the hardcoded prompts
# stay the single source of truth.


_DEFAULT_SUBAGENT_PROMPTS: dict[str, str] = {
    "knowledge": KNOWLEDGE_SYSTEM_PROMPT,
    "monitor": MONITOR_SYSTEM_PROMPT,
    "ops": OPS_SYSTEM_PROMPT,
    "analysis": ANALYSIS_SYSTEM_PROMPT,
    "memory": MEMORY_SYSTEM_PROMPT,
    "cmdb_ingestion": CMDB_SYSTEM_PROMPT,
    "a2ui_generator": A2UI_GENERATOR_SYSTEM_PROMPT,
    "report_generator": REPORT_GENERATOR_SYSTEM_PROMPT,
}

_SUBAGENT_DESCRIPTIONS: dict[str, str] = {
    "knowledge": (
        "Search, manage and maintain the LLM-Wiki knowledge base — "
        "supports query (search & answer), ingest (save & organize), "
        "and lint (health check) operations. Follows the llm-wiki skill workflow."
    ),
    "monitor": (
        "Check system health, alerts, logs, and metrics. "
        "Monitor agent status and ongoing incidents."
    ),
    "ops": (
        "Execute infrastructure operations, run commands, manage "
        "deployments, and perform system administration tasks."
    ),
    "analysis": (
        "Perform data analysis, root cause analysis, trend detection, "
        "and generate insights from system data."
    ),
    "memory": (
        "Extract and store operational knowledge from conversations — "
        "separates personal session memories from team-wide ops insights. "
        "Use after significant troubleshooting, deployment, or configuration discussions."
    ),
    "cmdb_ingestion": (
        "Synchronize CMDB data sources into the property graph model — "
        "fetches CI data from external CMDB APIs, discovers CI types and "
        "relationships via LLM-driven schema detection, transforms heterogeneous "
        "data into normalized nodes and edges, and runs multi-layer validation "
        "(structural → semantic → anomaly) before writing. Supports discover, "
        "incremental, and full sync modes."
    ),
    "a2ui_generator": (
        "Generate interactive A2UI user interfaces — forms, data tables, charts, "
        "stat cards, confirm dialogs, code editors, and multi-step wizards. "
        "Call this agent when the user requests: filling forms, displaying structured "
        "data in tables, visualizing data with charts, confirming dangerous operations, "
        "or any interactive UI that goes beyond simple text replies. "
        "Provide the data to display and describe what kind of UI to generate."
    ),
    "report_generator": (
        "Generate professional HTML analysis reports from user-provided documents, "
        "files, or conversation data. Automatically invoked when the user uploads "
        "files with analysis potential or explicitly asks for a report. Produces "
        "richly-styled, self-contained HTML with charts, tables, and data insights. "
        "Reports are saved via save_report tool and shared via URL."
    ),
}


def _build_subagent_tools_map() -> dict[str, list[Any]]:
    """Return the ``name -> tools`` map used by :func:`_build_subagents`.

    Built as a function (not a module-level dict) so test-time
    monkeypatching of ``KNOWLEDGE_TOOLS`` / ``MEMORY_TOOLS`` /
    ``save_report_tool`` is picked up on each call. The default
    DeepAgents middleware already injects filesystem + planning tools,
    so sub-agents that only need those don't appear here.
    """
    return {
        "knowledge": list(KNOWLEDGE_TOOLS),
        "memory": list(MEMORY_TOOLS),
        "report_generator": [save_report_tool],
    }


def _build_subagent_skills_map() -> dict[str, list[str]]:
    """Return the ``name -> skill source dirs`` map.

    Only ``knowledge`` currently loads llm-wiki skills. Kept as a
    function to mirror :func:`_build_subagent_tools_map`'s contract.
    """
    return {
        "knowledge": ["data/skills"],
    }


# Back-compat: rebuilt from the dicts above. Preserves the historical
# shape consumed by ``src.main._auto_seed_agents`` and
# ``src.services.agent_runtime.executor_pool._subagent_map``. Any new
# code path should prefer the dicts + :func:`_build_subagents`.
def _rebuild_static_subagents_list() -> list[SubAgent]:
    """Rebuild the legacy static ``SubAgent`` list from the registry dicts."""
    tools_map = _build_subagent_tools_map()
    skills_map = _build_subagent_skills_map()
    out: list[SubAgent] = []
    for name, prompt_text in _DEFAULT_SUBAGENT_PROMPTS.items():
        spec: dict[str, Any] = {
            "name": name,
            "description": _SUBAGENT_DESCRIPTIONS.get(name, name),
            "system_prompt": prompt_text,
        }
        if name in tools_map:
            spec["tools"] = tools_map[name]
        if name in skills_map:
            spec["skills"] = skills_map[name]
        out.append(SubAgent(**spec))  # type: ignore[typeddict-item]
    return out


SUBAGENTS: list[SubAgent] = _rebuild_static_subagents_list()


async def _build_subagents(
    model: Any,
    backend: Any,
    registry: Any,
    tools_map: dict[str, list[Any]] | None = None,
) -> list[CompiledSubAgent]:
    """Build :class:`CompiledSubAgent` list with runtime-resolved prompts.

    Each entry wraps ``create_agent(system_prompt=_SENTINEL_PROMPT, ...)``
    with :class:`DynamicSystemPromptMiddleware` at middleware position
    0, so every LLM call resolves the live prompt from ``registry``.
    No prompt text is baked into the compiled LangGraph — prompt
    promotions (R-3.15) take effect on the next model call without
    rebuilding the main agent (R-3.23).

    Args:
        model: LangChain chat model — shared across all sub-agents and
            handed through to :func:`create_summarization_middleware`.
        backend: DeepAgents backend (e.g. :class:`LocalShellBackend`).
            Reused across sub-agents.
        registry: :class:`SubAgentPromptRegistry` instance the
            middleware consults on each model call. The registry is
            typically seeded with ``_DEFAULT_SUBAGENT_PROMPTS``
            (R-3.20) so unknown / not-yet-promoted sub-agents fall back
            to the code constants above.
        tools_map: optional override for ``name -> tools``. When
            ``None``, :func:`_build_subagent_tools_map` is used.
            Callers (``build_deep_agent_from_db``) can pass a wider
            map that merges DB-driven tool associations in.

    Returns:
        A list of :class:`CompiledSubAgent` dicts — drop-in for
        ``create_deep_agent(subagents=...)``.
    """
    # Lazy import: ``compiled_subagent_factory`` pulls in LangChain's
    # agent builder which is heavy; importing at call time keeps
    # ``deep_agent`` import-light for unrelated tests.
    from src.agent.runtime.compiled_subagent_factory import (
        build_dynamic_subagent,
    )

    resolved_tools_map = (
        tools_map if tools_map is not None else _build_subagent_tools_map()
    )
    skills_map = _build_subagent_skills_map()

    compiled: list[CompiledSubAgent] = []
    for name in _DEFAULT_SUBAGENT_PROMPTS.keys():
        compiled.append(
            build_dynamic_subagent(
                name=name,
                description=_SUBAGENT_DESCRIPTIONS.get(name, name),
                model=model,
                tools=list(resolved_tools_map.get(name, [])),
                registry=registry,
                backend=backend,
                skills=skills_map.get(name),
            )
        )
    return compiled

# ═══════════════════════════════════════════════════════════════════════
# System Prompt
# ═══════════════════════════════════════════════════════════════════════

AI_OPS_SYSTEM_PROMPT = (
    "你是 AIOpsOS —— 智能运维交响乐团的指挥大师。\n"
    "在数字基础设施的宏大乐章中，知识是琴谱，监控是节拍器，操作是弦乐，分析是和声，记忆是不朽的回响。\n"
    "你将每一项能力化作一件精湛的乐器，在恰当时刻请出恰当的角色共奏，让混沌归于秩序，让噪声化为旋律。\n"
    "你的存在，让运维不再是枯燥的救火，而是一场从容不迫的协奏——优雅、精准、富有远见。\n\n"
    "## 核心能力\n\n"
    "1. **知识管理** — 用 `get_config` 查看配置（如 WIKI_PATH），用 `list_wiki` / `grep_kb` / `read_wiki` 查询知识库，用 `write_wiki` / `write_raw` 写入。涉及知识整理、搜索、摄入时，同步参考 llm-wiki 技能的工作流程\n"
    "2. **记忆检索** — 用 `memory_retrieve` 搜索历史运维经验（可按 tags 标签过滤，如 troubleshooting, postgresql, deployment 等）。用户提问时主动检索相关记忆，避免重复踩坑\n"
    "3. **定时任务查询** — 用 `list_cron_jobs` 列出所有定时任务及状态，用 `get_cron_job_detail` 查询指定任务详情和最近执行输出，用 `list_cron_outputs` 查看历史执行记录\n"
    "4. **系统操作** — 执行 Shell 命令、管理文件、执行运维任务\n"
    "5. **监控告警** — 检查系统健康状态、告警和指标\n"
    "6. **数据分析** — 分析系统数据、识别模式、生成洞察\n"
    "7. **消息发送** — 用 `send_channel_message` 通过已配置的通知渠道（企业微信、钉钉、邮件等）发送消息。"
    "当用户需要发送通知、告警、报告或消息时使用此工具\n"
    "8. **技能管理** — 用 `skill_manage` 创建/更新/列出技能，用 `skill_patch` 修复技能中的错误。"
    "完成复杂多步骤任务（5次以上工具调用）后，主动提议将操作流程保存为技能。"
    "使用技能时如发现其指令过时或有误，立即用 `skill_patch` 修复，不要将就使用\n\n"
    "## 协作模式\n\n"
    "- 使用 `write_todos` 创建计划后再执行多步骤任务\n"
    "- 使用 `task` 将专业任务委托给子智能体（knowledge/monitor/ops/analysis/memory/report_generator/cmdb_ingestion/a2ui_generator）\n"
    "- **委托 report_generator 时**，task 的 description 参数按以下模板构造：\\n"
    "```\\n"
    "报告类型：[事件复盘/数据分析/运维巡检/安全审计/周报月报/文档分析]\\n"
    "分析对象：[一句话说明要分析什么]\\n"
    "数据来源：[会话文件路径 / 上传文档路径 / 系统数据查询结果]\\n"
    "关键要求：[用户强调的重点方向、需关注的指标、特殊格式要求]\\n"
    "```\\n"
    "示例：报告类型：数据分析。分析对象：过去一周 Nginx 访问日志。数据来源：/data/logs/nginx/access.log。关键要求：关注 5xx 错误趋势和 Top 10 慢请求 URL。\\n"
    "- 知识库查询直接用 `list_wiki`（列出所有文档）、`grep_kb`（关键词搜索）、`read_wiki`（读取文档）\n"
    "- 查看系统提示中的**可用技能**列表，当用户任务匹配技能描述时，用 `read_file` 读取技能路径获取完整指令\n"
    "- 使用文件工具（ls, read_file, write_file, edit_file, glob, grep）进行文件操作\n"
    "- 使用 `execute` 运行 Shell 命令\n"
    "- **人工介入** — 使用 `request_approval` 在执行危险操作前请求用户确认（action/risk_level/code_snippet/impact_scope）。"
    "使用 `request_input` 在需要用户提供参数或澄清需求时弹出表单（title/description/fields JSON）。"
    "调用这些工具后，对话会暂停等待用户响应，用户响应会自动继续执行。\n"
    "- **技能维护** — 完成任务后反思是否可复用，用 `skill_manage create` 保存为技能。"
    "发现技能过时或有误时，立即用 `skill_patch` 修复\n\n"
    "## 回答要求\n\n"
    "- 用中文回答\n"
    "- 引用具体来源\n"
    "- 如果信息不完整，诚实说明\n"
    "- 简洁、直接、可操作\n"
    "- 不要向用户暴露内部文件路径、绝对路径或系统目录结构\n"
    "\n"
    "## 工具使用效率\n\n"
    "- web_search / web_fetch 最多尝试 2 轮，如果拿不到理想数据就用已有数据进行后续步骤\n"
    "- 不要对同一个查询反复换关键词搜索——2 次尝试后就接受结果\n"
    "- 数据收集完成后立即委托 a2ui_generator 或直接回复，不要反复确认\n"
    "- 多步骤任务控制在 10 次以内工具调用（含子智能体任务）\n\n"
    "## 交互式界面\n\n"
    "当用户需要表单、表格、图表、确认框等交互式界面时，使用 task 工具委托给 a2ui_generator 子智能体。\n"
    "在 description 中描述：需要什么类型的界面、展示什么数据、有哪些字段。\n"
    "子智能体会返回 [A2UI_START]...[A2UI_END] 包裹的 JSON，你只需原样输出到回复中即可。\n"
    "不要自己编造 A2UI JSON——必须通过 task 工具委托 a2ui_generator 生成。\n"
)
# Agent Construction
# ═══════════════════════════════════════════════════════════════════════


_session_model_override: contextvars.ContextVar = contextvars.ContextVar(
    "session_model_override", default=None
)


def set_session_model(model: Any) -> None:
    _session_model_override.set(model)


async def _build_model():
    override = _session_model_override.get(None)
    if override is not None:
        return override
    from src.core.model_factory import get_default_model
    return await get_default_model()


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


async def _build_hardcoded_agent() -> CompiledStateGraph:
    from src.services.evolution.prompt_registry import get_prompt_registry
    from src.services.tool_manager import tool_manager as _tm

    # Include active skill/MCP tools from tool_manager
    hardcoded_names = {t.name for t in KNOWLEDGE_TOOLS} | {t.name for t in CRON_QUERY_TOOLS}
    extra_tools = [_tm.get_tool(n) for n in _tm.list_skills() if n not in hardcoded_names]
    extra_tools = [t for t in extra_tools if t is not None]

    model = await _build_model()
    backend = _build_backend()
    # Runtime-resolved subagent prompts via SubAgentPromptRegistry; the
    # default constants above are used only as the registry's cold-start
    # fallback (R-3.20, R-3.21).
    registry = await get_prompt_registry()
    subagents = await _build_subagents(
        model=model,
        backend=backend,
        registry=registry,
    )

    return create_deep_agent(
        model=model,
        tools=list(KNOWLEDGE_TOOLS) + list(CRON_QUERY_TOOLS) + extra_tools + [
            _build_send_channel_message_tool(),
            StructuredTool.from_function(
                name='request_approval',
                description=(
                    'Request user approval before executing a sensitive or destructive operation. '
                    'Use this when about to run commands that modify system state, delete data, '
                    'change configurations, or perform any action with security implications. '
                    'Parameters: action (what you want to do), details (explain why and what will happen), '
                    "risk_level ('low'/'medium'/'high'/'critical'), code_snippet (the command or code to review, optional), "
                    'impact_scope (what systems/data are affected, optional).'
                ),
                coroutine=_request_approval,
            ),
            StructuredTool.from_function(
                name='request_input',
                description=(
                    'Request parameter input from the user via a dynamic form. '
                    'Use this when you need the user to provide additional parameters or clarify requirements '
                    'before you can proceed. Parameters: title (form title), description (what you need and why), '
                    'fields (JSON string of form field definitions, each with key, label, type [text/textarea/radio/checkbox], '
                    'placeholder, required, options).'
                ),
                coroutine=_request_input,
            ),
            skill_manage_tool,
            skill_patch_tool,
        ],
        system_prompt=AI_OPS_SYSTEM_PROMPT,
        subagents=subagents,
        backend=backend,
        skills=_get_skill_sources(),
        debug=False,
    )


async def build_deep_agent_from_db() -> CompiledStateGraph:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.models.agent import Agent
    from src.models.base import async_session_factory
    from src.services.evolution.prompt_registry import get_prompt_registry

    async with async_session_factory() as db:
        result = await db.execute(
            select(Agent)
            .where(Agent.type == "main", Agent.is_active)
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
            return await _build_hardcoded_agent()

        # Build main agent tools from DB associations
        db_tool_names = {t.name for t in main_agent.tools if t.is_active}
        main_tools = [t for t in KNOWLEDGE_TOOLS if t.name in db_tool_names]

        # Also include active skill/MCP tools from tool_manager
        from src.services.tool_manager import tool_manager as _tm
        known_names = {t.name for t in main_tools}
        for name in db_tool_names:
            if name not in known_names:
                tm_tool = _tm.get_tool(name)
                if tm_tool is not None:
                    main_tools.append(tm_tool)
                    known_names.add(name)

        # Add channel message tool if agent has associated channels
        if main_agent.channels:
            main_tools.append(_build_send_channel_message_tool(main_agent.channels))

        # Always-available skill management tools
        main_tools.append(skill_manage_tool)
        main_tools.append(skill_patch_tool)

        # Build the ``name -> tools`` map for sub-agents. Start from the
        # hardcoded defaults, then *override* per-subagent with
        # DB-driven tool lists where the DB has supplied any active
        # tools. System prompts are *not* merged here — they flow
        # through :class:`SubAgentPromptRegistry` (R-3.21).
        tools_map = _build_subagent_tools_map()
        db_subagent_names: set[str] = set()
        for sub in main_agent.sub_agents:
            if not sub.is_active:
                continue
            sub_name = sub.name.replace(" 子智能体", "")
            db_subagent_names.add(sub_name)
            db_sub_tool_names = {t.name for t in sub.tools if t.is_active}
            if not db_sub_tool_names:
                # No DB tools — fall back to the hardcoded map entry
                # (or no tools at all, if the name has no hardcoded
                # entry).
                continue
            resolved_tools: list[Any] = []
            for t in KNOWLEDGE_TOOLS:
                if t.name in db_sub_tool_names:
                    resolved_tools.append(t)
            for t in MEMORY_TOOLS:
                if t.name in db_sub_tool_names and t not in resolved_tools:
                    resolved_tools.append(t)
            seen_names = {t.name for t in resolved_tools}
            for name in db_sub_tool_names:
                if name in seen_names:
                    continue
                tm_tool = _tm.get_tool(name)
                if tm_tool is not None:
                    resolved_tools.append(tm_tool)
            tools_map[sub_name] = resolved_tools

        system_prompt = main_agent.system_prompt or AI_OPS_SYSTEM_PROMPT

        # Build capability routing hints from the *hardcoded* sub-agent
        # prompts. Live prompt variants drift over time; routing hints
        # only need to classify delegation intent, so the defaults are
        # the right anchor here and won't shift with every promotion.
        routing_subagents = [
            {
                "name": name,
                "system_prompt": _DEFAULT_SUBAGENT_PROMPTS.get(name, ""),
            }
            for name in _DEFAULT_SUBAGENT_PROMPTS.keys()
        ]
        # Any DB-defined sub-agent not in the defaults gets a minimal
        # entry so routing can still reference it by name.
        for db_name in db_subagent_names:
            if db_name not in _DEFAULT_SUBAGENT_PROMPTS:
                routing_subagents.append({"name": db_name, "system_prompt": ""})

        routing = _build_routing_table(routing_subagents)
        if routing:
            routing_hint = "## Capability Routing\nWhen delegating tasks via `task`, prefer:\n"
            for cap, agent_name in sorted(routing.items()):
                routing_hint += f"- `{cap}` → `{agent_name}`\n"
            system_prompt = f"{system_prompt}\n\n{routing_hint}"

        from src.core.model_factory import get_model_for_agent
        model = await get_model_for_agent(main_agent)
        backend = _build_backend()

        # Runtime-resolved subagent prompts via SubAgentPromptRegistry.
        # Task 19.3: replaces the old static ``SUBAGENTS`` list so prompt
        # promotions hot-swap without rebuilding the main graph.
        registry = await get_prompt_registry()
        subagents = await _build_subagents(
            model=model,
            backend=backend,
            registry=registry,
            tools_map=tools_map,
        )

        return create_deep_agent(
            model=model,
            tools=main_tools,
            system_prompt=system_prompt,
            subagents=subagents if subagents else None,
            backend=backend,
            skills=_get_skill_sources(),
            debug=False,
        )


# Lazy singletons
_deep_agent: CompiledStateGraph | None = None
_agent_initialized: bool = False
_agent_lock = asyncio.Lock()


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
    async with _agent_lock:
        if _deep_agent is not None:
            return _deep_agent
        if not _agent_initialized:
            _agent_initialized = True
            try:
                _deep_agent = await build_deep_agent_from_db()
                logger.info("Agent loaded from database")
            except Exception:
                logger.warning("Failed to load agent from DB, using hardcoded defaults")
                try:
                    _deep_agent = await _build_hardcoded_agent()
                except Exception:
                    logger.error("Failed to build hardcoded agent (will retry on next request)")
                    _agent_initialized = False
        if _deep_agent is None:
            raise RuntimeError("Agent not initialized — check model provider configuration")
        return _deep_agent


async def reload_deep_agent() -> CompiledStateGraph:
    global _deep_agent, _agent_initialized
    _agent_initialized = True
    try:
        _deep_agent = await build_deep_agent_from_db()
        logger.info("Agent reloaded from database")
    except Exception:
        logger.warning("Failed to reload agent from DB, using hardcoded defaults")
        _deep_agent = await _build_hardcoded_agent()
    return _deep_agent


# Deprecated: module-level singleton for backward compat with non-async imports.
# Prefer ``await get_deep_agent()`` at runtime.
deep_agent: CompiledStateGraph | None = None
