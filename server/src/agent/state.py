from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    session_id: str
    plan: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    final_answer: str
    iteration: int

    # Enhanced capabilities
    memories: list[dict]  # loaded from memory_service
    memory_tags: list[str]  # tags extracted from loaded memories
    knowledge_context: str  # retrieved knowledge context
    sub_agent_outputs: dict[str, Any]  # outputs from sub-agents
    current_sub_agent: str | None  # which sub-agent is active
    orchestration_decision: str  # "tool", "sub_agent", "knowledge", "direct"
