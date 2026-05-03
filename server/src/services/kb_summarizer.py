"""LLM-Wiki summarization pipeline.

Takes raw source documents and produces wiki-compliant markdown pages
with YAML frontmatter, wikilinks, and cross-references.
"""

import hashlib
import logging
import os
import re
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import settings

logger = logging.getLogger(__name__)


def _wiki_dir() -> str:
    return settings.wiki_path


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _safe_slug(title: str) -> str:
    s = title.lower().replace(" ", "-")
    s = re.sub(r"[^a-z0-9一-鿿_-]", "", s)
    return s[:80]


def _compute_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


SUMMARIZE_SYSTEM = """You are a knowledge curator — an alchemist who transforms raw documents
into structured gold. Like a cartographer mapping uncharted terrain, you read the
contours of a source text and chart it into the elegant, interconnected geography
of the LLM-Wiki standard.

Your task: read a raw source document and produce one or more wiki pages.

## Rules

1. **YAML frontmatter required** on every page:
```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query | summary
tags: [from taxonomy]
sources: [raw/filename.md]
---
```

2. **Wikilinks**: Use `[[page-name]]` to link between pages. Every page must link to at least 2 other pages.

3. **Structure**: Entity pages need overview, key facts, relationships. Concept pages need definition, current knowledge, open questions.

4. **Provenance**: Append `^[raw/filename.md]` markers on claims from specific sources.

5. **Chinese output**: Write page content in Chinese (titles can be bilingual if useful).

6. **Output format**: Return a JSON array of page objects, each with `filename` (without .md) and `content` (full markdown with frontmatter).

7. **Existing pages context**: If provided, incorporate cross-references to existing pages rather than duplicating.

Return ONLY the JSON array, no other text."""


async def summarize_raw_file(
    filepath: str,
    existing_pages: list[str] | None = None,
    model_name: str = "deepseek-v4-flash",
) -> list[dict]:
    """Process a raw source file and return wiki page dicts with filename + content."""
    content = _read_file(filepath)
    fname = os.path.basename(filepath)

    existing_ctx = ""
    if existing_pages:
        existing_ctx = "\n## Existing wiki pages (cross-reference these, don't duplicate):\n"
        for p in existing_pages:
            existing_ctx += f"- [[{p}]]\n"

    from src.core.model_factory import get_default_model
    llm = await get_default_model()

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    user_msg = (
        f"## Raw source: {fname}\n\n"
        f"{content[:12000]}\n"
        f"{existing_ctx}\n"
        f"Today's date: {today}\n"
        f"Source reference: raw/{fname}\n\n"
        "Generate wiki pages for this source. Return ONLY a JSON array."
    )

    resp = await llm.ainvoke([
        SystemMessage(content=SUMMARIZE_SYSTEM),
        HumanMessage(content=user_msg),
    ])

    return _parse_page_list(resp.content)


def finalize_wiki_pages(pages: list[dict], source_file: str) -> list[str]:
    """Write wiki pages to disk. Returns list of written file paths."""
    written = []
    wiki_path = os.path.join(_wiki_dir(), "wiki")

    for page in pages:
        filename = page.get("filename", "untitled")
        content = page.get("content", "")
        if not filename or not content:
            continue

        dest = os.path.join(wiki_path, f"{filename}.md")
        _write_file(dest, content)
        written.append(dest)
        logger.info("Wiki page written: %s", dest)

    return written


def update_index(pages: list[dict]) -> None:
    """Add new pages to index.md if not already present."""
    index_path = os.path.join(_wiki_dir(), "index.md")
    existing = _read_file(index_path) if os.path.exists(index_path) else "# Wiki Index\n\n> Content catalog.\n"

    for page in pages:
        filename = page.get("filename", "")
        if not filename:
            continue
        if f"[[{filename}]]" in existing:
            continue

        content = page.get("content", "")
        type_match = re.search(r"type:\s*(\w+)", content)
        ptype = type_match.group(1) if type_match else "concept"

        section_marker = {
            "entity": "## Entities",
            "concept": "## Concepts",
            "comparison": "## Comparisons",
            "query": "## Queries",
            "summary": "## Summaries",
        }.get(ptype, "## Concepts")

        if section_marker not in existing:
            existing += f"\n{section_marker}\n"

        summary = content.split("\n")[0].lstrip("# ").strip() if content else filename
        entry = f"- [[{filename}]] — {summary}\n"
        existing = existing.replace(
            section_marker + "\n",
            section_marker + "\n" + entry,
        )

    _write_file(index_path, existing)


def append_log(action: str, subject: str, files: list[str]) -> None:
    """Append an entry to log.md."""
    log_path = os.path.join(_wiki_dir(), "log.md")
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    if not os.path.exists(log_path):
        _write_file(log_path, "# Wiki Log\n\n> Chronological record of all wiki actions. Append-only.\n")

    entry = f"\n## [{today}] {action} | {subject}\n"
    for f in files:
        entry += f"- {os.path.basename(f)}\n"

    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(entry)


def add_raw_frontmatter(filepath: str, source_url: str = "") -> str:
    """Ensure raw file has frontmatter with sha256. Returns the sha256."""
    content = _read_file(filepath)

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
            current_hash = _compute_sha256(body)
            new_fm = re.sub(r"sha256:\s*\S*", f"sha256: {current_hash}", parts[1])
            if "sha256:" not in parts[1]:
                new_fm = parts[1].rstrip() + f"\nsha256: {current_hash}\n"
            new_content = f"---{new_fm}---{body}"
            _write_file(filepath, new_content)
            return current_hash

    body = content
    content_hash = _compute_sha256(body)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    fm = (
        f"---\n"
        f"source_url: {source_url}\n"
        f"ingested: {today}\n"
        f"sha256: {content_hash}\n"
        f"---\n\n"
    )
    _write_file(filepath, fm + body)
    return content_hash


async def compile_pipeline(filepath: str) -> dict:
    """Formal 5-stage compile pipeline: ingest → summarize → crossref → finalize → index."""

    result: dict = {
        "filepath": filepath,
        "ok": False,
        "stages": {},
        "wiki_pages_created": 0,
        "error": "",
    }

    try:
        # Stage 1: Ingest
        sha = add_raw_frontmatter(filepath)
        result["stages"]["ingest"] = {"sha256": sha}

        # Stage 2: Summarize
        existing = []
        wp = os.path.join(_wiki_dir(), "wiki")
        if os.path.isdir(wp):
            existing = sorted(p.stem for p in Path(wp).glob("*.md"))
        pages = await summarize_raw_file(filepath, existing_pages=existing)
        if not pages:
            result["error"] = "LLM generated no pages"
            return result
        result["stages"]["summarize"] = {"pages": [p.get("filename", "") for p in pages]}

        # Stage 3: Crossref — inject links_to into frontmatter
        from src.services.kb_crossref import parse_wikilinks as _parse_wl

        for page in pages:
            content = page.get("content", "")
            links = _parse_wl(content)
            if links:
                links_line = "links_to: [" + ", ".join(links) + "]\n"
                if "links_to:" in content:
                    content = re.sub(r"links_to:.*\n", links_line, content)
                elif content.count("---") >= 2:
                    content = content.replace("---\n", "---\n" + links_line, 1)
            page["content"] = content
        result["stages"]["crossref"] = {"processed": len(pages)}

        # Stage 4: Finalize
        written = finalize_wiki_pages(pages, filepath)
        result["stages"]["finalize"] = {"written": [os.path.basename(w) for w in written]}
        result["wiki_pages_created"] = len(written)

        # Stage 5: Index
        update_index(pages)
        append_log("compile", os.path.basename(filepath), written)
        result["stages"]["index"] = {"updated": True}

        result["ok"] = True
        _record_compile_meta(os.path.basename(filepath), result)

    except Exception as exc:
        logger.exception("Compile pipeline failed: %s", exc)
        result["error"] = str(exc)

    return result


def _record_compile_meta(source_name: str, result: dict) -> None:
    """Write compilation history to meta/ (per-source JSON + history.jsonl)."""
    import json as _json

    meta_dir = os.path.join(_wiki_dir(), "meta")
    os.makedirs(meta_dir, exist_ok=True)

    record = {
        "source": source_name,
        "timestamp": datetime.now(UTC).isoformat(),
        "ok": result["ok"],
        "stages": result.get("stages", {}),
        "wiki_pages_created": result.get("wiki_pages_created", 0),
        "error": result.get("error", ""),
    }

    stem = os.path.splitext(source_name)[0]
    meta_path = os.path.join(meta_dir, f"{stem}.json")
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            _json.dump(record, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning("Failed to write compile meta: %s", e)

    history_path = os.path.join(meta_dir, "history.jsonl")
    try:
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("Failed to append compile history: %s", e)


def get_compile_history(source_name: str = "") -> list[dict]:
    """Read compilation history from meta/history.jsonl. Optionally filter by source."""
    import json as _json

    meta_dir = os.path.join(_wiki_dir(), "meta")
    history_path = os.path.join(meta_dir, "history.jsonl")
    if not os.path.isfile(history_path):
        return []

    records = []
    try:
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = _json.loads(line)
                    if not source_name or r.get("source") == source_name:
                        records.append(r)
                except _json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return records


def _parse_page_list(text: str) -> list[dict]:
    """Parse LLM JSON output into list of page dicts."""
    import json

    clean = text.strip()
    if "```" in clean:
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        logger.warning("Failed to parse summarizer output as JSON")
    return []
