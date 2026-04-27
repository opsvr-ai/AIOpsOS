"""KnowledgeAgent — built-in knowledge sub-agent with LangChain skills pattern.

Uses a manual ReAct loop (instead of ``create_agent``) to avoid issues with
DeepSeek's ``reasoning_content`` field.

Follows progressive disclosure:
- Core system prompt describes KB tools and basic operations
- ``load_skill("llm-wiki")`` loads the full llm-wiki skill instructions on-demand
"""

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from src.agent.sub_agents.base import BaseSubAgent
from src.config import settings
from src.services.kb_tools import list_wiki_pages
from src.services.tool_manager import tool_manager

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 10


def _load_skill_prompt(skill_name: str) -> str:
    """Load a skill prompt from ``data/skills/{skill_name}.md``."""
    from pathlib import Path

    path = Path(settings.kb_wiki_dir) / ".." / "skills" / f"{skill_name}.md"
    if not path.is_file():
        return f"[Skill '{skill_name}' not found]"

    content = path.read_text(encoding="utf-8")

    # Strip optional YAML frontmatter (--- ... ---)
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2].strip()

    return content


class KnowledgeAgent(BaseSubAgent):
    """Knowledge base sub-agent — LLM-Wiki pattern with progressive skill loading.

    Follows the LangChain multi-agent skills pattern:
    - A ``load_skill`` tool returns detailed skill instructions on-demand
    - The core agent starts with a lightweight prompt
    - Heavy skill instructions are loaded progressively when needed
    """

    name = "knowledge"
    description = "Search, manage and maintain the LLM-Wiki knowledge base — supports query (search & answer), ingest (save & organize), and lint (health check) operations."
    system_prompt = (
        "你是一个知识库专家智能体，负责维护和查询 LLM-Wiki 知识库。\n\n"
        "## 核心原则\n"
        "知识库是持久化、可积累的 Wiki，不是 RAG。每次操作都会丰富知识库。\n\n"
        "## 操作类型\n"
        "根据用户请求自动判断操作类型：\n"
        "1. **查询** — 搜索知识库并综合回答。使用 grep_kb 搜索关键词，read_wiki 读取相关内容。\n"
        "2. **摄入** — 保存新内容到知识库。先用 write_raw 保存原始内容，再用 write_wiki 整理到 wiki。\n"
        "3. **检查** — 健康检查知识库，发现矛盾、缺口和孤儿页面。\n\n"
        "## 工具使用指南\n"
        "你可以直接使用以下工具：\n"
        "- **grep_kb**: 搜索知识库 wiki 文件（关键词查询），这是检索的主要方式\n"
        "- **read_wiki**: 读取 wiki 页面的完整内容\n"
        "- **list_wiki**: 列出所有 wiki 页面\n"
        "- **write_wiki**: 写入或更新 wiki 页面（filename + content）\n"
        "- **write_raw**: 保存原始源文件（不可变存档）\n\n"
        "## 渐进式技能加载\n"
        "当需要进行以下操作时，先调用 ``load_skill('llm-wiki')`` 加载完整的 llm-wiki 技能指令：\n"
        "- 用户要求「整理笔记」、「保存到知识库」、「处理源文件」→ 加载技能后执行摄入流程\n"
        "- 用户要求「健康检查」、「检查矛盾」→ 加载技能后执行检查流程\n"
        "- 常规查询可直接使用 grep_kb + read_wiki，无需加载技能\n\n"
        "## 回答要求\n"
        "- 用中文回答\n"
        "- 引用具体来源\n"
        "- 如果信息不完整，诚实说明\n"
        "- 如果回答本身有保存价值，建议保存为 wiki 页面"
    )

    def __init__(self) -> None:
        super().__init__()
        self._react_llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model="deepseek-v4-flash",
            temperature=0.3,
            timeout=60,
        )
        # Resolve KB tools once
        self._tools: dict[str, Any] = {}
        for name in ("grep_kb", "read_wiki", "list_wiki", "write_wiki", "write_raw"):
            t = tool_manager.get_tool(name)
            if t:
                self._tools[name] = t

    async def __call__(self, task: str, context: dict[str, Any] | None = None) -> str:
        """Execute knowledge base operation using a ReAct loop with progressive skill loading."""
        # Enrich context with DB data
        real_data = await self._fetch_real_data(task)

        msg_parts = [f"User request: {task}"]
        if context:
            ctx_lines = [f"{k}: {v}" for k, v in context.items()]
            msg_parts.append("Context:\n" + "\n".join(ctx_lines))
        if real_data:
            msg_parts.append(f"Knowledge base status:\n{real_data}")

        messages: list = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content="\n\n".join(msg_parts)),
        ]

        for iteration in range(_MAX_ITERATIONS):
            response = await self._react_llm.ainvoke(messages)

            # Strip reasoning_content — DeepSeek returns it but rejects it on re-send
            if isinstance(response, AIMessage):
                response.additional_kwargs.pop("reasoning_content", None)

            content = str(response.content or "")

            # If no tool calls, this is the final answer
            if not response.tool_calls:
                return content if content else "[knowledge: no output]"

            # Execute each tool call
            for tc in response.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id", "")

                # Intercept load_skill calls — not a registered tool, handled here
                if tool_name == "load_skill":
                    skill_name = tool_args.get("skill_name") or tool_args.get("skill") or "llm-wiki"
                    result = _load_skill_prompt(skill_name)
                else:
                    tool = self._tools.get(tool_name)
                    if tool is None:
                        result = f"Tool '{tool_name}' not found. Available tools: {', '.join(self._tools)}"
                        logger.warning("KnowledgeAgent requested unknown tool: %s", tool_name)
                    else:
                        try:
                            result = str(await tool.ainvoke(tool_args))
                        except Exception as exc:
                            result = f"Tool error: {exc}"
                            logger.exception("Tool '%s' failed in KnowledgeAgent", tool_name)

                messages.append(response)
                messages.append(ToolMessage(content=result, tool_call_id=tool_id))

        return f"[knowledge] Reached max iterations ({_MAX_ITERATIONS})"

    async def _fetch_real_data(self, task: str) -> str:
        """Fetch knowledge base overview from DB for context."""
        from sqlalchemy import func, select

        from src.models.base import async_session_factory
        from src.models.knowledge import KnowledgeDocument

        parts: list[str] = []
        try:
            async with async_session_factory() as db:
                total = await db.scalar(select(func.count(KnowledgeDocument.id)))
                parts.append(f"Total documents: {total or 0}")

                top = await db.execute(
                    select(KnowledgeDocument)
                    .order_by(KnowledgeDocument.created_at.desc())
                    .limit(10)
                )
                docs = list(top.scalars())
                if docs:
                    parts.append("Recent documents:")
                    for d in docs:
                        src = f" ({d.source})" if d.source else ""
                        parts.append(f"  - {d.title[:60]}{src} [{d.chunk_count} chunks]")

                # List wiki pages
                wiki_list = list_wiki_pages()
                if wiki_list and "No wiki" not in wiki_list:
                    parts.append(f"\nWiki pages:\n{wiki_list[:500]}")
        except Exception:
            logger.exception("KnowledgeAgent _fetch_real_data failed")
            return ""

        return "\n".join(parts) if len(parts) > 1 else ""
