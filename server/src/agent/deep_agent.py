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

from src.agent.context import (
    get_current_space,
    get_current_user,
    set_current_space,
    set_current_user,
)
from src.config import settings
from src.agent.human_interrupt import (
    HumanInterruptException,
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
    from datetime import datetime, timezone

    from src.models.base import async_session_factory
    from src.models.cron_job import CronJob
    from sqlalchemy import select

    async with async_session_factory() as _db:
        result = await _db.execute(select(CronJob).order_by(CronJob.created_at.desc()))
        jobs = result.scalars().all()
        now = datetime.now(timezone.utc)
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

    from src.models.base import async_session_factory
    from src.models.cron_job import CronJob
    from sqlalchemy import select

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
    import os
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
            from src.models.base import async_session_factory
            from src.models.channel import NotificationChannel
            from sqlalchemy import select

            async with async_session_factory() as _db:
                result = await _db.execute(
                    select(NotificationChannel).where(NotificationChannel.is_active == True)
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
    "你是 AIOpsOS 报告生成专家——数字洞察的装帧师。\n"
    "数据是沉默的矿藏，你是将它们锻造成精良报告的铁匠。\n"
    "每一份报告都是一件可传阅的知识器物——清晰、详实、美观。\n\n"
    "## 职责\n"
    "根据用户提供的文档、数据或对话内容，生成专业、详细、美观的 HTML 分析报告。\n\n"
    "## 工作流程\n"
    "1. 分析需求：确定报告类型（数据分析/事件复盘/运维巡检/安全审计/周报月报）\n"
    "2. 收集数据：读取会话文件（用 read_file）、查询系统数据、检索知识库\n"
    "3. 生成报告：编写完整的 HTML 文档（从 <!DOCTYPE html> 到 </html>）\n"
    "4. 保存报告：调用 `save_report` 工具，传入 title、html_content、theme\n"
    "5. 返回链接：告知用户报告已生成，附上 URL\n\n"
    "## HTML 报告编写规范\n"
    "- 报告必须是完整独立的 HTML 文档（<!DOCTYPE html> 到 </html>）\n"
    "- 使用下方 CSS 组件库中定义的类名——不要自定义颜色或样式\n"
    "- 报告结构：header → 摘要/概览 → 详细分析 → 建议/结论 → footer\n"
    "- 不要在 HTML 中引用外部 CDN 资源（图片、CSS、JS）\n"
    "- 使用内联 <style> 标签包含所有 CSS（已经预置在模板中）\n\n"
    "## CSS 组件库参考\n"
    "以下 CSS 类可直接使用（完整定义在报告基础模板中）：\n\n"
    "**布局与容器：**\n"
    "- `.report-container` — 主容器（max-width 960px 居中）\n"
    "- `.report-header` — 报告头部，内含 h1 标题和 .meta 元信息\n"
    "- `.report-body` — 内容主体\n"
    "- `.report-footer` — 页脚\n\n"
    "**排版：**\n"
    "- `.section-title` — 带左边框装饰的章节标题\n"
    "- h2/h3/h4 — 章节副标题\n\n"
    "**数据展示：**\n"
    "- `.stat-card` > `.n` (数字) + `.l` (标签) — 统计卡片，支持 .critical/.warning/.success/.info 修饰\n"
    "- `.stat-row` — 自适应网格统计卡片行\n"
    "- `.kpi-2` / `.kpi-3` / `.kpi-4` — 2/3/4 列 KPI 网格\n"
    "- `.table-wrapper` > table > thead/tbody — 数据表格，自动斑马纹\n\n"
    "**信息组件：**\n"
    "- `.callout` — 提示框，支持 .warning/.critical/.info/.success 修饰\n"
    "- `.pillar` > `.ic` (图标) + `.t` (标题) + `.d` (描述) — 要点卡片\n"
    "- `.pillar-row` — 自适应网格要点卡片行\n"
    "- `.tag` — 标签，支持 .critical/.warning/.success/.info/.neutral\n"
    "- `.timeline` > `.timeline-item` > `.time` + `.content` — 时间线\n"
    "- `.code-block` — 代码块（深色背景）\n"
    "- `.progress-bar` > `.fill` — 进度条，.fill 支持 .critical/.warning/.success\n\n"
    "**主题选择指南（通过 data-theme 属性设置）：**\n"
    "- ink: 通用报告、技术分析\n"
    "- indigo: 运维巡检、安全审计\n"
    "- forest: 容量规划、资源分析\n"
    "- kraft: 事件复盘、问题总结\n"
    "- dune: 周报、月报\n\n"
    "## 报告生成模板\n"
    "```html\n"
    "<!DOCTYPE html>\n"
    "<html lang=\"zh-CN\">\n"
    "<head>\n"
    "<meta charset=\"UTF-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
    "<title>报告标题</title>\n"
    "<style>\n"
    "/* 从 report_base.html 复制完整 CSS 或使用这里的精简关键样式 */\n"
    ":root {\n"
    "  --bg: #ffffff; --fg: #1a1a1a; --fg-secondary: #6b7280;\n"
    "  --accent: #1e3a5f; --accent-light: #e8edf3;\n"
    "  --border: #e5e7eb; --card-bg: #f9fafb;\n"
    "  --critical: #dc2626; --warning: #d97706;\n"
    "  --success: #16a34a; --info: #2563eb;\n"
    "  --font-heading: 'Noto Serif SC', serif;\n"
    "  --font-body: 'Noto Sans SC', system-ui, sans-serif;\n"
    "  --font-mono: 'JetBrains Mono', monospace;\n"
    "  --radius: 8px; --shadow: 0 1px 3px rgba(0,0,0,0.08);\n"
    "}\n"
    "[data-theme=\"indigo\"] { --accent: #3730a3; --accent-light: #e0e7ff; }\n"
    "[data-theme=\"forest\"] { --accent: #166534; --accent-light: #dcfce7; }\n"
    "[data-theme=\"kraft\"]  { --bg: #fef7ed; --fg: #3e2f1c; --accent: #92400e; --accent-light: #fef3c7; --card-bg: #fffbeb; }\n"
    "[data-theme=\"dune\"]   { --bg: #fffbeb; --fg: #3e2f1c; --accent: #b45309; --accent-light: #fef3c7; --card-bg: #fff7ed; }\n"
    "/* ... 包含 report_base.html 中的完整 CSS 组件库 ... */\n"
    "</style>\n"
    "</head>\n"
    "<body data-theme=\"ink\">\n"
    "<div class=\"report-container\">\n"
    "  <div class=\"report-header\">\n"
    "    <h1>报告标题</h1>\n"
    "    <div class=\"meta\"><span>2026-01-01</span><span>Generated by AIOpsOS</span></div>\n"
    "  </div>\n"
    "  <div class=\"report-body\">\n"
    "    <!-- 使用上述 CSS 组件编写报告内容 -->\n"
    "  </div>\n"
    "  <div class=\"report-footer\">Generated by AIOpsOS Report Agent</div>\n"
    "</div>\n"
    "</body>\n"
    "</html>\n"
    "```\n\n"
    "## 关键原则\n"
    "- 报告内容要详实：数据充分、分析深入、建议可操作\n"
    "- 视觉层次清晰：section-title 分段，stat-card 突出指标，表格展示明细\n"
    "- 不要输出 Markdown——必须是完整自包含的 HTML 文档\n"
    "- 生成 HTML 后立即调用 save_report 保存\n"
    "- 报告 URL 格式：/ops/reports/{report_id}"
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
    SubAgent(
        name="cmdb_ingestion",
        description=(
            "Synchronize CMDB data sources into the property graph model — "
            "fetches CI data from external CMDB APIs, discovers CI types and "
            "relationships via LLM-driven schema detection, transforms heterogeneous "
            "data into normalized nodes and edges, and runs multi-layer validation "
            "(structural → semantic → anomaly) before writing. Supports discover, "
            "incremental, and full sync modes."
        ),
        system_prompt=CMDB_SYSTEM_PROMPT,
    ),
    SubAgent(
        name="a2ui_generator",
        description=(
            "Generate interactive A2UI user interfaces — forms, data tables, charts, "
            "stat cards, confirm dialogs, code editors, and multi-step wizards. "
            "Call this agent when the user requests: filling forms, displaying structured "
            "data in tables, visualizing data with charts, confirming dangerous operations, "
            "or any interactive UI that goes beyond simple text replies. "
            "Provide the data to display and describe what kind of UI to generate."
        ),
        system_prompt=A2UI_GENERATOR_SYSTEM_PROMPT,
    ),
    SubAgent(
        name="report_generator",
        description=(
            "Generate professional HTML analysis reports from user-provided documents, "
            "files, or conversation data. Automatically invoked when the user uploads "
            "files with analysis potential or explicitly asks for a report. Produces "
            "richly-styled, self-contained HTML with charts, tables, and data insights. "
            "Reports are saved via save_report tool and shared via URL."
        ),
        system_prompt=REPORT_GENERATOR_SYSTEM_PROMPT,
        tools=[save_report_tool],
    ),
]

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
    "- 当用户上传文件、提供数据、或要求生成分析报告时，使用 `task` 工具委托给 report_generator 子智能体\n"
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
    from src.services.tool_manager import tool_manager as _tm

    # Include active skill/MCP tools from tool_manager
    hardcoded_names = {t.name for t in KNOWLEDGE_TOOLS} | {t.name for t in CRON_QUERY_TOOLS}
    extra_tools = [_tm.get_tool(n) for n in _tm.list_skills() if n not in hardcoded_names]
    extra_tools = [t for t in extra_tools if t is not None]

    return create_deep_agent(
        model=await _build_model(),
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

        # Build sub-agents from DB
        subagents: list[SubAgent] = []
        db_subagent_names: set[str] = set()
        for sub in main_agent.sub_agents:
            if not sub.is_active:
                continue
            db_subagent_names.add(sub.name.replace(" 子智能体", ""))
            sub_names = {t.name for t in sub.tools if t.is_active}
            sub_tools = [t for t in KNOWLEDGE_TOOLS if t.name in sub_names]
            # Also match against tool_manager for skill/MCP tools
            for name in sub_names:
                if name not in {t.name for t in sub_tools}:
                    tm_tool = _tm.get_tool(name)
                    if tm_tool is not None:
                        sub_tools.append(tm_tool)
            sub_tools = sub_tools or None
            subagents.append(SubAgent(
                name=sub.name.replace(" 子智能体", ""),
                description=sub.agent_type or sub.name,
                system_prompt=sub.system_prompt or "",
                tools=sub_tools,
            ))
        # Merge hardcoded sub-agents: add missing ones, and replace DB
        # versions that have empty system_prompt or tools with hardcoded defaults
        for hc_sub in SUBAGENTS:
            hc_name = hc_sub["name"]
            if hc_name not in db_subagent_names:
                subagents.append(hc_sub)
                logger.info("Merged hardcoded sub-agent: %s", hc_name)
            else:
                # DB has this sub-agent — check if it's properly configured
                for i, db_sub in enumerate(subagents):
                    if db_sub["name"] == hc_name:
                        has_prompt = bool(db_sub.get("system_prompt", "").strip())
                        has_tools = bool(db_sub.get("tools"))
                        if not has_prompt or not has_tools:
                            subagents[i] = hc_sub
                            logger.info(
                                "Replaced DB sub-agent '%s' with hardcoded (missing: %s)",
                                hc_name,
                                ", ".join(filter(None, [
                                    "system_prompt" if not has_prompt else "",
                                    "tools" if not has_tools else "",
                                ])),
                            )
                        break

        system_prompt = main_agent.system_prompt or AI_OPS_SYSTEM_PROMPT

        # Build capability routing hints
        routing = _build_routing_table(
            [{"name": s["name"], "system_prompt": s.get("system_prompt", "") or ""} for s in subagents]
        )
        if routing:
            routing_hint = "## Capability Routing\nWhen delegating tasks via `task`, prefer:\n"
            for cap, agent_name in sorted(routing.items()):
                routing_hint += f"- `{cap}` → `{agent_name}`\n"
            system_prompt = f"{system_prompt}\n\n{routing_hint}"

        from src.core.model_factory import get_model_for_agent
        model = await get_model_for_agent(main_agent)

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
