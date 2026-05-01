"""Base sub-agent class.

Each sub-agent is a self-contained LangChain node that can be invoked
by the main orchestrator with a specific task description.
Sub-agents can enrich their context with real data from the database.
"""

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage


class BaseSubAgent(ABC):
    """Abstract base for specialized sub-agents."""

    name: str = ""
    description: str = ""
    system_prompt: str = ""

    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._llm = model

    async def _get_llm(self):
        """Lazy-init the LLM from the platform's configured ModelProvider."""
        if self._llm is None:
            from src.core.model_factory import get_default_model
            self._llm = await get_default_model()
        return self._llm

    @abstractmethod
    async def __call__(self, task: str, context: dict[str, Any] | None = None) -> str:
        """Execute the sub-agent on a given task."""
        ...

    async def _fetch_real_data(self, task: str) -> str:
        """Override in subclasses to inject real DB data into the prompt.

        Returns a formatted string with relevant data from the database.
        """
        return ""

    def _build_messages(self, task: str, context: dict[str, Any] | None = None) -> list:
        msgs = [SystemMessage(content=self.system_prompt)]
        if context:
            ctx_str = "\n".join(f"{k}: {v}" for k, v in context.items())
            msgs.append(SystemMessage(content=f"Additional context:\n{ctx_str}"))
        msgs.append(HumanMessage(content=task))
        return msgs
