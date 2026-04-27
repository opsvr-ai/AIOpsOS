from src.agent.state import AgentState


async def init_node(state: AgentState) -> dict:
    return {
        "plan": [],
        "tool_results": [],
        "final_answer": "",
        "iteration": state.get("iteration", 0) + 1,
        "memories": [],
        "knowledge_context": "",
        "sub_agent_outputs": {},
        "current_sub_agent": None,
        "orchestration_decision": "direct",
    }
