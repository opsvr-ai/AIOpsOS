"""Unit + PBT tests for the WikiCompilerWorker pipeline.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 12.5 / R-2.12.

**Validates: Requirements 2.12, 2.13**

These tests drive :func:`src.services.kb.compile_logic.compile_wiki_async`
directly with stubbed LLM + DB factory — no Celery broker, no Kafka, no
real Postgres. The Celery wrapper in
``src.workers.tasks.wiki_compile.compile_wiki`` is just a thin adapter
so exercising the async body exercises the task for all practical
purposes.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st

from src.config import settings as app_settings
from src.services.kb.compile_logic import compile_wiki_async
from src.services.kb_summarizer import extract_frontmatter_and_body


# ---------------------------------------------------------------------------
# Fakes: LLM, wiki_compile_log store, DB factory
# ---------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    content: str


class _FakeSummarizeLLM:
    """Returns a canned JSON page list and counts invocations."""

    def __init__(
        self,
        *,
        pages: list[dict] | None = None,
        filenames: list[str] | None = None,
    ) -> None:
        self._pages = pages
        self._filenames = filenames or ["page-001"]
        self.calls: list[str] = []

    async def ainvoke(self, messages):
        # messages[-1].content is the user payload
        self.calls.append(getattr(messages[-1], "content", str(messages[-1])))
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if self._pages is not None:
            payload = self._pages
        else:
            payload = []
            for name in self._filenames:
                fm_lines = [
                    f"title: {name}",
                    f"created: {today}",
                    f"updated: {today}",
                    "type: concept",
                    "tags: [auto]",
                    f"sources: [raw/input.md]",
                ]
                body = (
                    f"# {name}\n\n"
                    "这是 [[another-page]] 引用的一个知识条目，包含必须的 ^[raw/input.md] "
                    "provenance 标记。内容足够长以便摘要。\n"
                    "更多正文以保证有截取空间。\n"
                )
                content = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
                payload.append({"filename": name, "content": content})
        return _FakeResponse(content=json.dumps(payload, ensure_ascii=False))


class _FakeCompileLog:
    """In-memory stand-in for the ``wiki_compile_log`` table."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    # Matches the async DB-factory contract used by compile_logic.
    def factory(self):
        store = self

        @asynccontextmanager
        async def _factory():
            yield _FakeSession(store)

        return _factory


class _FakeSession:
    def __init__(self, store: _FakeCompileLog) -> None:
        self._store = store

    async def execute(self, stmt, params=None):
        sql = str(stmt).strip()
        params = params or {}
        head = " ".join(sql.split()).lower()

        if head.startswith("select") and "from wiki_compile_log" in head:
            row = self._store.rows.get(params["rp"])
            return _FakeResult([_FakeRow(**row)] if row else [])

        if head.startswith("insert into wiki_compile_log"):
            self._store.rows[params["rp"]] = {
                "raw_path": params["rp"],
                "raw_sha256": params["sha"],
                "last_compiled_at": datetime.now(UTC),
                "wiki_path": params.get("wp"),
            }
            return _FakeResult([])

        return _FakeResult([])

    async def commit(self):
        return None


class _FakeRow:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeResult:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows
        self.rowcount = len(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0].__dict__[next(iter(self._rows[0].__dict__))] if self._rows else None


# ---------------------------------------------------------------------------
# Fixture: sandbox wiki_path inside tmp_path so finalize_wiki_pages is contained
# ---------------------------------------------------------------------------


@pytest.fixture
def wiki_sandbox(tmp_path, monkeypatch):
    """Point ``settings.wiki_path`` at ``tmp_path`` and ensure raw/ + wiki/ exist."""
    root = tmp_path
    monkeypatch.setattr(app_settings, "wiki_path", str(root))
    (root / "raw").mkdir(parents=True, exist_ok=True)
    (root / "wiki").mkdir(parents=True, exist_ok=True)
    return root


def _write_raw(root: Path, name: str, body: str) -> str:
    path = root / "raw" / name
    path.write_text(body, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_compile_same_sha_skips(wiki_sandbox):
    """Second compile of an unchanged file short-circuits without LLM call."""

    async def _run():
        raw = _write_raw(wiki_sandbox, "input.md", "# Hello\n\nSome content.\n")
        llm = _FakeSummarizeLLM(filenames=["concept-hello"])
        log = _FakeCompileLog()

        r1 = await compile_wiki_async(
            raw, db_factory=log.factory(), llm=llm, wiki_root=str(wiki_sandbox / "wiki")
        )
        assert r1.status == "ok"
        assert r1.pages_written == 1
        assert len(llm.calls) == 1

        r2 = await compile_wiki_async(
            raw, db_factory=log.factory(), llm=llm, wiki_root=str(wiki_sandbox / "wiki")
        )
        assert r2.status == "skipped"
        assert r2.reason == "unchanged"
        # No new LLM call.
        assert len(llm.calls) == 1

    asyncio.run(_run())


def test_compile_changed_sha_runs(wiki_sandbox):
    """Modifying raw bytes triggers a fresh compile with updated sha."""

    async def _run():
        raw = _write_raw(wiki_sandbox, "input.md", "# Hello v1\n\nSome content.\n")
        llm = _FakeSummarizeLLM(filenames=["concept-hello"])
        log = _FakeCompileLog()

        r1 = await compile_wiki_async(
            raw, db_factory=log.factory(), llm=llm, wiki_root=str(wiki_sandbox / "wiki")
        )
        assert r1.status == "ok"
        sha_v1 = r1.raw_sha256

        # Change the file bytes.
        Path(raw).write_text("# Hello v2\n\nRewritten content here.\n", encoding="utf-8")

        r2 = await compile_wiki_async(
            raw, db_factory=log.factory(), llm=llm, wiki_root=str(wiki_sandbox / "wiki")
        )
        assert r2.status == "ok"
        assert r2.raw_sha256 != sha_v1
        assert len(llm.calls) == 2

    asyncio.run(_run())


def test_compile_writes_precomputed_summary(wiki_sandbox):
    """Every wiki page emitted by the compiler carries precomputed_summary."""

    async def _run():
        raw = _write_raw(wiki_sandbox, "input.md", "# Hello\n\nBody.\n")
        llm = _FakeSummarizeLLM(filenames=["a", "b"])
        log = _FakeCompileLog()

        r = await compile_wiki_async(
            raw, db_factory=log.factory(), llm=llm, wiki_root=str(wiki_sandbox / "wiki")
        )
        assert r.status == "ok"
        assert r.pages_written == 2
        assert r.precomputed_summaries == 2

        for page_path in r.page_paths:
            text = Path(page_path).read_text(encoding="utf-8")
            fm, _ = extract_frontmatter_and_body(text)
            assert fm.get("precomputed_summary"), (
                f"missing precomputed_summary on {page_path}: fm={fm}"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PBT: compile idempotency (R-2.12)
# ---------------------------------------------------------------------------


pytestmark_property = [pytest.mark.property]


@hsettings(
    max_examples=6,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(
    content_length=st.integers(min_value=100, max_value=5000),
    file_count=st.integers(min_value=1, max_value=5),
)
@pytest.mark.property
def test_compile_idempotency(content_length: int, file_count: int, tmp_path_factory):
    """Unchanged file → skip; changed file → run.

    Hypothesis varies the content length (100..5000 chars) and number of
    generated wiki pages (1..5). For each shape we assert:

    * first compile returns ``ok`` with ``file_count`` pages written;
    * second compile (same bytes) returns ``skipped`` with no LLM calls;
    * mutating the raw file → third compile returns ``ok`` again.
    """

    async def _run():
        # Per-example sandbox so hypothesis examples don't collide.
        root = tmp_path_factory.mktemp("wiki-pbt")
        raw_dir = root / "raw"
        wiki_dir = root / "wiki"
        raw_dir.mkdir(parents=True, exist_ok=True)
        wiki_dir.mkdir(parents=True, exist_ok=True)

        # Content length is driven by hypothesis; just use repeating ASCII.
        body = "x" * content_length
        raw_path = raw_dir / "input.md"
        raw_path.write_text("# Page\n\n" + body + "\n", encoding="utf-8")

        filenames = [f"page-{i:03d}" for i in range(file_count)]
        llm = _FakeSummarizeLLM(filenames=filenames)
        log = _FakeCompileLog()

        r1 = await compile_wiki_async(
            str(raw_path),
            db_factory=log.factory(),
            llm=llm,
            wiki_root=str(wiki_dir),
        )
        assert r1.status == "ok"
        assert r1.pages_written == file_count
        calls_after_first = len(llm.calls)

        # Second compile — same bytes → skip, no further LLM calls.
        r2 = await compile_wiki_async(
            str(raw_path),
            db_factory=log.factory(),
            llm=llm,
            wiki_root=str(wiki_dir),
        )
        assert r2.status == "skipped"
        assert r2.reason == "unchanged"
        assert len(llm.calls) == calls_after_first

        # Mutate bytes → third compile runs again.
        raw_path.write_text(
            "# Page\n\n" + body + "\nAPPENDED\n", encoding="utf-8"
        )
        r3 = await compile_wiki_async(
            str(raw_path),
            db_factory=log.factory(),
            llm=llm,
            wiki_root=str(wiki_dir),
        )
        assert r3.status == "ok"
        assert len(llm.calls) == calls_after_first + 1

    asyncio.run(_run())
