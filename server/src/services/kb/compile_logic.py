"""Async body of :func:`compile_wiki` — the WikiCompilerWorker pipeline.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 12.1 /
R-2.12 / R-2.13.

The Celery wrapper in :mod:`src.workers.tasks.wiki_compile` is kept thin;
all state + LLM orchestration lives here so tests can drive the pipeline
with injected DB / LLM fakes.

High-level flow::

    raw_sha256 = sha256(file_bytes)
    SELECT wiki_compile_log WHERE raw_path = ...
      ↓
    if row.raw_sha256 == raw_sha256 → skip (unchanged)
      ↓
    existing_pages = glob wiki/ + frontmatter.sources matches raw
      ↓
    if existing_pages:  diff-based compile prompt
    else:               summarize_raw_file (full compile)
      ↓
    for each page: inject precomputed_summary (LLM or truncation)
      ↓
    finalize_wiki_pages  +  update_index
      ↓
    UPSERT wiki_compile_log row (ON CONFLICT (raw_path) DO UPDATE)
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text

from src.core.metrics import wiki_compile_total
from src.services.kb_summarizer import (
    add_precomputed_summary,
    compute_sha256,
    extract_frontmatter_and_body,
    finalize_wiki_pages,
    summarize_raw_file,
    update_index,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CompileResult:
    """Return value of :func:`compile_wiki_async`."""

    status: str                                     # ok | skipped | error
    raw_path: str
    pages_written: int = 0
    precomputed_summaries: int = 0
    raw_sha256: str | None = None
    reason: str | None = None
    page_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return out


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


DIFF_COMPILE_SYSTEM = """你是 wiki 增量更新器。给定旧 wiki 页面 + 原始文档新版本，
输出经过更新的 wiki 页面 JSON 数组。每个元素包含 ``filename`` 和 ``content``
两个字段；只返回 JSON 数组，不要其它文字。保持 frontmatter 中的
``sources: [raw/<filename>.md]`` 字段不变，并补齐 ``updated`` 日期。"""


SUMMARY_SYSTEM = (
    "用 200 字以内中文为以下 wiki 页面撰写摘要，只返回摘要文本，"
    "不要 markdown 格式或解释。"
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def compile_wiki_async(
    raw_path: str,
    *,
    db_factory: Any | None = None,
    llm: Any | None = None,
    summary_llm: Any | None = None,
    wiki_root: str | None = None,
) -> CompileResult:
    """Compile a raw knowledge file into one or more wiki pages.

    Idempotent: a second call with the same file bytes short-circuits on
    the ``wiki_compile_log`` sha256 check.

    All external dependencies are injectable so unit tests can avoid DB
    / network round-trips (see ``tests/workers/test_wiki_compile.py``).
    """
    if not os.path.isfile(raw_path):
        _metric("error")
        return CompileResult(
            status="error",
            raw_path=raw_path,
            reason="raw_path_missing",
        )

    raw_sha256 = compute_sha256(raw_path)
    factory = db_factory  # may be None in unit tests — then we skip DB writes

    # ------------------------------------------------------------------
    # 1. Short-circuit on unchanged sha256
    # ------------------------------------------------------------------
    prior = await _fetch_log_row(factory, raw_path)
    if prior and prior.get("raw_sha256") == raw_sha256:
        _metric("skipped")
        return CompileResult(
            status="skipped",
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            reason="unchanged",
        )

    # ------------------------------------------------------------------
    # 2. Resolve LLM + discover existing pages for this raw file
    # ------------------------------------------------------------------
    model = llm if llm is not None else await _default_llm()
    existing_pages = _find_existing_pages_for_raw(raw_path, wiki_root=wiki_root)

    # ------------------------------------------------------------------
    # 3. Compile — diff path when pages already exist, full path otherwise
    # ------------------------------------------------------------------
    try:
        if existing_pages:
            pages = await _compile_with_diff(
                raw_path, existing_pages, model
            )
        else:
            pages = await _compile_full(raw_path, model, wiki_root=wiki_root)
    except Exception as exc:
        logger.exception("compile_wiki: LLM step failed")
        _metric("error")
        return CompileResult(
            status="error",
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            reason=f"llm_failed:{exc.__class__.__name__}",
        )

    if not pages:
        _metric("error")
        return CompileResult(
            status="error",
            raw_path=raw_path,
            raw_sha256=raw_sha256,
            reason="no_pages_generated",
        )

    # ------------------------------------------------------------------
    # 4. Inject precomputed_summary into every page
    # ------------------------------------------------------------------
    summaries_count = 0
    for page in pages:
        content = page.get("content", "")
        if not content:
            continue
        fm, body = extract_frontmatter_and_body(content)
        if "precomputed_summary" in fm and fm.get("precomputed_summary"):
            # Already carries a summary — don't overwrite.
            continue
        summary = await _produce_summary(body, summary_llm)
        page["content"] = add_precomputed_summary(fm, body, summary)
        summaries_count += 1

    # ------------------------------------------------------------------
    # 5. Write pages + update index
    # ------------------------------------------------------------------
    written = finalize_wiki_pages(pages, raw_path)
    try:
        update_index(pages)
    except Exception:
        logger.exception("compile_wiki: update_index failed (non-fatal)")

    # ------------------------------------------------------------------
    # 6. UPSERT wiki_compile_log
    # ------------------------------------------------------------------
    first_wiki_path = written[0] if written else None
    await _upsert_log_row(
        factory,
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        wiki_path=first_wiki_path,
    )

    _metric("ok")
    return CompileResult(
        status="ok",
        raw_path=raw_path,
        raw_sha256=raw_sha256,
        pages_written=len(written),
        precomputed_summaries=summaries_count,
        page_paths=list(written),
    )


# ---------------------------------------------------------------------------
# Compile paths
# ---------------------------------------------------------------------------


async def _compile_full(
    raw_path: str, model: Any, *, wiki_root: str | None = None
) -> list[dict]:
    """Fresh compile when no existing wiki page references the raw file."""
    existing_stems: list[str] = []
    from src.config import settings
    root = wiki_root or os.path.join(settings.wiki_path, "wiki")
    if os.path.isdir(root):
        existing_stems = sorted(p.stem for p in Path(root).glob("*.md"))
    return await summarize_raw_file(
        raw_path,
        existing_pages=existing_stems,
    ) if model is None else await _summarize_with_llm(raw_path, existing_stems, model)


async def _summarize_with_llm(
    raw_path: str, existing_stems: list[str], model: Any
) -> list[dict]:
    """Call ``summarize_raw_file`` but with an injected model.

    ``summarize_raw_file`` resolves the model via ``get_default_model`` when
    called without hooks; we duplicate the smallest amount of its body here
    so tests can inject a stub LLM without monkey-patching the module.
    """
    # Reuse the production system prompt from kb_summarizer.
    from src.services.kb_summarizer import SUMMARIZE_SYSTEM, _parse_page_list

    with open(raw_path, encoding="utf-8") as fh:
        raw_content = fh.read()
    fname = os.path.basename(raw_path)

    existing_ctx = ""
    if existing_stems:
        existing_ctx = "\n## Existing wiki pages (cross-reference these, don't duplicate):\n"
        for p in existing_stems:
            existing_ctx += f"- [[{p}]]\n"

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    user_msg = (
        f"## Raw source: {fname}\n\n"
        f"{raw_content[:12000]}\n"
        f"{existing_ctx}\n"
        f"Today's date: {today}\n"
        f"Source reference: raw/{fname}\n\n"
        "Generate wiki pages for this source. Return ONLY a JSON array."
    )

    resp = await model.ainvoke(
        [
            SystemMessage(content=SUMMARIZE_SYSTEM),
            HumanMessage(content=user_msg),
        ]
    )
    raw = getattr(resp, "content", resp)
    if isinstance(raw, (list, tuple)):
        raw = "".join(str(p) for p in raw)
    return _parse_page_list(str(raw))


async def _compile_with_diff(
    raw_path: str,
    existing_pages: list[dict],
    model: Any,
) -> list[dict]:
    """Diff-based compile: feed the LLM OLD_WIKI + raw_bytes so it can patch."""
    with open(raw_path, encoding="utf-8") as fh:
        raw_content = fh.read()

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    wiki_block = "\n\n".join(
        f"<<<page filename={p['filename']}>>>\n{p['content']}\n<<<end>>>"
        for p in existing_pages
    )
    user_msg = (
        f"## 旧 wiki 页面\n{wiki_block}\n\n"
        f"## 原始文档新版本 (raw/{os.path.basename(raw_path)})\n"
        f"{raw_content[:12000]}\n\n"
        f"今天日期: {today}。请输出完整的新 wiki 页面 JSON 数组。"
    )

    resp = await model.ainvoke(
        [
            SystemMessage(content=DIFF_COMPILE_SYSTEM),
            HumanMessage(content=user_msg),
        ]
    )
    raw = getattr(resp, "content", resp)
    if isinstance(raw, (list, tuple)):
        raw = "".join(str(p) for p in raw)
    from src.services.kb_summarizer import _parse_page_list

    pages = _parse_page_list(str(raw))
    if not pages:
        # LLM returned nothing useful; fall back to re-writing the existing pages as-is.
        pages = [
            {"filename": p["filename"], "content": p["content"]}
            for p in existing_pages
        ]
    return pages


# ---------------------------------------------------------------------------
# precomputed_summary generator
# ---------------------------------------------------------------------------


async def _produce_summary(body: str, summary_llm: Any | None) -> str:
    """Return a ≤ 300-char summary for a wiki page body.

    When ``summary_llm`` is provided we call it; otherwise we fall back
    to a deterministic truncation of the page body stripped of wikilinks
    (matches the lightweight path required by task 12.1 step 4).
    """
    stripped = _strip_markdown(body)
    if summary_llm is not None:
        try:
            resp = await summary_llm.ainvoke(
                [
                    SystemMessage(content=SUMMARY_SYSTEM),
                    HumanMessage(content=stripped[:4000]),
                ]
            )
            raw = getattr(resp, "content", resp)
            if isinstance(raw, (list, tuple)):
                raw = "".join(str(p) for p in raw)
            summary = str(raw).strip()
            if summary:
                return summary[:300]
        except Exception:
            logger.exception("compile_wiki: summary_llm failed; using truncation")

    # Deterministic fallback — first 300 chars of stripped body.
    return stripped[:300].strip()


_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_PROVENANCE_RE = re.compile(r"\^\[[^\]]+\]")


def _strip_markdown(text: str) -> str:
    """Strip wikilinks, provenance markers and common markdown noise."""
    if not text:
        return ""
    out = _WIKILINK_RE.sub(lambda m: m.group(1), text)
    out = _PROVENANCE_RE.sub("", out)
    # Strip headings / emphasis marks / list bullets for a flat summary.
    out = re.sub(r"^#+\s*", "", out, flags=re.MULTILINE)
    out = re.sub(r"[*_`]", "", out)
    out = re.sub(r"^\s*[-*]\s+", "", out, flags=re.MULTILINE)
    # Collapse whitespace.
    out = re.sub(r"\s+", " ", out)
    return out.strip()


# ---------------------------------------------------------------------------
# Existing-pages discovery
# ---------------------------------------------------------------------------


def _find_existing_pages_for_raw(
    raw_path: str, *, wiki_root: str | None = None
) -> list[dict]:
    """Return wiki pages whose frontmatter ``sources`` references ``raw_path``."""
    from src.config import settings

    root = wiki_root or os.path.join(settings.wiki_path, "wiki")
    if not os.path.isdir(root):
        return []

    fname = os.path.basename(raw_path)
    needle = f"raw/{fname}"
    out: list[dict] = []
    for p in Path(root).glob("*.md"):
        try:
            with open(p, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        fm, _ = extract_frontmatter_and_body(content)
        sources = fm.get("sources") or []
        if isinstance(sources, str):
            sources = [sources]
        refs = [str(s).strip() for s in sources]
        if needle in refs or fname in refs:
            out.append({"filename": p.stem, "content": content, "path": str(p)})
    return out


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _fetch_log_row(db_factory: Any | None, raw_path: str) -> dict | None:
    """Return the ``wiki_compile_log`` row for ``raw_path`` or ``None``."""
    if db_factory is None:
        return None
    try:
        async with db_factory() as session:
            row = await session.execute(
                text(
                    "SELECT raw_path, raw_sha256, last_compiled_at, wiki_path "
                    "FROM wiki_compile_log WHERE raw_path = :rp"
                ),
                {"rp": raw_path},
            )
            fetched = row.first()
    except Exception:
        logger.debug(
            "wiki_compile_log fetch failed (treating as absent)", exc_info=True
        )
        return None
    if fetched is None:
        return None
    return {
        "raw_path": fetched.raw_path,
        "raw_sha256": fetched.raw_sha256,
        "last_compiled_at": fetched.last_compiled_at,
        "wiki_path": fetched.wiki_path,
    }


async def _upsert_log_row(
    db_factory: Any | None,
    *,
    raw_path: str,
    raw_sha256: str,
    wiki_path: str | None,
) -> None:
    if db_factory is None:
        return
    try:
        async with db_factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO wiki_compile_log
                        (raw_path, raw_sha256, last_compiled_at, wiki_path)
                    VALUES (:rp, :sha, now(), :wp)
                    ON CONFLICT (raw_path) DO UPDATE
                    SET raw_sha256 = EXCLUDED.raw_sha256,
                        last_compiled_at = EXCLUDED.last_compiled_at,
                        wiki_path = EXCLUDED.wiki_path
                    """
                ),
                {"rp": raw_path, "sha": raw_sha256, "wp": wiki_path},
            )
            await session.commit()
    except Exception:
        logger.exception("wiki_compile_log upsert failed (non-fatal)")


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


async def _default_llm() -> Any | None:
    try:
        from src.core.model_factory import get_default_model

        return await get_default_model()
    except Exception:
        logger.debug("default LLM unavailable for wiki compile", exc_info=True)
        return None


def _metric(status: str) -> None:
    try:
        wiki_compile_total.labels(status=status).inc()
    except Exception:
        logger.debug("wiki_compile_total metric inc failed", exc_info=True)


__all__ = ["CompileResult", "compile_wiki_async"]
