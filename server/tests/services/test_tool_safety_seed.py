"""Unit tests for the ToolDispatcher safety seed (Phase H / Task 16.1).

These exercise only the in-memory seed path of ``ToolManager``; no DB or
Redis required. Covers:

* The built-in seed list matches design.md § ToolDispatcher.
* ``get_parallel_safe_tools`` is a superset of the design's parallel-safe set.
* Idempotent reseeding preserves runtime overrides (``set_safety`` wins).

Requirements: R-1.7.
"""
from __future__ import annotations

import pytest

from src.services.tool_manager import (
    DEFAULT_SAFETY,
    DESTRUCTIVE,
    SAFE_PARALLEL,
    ToolManager,
    _BUILTIN_SAFETY_SEED,
)


# Values taken verbatim from design.md § ToolDispatcher.
_DESIGN_PARALLEL_SAFE: frozenset[str] = frozenset({
    "grep_kb",
    "read_wiki",
    "list_wiki",
    "memory_retrieve",
    "list_cron_jobs",
    "get_config",
    "list_datasources",
    "query_cmdb_nodes",
    "search_logs",
    "count_logs",
    "search_tickets",
    "get_ticket_detail",
})

_DESIGN_DESTRUCTIVE: frozenset[str] = frozenset({
    "execute",
    "write_wiki",
    "write_raw",
    "cron_create",
    "sync_datasource",
})


def test_seed_safety_from_defaults():
    """After seeding, every classification should match the design table."""
    tm = ToolManager()
    tm.seed_safety_from_defaults()

    # Specific spot-checks required by DoD §5.
    assert tm.get_safety("grep_kb") == SAFE_PARALLEL
    assert tm.get_safety("execute") == DESTRUCTIVE
    # Unseeded names fall back to the module default (sequential).
    assert tm.get_safety("never_seen") == DEFAULT_SAFETY == "sequential"

    assert tm.is_destructive("write_wiki") is True
    assert tm.is_destructive("grep_kb") is False

    # Every entry in _BUILTIN_SAFETY_SEED lands in the in-memory dict.
    for name, expected in _BUILTIN_SAFETY_SEED.items():
        assert tm.get_safety(name) == expected, (
            f"seed entry {name!r} classification mismatch"
        )


def test_get_parallel_safe_tools_contains_seeded():
    """get_parallel_safe_tools() ⊇ design.md's parallel-safe set."""
    tm = ToolManager()
    tm.seed_safety_from_defaults()

    parallel = set(tm.get_parallel_safe_tools())
    missing = _DESIGN_PARALLEL_SAFE - parallel
    assert not missing, (
        f"seed missed design-level parallel-safe tools: {sorted(missing)}"
    )

    # Destructive tools must *not* leak into the parallel-safe list.
    leaked = _DESIGN_DESTRUCTIVE & parallel
    assert not leaked, f"destructive tools leaked into parallel-safe list: {leaked}"


def test_reload_preserves_custom_safety():
    """Runtime ``set_safety`` overrides survive a re-seed (idempotent).

    This guards against a reload clobbering classifications that were
    set manually (e.g. by DB hydration or an admin override).
    """
    tm = ToolManager()
    tm.set_safety("my_custom_tool", DESTRUCTIVE)
    # First seed should not touch 'my_custom_tool'.
    tm.seed_safety_from_defaults()
    assert tm.get_safety("my_custom_tool") == DESTRUCTIVE

    # Second seed (simulating a reload) must also leave it alone.
    tm.seed_safety_from_defaults()
    assert tm.get_safety("my_custom_tool") == DESTRUCTIVE

    # And the seed must still have populated the built-ins.
    assert tm.get_safety("grep_kb") == SAFE_PARALLEL


def test_set_safety_rejects_unknown_classification():
    """Sanity check: set_safety still guards the enum."""
    tm = ToolManager()
    with pytest.raises(ValueError):
        tm.set_safety("whatever", "not-a-real-class")


def test_seed_dict_matches_design_partitions():
    """The module-level seed table should exactly match design.md."""
    parallel_in_seed = {
        n for n, c in _BUILTIN_SAFETY_SEED.items() if c == SAFE_PARALLEL
    }
    destructive_in_seed = {
        n for n, c in _BUILTIN_SAFETY_SEED.items() if c == DESTRUCTIVE
    }
    assert parallel_in_seed == set(_DESIGN_PARALLEL_SAFE)
    assert destructive_in_seed == set(_DESIGN_DESTRUCTIVE)
