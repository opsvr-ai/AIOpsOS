"""InterruptManager — manages human-in-the-loop interrupt state.

Interrupts are stored in-memory keyed by session_id. When the agent calls
a human_interrupt tool, the tool stores the interrupt here and the SSE
stream emits an interrupt event. The frontend displays the appropriate
card (SecurityConfirmCard or InlineParameterForm). When the user responds,
the interrupt is resolved and the agent resumes.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)

InterruptType = Literal["approval", "form"]


@dataclass
class Interrupt:
    id: str
    session_id: str
    type: InterruptType
    data: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved: bool = False
    response: dict[str, Any] | None = None
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self, timeout: float = 300) -> dict[str, Any] | None:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return self.response
        except TimeoutError:
            return None

    def resolve(self, response: dict[str, Any]) -> None:
        self.response = response
        self.resolved = True
        self._event.set()


class InterruptManager:
    def __init__(self) -> None:
        self._interrupts: dict[str, Interrupt] = {}

    def create(
        self,
        session_id: str,
        interrupt_type: InterruptType,
        data: dict[str, Any],
    ) -> Interrupt:
        import uuid

        interrupt = Interrupt(
            id=str(uuid.uuid4()),
            session_id=session_id,
            type=interrupt_type,
            data=data,
        )
        self._interrupts[interrupt.id] = interrupt
        logger.info("Interrupt created: id=%s type=%s session=%s", interrupt.id, interrupt_type, session_id)
        return interrupt

    def get(self, interrupt_id: str) -> Interrupt | None:
        return self._interrupts.get(interrupt_id)

    def get_pending_for_session(self, session_id: str) -> Interrupt | None:
        for interrupt in self._interrupts.values():
            if interrupt.session_id == session_id and not interrupt.resolved:
                return interrupt
        return None

    def resolve(self, interrupt_id: str, response: dict[str, Any]) -> bool:
        interrupt = self._interrupts.get(interrupt_id)
        if interrupt is None or interrupt.resolved:
            return False
        interrupt.resolve(response)
        return True

    def cleanup_session(self, session_id: str) -> None:
        to_remove = [k for k, v in self._interrupts.items() if v.session_id == session_id]
        for k in to_remove:
            del self._interrupts[k]


interrupt_manager = InterruptManager()
