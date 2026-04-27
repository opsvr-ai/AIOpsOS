"""LLM-Wiki summarization pipeline.

Takes raw source documents and produces wiki-compliant markdown pages
with YAML frontmatter, wikilinks, and cross-references.
"""

import hashlib
import logging
import os
import re
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import settings

logger = logging.getLogger(__name__)


def _wiki_dir() -> str:
    return settings.wiki_path


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
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


SUMMARIZE_SYSTEM = """You are a knowledge base curator following the LLM-Wiki standard.
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

    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=model_name,
        temperature=0.3,
        timeout=120,
        max_retries=1,
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

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
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fm = (
        f"---\n"
        f"source_url: {source_url}\n"
        f"ingested: {today}\n"
        f"sha256: {content_hash}\n"
        f"---\n\n"
    )
    _write_file(filepath, fm + body)
    return content_hash


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
