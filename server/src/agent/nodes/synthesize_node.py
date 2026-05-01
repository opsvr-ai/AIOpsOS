import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.state import AgentState
from src.core.model_factory import get_default_model
from src.services.tool_manager import tool_manager


async def synthesize_node(state: AgentState) -> dict:
    """Synthesize tool results / sub-agent outputs into a final answer."""
    llm = await get_default_model()

    user_msg = state["messages"][0].content if state["messages"] else ""
    tool_results = state.get("tool_results", [])
    sub_agent_outputs = state.get("sub_agent_outputs", {})
    memories = state.get("memories", [])
    knowledge = state.get("knowledge_context", "")

    parts = [f"User request: {user_msg}"]

    if tool_results:
        parts.append(f"\nTool results:\n{json.dumps(tool_results, ensure_ascii=False, indent=2)}")

    if sub_agent_outputs:
        parts.append(f"\nSub-agent outputs:\n{json.dumps(sub_agent_outputs, ensure_ascii=False, indent=2)}")

    tool_descriptions = tool_manager.describe_tools()

    system = (
        "You are the voice of AIOpsOS — the storyteller who weaves scattered threads "
        "of data into a tapestry of clarity.\n"
        "Like a seasoned navigator recounting the journey, you take tool results, "
        "sub-agent insights, and raw outputs, then distill them into a response that "
        "illuminates rather than overwhelms. When the path was smooth, you celebrate. "
        "When obstacles arose, you speak truth without flinching.\n\n"
        "Synthesize the available information into a clear, actionable response in "
        "Chinese. Cite specific outputs when relevant. If something failed or was not "
        "found, explain that honestly.\n\n"
        "When the user asks about available tools or skills, reference the actual "
        "registered tools below. Do NOT invent capabilities that aren't listed.\n\n"
        f"Registered system tools:\n{tool_descriptions}"
    )

    context_parts = []
    if memories:
        mem_text = "\n".join(
            f"- {m.get('content', '')[:200]}" for m in memories[:5]
        )
        context_parts.append(f"Relevant memories:\n{mem_text}")
    if knowledge:
        context_parts.append(f"Knowledge base context:\n{knowledge[:1000]}")

    if context_parts:
        system += "\n\n" + "\n\n".join(context_parts)

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content="\n".join(parts)),
    ])

    return {"messages": [response], "final_answer": response.content}
