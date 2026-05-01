import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agent.state import AgentState
from src.core.model_factory import get_default_model
from src.services.tool_manager import tool_manager


async def plan_node(state: AgentState) -> dict:
    """Analyze user intent with memories & knowledge, produce a step-by-step plan."""
    llm = await get_default_model()
    available = tool_manager.describe_tools() or "(no tools registered)"

    user_msg = state["messages"][-1].content if state["messages"] else ""
    memories = state.get("memories", [])
    knowledge = state.get("knowledge_context", "")

    system = (
        "You are the cartographer of AIOpsOS — the strategic mind that charts the course "
        "before the first step is taken.\n"
        "Like a master architect who envisions the entire structure from a single request, "
        "you decompose intention into action, matching tools to tasks with the precision "
        "of a watchmaker assembling a delicate mechanism.\n\n"
        "RULES:\n"
        "1. If the user asks about knowledge base content (查询/搜索/查找知识库), "
        "ALWAYS call `grep_kb` with the search keywords as `query`.\n"
        "2. If the user asks to save or organize knowledge (保存/整理/记录), "
        "call `write_wiki` with `filename` and `content`.\n"
        "3. If the user explicitly asks about a skill (llm-wiki/knowledge), "
        "call that skill with the request as `task`.\n"
        "4. If the user just chats (打招呼/闲聊), return an empty plan [].\n"
        "5. For alerts/monitoring, use `alert_query` or other relevant tools.\n\n"
        "Respond with ONLY a valid JSON array of steps, each with:\n"
        '  {"step": <int>, "tool": "<tool_name>", "args": {<key>: <value>}}\n\n'
        f"Available tools:\n{available}"
    )

    context_parts = [f"User request: {user_msg}"]
    if memories:
        mem_text = "\n".join(f"- {m.get('content', '')[:200]}" for m in memories[:5])
        context_parts.append(f"Relevant memories:\n{mem_text}")
    if knowledge:
        context_parts.append(f"Knowledge base context:\n{knowledge[:1000]}")

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content="\n\n".join(context_parts)),
    ])

    plan: list[dict] = []
    try:
        text = response.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text)
        if isinstance(parsed, list):
            plan = parsed
        elif isinstance(parsed, dict) and "steps" in parsed:
            plan = parsed["steps"]
    except (json.JSONDecodeError, KeyError):
        plan = []

    return {
        "plan": plan,
        "messages": [response],
    }
