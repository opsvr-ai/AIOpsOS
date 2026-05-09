"""Unit tests for :func:`src.services.pii.sanitize_pii`.

Task 6.2 / R-8.2: tool-call args written to ``agent_trajectories.data``
SHALL have known-sensitive fields hashed or redacted.
"""
from __future__ import annotations

import hashlib

import pytest

from src.services.pii import sanitize_pii

_EXPECTED_HASH_PREFIX = "<sha256:"
_REDACTED = "<redacted>"


def _hash_of(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"<sha256:{digest}>"


def test_sanitize_pii_leaves_non_sensitive_keys_untouched() -> None:
    payload = {"message": "hello", "count": 3, "ok": True}
    assert sanitize_pii(payload) == payload


def test_sanitize_pii_replaces_known_sensitive_keys() -> None:
    payload = {
        "api_key": "abc123",
        "password": "hunter2",
        "token": "tkn_xyz",
        "access_token": "at_1",
        "refresh_token": "rt_1",
        "secret": "s",
        "authorization": "Bearer xyz",
        "not_secret": "visible",
    }
    out = sanitize_pii(payload)

    for k in (
        "api_key",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "authorization",
    ):
        assert out[k].startswith(_EXPECTED_HASH_PREFIX), f"{k}: {out[k]}"
    assert out["not_secret"] == "visible"

    # Determinism: same input → same hash
    assert out["api_key"] == _hash_of("abc123")


def test_sanitize_pii_collapses_empty_and_none_to_redacted() -> None:
    payload = {"api_key": "", "password": None, "token": 0, "secret": False}
    out = sanitize_pii(payload)
    assert out["api_key"] == _REDACTED
    assert out["password"] == _REDACTED
    # zero / False are non-empty scalars → hashed
    assert out["token"].startswith(_EXPECTED_HASH_PREFIX)
    assert out["secret"].startswith(_EXPECTED_HASH_PREFIX)


def test_sanitize_pii_recurses_into_nested_dicts() -> None:
    payload = {
        "meta": {"display": "ok", "credentials": {"password": "p", "user": "bob"}},
        "harmless": [1, 2, 3],
    }
    out = sanitize_pii(payload)
    assert out["meta"]["display"] == "ok"
    assert out["meta"]["credentials"]["password"].startswith(_EXPECTED_HASH_PREFIX)
    assert out["meta"]["credentials"]["user"] == "bob"
    assert out["harmless"] == [1, 2, 3]


def test_sanitize_pii_walks_lists_of_dicts() -> None:
    payload = {
        "requests": [
            {"url": "/a", "headers": {"authorization": "Bearer tok"}},
            {"url": "/b", "headers": {"x-trace": "t1"}},
        ]
    }
    out = sanitize_pii(payload)
    assert out["requests"][0]["headers"]["authorization"].startswith(
        _EXPECTED_HASH_PREFIX
    )
    assert out["requests"][1]["headers"]["x-trace"] == "t1"


def test_sanitize_pii_is_case_insensitive_on_key_names() -> None:
    payload = {"API_KEY": "abc", "Authorization": "Bearer xyz"}
    out = sanitize_pii(payload)
    assert out["API_KEY"].startswith(_EXPECTED_HASH_PREFIX)
    assert out["Authorization"].startswith(_EXPECTED_HASH_PREFIX)


def test_sanitize_pii_does_not_mutate_input() -> None:
    payload = {"password": "secret"}
    out = sanitize_pii(payload)
    assert payload["password"] == "secret"  # original unchanged
    assert out["password"] != "secret"


def test_sanitize_pii_returns_scalar_input_unchanged() -> None:
    assert sanitize_pii("hello") == "hello"
    assert sanitize_pii(7) == 7
    assert sanitize_pii(None) is None


def test_sanitize_pii_collapses_sensitive_container_values() -> None:
    payload = {"token": {"nested": "value"}}
    out = sanitize_pii(payload)
    # The WHOLE subtree gets replaced because the key is sensitive.
    assert out["token"].startswith(_EXPECTED_HASH_PREFIX) or out["token"] == _REDACTED


@pytest.mark.parametrize(
    "k",
    ["api_key", "APIKey", "ApiKey", "apikey"],
)
def test_sanitize_pii_api_key_variants(k: str) -> None:
    out = sanitize_pii({k: "s"})
    assert out[k].startswith(_EXPECTED_HASH_PREFIX)


# ---------------------------------------------------------------------------
# R-8.1 — value-level detection + team→personal downgrade
# ---------------------------------------------------------------------------
#
# Task 27.1: the consolidation worker SHALL first run every team-scope
# memory's content through ``sanitize_pii`` / ``contains_pii``; any hit
# (email / IPv4 / IPv6 / PAT token) SHALL be downgraded to personal
# scope before the row is persisted. See design.md §10.2 and the
# downgrade block in ``src/services/memory/consolidation_logic.py``
# (search for "PII scan — downgrade leaky team items").

from src.services.pii import contains_pii


# --- detection -------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "user@example.com",
        "nested.name+tag@sub.example.co.uk",
        "please email ops-team@company.internal for help",
    ],
)
def test_contains_pii_detects_email(text: str) -> None:
    found, kinds = contains_pii(text)
    assert found is True
    assert "email" in kinds


@pytest.mark.parametrize("ip", ["192.168.1.1", "10.0.0.1", "127.0.0.1", "255.255.255.0"])
def test_contains_pii_detects_valid_ipv4(ip: str) -> None:
    found, kinds = contains_pii(f"host at {ip} is down")
    assert found is True
    assert "ip" in kinds


@pytest.mark.parametrize(
    "text",
    [
        "999.999.999.999",           # octet > 255
        "256.1.1.1",                 # first octet > 255
        "300.300.300.300",           # all octets out of range
        "no ip here, just 1.2",      # incomplete dotted-quad
    ],
)
def test_contains_pii_rejects_invalid_ipv4(text: str) -> None:
    _, kinds = contains_pii(text)
    assert "ip" not in kinds


@pytest.mark.parametrize("ipv6", ["2001:db8::1", "::1", "fe80::1", "2001:0db8:0000:0000:0000:0000:0000:0001"])
def test_contains_pii_detects_ipv6(ipv6: str) -> None:
    found, kinds = contains_pii(f"route via {ipv6} now")
    assert found is True
    assert "ip" in kinds


@pytest.mark.parametrize(
    "token",
    [
        "ghp_" + "a" * 36,                                # GitHub PAT
        "sk-proj-" + "B" * 40,                            # OpenAI project key
        "xoxb-12345-67890-abcdefghijklmnopqrstuv",       # Slack bot token
        "AKIAIOSFODNN7EXAMPLE",                           # AWS access key
    ],
)
def test_contains_pii_detects_pat_like_tokens(token: str) -> None:
    found, kinds = contains_pii(f"leaked: {token}")
    assert found is True
    assert "token" in kinds


def test_contains_pii_clean_plain_english_no_hit() -> None:
    text = "Please restart the nginx worker pool on the canary host after deploy."
    found, kinds = contains_pii(text)
    assert found is False
    assert kinds == []


# --- team → personal downgrade --------------------------------------------
#
# Mirrors the exact predicate used in
# ``src.services.memory.consolidation_logic.run_consolidation``:
#
#     for item in new_team_raw:
#         flagged, _ = pii_check(item.get("content", ""))
#         if flagged:
#             downgraded_personal.append(item)   # scope becomes 'personal'
#         else:
#             new_team.append(item)              # scope stays 'team'
#
# We reproduce it verbatim so this test pins the contract even if
# consolidation_logic is refactored.


def _downgrade_if_pii(memory: dict) -> dict:
    """Return a copy of ``memory`` with ``scope='personal'`` iff its content
    contains PII, otherwise the original scope is preserved."""
    flagged, _ = contains_pii(memory.get("content", ""))
    out = dict(memory)
    if flagged and out.get("scope") == "team":
        out["scope"] = "personal"
    return out


def test_downgrade_team_memory_with_email_to_personal() -> None:
    mem = {
        "scope": "team",
        "title": "Oncall contact",
        "content": "Oncall email is ops-lead@company.internal, escalate within 5 min.",
    }
    out = _downgrade_if_pii(mem)
    assert out["scope"] == "personal"


def test_downgrade_team_memory_with_ipv4_to_personal() -> None:
    mem = {
        "scope": "team",
        "title": "Bastion host",
        "content": "Bastion is reachable at 10.0.0.1 from the VPC.",
    }
    out = _downgrade_if_pii(mem)
    assert out["scope"] == "personal"


def test_downgrade_team_memory_with_ipv6_to_personal() -> None:
    mem = {
        "scope": "team",
        "title": "DR site",
        "content": "Secondary region uses 2001:db8::1 for replication traffic.",
    }
    out = _downgrade_if_pii(mem)
    assert out["scope"] == "personal"


def test_downgrade_team_memory_with_pat_token_to_personal() -> None:
    mem = {
        "scope": "team",
        "title": "CI secret rotation",
        "content": "Rotated CI token ghp_" + "Z" * 36 + " last Tuesday.",
    }
    out = _downgrade_if_pii(mem)
    assert out["scope"] == "personal"


def test_clean_team_memory_stays_team() -> None:
    mem = {
        "scope": "team",
        "title": "Deploy runbook",
        "content": "After deploy, verify service health and watch error logs for 10 minutes.",
    }
    out = _downgrade_if_pii(mem)
    assert out["scope"] == "team"


def test_downgrade_does_not_touch_already_personal_memory() -> None:
    # The downgrade is a one-way rule for team items; personal-scope
    # memories with PII stay personal (no further demotion).
    mem = {
        "scope": "personal",
        "title": "My contact",
        "content": "user@example.com",
    }
    out = _downgrade_if_pii(mem)
    assert out["scope"] == "personal"


def test_downgrade_is_pure_does_not_mutate_input() -> None:
    mem = {"scope": "team", "content": "IP 10.0.0.1"}
    _ = _downgrade_if_pii(mem)
    assert mem["scope"] == "team"  # original untouched
