"""Regression tests for ``tool_manager.skill_scan`` ``.candidate/`` exclusion.

Spec task 21.5 (agent-runtime-optimization-evolution), R-3.14.

The reflection pipeline (task 21.4 / ``SkillCandidateStore``) materialises
proposed skills under ``data/skills/.candidate/<name>/SKILL.md`` before
they are evaluated and promoted. Until a candidate is promoted, it must
NOT be visible to any component that discovers live skills on the
filesystem:

* :func:`src.services.skill_sync.list_filesystem_skills` — feeds the
  control-plane sync UI and ``auto_register_filesystem_skills`` at
  startup.
* :func:`src.services.skill_sync.batch_inconsistency_count` — drives
  the consistency badge in the admin surface.

Both walk ``data/skills/`` with :meth:`pathlib.Path.rglob`, so the
naive implementation would happily pick up ``.candidate/*/SKILL.md``
alongside the real skills. This test suite pins the exclusion rule in
place.

Related guards:

* ``.gitignore`` contains ``data/skills/.candidate/`` so the staging
  area never ships in source control (checked in a separate test).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services import skill_sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SKILL_TEMPLATE = """---
name: {name}
description: {description}
---

{body}
"""


def _write_skill(root: Path, rel_dir: str, name: str, description: str, body: str = "body") -> Path:
    """Create a ``<root>/<rel_dir>/SKILL.md`` and return the SKILL.md path."""
    skill_dir = root / rel_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    md = skill_dir / "SKILL.md"
    md.write_text(
        _SKILL_TEMPLATE.format(name=name, description=description, body=body),
        encoding="utf-8",
    )
    return md


@pytest.fixture()
def patched_skills_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``skill_sync.SKILLS_DIR`` at a tmp path for each test."""
    fake_root = tmp_path / "skills"
    fake_root.mkdir()
    monkeypatch.setattr(skill_sync, "SKILLS_DIR", fake_root)
    return fake_root


# ---------------------------------------------------------------------------
# list_filesystem_skills
# ---------------------------------------------------------------------------


def test_list_filesystem_skills_finds_normal_skill(patched_skills_dir: Path) -> None:
    """Sanity: a normal skill under ``data/skills/<name>/`` is discovered."""
    _write_skill(patched_skills_dir, "normal_skill", "normal_skill", "a real skill")

    skills = skill_sync.list_filesystem_skills()

    names = {s["name"] for s in skills}
    assert names == {"normal_skill"}


def test_list_filesystem_skills_skips_candidate_subtree(patched_skills_dir: Path) -> None:
    """``.candidate/<name>/SKILL.md`` is invisible to the scanner (R-3.14)."""
    _write_skill(patched_skills_dir, "live_skill", "live_skill", "live")
    _write_skill(
        patched_skills_dir,
        ".candidate/proposed_skill",
        "proposed_skill",
        "a reflector candidate",
    )

    skills = skill_sync.list_filesystem_skills()

    names = {s["name"] for s in skills}
    assert "proposed_skill" not in names
    assert names == {"live_skill"}


def test_list_filesystem_skills_ignores_deeply_nested_candidate(
    patched_skills_dir: Path,
) -> None:
    """Exclusion applies when ``.candidate`` appears at any depth."""
    # Nested layout: data/skills/standard/devops/<name>/SKILL.md is legal; one
    # placed under .candidate/ at any depth must still be excluded.
    _write_skill(
        patched_skills_dir,
        ".candidate/standard/devops/nested_proposal",
        "nested_proposal",
        "nested candidate",
    )
    _write_skill(
        patched_skills_dir,
        "standard/devops/classified_live",
        "classified_live",
        "live classified skill",
    )

    skills = skill_sync.list_filesystem_skills()

    names = {s["name"] for s in skills}
    assert "nested_proposal" not in names
    assert names == {"classified_live"}


def test_list_filesystem_skills_only_candidates_returns_empty(
    patched_skills_dir: Path,
) -> None:
    """With ``.candidate/`` as the only content, the scanner returns nothing."""
    _write_skill(patched_skills_dir, ".candidate/only_one", "only_one", "only candidate")
    _write_skill(patched_skills_dir, ".candidate/only_two", "only_two", "only candidate")

    assert skill_sync.list_filesystem_skills() == []


# ---------------------------------------------------------------------------
# batch_inconsistency_count
# ---------------------------------------------------------------------------


def _fake_tool(name: str, description: str, body: str) -> SimpleNamespace:
    """Minimal duck-typed Tool that satisfies ``compute_content_hash``."""
    return SimpleNamespace(
        name=name,
        type="skill",
        description=description,
        config={"skill_prompt": body},
    )


def test_batch_inconsistency_count_does_not_consult_candidate_files(
    patched_skills_dir: Path,
) -> None:
    """A DB tool named ``proposed_skill`` is considered inconsistent even when
    a SKILL.md happens to live under ``.candidate/`` with matching content —
    because that file is off-limits for live-skill discovery (R-3.14)."""
    # Create a candidate SKILL.md whose content would hash-match the DB row
    # IF the scanner read it. The exclusion must make it invisible.
    body = "candidate-only body"
    _write_skill(
        patched_skills_dir,
        ".candidate/proposed_skill",
        "proposed_skill",
        "candidate desc",
        body=body,
    )

    tool = _fake_tool("proposed_skill", "candidate desc", body)

    # Not found → counted as inconsistent.
    assert skill_sync.batch_inconsistency_count([tool]) == 1


def test_batch_inconsistency_count_recognises_live_skill(
    patched_skills_dir: Path,
) -> None:
    """Control: a live skill with matching hash is NOT counted as inconsistent."""
    body = "live body"
    _write_skill(
        patched_skills_dir,
        "live_skill",
        "live_skill",
        "live desc",
        body=body,
    )

    tool = _fake_tool("live_skill", "live desc", body)

    assert skill_sync.batch_inconsistency_count([tool]) == 0


# ---------------------------------------------------------------------------
# .gitignore
# ---------------------------------------------------------------------------


def test_gitignore_excludes_candidate_staging_area() -> None:
    """R-3.14: the repo-root ``.gitignore`` must list ``data/skills/.candidate/``.

    Anchored on the workspace root so the test passes regardless of where
    pytest is invoked from inside the server package.
    """
    # server/tests/services/test_skill_scan_excludes_candidate.py
    # → server/tests/services → server/tests → server → <repo>
    repo_root = Path(__file__).resolve().parents[3]
    gitignore = repo_root / ".gitignore"
    assert gitignore.is_file(), f".gitignore not found at {gitignore}"

    text = gitignore.read_text(encoding="utf-8")
    # Accept either an explicit root-anchored entry or a bare pattern.
    candidates = (
        "data/skills/.candidate/",
        "/data/skills/.candidate/",
        "server/data/skills/.candidate/",
    )
    assert any(c in text for c in candidates), (
        "Expected one of "
        + ", ".join(candidates)
        + " in .gitignore (R-3.14); got:\n"
        + text
    )
