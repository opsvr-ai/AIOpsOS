"""Cross-reference engine for LLM-Wiki.

Scans all wiki pages to build a link graph, detects broken wikilinks
and orphan pages, and provides backlink queries.
"""

import logging
import os
import re
from collections import defaultdict
from pathlib import Path

from src.config import settings

logger = logging.getLogger(__name__)

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _wiki_dir() -> str:
    return os.path.join(settings.wiki_path, "wiki")


def _normalize_link(raw: str) -> str:
    """Extract the actual page name from a wikilink, handling pipe syntax.

    [[Target Page|display text]] → Target Page
    """
    return raw.split("|")[0].strip()


def parse_wikilinks(content: str) -> list[str]:
    """Extract normalized [[page-name]] links from markdown content, deduplicated in order."""
    raw = WIKILINK_RE.findall(content)
    return list(dict.fromkeys(_normalize_link(r) for r in raw))


def build_link_graph() -> dict[str, dict[str, set[str]]]:
    """Scan all wiki pages and return {page_name: {'out': set, 'in': set}}."""
    wiki = _wiki_dir()
    root = settings.wiki_path

    outbound: dict[str, set[str]] = {}
    # Scan wiki/ subdirectory
    if os.path.isdir(wiki):
        for fp in Path(wiki).glob("*.md"):
            try:
                text = fp.read_text(encoding="utf-8")
            except OSError:
                continue
            outbound[fp.stem] = set(parse_wikilinks(text))
    # Also scan root-level .md files (index.md, log.md)
    if os.path.isdir(root):
        for fp in Path(root).glob("*.md"):
            if fp.stem in outbound:
                continue
            try:
                text = fp.read_text(encoding="utf-8")
            except OSError:
                continue
            outbound[fp.stem] = set(parse_wikilinks(text))

    inbound: dict[str, set[str]] = defaultdict(set)
    for page, links in outbound.items():
        for target in links:
            inbound[target].add(page)

    graph: dict[str, dict[str, set[str]]] = {}
    for page in sorted(outbound.keys()):
        graph[page] = {
            "out": outbound.get(page, set()),
            "in": inbound.get(page, set()),
        }
    return graph


def find_orphans() -> list[dict]:
    """Find wiki pages with zero inbound links."""
    graph = build_link_graph()
    orphans = []
    for page, links in graph.items():
        if not links["in"]:
            orphans.append({"page": page, "outbound_count": len(links["out"])})
    orphans.sort(key=lambda x: x["page"])
    return orphans


def find_broken_links() -> list[dict]:
    """Find wikilinks that point to non-existent pages."""
    wiki = _wiki_dir()
    root = settings.wiki_path

    existing = {fp.stem for fp in Path(wiki).glob("*.md")} if os.path.isdir(wiki) else set()
    if os.path.isdir(root):
        existing |= {fp.stem for fp in Path(root).glob("*.md")}
    existing_lower = {s.lower(): s for s in existing}
    broken: list[dict] = []

    if not os.path.isdir(wiki):
        return []

    for fp in Path(wiki).glob("*.md"):
        try:
            text = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for link in parse_wikilinks(text):
            # Case-insensitive match, also try with .md suffix stripped
            if link not in existing and link.lower() not in existing_lower:
                broken.append({"source": fp.stem, "target": link})

    grouped: dict[tuple[str, str], int] = {}
    for b in broken:
        key = (b["source"], b["target"])
        grouped[key] = grouped.get(key, 0) + 1

    result = [{"source": s, "target": t, "count": c} for (s, t), c in grouped.items()]
    result.sort(key=lambda x: (x["target"], x["source"]))
    return result


def get_backlinks(page: str) -> list[str]:
    """Return sorted list of page names that link to the given page."""
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return []
    backlinks = []
    target = page.removesuffix(".md")
    for fp in Path(wiki).glob("*.md"):
        if fp.stem == target:
            continue
        try:
            if f"[[{target}]]" in fp.read_text(encoding="utf-8"):
                backlinks.append(fp.stem)
        except OSError:
            continue
    return sorted(backlinks)


def get_link_stats() -> dict:
    """Return summary statistics about the wiki link graph."""
    graph = build_link_graph()
    pages = len(graph)
    orphans = sum(1 for links in graph.values() if not links["in"])
    total_out = sum(len(links["out"]) for links in graph.values())
    broken = len(find_broken_links())
    avg_out = round(total_out / pages, 1) if pages > 0 else 0
    most_linked = sorted(graph.items(), key=lambda kv: len(kv[1]["in"]), reverse=True)[:10]
    top_pages = [{"page": p, "inbound": len(l["in"])} for p, l in most_linked if l["in"]]

    return {
        "total_pages": pages,
        "orphan_pages": orphans,
        "broken_links": broken,
        "total_outbound_links": total_out,
        "avg_outbound_per_page": avg_out,
        "most_linked_pages": top_pages,
    }
