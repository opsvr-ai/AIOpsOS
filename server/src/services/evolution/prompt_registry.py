"""Process-wide snapshot of sub-agent prompt versions.

Spec: .kiro/specs/agent-runtime-optimization-evolution,
task 18.2 / R-3.15, R-3.16, R-3.20.

Design goals (mirrors ``design.md §Prompt Registry``):

1. **Lock-free reads.** ``get_active`` / ``get_shadow`` / ``get_ab`` do a
   single dict lookup. Dict-reassignment (``self._snapshot = {...}``) is
   atomic under CPython's GIL so readers either see the pre-swap dict
   or the post-swap dict, never a partially-populated one. This is the
   same pattern ``FeatureFlagService`` uses.
2. **Serialized writes.** ``load`` / ``apply_promotion`` / ``refresh``
   take an :class:`asyncio.Lock` so two hot-reload events can't
   interleave writes. A reader arriving mid-write still gets a
   consistent snapshot because we build the new dict first and only
   swap the reference once.
3. **Never-fail reads.** If the DB is empty for a given sub-agent,
   :meth:`get_active` falls back to the code-level default from
   ``_DEFAULT_SUBAGENT_PROMPTS``. The system can boot cold with no
   DB rows at all and still serve traffic.
4. **Re-read on promotion.** ``apply_promotion`` always re-fetches the
   referenced row by id before applying it. That way replayed Kafka
   messages with stale bodies can't roll the snapshot backwards — the
   DB is the source of truth; the event is only a trigger.

The dict keys are ``sub_agent_name`` strings (``"knowledge"``, ``"ops"``,
…). The values are immutable :class:`PromptVersion` dataclasses so
anything a caller holds onto is safe to read without further locking.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from src.services.prompt_versions.repository import (
    PromptVersionRow,
    SubAgentPromptVersionRepository,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


PromptStatus = Literal[
    "proposed", "shadow", "ab", "active", "retired", "rejected"
]


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """Immutable snapshot of a single prompt version.

    ``source='default'`` means the registry is serving a code-level
    fallback from ``_DEFAULT_SUBAGENT_PROMPTS`` because no DB row
    exists yet for this sub-agent. ``source='db'`` means the payload
    originated from ``sub_agent_prompt_versions``.
    """

    id: str
    sub_agent_name: str
    status: PromptStatus
    system_prompt: str
    version_no: int
    manifest_sha256: str
    parent_version_id: str | None
    activated_at: float | None
    source: Literal["db", "default"]


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    """Per-request view of which version each candidate lane holds.

    Populated by :class:`ShadowABRouter` (future task 24) and fed into
    :class:`DynamicSystemPromptMiddleware` (task 19). The registry
    itself doesn't need this type — it's defined here so the router
    and the registry share one import.
    """

    active: PromptVersion
    shadow: PromptVersion | None = None
    ab: PromptVersion | None = None


@dataclass(frozen=True, slots=True)
class PromotionEvent:
    """Kafka ``ops.agent.promotion`` payload (the subset we care about).

    The real message may carry more fields (audit info, rationale) but
    the registry only needs an id + expected target status to re-query
    the DB. ``event_id`` is a stable dedupe key used by
    :class:`PromptReloader` (task 20); we expose it here so tests can
    construct synthetic events without pulling in Kafka.
    """

    event_id: str
    new_version_id: str
    to_status: PromptStatus
    sub_agent_name: str | None = None  # optional hint; re-verified via DB
    emitted_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SubAgentPromptRegistry:
    """Process-wide snapshot + hot-swap driver for sub-agent prompts.

    Construction is cheap; callers must ``await load()`` before the
    first ``get_active()`` to populate the DB-backed rows. Readers can
    actually call ``get_active()`` before load and will simply get the
    default fallback — useful during FastAPI startup races.
    """

    def __init__(
        self,
        repo: SubAgentPromptVersionRepository,
        defaults: dict[str, str],
    ) -> None:
        """
        Args:
            repo: fully-constructed repository. The registry never
                opens its own session factory; the caller decides.
            defaults: ``sub_agent_name -> fallback system_prompt``.
                This is the cold-start safety net for sub-agents that
                have no DB row yet. The dict is defensively copied on
                construction so the caller can mutate theirs without
                affecting us.
        """
        self._repo = repo
        self._defaults: dict[str, str] = dict(defaults)
        self._write_lock = asyncio.Lock()

        # Reference-swap dicts. Readers see `self._snapshot` under GIL
        # protection; writers build a fresh dict + reassign. Never
        # mutate these in place.
        self._snapshot: dict[str, PromptVersion] = {}
        self._shadow: dict[str, PromptVersion] = {}
        self._ab: dict[str, PromptVersion] = {}
        self._by_id: dict[str, PromptVersion] = {}

        self._loaded = False
        # Dedupe promotion events so a replayed Kafka message is a
        # no-op. Bounded deque to cap memory; older ids eventually
        # fall out which is fine because the DB-reread guard catches
        # any replay that somehow bypasses this cache.
        self._applied_events: set[str] = set()
        self._applied_events_order: list[str] = []
        self._applied_events_max = 1024

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """First-time (or forced) load from DB.

        Idempotent: a second call is a no-op unless ``refresh()`` has
        been called in between. Safe to invoke from concurrent
        startup paths.
        """
        async with self._write_lock:
            if self._loaded:
                return
            await self._reload_unlocked()
            self._loaded = True

    async def refresh(self) -> None:
        """Force a full reload — convenient for admin endpoints / tests."""
        async with self._write_lock:
            await self._reload_unlocked()
            self._loaded = True

    async def _reload_unlocked(self) -> None:
        """Rebuild all snapshot dicts from the repository. Holds the lock."""
        try:
            rows = await self._repo.list_live()
        except Exception:
            # Don't wipe a healthy snapshot just because DB blipped.
            logger.exception(
                "prompt_registry: list_live failed, keeping current snapshot"
            )
            return

        new_active: dict[str, PromptVersion] = {}
        new_shadow: dict[str, PromptVersion] = {}
        new_ab: dict[str, PromptVersion] = {}
        new_by_id: dict[str, PromptVersion] = {}

        # Track version_no per sub-agent: rows are ordered newest first
        # by the repo so we count backwards.
        per_name_count: dict[str, int] = {}
        per_name_seen_rows: dict[str, list[PromptVersionRow]] = {}
        for r in rows:
            per_name_seen_rows.setdefault(r.sub_agent_name, []).append(r)

        for name, name_rows in per_name_seen_rows.items():
            # Oldest-first to assign a stable monotonic version_no = 1, 2, ...
            sorted_rows = sorted(name_rows, key=lambda r: r.created_at)
            for idx, row in enumerate(sorted_rows, start=1):
                pv = _row_to_version(row, version_no=idx)
                new_by_id[pv.id] = pv
                if pv.status == "active":
                    new_active[name] = pv
                elif pv.status == "shadow":
                    new_shadow[name] = pv
                elif pv.status == "ab":
                    new_ab[name] = pv
            per_name_count[name] = len(sorted_rows)

        # Cold-start defaults — only fill in sub-agents that have no
        # active row. Shadow / ab lanes stay empty (there's no sensible
        # default for a candidate that doesn't exist yet).
        for name, prompt_text in self._defaults.items():
            if name not in new_active:
                default_pv = _default_version(name, prompt_text)
                new_active[name] = default_pv
                new_by_id[default_pv.id] = default_pv

        # Atomic swap — dict reassignment is GIL-safe so readers are
        # unaffected mid-swap.
        self._snapshot = new_active
        self._shadow = new_shadow
        self._ab = new_ab
        self._by_id = new_by_id

        logger.info(
            "prompt_registry: loaded active=%d shadow=%d ab=%d defaults=%d",
            len(new_active) - sum(1 for p in new_active.values() if p.source == "default"),
            len(new_shadow),
            len(new_ab),
            sum(1 for p in new_active.values() if p.source == "default"),
        )

    # ------------------------------------------------------------------
    # Lock-free reads
    # ------------------------------------------------------------------

    def get_active(self, sub_agent_name: str) -> PromptVersion:
        """Return the current active version for ``sub_agent_name``.

        Never returns ``None`` — falls back to the code-level default
        if the DB hasn't supplied one (R-3.20). If the sub_agent_name
        is entirely unknown (not in defaults and not in DB), returns
        a synthetic empty-prompt version so ``pv.system_prompt`` is
        always a string.
        """
        pv = self._snapshot.get(sub_agent_name)
        if pv is not None:
            return pv
        # Unknown sub-agent: still return something callable to avoid
        # blowing up callers. An empty prompt is safer than None because
        # DeepAgents concatenates it into the LLM system_message.
        default_prompt = self._defaults.get(sub_agent_name, "")
        return _default_version(sub_agent_name, default_prompt)

    def get_shadow(self, sub_agent_name: str) -> PromptVersion | None:
        """Return the current shadow-lane version, if any."""
        return self._shadow.get(sub_agent_name)

    def get_ab(self, sub_agent_name: str) -> PromptVersion | None:
        """Return the current AB-lane version, if any."""
        return self._ab.get(sub_agent_name)

    def get_by_id(self, version_id: str | uuid.UUID) -> PromptVersion | None:
        """Look up any loaded version (active/shadow/ab) by id.

        Used by :class:`DynamicSystemPromptMiddleware` when
        :class:`ShadowABRouter` pinned a specific variant for a
        request. Returns ``None`` for ids the registry hasn't loaded —
        callers should fall back to ``get_active``.
        """
        key = str(version_id)
        return self._by_id.get(key)

    def snapshot_size(self) -> dict[str, int]:
        """Diagnostic peek at the three lane sizes — for ``/metrics`` and tests."""
        return {
            "active": len(self._snapshot),
            "shadow": len(self._shadow),
            "ab": len(self._ab),
        }

    # ------------------------------------------------------------------
    # Hot-reload entry point
    # ------------------------------------------------------------------

    async def apply_promotion(self, event: PromotionEvent) -> bool:
        """Apply a single Kafka promotion event.

        Returns ``True`` if the snapshot changed, ``False`` if the
        event was a no-op (replayed, stale, or pointed at a row that
        has since moved on in the DB).

        Steps (order matters):

        1. Dedupe by ``event.event_id``.
        2. Re-fetch the target row from the repository. The DB row is
           the source of truth; the event carries just the id.
        3. If the row has drifted away from ``event.to_status`` (Promoter
           already rolled back, or someone manually edited the row),
           ignore and log. This keeps replays from resurrecting
           retired versions.
        4. Build a new lane dict and swap atomically.

        This method holds :attr:`_write_lock` for the entire duration
        so two concurrent events serialise cleanly.
        """
        async with self._write_lock:
            if event.event_id in self._applied_events:
                return False

            fresh = await self._repo.get_by_id(event.new_version_id)
            if fresh is None:
                logger.warning(
                    "prompt_registry: promotion event %s references unknown id %s",
                    event.event_id,
                    event.new_version_id,
                )
                self._record_event(event.event_id)
                return False

            if fresh.status != event.to_status:
                # DB has moved on — honor the DB, not the event.
                logger.info(
                    "prompt_registry: event %s target status=%s but DB shows %s; skipping",
                    event.event_id,
                    event.to_status,
                    fresh.status,
                )
                self._record_event(event.event_id)
                return False

            changed = await self._apply_row_unlocked(fresh)
            self._record_event(event.event_id)
            return changed

    async def _apply_row_unlocked(self, fresh: PromptVersionRow) -> bool:
        """Rebuild lanes to reflect ``fresh``. Expects the write lock held.

        We deliberately don't do surgical mutation of the lane dicts —
        we build fresh ones and reassign. That keeps the readers' view
        atomic and is cheap enough for the single-event hot path
        (dicts here are ≤ ~10 entries each).
        """
        status = fresh.status
        name = fresh.sub_agent_name

        # Copy current lanes — we'll mutate locally then reassign.
        new_active = dict(self._snapshot)
        new_shadow = dict(self._shadow)
        new_ab = dict(self._ab)
        new_by_id = dict(self._by_id)

        if status == "active":
            # Monotonic version_no: count this name's existing loaded versions + 1
            version_no = self._next_version_no(name)
            pv = _row_to_version(fresh, version_no=version_no)
            prev = new_active.get(name)
            new_active[name] = pv
            # A newly active version displaces same-name shadow/ab rows
            # (R-3: "same-name active only one; shadow/ab cleared on promote").
            new_shadow.pop(name, None)
            new_ab.pop(name, None)
            new_by_id[pv.id] = pv
            logger.info(
                "prompt_registry: hot-swap %s active %s(%s) -> %s(v%s)",
                name,
                prev.id if prev else "<default>",
                prev.source if prev else "default",
                pv.id,
                version_no,
            )

        elif status == "shadow":
            pv = _row_to_version(fresh, version_no=self._next_version_no(name))
            new_shadow[name] = pv
            new_by_id[pv.id] = pv

        elif status == "ab":
            pv = _row_to_version(fresh, version_no=self._next_version_no(name))
            new_ab[name] = pv
            new_by_id[pv.id] = pv

        elif status in ("retired", "rejected"):
            # Remove from whichever lane holds this id. If the retired
            # row was active, fall back to the previous historical
            # active — or to the code-level default.
            changed = False
            if (cur := new_active.get(name)) and cur.id == str(fresh.id):
                prev = await self._repo.get_previous_active(
                    name, before_id=fresh.id
                )
                if prev is not None:
                    replacement = _row_to_version(
                        prev, version_no=self._next_version_no(name)
                    )
                else:
                    replacement = _default_version(
                        name, self._defaults.get(name, "")
                    )
                new_active[name] = replacement
                new_by_id[replacement.id] = replacement
                changed = True
            if (cur := new_shadow.get(name)) and cur.id == str(fresh.id):
                new_shadow.pop(name)
                changed = True
            if (cur := new_ab.get(name)) and cur.id == str(fresh.id):
                new_ab.pop(name)
                changed = True
            new_by_id.pop(str(fresh.id), None)
            if not changed:
                # Nothing to retire — event was for a row we never loaded.
                return False
            logger.info(
                "prompt_registry: retired %s (status=%s)", fresh.id, status
            )

        else:  # proposed or anything else — not live, ignore.
            return False

        # Single atomic swap point.
        self._snapshot = new_active
        self._shadow = new_shadow
        self._ab = new_ab
        self._by_id = new_by_id
        return True

    def _next_version_no(self, sub_agent_name: str) -> int:
        """Rough monotonic version number for the in-memory view.

        DB-side version ordering is by ``created_at``; what matters for
        observability is that swaps produce strictly increasing
        numbers during a process's lifetime. We count distinct loaded
        ids per name.
        """
        seen = sum(
            1 for pv in self._by_id.values() if pv.sub_agent_name == sub_agent_name
        )
        return seen + 1

    def _record_event(self, event_id: str) -> None:
        """Remember ``event_id`` so replays are cheap no-ops."""
        if event_id in self._applied_events:
            return
        self._applied_events.add(event_id)
        self._applied_events_order.append(event_id)
        if len(self._applied_events_order) > self._applied_events_max:
            old = self._applied_events_order.pop(0)
            self._applied_events.discard(old)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_version(row: PromptVersionRow, *, version_no: int) -> PromptVersion:
    """Convert a repository row to an immutable :class:`PromptVersion`."""
    activated_ts = (
        row.activated_at.timestamp() if isinstance(row.activated_at, datetime) else None
    )
    return PromptVersion(
        id=str(row.id),
        sub_agent_name=row.sub_agent_name,
        status=row.status,  # type: ignore[arg-type]
        system_prompt=row.system_prompt,
        version_no=version_no,
        manifest_sha256=row.manifest_sha256 or "",
        parent_version_id=(
            str(row.parent_version_id) if row.parent_version_id else None
        ),
        activated_at=activated_ts,
        source="db",
    )


def _default_version(sub_agent_name: str, prompt_text: str) -> PromptVersion:
    """Synthesise a fallback :class:`PromptVersion` from a code-level default.

    The id is deterministic (``default::<name>``) so that ``get_by_id``
    can round-trip it. Real DB rows use UUIDs so there's no collision
    possible.
    """
    return PromptVersion(
        id=f"default::{sub_agent_name}",
        sub_agent_name=sub_agent_name,
        status="active",
        system_prompt=prompt_text,
        version_no=0,
        manifest_sha256="",
        parent_version_id=None,
        activated_at=None,
        source="default",
    )


# ---------------------------------------------------------------------------
# Process-singleton accessor
# ---------------------------------------------------------------------------


_SINGLETON_LOCK = asyncio.Lock()
_SINGLETON: SubAgentPromptRegistry | None = None


def _load_default_prompts() -> dict[str, str]:
    """Lazy import of ``_DEFAULT_SUBAGENT_PROMPTS`` from ``deep_agent.py``.

    The import is deferred because ``deep_agent`` pulls in a lot of
    heavy downstream modules (LangChain, DeepAgents, many tool
    modules). Doing the import at registry-construction time only
    pays that cost once, at first use.

    If the symbol doesn't exist yet (task 19 hasn't landed), we fall
    back to an empty dict — the registry still boots, it just won't
    have any fallback prompts. That's preferable to failing startup.
    """
    try:
        # pylint: disable=import-outside-toplevel
        from src.agent import deep_agent  # type: ignore[import-untyped]
    except Exception:  # pragma: no cover - only hit in stripped-down test envs
        logger.debug(
            "prompt_registry: deep_agent import failed; defaults will be empty"
        )
        return {}

    defaults = getattr(deep_agent, "_DEFAULT_SUBAGENT_PROMPTS", None)
    if isinstance(defaults, dict) and defaults:
        return {str(k): str(v) for k, v in defaults.items()}

    # Fallback: reconstruct from the module's SUBAGENTS list, if present.
    # Enables the registry to work before task 19 introduces the
    # explicit dict.
    subagents = getattr(deep_agent, "SUBAGENTS", None) or []
    out: dict[str, str] = {}
    for sa in subagents:
        try:
            name = sa["name"]  # type: ignore[index]
            prompt_text = sa.get("system_prompt", "")  # type: ignore[attr-defined]
        except (TypeError, KeyError, AttributeError):
            continue
        if isinstance(name, str) and isinstance(prompt_text, str) and prompt_text:
            out[name] = prompt_text
    return out


async def get_prompt_registry() -> SubAgentPromptRegistry:
    """Return (and lazily construct) the process-wide registry singleton.

    The first caller builds the registry, loads it from DB, and caches
    it. Subsequent callers get the same instance. Thread/coroutine
    safety is handled by :data:`_SINGLETON_LOCK`.
    """
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    async with _SINGLETON_LOCK:
        if _SINGLETON is None:
            repo = SubAgentPromptVersionRepository()
            defaults = _load_default_prompts()
            reg = SubAgentPromptRegistry(repo=repo, defaults=defaults)
            try:
                await reg.load()
            except Exception:
                # Never fail singleton creation over a DB hiccup — the
                # registry will serve defaults and try again on the
                # next refresh.
                logger.exception("prompt_registry: initial load failed")
            _SINGLETON = reg
    return _SINGLETON


async def shutdown_prompt_registry() -> None:
    """Drop the singleton reference. Useful for tests and shutdown hooks."""
    global _SINGLETON
    _SINGLETON = None


def _reset_singleton_for_tests() -> None:
    """Test-only: drop the singleton synchronously."""
    global _SINGLETON
    _SINGLETON = None


__all__ = [
    "PromotionEvent",
    "PromptStatus",
    "PromptVersion",
    "ResolvedPrompt",
    "SubAgentPromptRegistry",
    "get_prompt_registry",
    "shutdown_prompt_registry",
]
