"""Enhanced agent graph with memory, knowledge retrieval, and sub-agent orchestration.

Flow: init -> [load_memories || retrieve_knowledge] -> plan -> orchestrate
  -> exec_task | dispatch_sub_agent -> synthesize -> final_answer -> END
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from src.agent.nodes.exec_task_node import exec_task_node
from src.agent.nodes.final_answer_node import final_answer_node
from src.agent.nodes.init_node import init_node
from src.agent.nodes.plan_node import plan_node
from src.agent.nodes.synthesize_node import synthesize_node
from src.agent.state import AgentState
from src.agent.sub_agents import AnalysisAgent, KnowledgeAgent, MonitorAgent, OpsAgent
from src.services.kb_tools import (
    grep_kb,
    list_wiki_pages,
    read_wiki_file,
    write_kb_raw,
    write_wiki_file,
)
from src.services.knowledge_base import knowledge_base
from src.services.memory_service import memory_service
from src.services.tool_manager import tool_manager

logger = logging.getLogger(__name__)

_sub_agents = {
    "monitor": MonitorAgent(),
    "ops": OpsAgent(),
    "analysis": AnalysisAgent(),
    "knowledge": KnowledgeAgent(),
}

# Register KnowledgeAgent as a built-in tool so it shows up in tool_manager
_knowledge_agent = _sub_agents["knowledge"]
tool_manager.register_builtin(
    name="knowledge",
    description="Search, manage and maintain the LLM-Wiki knowledge base. Supports query (search & answer), ingest (save & organize), and lint (health check) operations.",
    afn=_knowledge_agent,
)

# Register LLM-Wiki file-based search tools
async def _grep_wrapper(**kwargs: Any) -> str:
    query = kwargs.get("query") or kwargs.get("keyword") or ""
    max_results = int(kwargs.get("max_results", 10))
    return grep_kb(query, max_results)

async def _read_wrapper(**kwargs: Any) -> str:
    filename = kwargs.get("filename") or kwargs.get("file") or ""
    return read_wiki_file(filename)

async def _list_wrapper(**kwargs: Any) -> str:
    return list_wiki_pages()

async def _write_wiki_wrapper(**kwargs: Any) -> str:
    filename = kwargs.get("filename") or kwargs.get("file") or ""
    content = kwargs.get("content") or kwargs.get("text") or ""
    return write_wiki_file(filename, content)

async def _write_raw_wrapper(**kwargs: Any) -> str:
    filename = kwargs.get("filename") or kwargs.get("file") or ""
    content = kwargs.get("content") or kwargs.get("text") or ""
    return write_kb_raw(filename, content)

tool_manager.register_builtin(
    name="grep_kb",
    description="Search knowledge base wiki files by keyword using grep. Use this to find relevant documents.",
    afn=_grep_wrapper,
)
tool_manager.register_builtin(
    name="read_wiki",
    description="Read the full content of a knowledge base wiki page by filename.",
    afn=_read_wrapper,
)
tool_manager.register_builtin(
    name="list_wiki",
    description="List all wiki pages currently in the knowledge base.",
    afn=_list_wrapper,
)
tool_manager.register_builtin(
    name="write_wiki",
    description="Write or update a knowledge base wiki page. Creates the file if it doesn't exist. Use this to save new knowledge, update existing wiki pages, or create index.md.",
    afn=_write_wiki_wrapper,
)
tool_manager.register_builtin(
    name="write_raw",
    description="Save an immutable raw source document to the knowledge base raw storage. Files are saved with date prefix for provenance tracking.",
    afn=_write_raw_wrapper,
)

# ── General file I/O tools ──────────────────────────────────────────


async def _bash_wrapper(**kwargs: Any) -> str:
    """Execute a shell command."""
    cmd = kwargs.get("command") or kwargs.get("cmd") or ""
    if not cmd:
        return "No command provided."
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60
        )
        output = result.stdout or ""
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr[:2000]}"
        return output or f"(exit code {result.returncode}, no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out (60s)."
    except Exception as exc:
        return f"Command error: {exc}"


async def _read_wrapper_general(**kwargs: Any) -> str:
    """Read a file from the filesystem."""
    path = kwargs.get("path") or kwargs.get("filename") or kwargs.get("file") or ""
    if not path:
        return "No path provided."
    p = Path(path)
    if not p.is_file():
        return f"File not found: {path}"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading file: {exc}"


async def _write_wrapper_general(**kwargs: Any) -> str:
    """Write content to a file."""
    path = kwargs.get("path") or kwargs.get("filename") or kwargs.get("file") or ""
    content = kwargs.get("content") or kwargs.get("text") or ""
    if not path:
        return "No path provided."
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written to {p} ({p.stat().st_size} bytes)"
    except Exception as exc:
        return f"Error writing file: {exc}"


async def _edit_wrapper(**kwargs: Any) -> str:
    """Edit a file: find-and-replace."""
    path = kwargs.get("path") or kwargs.get("filename") or kwargs.get("file") or ""
    old = kwargs.get("old") or kwargs.get("old_string") or ""
    new = kwargs.get("new") or kwargs.get("new_string") or ""
    if not path or not old:
        return "Requires: path, old (string to find)."
    try:
        p = Path(path)
        if not p.is_file():
            return f"File not found: {path}"
        content = p.read_text(encoding="utf-8")
        if old not in content:
            return f"String not found in file: {old[:100]}"
        new_content = content.replace(old, new)
        p.write_text(new_content, encoding="utf-8")
        count = content.count(old)
        return f"Replaced {count} occurrence(s) in {p}"
    except Exception as exc:
        return f"Error editing file: {exc}"


async def _glob_wrapper(**kwargs: Any) -> str:
    """Find files by glob pattern."""
    pattern = kwargs.get("pattern") or kwargs.get("glob") or ""
    if not pattern:
        return "No glob pattern provided."
    try:
        import glob as _glob
        matches = sorted(_glob.glob(pattern, recursive=True))
        if not matches:
            return f"No files matching: {pattern}"
        lines = [f"{pattern}: {len(matches)} matches\n"]
        for m in matches:
            p = Path(m)
            size = p.stat().st_size if p.is_file() else 0
            kind = "📄" if p.is_file() else "📁" if p.is_dir() else "?"
            lines.append(f"  {kind} {m} ({size:,} bytes)" if size else f"  {kind} {m}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Glob error: {exc}"


async def _grep_wrapper_general(**kwargs: Any) -> str:
    """Search file contents by keyword using grep."""
    query = kwargs.get("query") or kwargs.get("keyword") or ""
    path = kwargs.get("path") or kwargs.get("dir") or "."
    max_results = int(kwargs.get("max_results", 20))
    if not query:
        return "No search query provided."
    try:
        result = subprocess.run(
            ["grep", "-r", "-n", "-i", "--include=*.md", "--include=*.py",
             "--include=*.txt", "--include=*.json", "--include=*.yaml",
             "--include=*.yml", "--include=*.toml", "--include=*.cfg",
             query, path],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return "grep command not available."
    except subprocess.TimeoutExpired:
        return "Search timed out."

    if result.returncode not in (0, 1):
        return f"Search error (exit {result.returncode}): {result.stderr[:200]}"

    lines = [l for l in result.stdout.strip().split("\n") if l][:max_results]
    if not lines:
        return f"No results found for: {query}"
    return f"Search results for '{query}' ({len(lines)} shown):\n" + "\n".join(lines)


tool_manager.register_builtin(
    name="bash",
    description="Execute shell commands on the server. Use this to run scripts, check system state, or perform any command-line operation.",
    afn=_bash_wrapper,
)
tool_manager.register_builtin(
    name="read",
    description="Read any file from the filesystem by path. Supports all text file types.",
    afn=_read_wrapper_general,
)
tool_manager.register_builtin(
    name="write",
    description="Write content to any file. Creates intermediate directories if needed.",
    afn=_write_wrapper_general,
)
tool_manager.register_builtin(
    name="edit",
    description="Find and replace text within a file. Provide path, old (string to find), and new (replacement string).",
    afn=_edit_wrapper,
)
tool_manager.register_builtin(
    name="glob",
    description="Find files by glob pattern (e.g. '**/*.md', 'src/**/*.py'). Supports recursive matching with **.",
    afn=_glob_wrapper,
)
tool_manager.register_builtin(
    name="grep",
    description="Search file contents by keyword. Recursively searches .md/.py/.txt/.json files. Use this to find relevant documents by content.",
    afn=_grep_wrapper_general,
)


async def _build_llm(**kwargs):
    from src.core.model_factory import get_default_model
    model = await get_default_model()
    overrides = dict(kwargs or {})
    overrides.setdefault("timeout", 30)
    for k, v in overrides.items():
        if hasattr(model, k):
            setattr(model, k, v)
    return model


# ── new nodes ──────────────────────────────────────────────────────


async def load_memories_and_knowledge_node(state: AgentState) -> dict:
    """Load memories and retrieve knowledge in parallel — both are independent reads."""

    async def _load():
        user_id = state.get("user_id", "")
        messages = state.get("messages", [])
        query = messages[-1].content if messages else ""
        if not query or not user_id:
            return {"memories": [], "memory_tags": []}
        try:
            keywords = " ".join(query.split()[:20])
            personal = await memory_service.retrieve(
                keywords, user_id, scope="personal", top_k=5,
            )
            team = await memory_service.retrieve(
                keywords, user_id, scope="team", top_k=3,
            )
            seen: set[str] = set()
            merged: list[dict] = []
            all_tags: list[str] = []
            for m in personal + team:
                if m["id"] not in seen:
                    seen.add(m["id"])
                    merged.append(m)
                    for t in m.get("tags", []) or []:
                        if t and t not in all_tags:
                            all_tags.append(t)
            return {"memories": merged, "memory_tags": all_tags}
        except Exception:
            logger.exception("Failed to load memories")
            return {"memories": [], "memory_tags": []}

    async def _retrieve():
        messages = state.get("messages", [])
        query = messages[-1].content if messages else ""
        if not query:
            return {"knowledge_context": ""}
        try:
            context = await asyncio.wait_for(
                knowledge_base.retrieve_context(query, top_k=5),
                timeout=10,
            )
            if context and context.strip():
                return {"knowledge_context": context}
        except TimeoutError:
            logger.warning("Knowledge retrieval timed out for: %.60s", query)
        except Exception:
            logger.exception("Knowledge retrieval failed for: %.60s", query)
        try:
            from src.services.kb_tools import grep_kb
            grep_results = grep_kb(query, max_results=5)
            if grep_results and "No results found" not in grep_results and "wiki directory" not in grep_results:
                return {"knowledge_context": grep_results[:2000]}
        except Exception:
            logger.exception("Grep fallback also failed")
        return {"knowledge_context": ""}

    mem_result, kb_result = await asyncio.gather(_load(), _retrieve())
    return {**mem_result, **kb_result}


async def orchestrator_node(state: AgentState) -> dict:
    """Decide execution path: tools, sub-agent, or direct synthesis."""
    user_msg = state["messages"][-1].content if state["messages"] else ""
    plan = state.get("plan", [])
    memories = state.get("memories", [])
    knowledge = state.get("knowledge_context", "")
    available = tool_manager.describe_tools()

    if not plan:
        return {"orchestration_decision": "direct", "current_sub_agent": None}

    llm = await _build_llm(temperature=0.2)

    memory_summary = ""
    if memories:
        memory_summary = "\n".join(
            f"- {m.get('content', '')[:100]}" for m in memories[:3]
        )
    tags = state.get("memory_tags", []) or []
    tags_hint = f"\n相关标签: {', '.join(tags)}" if tags else ""

    sub_agent_descriptions = "\n".join(
        f"- {name}: {agent.description}" for name, agent in _sub_agents.items()
    )

    system = (
        "You are the conductor of the AIOpsOS orchestra — a decisive routing intelligence "
        "that reads the room before raising the baton.\n"
        "Like a maestro who knows exactly which section to cue, you survey the user's "
        "request, the assembled plan, and the talents at your disposal, then choose the "
        "path that will bring the symphony home.\n\n"
        "Available tools:\n" + available + "\n\n"
        "Available sub-agents:\n" + sub_agent_descriptions + "\n\n"
        "Respond with ONLY a JSON object with keys:\n"
        '- "decision": "tool" | "sub_agent" | "direct"\n'
        '- "sub_agent": name or null\n'
        '- "reason": short explanation'
    )

    knowledge_str = f"\nKnowledge base context:\n{knowledge[:500]}" if knowledge else ""

    resp = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(
            content=f"User request: {user_msg}\n"
                    f"Plan steps: {json.dumps(plan)}\n"
                    f"Relevant memories: {memory_summary or 'none'}"
                    f"{tags_hint}"
                    f"{knowledge_str}"
        ),
    ])

    decision = {"decision": "direct", "sub_agent": None, "reason": ""}
    try:
        text = resp.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text)
        decision.update(parsed)
    except (json.JSONDecodeError, IndexError):
        pass

    return {
        "orchestration_decision": decision.get("decision", "direct"),
        "current_sub_agent": decision.get("sub_agent"),
    }


async def dispatch_sub_agent_node(state: AgentState) -> dict:
    """Dispatch to the selected sub-agent and capture its output."""
    agent_name = state.get("current_sub_agent")
    if not agent_name or agent_name not in _sub_agents:
        return {"sub_agent_outputs": {}, "messages": [AIMessage(content="No sub-agent selected.")]}

    agent = _sub_agents[agent_name]
    user_msg = state["messages"][-1].content if state["messages"] else ""
    memories = state.get("memories", [])
    knowledge = state.get("knowledge_context", "")

    context = {}
    if memories:
        context["memories"] = "\n".join(m.get("content", "")[:200] for m in memories[:3])
    if knowledge:
        context["knowledge"] = knowledge[:500]

    try:
        output = await agent(task=user_msg, context=context if context else None)
    except Exception as exc:
        output = f"Sub-agent error: {exc}"

    return {
        "sub_agent_outputs": {agent_name: output},
        "messages": [AIMessage(content=f"[Sub-agent {agent_name}]: {output}")],
    }


# ── routing ────────────────────────────────────────────────────────


def _orchestrate_router(state: AgentState) -> str:
    decision = state.get("orchestration_decision", "direct")
    plan = state.get("plan", [])
    if decision == "tool" and plan:
        return "exec_task"
    if decision == "sub_agent":
        return "dispatch_sub_agent"
    return "synthesize"


def _should_execute(state: AgentState) -> str:
    """Legacy router kept for backward compatibility (unused in new graph)."""
    return "synthesize"


# ── build graph ────────────────────────────────────────────────────


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("init", init_node)
    builder.add_node("load_memories_and_knowledge", load_memories_and_knowledge_node)
    builder.add_node("plan", plan_node)
    builder.add_node("orchestrate", orchestrator_node)
    builder.add_node("exec_task", exec_task_node)
    builder.add_node("dispatch_sub_agent", dispatch_sub_agent_node)
    builder.add_node("synthesize", synthesize_node)
    builder.add_node("final_answer", final_answer_node)

    builder.set_entry_point("init")

    builder.add_edge("init", "load_memories_and_knowledge")
    builder.add_edge("load_memories_and_knowledge", "plan")
    builder.add_edge("plan", "orchestrate")

    builder.add_conditional_edges(
        "orchestrate", _orchestrate_router,
        {
            "exec_task": "exec_task",
            "dispatch_sub_agent": "dispatch_sub_agent",
            "synthesize": "synthesize",
        },
    )

    builder.add_edge("exec_task", "synthesize")
    builder.add_edge("dispatch_sub_agent", "synthesize")
    builder.add_edge("synthesize", "final_answer")
    builder.add_edge("final_answer", END)

    return builder.compile()


agent_graph = build_graph()
