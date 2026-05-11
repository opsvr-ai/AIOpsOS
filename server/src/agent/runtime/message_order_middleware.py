"""Middleware to ensure system message is always at the beginning.

Some OpenAI-compatible APIs (e.g., certain Chinese LLM providers) require
the system message to be the first message in the conversation. The
summarization middleware from deepagents may reorder messages during
truncation, causing API errors.

This middleware runs after summarization and ensures the system message
is moved to the front if it's not already there.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)


class MessageOrderMiddleware(AgentMiddleware):
    """Ensures system message is always at the beginning of the message list.
    
    This middleware should be placed AFTER summarization middleware to fix
    any message reordering that may have occurred during truncation.
    """

    def wrap_model_call(
        self,
        handler: Any,
    ) -> Any:
        """Wrap sync model calls to fix message order."""

        def wrapper(request: ModelRequest, inner_handler: Any) -> ModelResponse:
            fixed_request = self._fix_message_order(request)
            return inner_handler(fixed_request)

        return wrapper

    async def awrap_model_call(
        self,
        handler: Any,
    ) -> Any:
        """Wrap async model calls to fix message order."""

        async def wrapper(request: ModelRequest, inner_handler: Any) -> ModelResponse:
            fixed_request = self._fix_message_order(request)
            return await inner_handler(fixed_request)

        return wrapper

    def _fix_message_order(self, request: ModelRequest) -> ModelRequest:
        """Ensure system message is at the beginning of messages list.
        
        If the request has a system_message attribute, it will be handled
        by LangChain. This method handles cases where system messages
        might be embedded in the messages list itself.
        """
        messages = list(request.messages) if request.messages else []
        
        if not messages:
            return request
        
        # Find system messages that are not at the beginning
        system_messages = []
        other_messages = []
        
        for i, msg in enumerate(messages):
            if isinstance(msg, SystemMessage):
                if i == 0:
                    # System message is already at the beginning, no fix needed
                    return request
                system_messages.append(msg)
            else:
                other_messages.append(msg)
        
        if not system_messages:
            # No system messages in the list, nothing to fix
            return request
        
        # Reorder: system messages first, then others
        fixed_messages = system_messages + other_messages
        
        logger.debug(
            "MessageOrderMiddleware: reordered %d system message(s) to front",
            len(system_messages),
        )
        
        return request.override(messages=fixed_messages)


__all__ = ["MessageOrderMiddleware"]
