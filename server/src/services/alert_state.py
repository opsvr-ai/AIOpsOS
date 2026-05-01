"""Alert state machine — simple transition table.

Flow: pending -> analyzing -> awaiting_review -> confirmed/dismissed -> closed
"""

VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"analyzing", "dismissed"},
    "analyzing": {"awaiting_review", "dismissed"},
    "awaiting_review": {"confirmed", "dismissed"},
    "confirmed": {"closed"},
    "dismissed": {"closed"},
    "closed": set(),
}

ALLOWED_ACTIONS: dict[str, list[str]] = {
    "pending": ["analyze", "dismiss"],
    "analyzing": ["dismiss"],
    "awaiting_review": ["confirm", "dismiss"],
    "confirmed": ["close"],
    "dismissed": ["close"],
    "closed": [],
}

ACTION_TO_STATUS: dict[str, str] = {
    "analyze": "analyzing",
    "confirm": "confirmed",
    "dismiss": "dismissed",
    "close": "closed",
}


def validate_transition(current: str, new: str) -> bool:
    """Return True if transition from current to new status is valid."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return new in allowed


def validate_action(current: str, action: str) -> bool:
    """Return True if the action is allowed on an alert with current status."""
    allowed = ALLOWED_ACTIONS.get(current, [])
    return action in allowed
