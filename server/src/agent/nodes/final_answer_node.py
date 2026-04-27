from langchain_core.messages import AIMessage

from src.agent.state import AgentState


async def final_answer_node(state: AgentState) -> dict:
    answer = state.get("final_answer", "")
    if not answer:
        answer = "No results produced."
    return {"messages": [AIMessage(content=answer)]}
