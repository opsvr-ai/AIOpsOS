"""Unit tests for the evolution admin API — task 23.4.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 23.4
(Phase L — Admin API for evolution).

**Validates: Requirements 3.8, 3.9, 8.4**

Surface covered:

* ``GET    /evolution/sub-agents/{name}/prompt-versions``
* ``POST   /evolution/sub-agents/{name}/rollback``
* ``POST   /evolution/sub-agents/{name}/prompt-versions/{id}/activate``
* ``GET    /evolution/sub-agents/{name}/prompt-versions/{id}/diff``
* ``GET    /evolution/candidates``
* ``POST   /evolution/candidates/{id}/promote``
* ``POST   /evolution/candidates/{id}/reject``
* Admin guard — every mutating endpoint returns 401 without a valid
  bearer token (R-8.4).

Design notes:

* The router is mounted on a freshly-constructed ``FastAPI`` app —
  we don't import ``src.main`` because that triggers DB init, tool
  manager reloads, and agent pre-warming we don't need. Mounting the
  router in isolation gives us a fast, dependency-overrideable
  harness.
* Every upstream service (``SkillCandidateStore``, ``Promoter``,
  ``SubAgentPromptVersionRepository``) is replaced with a narrow
  in-memory fake via ``app.dependency_overrides``. This keeps the
  tests hermetic — no DB, no Kafka, no filesystem writes.
* A single ``require_admin`` override grants admin to any request;
  one focused test removes the override to exercise the unauth path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.control.evolution import (
    get_candidate_store,
    get_promoter,
    get_prompt_repo,
    router as evolution_router,
)
from src.api.deps import require_admin
from src.services.evolution.candidate_store import (
    CandidateRow,
    InvalidStateTransition,
    STATE_TRANSITIONS,
)
from src.services.evolution.promoter import RollbackResult
from src.services.prompt_versions.repository import PromptVersionRow


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakePromptRepo:
    """In-memory stand-in for :class:`SubAgentPromptVersionRepository`."""

    rows: list[PromptVersionRow] = field(default_factory=list)

    async def list_by_sub_agent(self, name: str) -> list[PromptVersionRow]:
        matches = [r for r in self.rows if r.sub_agent_name == name]
        # Preserve the repo contract: newest-first by created_at.
        return sorted(matches, key=lambda r: r.created_at, reverse=True)

    async def get_by_id(self, version_id: Any) -> PromptVersionRow | None:
        vid = str(version_id)
        for r in self.rows:
            if str(r.id) == vid:
                return r
        return None

    async def get_active(self, name: str) -> PromptVersionRow | None:
        for r in self.rows:
            if r.sub_agent_name == name and r.status == "active":
                return r
        return None

    def _replace(self, row: PromptVersionRow, **changes: Any) -> None:
        """Utility for tests — rewrite a row's fields in place."""
        idx = self.rows.index(row)
        from dataclasses import replace as _replace

        self.rows[idx] = _replace(row, **changes)


@dataclass
class _FakeCandidateStore:
    """In-memory stand-in for :class:`SkillCandidateStore`.

    Candidate rows are stored by uuid and bridged into the
    :class:`_FakePromptRepo` for prompt_patch kinds so the activate
    endpoint — which reads from both — stays consistent.
    """

    candidates: dict[uuid.UUID, CandidateRow] = field(default_factory=dict)
    prompt_repo: _FakePromptRepo | None = None

    async def get(self, cid: uuid.UUID) -> CandidateRow | None:
        return self.candidates.get(cid)

    async def list_by_status(self, status: str) -> list[CandidateRow]:
        return [r for r in self.candidates.values() if r.status == status]

    async def update_status(self, cid: uuid.UUID, new_status: str) -> None:
        row = self.candidates.get(cid)
        if row is None:
            raise LookupError(f"candidate {cid} not found")
        if row.status == new_status:
            return
        allowed = STATE_TRANSITIONS.get(row.status, frozenset())
        if new_status not in allowed:
            raise InvalidStateTransition(row.status, new_status)
        # Replace the frozen dataclass.
        from dataclasses import replace as _replace

        self.candidates[cid] = _replace(row, status=new_status)
        # Mirror into the prompt repo so ``activate_prompt_patch`` and
        # subsequent ``get_by_id`` calls see consistent state.
        if self.prompt_repo is not None:
            for pv in list(self.prompt_repo.rows):
                if str(pv.id) == str(cid):
                    self.prompt_repo._replace(pv, status=new_status)


@dataclass
class _FakePromoter:
    """Stand-in for :class:`Promoter` — records calls; mutates fakes."""

    candidate_store: _FakeCandidateStore
    prompt_repo: _FakePromptRepo
    rollback_called_with: list[str] = field(default_factory=list)
    activate_calls: list[tuple[str, uuid.UUID]] = field(default_factory=list)
    rollback_result: RollbackResult | None = None

    async def rollback_prompt(self, name: str) -> RollbackResult:
        self.rollback_called_with.append(name)
        if self.rollback_result is not None:
            return self.rollback_result
        return RollbackResult(
            ok=True,
            kind="prompt_patch",
            name=name,
            retired_version_id=uuid.uuid4(),
            restored_version_id=uuid.uuid4(),
            reason="ok",
            event_published=True,
        )

    async def activate_prompt_patch(self, cid: uuid.UUID) -> None:
        self.activate_calls.append(("prompt_patch", cid))
        # Mirror the real promoter's side effect: row.status == 'ab' -> 'active'
        row = self.candidate_store.candidates.get(cid)
        if row is not None and row.status == "ab":
            from dataclasses import replace as _replace

            self.candidate_store.candidates[cid] = _replace(row, status="active")
        # Mirror in prompt repo too.
        for pv in list(self.prompt_repo.rows):
            if str(pv.id) == str(cid) and pv.status == "ab":
                self.prompt_repo._replace(
                    pv, status="active", activated_at=datetime.now(UTC)
                )

    async def activate_skill(self, cid: uuid.UUID) -> None:
        self.activate_calls.append(("skill", cid))
        row = self.candidate_store.candidates.get(cid)
        if row is not None and row.status == "ab":
            from dataclasses import replace as _replace

            self.candidate_store.candidates[cid] = _replace(row, status="active")

    async def activate_tool_config(self, cid: uuid.UUID) -> None:
        self.activate_calls.append(("tool_config", cid))
        row = self.candidate_store.candidates.get(cid)
        if row is not None and row.status == "ab":
            from dataclasses import replace as _replace

            self.candidate_store.candidates[cid] = _replace(row, status="active")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_prompt_row(
    *,
    sub_agent: str,
    status: str = "proposed",
    prompt: str = "hello world",
    activated: datetime | None = None,
    retired: datetime | None = None,
    created: datetime | None = None,
) -> PromptVersionRow:
    now = datetime.now(UTC)
    return PromptVersionRow(
        id=uuid.uuid4(),
        sub_agent_name=sub_agent,
        candidate_id=None,
        system_prompt=prompt,
        rationale=None,
        status=status,
        parent_version_id=None,
        manifest_sha256=None,
        activated_at=activated,
        retired_at=retired,
        created_at=created or now,
    )


def _make_candidate_row(
    *,
    kind: str = "prompt_patch",
    name: str = "cand",
    status: str = "proposed",
    target_ref: str | None = None,
    data: dict | None = None,
) -> CandidateRow:
    return CandidateRow(
        id=uuid.uuid4(),
        kind=kind,
        name=name,
        status=status,
        table=(
            "sub_agent_prompt_versions"
            if kind == "prompt_patch"
            else "skill_candidates"
        ),
        target_ref=target_ref,
        tags=[],
        data=data or {},
    )


class _FakeAdmin:
    """Minimal admin principal for audit-log extraction."""

    id = "test-admin"
    username = "test-admin"


@pytest.fixture
def fake_prompt_repo() -> _FakePromptRepo:
    return _FakePromptRepo()


@pytest.fixture
def fake_candidate_store(fake_prompt_repo: _FakePromptRepo) -> _FakeCandidateStore:
    return _FakeCandidateStore(prompt_repo=fake_prompt_repo)


@pytest.fixture
def fake_promoter(
    fake_candidate_store: _FakeCandidateStore,
    fake_prompt_repo: _FakePromptRepo,
) -> _FakePromoter:
    return _FakePromoter(
        candidate_store=fake_candidate_store, prompt_repo=fake_prompt_repo
    )


@pytest.fixture
def app(
    fake_prompt_repo: _FakePromptRepo,
    fake_candidate_store: _FakeCandidateStore,
    fake_promoter: _FakePromoter,
) -> FastAPI:
    """Minimal FastAPI app with the evolution router and overrides wired."""
    app = FastAPI()
    app.include_router(evolution_router, prefix="/api/v1")

    app.dependency_overrides[get_prompt_repo] = lambda: fake_prompt_repo
    app.dependency_overrides[get_candidate_store] = lambda: fake_candidate_store
    app.dependency_overrides[get_promoter] = lambda: fake_promoter
    app.dependency_overrides[require_admin] = lambda: _FakeAdmin()
    return app


@pytest_asyncio.fixture
async def client(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


BASE = "/api/v1/evolution"


# ===========================================================================
# GET prompt-versions
# ===========================================================================


@pytest.mark.asyncio
async def test_list_prompt_versions_returns_all_rows_newest_first(
    client: AsyncClient, fake_prompt_repo: _FakePromptRepo
) -> None:
    old = _make_prompt_row(
        sub_agent="ops",
        status="retired",
        created=datetime(2024, 1, 1, tzinfo=UTC),
    )
    new = _make_prompt_row(
        sub_agent="ops",
        status="active",
        activated=datetime.now(UTC),
        created=datetime(2024, 6, 1, tzinfo=UTC),
    )
    other = _make_prompt_row(
        sub_agent="monitor", status="proposed"
    )
    fake_prompt_repo.rows.extend([old, new, other])

    res = await client.get(f"{BASE}/sub-agents/ops/prompt-versions")
    assert res.status_code == 200
    data = res.json()
    assert [row["id"] for row in data] == [str(new.id), str(old.id)]
    assert data[0]["status"] == "active"
    assert data[1]["status"] == "retired"


# ===========================================================================
# POST rollback
# ===========================================================================


@pytest.mark.asyncio
async def test_rollback_invokes_promoter_and_returns_result(
    client: AsyncClient, fake_promoter: _FakePromoter
) -> None:
    restored = uuid.uuid4()
    retired = uuid.uuid4()
    fake_promoter.rollback_result = RollbackResult(
        ok=True,
        kind="prompt_patch",
        name="ops",
        retired_version_id=retired,
        restored_version_id=restored,
        reason="ok",
        event_published=True,
    )
    res = await client.post(f"{BASE}/sub-agents/ops/rollback")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["restored_version_id"] == str(restored)
    assert body["retired_version_id"] == str(retired)
    assert body["event_published"] is True
    assert fake_promoter.rollback_called_with == ["ops"]


@pytest.mark.asyncio
async def test_rollback_noop_when_no_previous_version(
    client: AsyncClient, fake_promoter: _FakePromoter
) -> None:
    fake_promoter.rollback_result = RollbackResult(
        ok=False,
        kind="prompt_patch",
        name="ops",
        retired_version_id=None,
        restored_version_id=None,
        reason="no active version to roll back",
    )
    res = await client.post(f"{BASE}/sub-agents/ops/rollback")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"].startswith("no active")


# ===========================================================================
# POST activate (admin override)
# ===========================================================================


@pytest.mark.asyncio
async def test_activate_walks_from_proposed_to_active(
    client: AsyncClient,
    fake_prompt_repo: _FakePromptRepo,
    fake_candidate_store: _FakeCandidateStore,
    fake_promoter: _FakePromoter,
) -> None:
    """A proposed prompt version can be force-activated end-to-end."""
    row = _make_prompt_row(sub_agent="ops", status="proposed")
    fake_prompt_repo.rows.append(row)
    # Mirror into the candidate store so update_status has state to mutate.
    fake_candidate_store.candidates[row.id] = CandidateRow(
        id=row.id,
        kind="prompt_patch",
        name="ops",
        status="proposed",
        table="sub_agent_prompt_versions",
        target_ref="ops",
        tags=[],
        data={"system_prompt": row.system_prompt},
    )

    res = await client.post(
        f"{BASE}/sub-agents/ops/prompt-versions/{row.id}/activate"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from_status"] == "proposed"
    assert body["to_status"] == "active"
    assert body["action"] == "activated"
    # Promoter must have been invoked for the final ab→active hop.
    assert ("prompt_patch", row.id) in fake_promoter.activate_calls


@pytest.mark.asyncio
async def test_activate_noop_when_already_active(
    client: AsyncClient, fake_prompt_repo: _FakePromptRepo
) -> None:
    row = _make_prompt_row(sub_agent="ops", status="active")
    fake_prompt_repo.rows.append(row)

    res = await client.post(
        f"{BASE}/sub-agents/ops/prompt-versions/{row.id}/activate"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "noop"
    assert body["from_status"] == "active"
    assert body["to_status"] == "active"


@pytest.mark.asyncio
async def test_activate_rejects_retired_version(
    client: AsyncClient, fake_prompt_repo: _FakePromptRepo
) -> None:
    row = _make_prompt_row(sub_agent="ops", status="retired")
    fake_prompt_repo.rows.append(row)

    res = await client.post(
        f"{BASE}/sub-agents/ops/prompt-versions/{row.id}/activate"
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_activate_404_when_version_missing(client: AsyncClient) -> None:
    res = await client.post(
        f"{BASE}/sub-agents/ops/prompt-versions/{uuid.uuid4()}/activate"
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_activate_400_on_sub_agent_mismatch(
    client: AsyncClient, fake_prompt_repo: _FakePromptRepo
) -> None:
    row = _make_prompt_row(sub_agent="monitor", status="proposed")
    fake_prompt_repo.rows.append(row)

    res = await client.post(
        f"{BASE}/sub-agents/ops/prompt-versions/{row.id}/activate"
    )
    assert res.status_code == 400


# ===========================================================================
# GET diff
# ===========================================================================


@pytest.mark.asyncio
async def test_diff_against_active(
    client: AsyncClient, fake_prompt_repo: _FakePromptRepo
) -> None:
    active = _make_prompt_row(
        sub_agent="ops",
        status="active",
        prompt="line one\nline two\n",
        activated=datetime.now(UTC),
    )
    proposed = _make_prompt_row(
        sub_agent="ops",
        status="proposed",
        prompt="line one\nline two changed\nline three\n",
    )
    fake_prompt_repo.rows.extend([active, proposed])

    res = await client.get(
        f"{BASE}/sub-agents/ops/prompt-versions/{proposed.id}/diff"
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["requested_version_id"] == str(proposed.id)
    assert body["active_version_id"] == str(active.id)
    assert body["added"] > 0
    assert body["removed"] > 0
    assert "line two changed" in body["diff"]


@pytest.mark.asyncio
async def test_diff_no_active_falls_back_to_empty(
    client: AsyncClient, fake_prompt_repo: _FakePromptRepo
) -> None:
    proposed = _make_prompt_row(
        sub_agent="ops",
        status="proposed",
        prompt="line one\nline two\n",
    )
    fake_prompt_repo.rows.append(proposed)

    res = await client.get(
        f"{BASE}/sub-agents/ops/prompt-versions/{proposed.id}/diff"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["active_version_id"] is None
    # All added lines, nothing removed.
    assert body["added"] >= 2
    assert body["removed"] == 0


# ===========================================================================
# GET candidates
# ===========================================================================


@pytest.mark.asyncio
async def test_list_candidates_filters_by_status(
    client: AsyncClient, fake_candidate_store: _FakeCandidateStore
) -> None:
    shadow_a = _make_candidate_row(kind="skill", name="alpha", status="shadow")
    shadow_b = _make_candidate_row(
        kind="prompt_patch", name="beta_patch", status="shadow", target_ref="ops"
    )
    ab = _make_candidate_row(kind="tool_config", name="gamma", status="ab")
    for r in [shadow_a, shadow_b, ab]:
        fake_candidate_store.candidates[r.id] = r

    res = await client.get(f"{BASE}/candidates", params={"status": "shadow"})
    assert res.status_code == 200
    data = res.json()
    ids = {row["id"] for row in data}
    assert ids == {str(shadow_a.id), str(shadow_b.id)}


@pytest.mark.asyncio
async def test_list_candidates_rejects_unknown_status(client: AsyncClient) -> None:
    res = await client.get(f"{BASE}/candidates", params={"status": "unknown"})
    assert res.status_code == 400


# ===========================================================================
# POST promote (one-edge advance)
# ===========================================================================


@pytest.mark.asyncio
async def test_promote_proposed_to_shadow(
    client: AsyncClient, fake_candidate_store: _FakeCandidateStore
) -> None:
    row = _make_candidate_row(kind="skill", status="proposed")
    fake_candidate_store.candidates[row.id] = row

    res = await client.post(f"{BASE}/candidates/{row.id}/promote")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["from_status"] == "proposed"
    assert body["to_status"] == "shadow"
    assert body["action"] == "advanced"
    assert fake_candidate_store.candidates[row.id].status == "shadow"


@pytest.mark.asyncio
async def test_promote_ab_skill_invokes_activation(
    client: AsyncClient,
    fake_candidate_store: _FakeCandidateStore,
    fake_promoter: _FakePromoter,
) -> None:
    row = _make_candidate_row(kind="skill", status="ab")
    fake_candidate_store.candidates[row.id] = row

    res = await client.post(f"{BASE}/candidates/{row.id}/promote")
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "activated"
    assert body["to_status"] == "active"
    assert ("skill", row.id) in fake_promoter.activate_calls


@pytest.mark.asyncio
async def test_promote_active_candidate_is_noop(
    client: AsyncClient, fake_candidate_store: _FakeCandidateStore
) -> None:
    row = _make_candidate_row(status="active")
    fake_candidate_store.candidates[row.id] = row

    res = await client.post(f"{BASE}/candidates/{row.id}/promote")
    assert res.status_code == 200
    body = res.json()
    assert body["action"] == "noop"


@pytest.mark.asyncio
async def test_promote_404_when_candidate_missing(client: AsyncClient) -> None:
    res = await client.post(f"{BASE}/candidates/{uuid.uuid4()}/promote")
    assert res.status_code == 404


# ===========================================================================
# POST reject
# ===========================================================================


@pytest.mark.asyncio
async def test_reject_moves_to_rejected(
    client: AsyncClient, fake_candidate_store: _FakeCandidateStore
) -> None:
    row = _make_candidate_row(status="shadow")
    fake_candidate_store.candidates[row.id] = row

    res = await client.post(f"{BASE}/candidates/{row.id}/reject")
    assert res.status_code == 200
    body = res.json()
    assert body["to_status"] == "rejected"
    assert fake_candidate_store.candidates[row.id].status == "rejected"


@pytest.mark.asyncio
async def test_reject_terminal_state_is_409(
    client: AsyncClient, fake_candidate_store: _FakeCandidateStore
) -> None:
    row = _make_candidate_row(status="retired")
    fake_candidate_store.candidates[row.id] = row

    res = await client.post(f"{BASE}/candidates/{row.id}/reject")
    assert res.status_code == 409


# ===========================================================================
# Admin guard (R-8.4)
# ===========================================================================


@pytest.mark.asyncio
async def test_mutating_endpoints_require_admin(
    app: FastAPI,
    fake_prompt_repo: _FakePromptRepo,
    fake_candidate_store: _FakeCandidateStore,
) -> None:
    """Without the admin override, every mutating endpoint returns 401."""
    # Drop the admin override so the real dependency runs.
    app.dependency_overrides.pop(require_admin, None)

    row = _make_prompt_row(sub_agent="ops", status="proposed")
    fake_prompt_repo.rows.append(row)
    cand = _make_candidate_row(status="proposed")
    fake_candidate_store.candidates[cand.id] = cand

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        endpoints = [
            ("GET", f"{BASE}/sub-agents/ops/prompt-versions"),
            ("POST", f"{BASE}/sub-agents/ops/rollback"),
            (
                "POST",
                f"{BASE}/sub-agents/ops/prompt-versions/{row.id}/activate",
            ),
            (
                "GET",
                f"{BASE}/sub-agents/ops/prompt-versions/{row.id}/diff",
            ),
            ("GET", f"{BASE}/candidates?status=shadow"),
            ("POST", f"{BASE}/candidates/{cand.id}/promote"),
            ("POST", f"{BASE}/candidates/{cand.id}/reject"),
        ]
        for method, url in endpoints:
            res = await c.request(method, url)
            assert res.status_code == 401, (
                f"{method} {url} returned {res.status_code}, expected 401"
            )
