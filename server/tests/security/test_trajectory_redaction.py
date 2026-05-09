"""Trajectory-sink redaction tests — R-8.2.

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 6.2 + 27.2 /
R-8.2.

> before writing ``agent_trajectories.data``, known sensitive fields
> (``api_key``, ``password``, ``token``) in ``tool_args`` SHALL be hashed
> (not stored in cleartext).

The guard lives in :meth:`TrajectorySink._sanitise_event`, which wraps
:func:`src.services.pii.sanitize_pii` over every event's ``data`` and
``metadata`` before rows are converted for insert. These tests exercise
that pipeline end-to-end — from a realistic ``tool_call`` event built
with :class:`TrajectoryEvent`, through ``_sanitise_event``, into the
row dict that SQLAlchemy bulk-inserts. No network I/O is required.

Validates: Requirements R-8.2
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from src.schemas.trajectory import TrajectoryEvent
from src.services.agent_runtime.trajectory import (
    TrajectorySink,
    _event_to_row,
)

# Existing sanitiser emits "<sha256:XXXX...>" placeholders and collapses
# None / empty strings to "<redacted>". R-8.2 says "hashed (not stored in
# cleartext)" — both placeholders satisfy that contract.
_PLACEHOLDER_RE = re.compile(r"^(?:<sha256:[0-9a-f]{6,}>|<redacted>)$")


def _make_tool_call_event(args: dict) -> TrajectoryEvent:
    """Build a ``tool_call`` event whose ``data.tool_args`` is *args*."""
    return TrajectoryEvent(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        kind="tool_call",
        ts=datetime.now(tz=timezone.utc),
        outcome="ok",
        data={"tool_name": "http_request", "tool_args": args},
    )


# ---------------------------------------------------------------------------
# Top-level sensitive fields
# ---------------------------------------------------------------------------


def test_api_key_in_tool_args_is_redacted_before_storage() -> None:
    event = _make_tool_call_event({"api_key": "sk-live-SECRET-abc123", "url": "/v1/x"})

    sanitised = TrajectorySink._sanitise_event(event)

    redacted = sanitised.data["tool_args"]["api_key"]
    assert _PLACEHOLDER_RE.match(redacted), f"unexpected placeholder: {redacted!r}"
    assert "SECRET" not in redacted
    assert sanitised.data["tool_args"]["url"] == "/v1/x"

    # Row we'd bulk-insert must not contain the cleartext secret either.
    row = _event_to_row(sanitised)
    assert "sk-live-SECRET-abc123" not in str(row["data"])


def test_password_in_tool_args_is_redacted() -> None:
    event = _make_tool_call_event({"password": "hunter2!", "username": "alice"})

    sanitised = TrajectorySink._sanitise_event(event)

    assert _PLACEHOLDER_RE.match(sanitised.data["tool_args"]["password"])
    assert sanitised.data["tool_args"]["username"] == "alice"


def test_token_in_tool_args_is_redacted() -> None:
    event = _make_tool_call_event({"token": "tkn_live_XYZ", "endpoint": "/chat"})

    sanitised = TrajectorySink._sanitise_event(event)

    assert _PLACEHOLDER_RE.match(sanitised.data["tool_args"]["token"])
    assert "tkn_live_XYZ" not in str(_event_to_row(sanitised)["data"])


# ---------------------------------------------------------------------------
# Nested / deeper containers
# ---------------------------------------------------------------------------


def test_nested_token_is_redacted() -> None:
    event = _make_tool_call_event(
        {
            "headers": {
                "Content-Type": "application/json",
                "x-auth": {"token": "nested-TOP-SECRET"},
            },
            "method": "POST",
        }
    )

    sanitised = TrajectorySink._sanitise_event(event)

    inner = sanitised.data["tool_args"]["headers"]["x-auth"]["token"]
    assert _PLACEHOLDER_RE.match(inner)
    assert "TOP-SECRET" not in str(_event_to_row(sanitised)["data"])
    # Non-sensitive siblings untouched.
    assert sanitised.data["tool_args"]["headers"]["Content-Type"] == "application/json"
    assert sanitised.data["tool_args"]["method"] == "POST"


def test_secret_in_list_of_dicts_is_redacted() -> None:
    event = _make_tool_call_event(
        {
            "credentials": [
                {"name": "primary", "api_key": "KEY-1"},
                {"name": "backup", "api_key": "KEY-2"},
            ]
        }
    )

    sanitised = TrajectorySink._sanitise_event(event)

    creds = sanitised.data["tool_args"]["credentials"]
    assert _PLACEHOLDER_RE.match(creds[0]["api_key"])
    assert _PLACEHOLDER_RE.match(creds[1]["api_key"])
    assert creds[0]["name"] == "primary"
    assert creds[1]["name"] == "backup"


# ---------------------------------------------------------------------------
# Case-insensitive matching
# ---------------------------------------------------------------------------


def test_case_insensitive_redaction_of_common_variants() -> None:
    event = _make_tool_call_event(
        {
            "Authorization": "Bearer ey.JWT.sig",
            "api_KEY": "mixed-case-secret",
            "Password": "P@ss",
            "filename": "report.csv",
            "query": "status=ok",
        }
    )

    sanitised = TrajectorySink._sanitise_event(event)
    args = sanitised.data["tool_args"]

    for k in ("Authorization", "api_KEY", "Password"):
        assert _PLACEHOLDER_RE.match(args[k]), f"{k} not redacted: {args[k]!r}"

    # Clean fields stay unmodified.
    assert args["filename"] == "report.csv"
    assert args["query"] == "status=ok"

    # None of the original secret values should survive anywhere in the row.
    row_str = str(_event_to_row(sanitised)["data"])
    for leak in ("Bearer ey.JWT.sig", "mixed-case-secret", "P@ss"):
        assert leak not in row_str, f"leaked value found in row: {leak!r}"


# ---------------------------------------------------------------------------
# Non-sensitive payloads
# ---------------------------------------------------------------------------


def test_clean_tool_args_pass_through_unchanged() -> None:
    clean = {
        "query": "SELECT 1",
        "filename": "/tmp/out.txt",
        "limit": 50,
        "options": {"verbose": True, "timeout_ms": 2000},
    }
    event = _make_tool_call_event(clean)

    sanitised = TrajectorySink._sanitise_event(event)

    # Deep-equal with the input — nothing was rewritten.
    assert sanitised.data["tool_args"] == clean
    assert sanitised.data["tool_name"] == "http_request"
    # Source event never mutated in place.
    assert event.data["tool_args"] == clean


# ---------------------------------------------------------------------------
# Metadata path (sanitiser covers both ``data`` and ``metadata``)
# ---------------------------------------------------------------------------


def test_metadata_sensitive_fields_are_redacted() -> None:
    event = TrajectoryEvent(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        kind="tool_call",
        ts=datetime.now(tz=timezone.utc),
        outcome="ok",
        data={"tool_name": "mcp_call", "tool_args": {"q": "ok"}},
        metadata={"auth": {"api_key": "meta-SECRET"}, "trace_id": "t-1"},
    )

    sanitised = TrajectorySink._sanitise_event(event)

    assert _PLACEHOLDER_RE.match(sanitised.metadata["auth"]["api_key"])
    assert sanitised.metadata["trace_id"] == "t-1"
    assert "meta-SECRET" not in str(_event_to_row(sanitised)["data"])


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_sanitiser_is_idempotent_on_already_scrubbed_data() -> None:
    event = _make_tool_call_event(
        {
            "api_key": "live-key-1",
            "nested": {"password": "p1"},
            "filename": "keep.txt",
        }
    )

    once = TrajectorySink._sanitise_event(event)
    twice = TrajectorySink._sanitise_event(once)

    # Dict-level equality is the property: running the sanitiser again on
    # an already-scrubbed event must not mutate placeholders into a
    # different shape or re-hash them to a new value.
    assert once.data == twice.data
    assert once.metadata == twice.metadata

    # Spot-check: the placeholder is stable (hash of input is
    # deterministic in the underlying helper).
    assert once.data["tool_args"]["api_key"] == twice.data["tool_args"]["api_key"]
    assert (
        once.data["tool_args"]["nested"]["password"]
        == twice.data["tool_args"]["nested"]["password"]
    )
    assert twice.data["tool_args"]["filename"] == "keep.txt"


def test_deterministic_hash_for_same_value() -> None:
    """Same cleartext → same placeholder across events (determinism)."""
    e1 = _make_tool_call_event({"api_key": "shared-secret"})
    e2 = _make_tool_call_event({"api_key": "shared-secret"})

    s1 = TrajectorySink._sanitise_event(e1)
    s2 = TrajectorySink._sanitise_event(e2)

    assert s1.data["tool_args"]["api_key"] == s2.data["tool_args"]["api_key"]


def test_distinct_values_produce_distinct_placeholders() -> None:
    e1 = _make_tool_call_event({"api_key": "secret-A"})
    e2 = _make_tool_call_event({"api_key": "secret-B"})

    s1 = TrajectorySink._sanitise_event(e1)
    s2 = TrajectorySink._sanitise_event(e2)

    assert s1.data["tool_args"]["api_key"] != s2.data["tool_args"]["api_key"]
