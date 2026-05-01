"""Request-scoped context variables for agent tools and handlers.

Context variables propagate user/space/session from FastAPI request handlers
into agent tools without threading concerns in async context.
"""

import contextvars

_current_user_ctx: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "current_user", default={}
)


def set_current_user(
    user_id: str,
    session_id: str = "",
    username: str = "",
    email: str = "",
    roles: list[str] | None = None,
) -> None:
    """Set the current user context for tool access and personalization."""
    _current_user_ctx.set({
        "user_id": user_id,
        "session_id": session_id,
        "username": username,
        "email": email,
        "roles": roles or [],
    })


def get_current_user() -> dict[str, str]:
    """Get the current user context (safe to call from tools)."""
    return _current_user_ctx.get()


def set_current_space(space_id: str = "", space_name: str = "", space_role: str = "") -> None:
    """Set the current space context."""
    ctx = _current_user_ctx.get()
    ctx["space_id"] = space_id
    ctx["space_name"] = space_name
    ctx["space_role"] = space_role
    _current_user_ctx.set(ctx)


def get_current_space() -> dict[str, str]:
    """Get the current space context."""
    ctx = _current_user_ctx.get()
    return {
        "space_id": ctx.get("space_id", ""),
        "space_name": ctx.get("space_name", ""),
        "space_role": ctx.get("space_role", ""),
    }
