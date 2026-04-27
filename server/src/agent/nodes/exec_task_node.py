import json

from langchain_core.messages import AIMessage

from src.agent.state import AgentState
from src.services.tool_manager import tool_manager


async def exec_task_node(state: AgentState) -> dict:
    """Execute each step in the plan using registered tools."""
    plan = state.get("plan", [])
    results: list[dict] = []
    messages: list = []

    for step in plan:
        tool_name = step.get("tool", "")
        step_args = step.get("args", {})
        tool = tool_manager.get_tool(tool_name)
        if tool is None:
            output = f"Tool '{tool_name}' not found"
        else:
            try:
                output = await tool.ainvoke(step_args)
            except Exception as exc:
                output = f"Tool error: {exc}"

        results.append({"step": step.get("step"), "tool": tool_name, "output": output})
        messages.append(
            AIMessage(content=f"[{tool_name}] {output}")
        )

    return {"tool_results": results, "messages": messages}
