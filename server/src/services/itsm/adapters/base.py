"""ITSM adapter abstract base — uniform interface for external ITSM systems."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TicketData:
    external_id: str = ""
    ticket_type: str = "incident"
    title: str = ""
    description: str = ""
    status: str = "new"
    priority: str = "medium"
    affected_service: str = ""
    assignee: str = ""
    custom_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class TicketResult:
    success: bool
    external_id: str = ""
    url: str = ""
    message: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict)


class ItsmAdapter(ABC):
    """Abstract adapter for external ITSM system operations."""

    @abstractmethod
    async def create_ticket(self, ticket: TicketData) -> TicketResult:
        """Create a new ticket in the external ITSM system."""

    @abstractmethod
    async def update_ticket(self, external_id: str, updates: dict[str, Any]) -> TicketResult:
        """Update fields on an existing ticket."""

    @abstractmethod
    async def get_ticket(self, external_id: str) -> TicketResult:
        """Fetch current state of a ticket."""

    @abstractmethod
    async def transition_state(self, external_id: str, new_status: str, comment: str = "") -> TicketResult:
        """Transition ticket to a new status/workflow state."""

    @abstractmethod
    async def add_comment(self, external_id: str, comment: str) -> TicketResult:
        """Add a comment/note to a ticket."""

    @abstractmethod
    async def close_ticket(self, external_id: str, resolution: str = "") -> TicketResult:
        """Close/resolve a ticket with optional resolution note."""

    @abstractmethod
    async def test_connection(self) -> TicketResult:
        """Verify connectivity and credentials."""
