"""Admin-facing REST API for the evolution pipeline.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.4
(Phase L — Promoter + Rollback). Covers:

* R-3.8 — admin can force-promote / activate a candidate along the
  state machine (``proposed → shadow → ab → active``) with the same
  transactional / audit guarantees as the automated promoter.
* R-3.9 — admin can roll back the active version of a sub-agent
  prompt; the Promoter's rollback path handles the transactional
  swap + Kafka event.
* R-8.4 — every mutating endpoint goes through :func:`require_admin`
  and writes an audit-log line. The audit line is structured so
  operators can scrape it today, and will be routed through a proper
  ``audit_logs`` service once that lands (mirrors the transitional
  pattern used in ``src.api.control.kafka``).

Endpoints:

* ``GET  /sub-agents/{name}/prompt-versions`` — full version chain
  (any status) ordered newest-first.
* ``POST /sub-agents/{name}/rollback`` — invoke
  :meth:`Promoter.rollback_prompt`.
* ``POST /sub-agents/{name}/prompt-versions/{id}/activate`` —
  admin-override activation. Walks the candidate's current status one
  edge at a time toward ``active`` so the state-machine invariants
  still hold (R-3.4).
* ``GET  /sub-agents/{name}/prompt-versions/{id}/diff`` — unified diff
  between a given version's body and the current active version.
* ``GET  /candidates`` — list candidates by status (both
  ``skill_candidates`` and ``sub_agent_prompt_versions`` tables).
* ``POST /candidates/{id}/promote`` — advance a candidate one edge
  (``proposed → shadow → ab → active``). Enforces R-3.4.
* ``POST /candidates/{id}/reject`` — mark a candidate ``rejected``.

All routes are mounted under ``/api/v1/evolution/...`` — the control
plane mounts ``api.control.router`` with a ``/api/v1`` prefix, so the
external URL shape matches the spec's ``/api/control/...`` intent
(``/api/v1`` is this deployment's stable public prefix for the
control plane).
"""
from __future__ import annotations

import difflib
import logging
import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import require_admin
from src.schemas.evolution import (
    ActivateResultOut,
    CandidateOut,
    PromoteResultOut,
    PromptVersionDiffOut,
    PromptVersionSummaryOut,
    RejectResultOut,
    RollbackResultOut,
)
from src.services.evolution.candidate_store import (
    ALL_STATUSES,
    CandidateRow,
    InvalidStateTransition,
    STATE_TRANSITIONS,
    SkillCandidateStore,
)
from src.services.evolution.promoter import Promoter
from src.services.prompt_versions.repository import (
    PromptVersionRow,
    SubAgentPromptVersionRepository,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/evolution", tags=["evolution"])


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def _audit(user: Any, action: str, **fields: Any) -> None:
    """Structured audit log stand-in.

    Matches the pattern in :mod:`src.api.control.kafka` — we log a
    recognisable prefix (``AUDIT evolution action=...``) so ops
    scraping tools pick it up. Once an ``audit_logs`` table /
    service is provisioned, this helper routes through it instead.
    """
    actor = getattr(user, "username", None) or getattr(user, "id", "<unknown>")
    payload = ", ".join(f"{k}={v}" for k, v in fields.items())
    logger.info(
        "AUDIT evolution action=%s actor=%s %s",
        action,
        actor,
        payload,
    )


# ---------------------------------------------------------------------------
# Dependency injection — tests override these via ``app.dependency_overrides``.
# ---------------------------------------------------------------------------


def get_prompt_repo() -> SubAgentPromptVersionRepository:
    """Factory for the sub-agent prompt-version repository.

    Wrapped in a dependency so tests can swap in an in-memory fake
    without monkey-patching the module. Default: the process-wide
    ``async_session_factory``.
    """
    return SubAgentPromptVersionRepository()


def get_candidate_store() -> SkillCandidateStore:
    """Factory for the :class:`SkillCandidateStore`."""
    return SkillCandidateStore()


def get_promoter() -> Promoter:
    """Factory for the :class:`Promoter`.

    The default construction is zero-arg; the promoter resolves its
    own DB factory + Kafka producer lazily on first use. Tests inject
    a promoter with fakes for both.
    """
    return Promoter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summary_from_row(row: PromptVersionRow) -> PromptVersionSummaryOut:
    """Convert a repository row to the summary response shape."""
    return PromptVersionSummaryOut(
        id=row.id,
        sub_agent_name=row.sub_agent_name,
        status=row.status,
        manifest_sha256=row.manifest_sha256,
        candidate_id=row.candidate_id,
        parent_version_id=row.parent_version_id,
        activated_at=row.activated_at,
        retired_at=row.retired_at,
        created_at=row.created_at,
    )


def _candidate_to_out(row: CandidateRow) -> CandidateOut:
    """Project a :class:`CandidateRow` to the admin summary shape.

    The ``data`` blob carries kind-specific state (prompt body for
    prompt_patch, tag payload for tool_config). We expose it under
    ``extra`` so the admin UI can render it without the route having
    to carry different response shapes per kind.
    """
    return CandidateOut(
        id=row.id,
        kind=row.kind,
        name=row.name,
        status=row.status,
        table=row.table,
        target_ref=row.target_ref,
        extra=dict(row.data or {}),
    )


def _unified_diff(old_text: str, new_text: str, *, old_label: str, new_label: str) -> tuple[str, int, int]:
    """Return (``diff_text``, ``added``, ``removed``) for two prompt bodies.

    ``difflib.unified_diff`` returns an iterable of already-newline-
    terminated lines; we join with the empty string so the output
    reads as a normal diff. ``added`` / ``removed`` count the ``+``
    and ``-`` body lines (ignoring the ``+++`` / ``---`` headers and
    the ``@@`` hunk headers).
    """
    old_lines = (old_text or "").splitlines(keepends=True)
    new_lines = (new_text or "").splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=old_label,
        tofile=new_label,
        lineterm="",
    )
    lines: list[str] = []
    added = 0
    removed = 0
    for line in diff_iter:
        lines.append(line)
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    # Join with \n — ``difflib`` emits headers without trailing
    # newlines when ``lineterm=""``; body lines already carry their
    # own terminators because we passed ``keepends=True`` above.
    text = "\n".join(lines)
    return text, added, removed


# ---------------------------------------------------------------------------
# Prompt-version endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/sub-agents/{name}/prompt-versions",
    response_model=list[PromptVersionSummaryOut],
)
async def list_prompt_versions(
    name: str,
    _admin=Depends(require_admin),
    repo: SubAgentPromptVersionRepository = Depends(get_prompt_repo),
) -> list[PromptVersionSummaryOut]:
    """Return every ``sub_agent_prompt_versions`` row for *name*.

    Ordering is newest-first by ``created_at`` — operators scan the
    top row to see the latest proposal / rollback. No pagination:
    the audit trail per sub-agent is small (< 100 rows in all
    realistic scenarios), so a single-shot list keeps the admin UI
    simple.
    """
    rows = await repo.list_by_sub_agent(name)
    return [_summary_from_row(r) for r in rows]


@router.post(
    "/sub-agents/{name}/rollback",
    response_model=RollbackResultOut,
)
async def rollback_prompt(
    name: str,
    admin=Depends(require_admin),
    promoter: Promoter = Depends(get_promoter),
) -> RollbackResultOut:
    """Roll back the active prompt version for *name* (R-3.9).

    Returns the :class:`~src.services.evolution.promoter.RollbackResult`
    serialised as JSON. A no-op rollback (no active version, or no
    previous version to restore) is surfaced as HTTP 200 with
    ``ok=False`` and a human-readable ``reason`` — the caller should
    treat the result as advisory, not as an error.
    """
    result = await promoter.rollback_prompt(name)
    _audit(
        admin,
        "rollback_prompt",
        sub_agent=name,
        ok=result.ok,
        retired=str(result.retired_version_id) if result.retired_version_id else None,
        restored=str(result.restored_version_id) if result.restored_version_id else None,
        event_published=result.event_published,
    )
    return RollbackResultOut(**asdict(result))


@router.post(
    "/sub-agents/{name}/prompt-versions/{version_id}/activate",
    response_model=ActivateResultOut,
)
async def activate_prompt_version(
    name: str,
    version_id: uuid.UUID,
    admin=Depends(require_admin),
    repo: SubAgentPromptVersionRepository = Depends(get_prompt_repo),
    store: SkillCandidateStore = Depends(get_candidate_store),
    promoter: Promoter = Depends(get_promoter),
) -> ActivateResultOut:
    """Admin-override activation for a specific prompt version.

    Walks the candidate's current status one edge at a time toward
    ``active``, invoking :meth:`Promoter.activate_prompt_patch` for
    the final ``ab → active`` hop so the DB transaction + Kafka event
    shape matches a normal automated promotion. Interim edges
    (``proposed → shadow``, ``shadow → ab``) go through
    :meth:`SkillCandidateStore.update_status` which enforces R-3.4.

    Already-active versions return ``action='noop'``. Terminal states
    (``retired``, ``rejected``) return 409 — re-activating a retired
    version should go through the regular rollback endpoint instead,
    which has the right semantics and audit story.
    """
    row = await repo.get_by_id(version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="prompt version not found")
    if row.sub_agent_name != name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"sub-agent mismatch: version belongs to "
                f"{row.sub_agent_name!r}, not {name!r}"
            ),
        )

    start_status = row.status

    if start_status == "active":
        _audit(admin, "activate_prompt_noop", sub_agent=name, version=str(version_id))
        return ActivateResultOut(
            candidate_id=version_id,
            from_status=start_status,
            to_status=start_status,
            action="noop",
            reason="already active",
        )

    if start_status in {"retired", "rejected"}:
        raise HTTPException(
            status_code=409,
            detail=(
                f"version status {start_status!r} cannot be re-activated; "
                "use the rollback endpoint to restore a retired version"
            ),
        )

    # Walk the state machine: proposed → shadow → ab, then activate.
    try:
        if start_status == "proposed":
            await store.update_status(version_id, "shadow")
        # Re-read so we work against the current status in case another
        # writer moved the row between steps.
        current = await repo.get_by_id(version_id)
        if current is None:
            raise HTTPException(status_code=404, detail="prompt version vanished mid-activation")

        if current.status == "shadow":
            await store.update_status(version_id, "ab")
        current = await repo.get_by_id(version_id)
        if current is None:
            raise HTTPException(status_code=404, detail="prompt version vanished mid-activation")

        if current.status == "ab":
            await promoter.activate_prompt_patch(version_id)
    except InvalidStateTransition as exc:
        # A concurrent writer beat us to a transition. Surface as 409.
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    final = await repo.get_by_id(version_id)
    final_status = final.status if final else "unknown"

    _audit(
        admin,
        "activate_prompt",
        sub_agent=name,
        version=str(version_id),
        from_status=start_status,
        to_status=final_status,
    )
    return ActivateResultOut(
        candidate_id=version_id,
        from_status=start_status,
        to_status=final_status,
        action="activated" if final_status == "active" else "promoted",
        reason=(
            f"advanced from {start_status} to {final_status} via admin override"
        ),
    )


@router.get(
    "/sub-agents/{name}/prompt-versions/{version_id}/diff",
    response_model=PromptVersionDiffOut,
)
async def diff_prompt_version(
    name: str,
    version_id: uuid.UUID,
    _admin=Depends(require_admin),
    repo: SubAgentPromptVersionRepository = Depends(get_prompt_repo),
) -> PromptVersionDiffOut:
    """Return a unified diff of *version_id* against the current active version.

    If no active version exists (sub-agent still on the registry
    default), the diff is computed against the empty string — which
    typically results in an all-added diff. Diffing a version against
    itself (e.g. the caller is the active version) returns an empty
    ``diff`` with ``added=0, removed=0``.
    """
    row = await repo.get_by_id(version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="prompt version not found")
    if row.sub_agent_name != name:
        raise HTTPException(
            status_code=400,
            detail=(
                f"sub-agent mismatch: version belongs to "
                f"{row.sub_agent_name!r}, not {name!r}"
            ),
        )

    active = await repo.get_active(name)
    active_body = active.system_prompt if active is not None else ""
    active_label = (
        f"active:{active.id}" if active is not None else "active:<none>"
    )
    requested_label = f"{row.status}:{row.id}"

    diff_text, added, removed = _unified_diff(
        active_body,
        row.system_prompt,
        old_label=active_label,
        new_label=requested_label,
    )

    return PromptVersionDiffOut(
        sub_agent_name=name,
        requested_version_id=row.id,
        requested_status=row.status,
        active_version_id=active.id if active is not None else None,
        active_status=active.status if active is not None else None,
        diff=diff_text,
        added=added,
        removed=removed,
    )


# ---------------------------------------------------------------------------
# Candidate endpoints
# ---------------------------------------------------------------------------


@router.get("/candidates", response_model=list[CandidateOut])
async def list_candidates(
    status: str = Query(
        "shadow",
        description=(
            "Candidate status to filter by: proposed | shadow | ab | "
            "active | retired | rejected."
        ),
    ),
    _admin=Depends(require_admin),
    store: SkillCandidateStore = Depends(get_candidate_store),
) -> list[CandidateOut]:
    """List candidates in a given status across both tables.

    Queries ``skill_candidates`` (skill + tool_config) and
    ``sub_agent_prompt_versions`` (prompt_patch) and merges the
    results. Unknown statuses return an empty list (mirrors the
    store's own defensive behaviour).
    """
    if status not in ALL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid status {status!r}; expected one of "
                f"{sorted(ALL_STATUSES)}"
            ),
        )
    rows = await store.list_by_status(status)
    return [_candidate_to_out(r) for r in rows]


@router.post("/candidates/{candidate_id}/promote", response_model=PromoteResultOut)
async def promote_candidate(
    candidate_id: uuid.UUID,
    admin=Depends(require_admin),
    store: SkillCandidateStore = Depends(get_candidate_store),
    promoter: Promoter = Depends(get_promoter),
) -> PromoteResultOut:
    """Advance a candidate one edge along the state machine (R-3.4).

    Edge table:

    * ``proposed → shadow``
    * ``shadow  → ab``
    * ``ab      → active``   (kind-specific activation path)

    Terminal states (``active``, ``retired``, ``rejected``) return
    ``action='noop'`` with a human-readable reason. Forward promotion
    past ``active`` is not a legal edge; use the rollback endpoint to
    move ``active → retired`` through the proper DB + Kafka swap.
    """
    row = await store.get(candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")

    if row.status in {"active", "retired", "rejected"}:
        _audit(
            admin,
            "promote_candidate_noop",
            candidate=str(candidate_id),
            status=row.status,
        )
        return PromoteResultOut(
            candidate_id=candidate_id,
            from_status=row.status,
            to_status=row.status,
            action="noop",
            reason=f"candidate in terminal/active status {row.status!r}",
        )

    try:
        if row.status == "proposed":
            await store.update_status(candidate_id, "shadow")
            to_status = "shadow"
            action = "advanced"
        elif row.status == "shadow":
            await store.update_status(candidate_id, "ab")
            to_status = "ab"
            action = "advanced"
        elif row.status == "ab":
            if row.kind == "skill":
                await promoter.activate_skill(candidate_id)
            elif row.kind == "prompt_patch":
                await promoter.activate_prompt_patch(candidate_id)
            elif row.kind == "tool_config":
                await promoter.activate_tool_config(candidate_id)
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"unknown candidate kind {row.kind!r}",
                )
            to_status = "active"
            action = "activated"
        else:  # pragma: no cover - defensive; all live states handled above
            raise HTTPException(
                status_code=500, detail=f"unexpected status {row.status!r}"
            )
    except InvalidStateTransition as exc:
        # Raised when the DB row has already moved (concurrent write).
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _audit(
        admin,
        "promote_candidate",
        candidate=str(candidate_id),
        kind=row.kind,
        from_status=row.status,
        to_status=to_status,
    )
    return PromoteResultOut(
        candidate_id=candidate_id,
        from_status=row.status,
        to_status=to_status,
        action=action,  # type: ignore[arg-type]
        reason=f"advanced from {row.status} to {to_status} via admin override",
    )


@router.post("/candidates/{candidate_id}/reject", response_model=RejectResultOut)
async def reject_candidate(
    candidate_id: uuid.UUID,
    admin=Depends(require_admin),
    store: SkillCandidateStore = Depends(get_candidate_store),
) -> RejectResultOut:
    """Transition a candidate to ``rejected`` (terminal).

    Only live statuses (``proposed``, ``shadow``, ``ab``) support this
    edge — see :data:`STATE_TRANSITIONS`. Candidates already in a
    terminal state return 409 so the caller knows the reject was a
    no-op and can act on it.
    """
    row = await store.get(candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="candidate not found")

    allowed = STATE_TRANSITIONS.get(row.status, frozenset())
    if "rejected" not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"cannot reject candidate in status {row.status!r}; "
                f"legal rejections require status in "
                f"{sorted(s for s, edges in STATE_TRANSITIONS.items() if 'rejected' in edges)}"
            ),
        )

    try:
        await store.update_status(candidate_id, "rejected")
    except InvalidStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    _audit(
        admin,
        "reject_candidate",
        candidate=str(candidate_id),
        kind=row.kind,
        from_status=row.status,
    )
    return RejectResultOut(
        candidate_id=candidate_id,
        from_status=row.status,
        to_status="rejected",
        reason=f"rejected from status {row.status} via admin override",
    )


__all__ = [
    "get_candidate_store",
    "get_promoter",
    "get_prompt_repo",
    "router",
]
