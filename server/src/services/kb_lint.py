"""LLM-Wiki lint service — quality checks for knowledge base health.

Produces structured LintReport with health score 0-100.
10 deterministic checks run immediately; page-contradiction check
is deferred (requires LLM).
"""

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from src.config import settings
from src.schemas.kb_lint import LintIssue, LintReport
from src.services.kb_crossref import find_broken_links, find_orphans

logger = logging.getLogger(__name__)

VALID_TYPES = {"entity", "concept", "comparison", "summary", "query"}
REQUIRED_FM_FIELDS = {"title", "type", "created"}


def _wiki_dir() -> str:
    return os.path.join(settings.wiki_path, "wiki")


def _wiki_root() -> str:
    return settings.wiki_path


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    fm: dict = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    key, _, val = line.partition(":")
                    key, val = key.strip(), val.strip()
                    if val.startswith("[") and val.endswith("]"):
                        fm[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
                    else:
                        fm[key] = val.strip("\"'")
    return fm, body


def fix_lint_issue(check_id: str, page: str = "") -> dict:
    """Attempt to auto-fix a lint issue. Returns {ok, message, issue_id}."""
    fix_map: dict[str, callable] = {
        "broken_link": _fix_broken_link,
        "orphan_page": _fix_orphan_page,
        "index_incomplete": _fix_index_incomplete,
        "index_missing": _fix_index_missing,
        "index_stale_entries": _fix_index_stale,
        "missing_frontmatter": _fix_missing_frontmatter,
        "missing_fm_field": _fix_missing_fm_field,
        "invalid_page_type": _fix_invalid_page_type,
        "source_drift": _fix_source_drift,
        "missing_tags": _fix_missing_tags,
        "no_sources": _fix_no_sources,
        "no_wikilinks": _fix_no_wikilinks,
        "log_missing": _fix_log_missing,
        "stale_content": _fix_stale_content,
    }
    fixer = fix_map.get(check_id)
    if not fixer:
        return {"ok": False, "issue_id": check_id, "message": f"No auto-fix available for '{check_id}'"}
    try:
        return fixer(page)
    except Exception as e:
        logger.exception("Fix failed for %s page=%s", check_id, page)
        return {"ok": False, "issue_id": check_id, "message": str(e)}


def _read_wiki_page(stem: str) -> tuple[str, str]:
    wiki = _wiki_dir()
    fp = os.path.join(wiki, f"{stem}.md")
    if not os.path.isfile(fp):
        raise FileNotFoundError(f"Page not found: {stem}")
    return Path(fp).read_text(encoding="utf-8"), fp


def _write_wiki_page(filepath: str, content: str) -> None:
    Path(filepath).write_text(content, encoding="utf-8")


def _rebuild_frontmatter(fm: dict, body: str) -> str:
    fm_lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            items = ", ".join(str(x) for x in v)
            fm_lines.append(f"{k}: [{items}]")
        else:
            fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    return "\n".join(fm_lines) + "\n" + body


def _page_exists(name: str) -> bool:
    wiki = _wiki_dir()
    root = _wiki_root()
    return (os.path.isfile(os.path.join(wiki, f"{name}.md")) or
            os.path.isfile(os.path.join(root, f"{name}.md")))


def _fix_broken_link(page: str) -> dict:
    if not page:
        broken = find_broken_links()
        fixed = 0
        for bl in broken:
            if _page_exists(bl["target"]):
                continue
            content, fp = _read_wiki_page(bl["source"])
            new_content = re.sub(
                rf"\[\[{re.escape(bl['target'])}(\|[^\]]+)?\]\]\s*", "", content,
            )
            if new_content != content:
                _write_wiki_page(fp, new_content)
                fixed += 1
        return {"ok": True, "issue_id": "broken_link", "message": f"Removed {fixed} broken links"}
    else:
        content, fp = _read_wiki_page(page)
        fixed = 0
        for m in re.finditer(r"\[\[([^\]]+)\]\]", content):
            target = m.group(1).split("|")[0].strip()
            if not _page_exists(target):
                content = content.replace(m.group(0), "", 1)
                fixed += 1
        if fixed:
            _write_wiki_page(fp, content)
        return {"ok": True, "issue_id": "broken_link", "message": f"Removed {fixed} broken link(s) from [[{page}]]"}


def _fix_orphan_page(page: str) -> dict:
    index_path = os.path.join(_wiki_root(), "index.md")
    if page:
        orphans = [page]
    else:
        orphans = [o["page"] for o in find_orphans()]
    if not orphans:
        return {"ok": True, "issue_id": "orphan_page", "message": "No orphan pages"}
    if not os.path.isfile(index_path):
        _write_wiki_page(index_path, "# Wiki Index\n\n> Content catalog.\n")
    content = Path(index_path).read_text(encoding="utf-8")
    added = 0
    for orphan in orphans:
        if f"[[{orphan}]]" in content:
            continue
        if "## Concepts" in content:
            content = content.replace("## Concepts\n", f"## Concepts\n- [[{orphan}]]\n")
        else:
            content += f"\n## Concepts\n- [[{orphan}]]\n"
        added += 1
    _write_wiki_page(index_path, content)
    return {"ok": True, "issue_id": "orphan_page", "message": f"Added {added} orphan(s) to index.md"}


def _fix_index_incomplete(_page: str) -> dict:
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return {"ok": False, "issue_id": "index_incomplete", "message": "Wiki directory not found"}
    index_path = os.path.join(_wiki_root(), "index.md")
    pages_by_type: dict[str, list[str]] = {}
    for fp in Path(wiki).glob("*.md"):
        try:
            c = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _parse_frontmatter(c)
        pages_by_type.setdefault(fm.get("type", "concept"), []).append(fp.stem)
    labels = {"entity": "## Entities", "concept": "## Concepts",
              "comparison": "## Comparisons", "summary": "## Summaries", "query": "## Queries"}
    lines = ["# Wiki Index\n", "\n> Auto-generated content catalog.\n"]
    for pt in ["entity", "concept", "comparison", "summary", "query"]:
        ps = sorted(pages_by_type.pop(pt, []))
        if not ps:
            continue
        lines.append(f"\n{labels[pt]}\n")
        for p in ps:
            lines.append(f"- [[{p}]]\n")
    for pt, ps in sorted(pages_by_type.items()):
        lines.append(f"\n## {pt}\n")
        for p in sorted(ps):
            lines.append(f"- [[{p}]]\n")
    _write_wiki_page(index_path, "".join(lines))
    return {"ok": True, "issue_id": "index_incomplete", "message": "Index regenerated from all wiki pages"}


def _fix_index_missing(_page: str) -> dict:
    return _fix_index_incomplete("")


def _fix_index_stale(_page: str) -> dict:
    index_path = os.path.join(_wiki_root(), "index.md")
    if not os.path.isfile(index_path):
        return {"ok": True, "issue_id": "index_stale_entries", "message": "No index.md to clean"}
    wiki = _wiki_dir()
    existing = {fp.stem for fp in Path(wiki).glob("*.md")} if os.path.isdir(wiki) else set()
    content = Path(index_path).read_text(encoding="utf-8")
    removed = 0
    for m in re.finditer(r"- \[\[([^\]]+)\]\]", content):
        if m.group(1) not in existing:
            content = content.replace(m.group(0), "", 1)
            removed += 1
    _write_wiki_page(index_path, content)
    return {"ok": True, "issue_id": "index_stale_entries", "message": f"Removed {removed} stale entries"}


def _fix_missing_frontmatter(page: str) -> dict:
    content, fp = _read_wiki_page(page)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    fm = (f"---\ntitle: {page}\ntype: concept\ncreated: {today}\nupdated: {today}\n"
          f"tags: []\nsources: []\n---\n\n")
    _write_wiki_page(fp, fm + content)
    return {"ok": True, "issue_id": "missing_frontmatter", "message": f"Added frontmatter to [[{page}]]"}


def _fix_missing_fm_field(page: str) -> dict:
    content, fp = _read_wiki_page(page)
    fm, body = _parse_frontmatter(content)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    defaults = {"title": page, "type": "concept", "created": today}
    changed = False
    for field in REQUIRED_FM_FIELDS:
        if field not in fm:
            fm[field] = defaults.get(field, today)
            changed = True
    if not changed:
        return {"ok": True, "issue_id": "missing_fm_field", "message": f"No missing fields in [[{page}]]"}
    _write_wiki_page(fp, _rebuild_frontmatter(fm, body))
    return {"ok": True, "issue_id": "missing_fm_field", "message": f"Added missing fields to [[{page}]]"}


def _fix_invalid_page_type(page: str) -> dict:
    content, fp = _read_wiki_page(page)
    fm, body = _parse_frontmatter(content)
    old_type = fm.get("type", "")
    fm["type"] = "concept"
    _write_wiki_page(fp, _rebuild_frontmatter(fm, body))
    return {"ok": True, "issue_id": "invalid_page_type", "message": f"Changed type from '{old_type}' to 'concept' in [[{page}]]"}


def _fix_source_drift(page: str) -> dict:
    content, fp = _read_wiki_page(page)
    fm, body = _parse_frontmatter(content)
    sources = fm.get("sources", [])
    if not isinstance(sources, list) or not sources:
        return {"ok": True, "issue_id": "source_drift", "message": "No sources to fix"}
    raw_dir = os.path.join(_wiki_root(), "raw")
    raw_files = {fp.name for fp in Path(raw_dir).glob("*.md")} if os.path.isdir(raw_dir) else set()
    new_sources = [s for s in sources if s.replace("raw/", "").strip() in raw_files]
    if len(new_sources) == len(sources):
        return {"ok": True, "issue_id": "source_drift", "message": f"All sources still valid in [[{page}]]"}
    fm["sources"] = new_sources
    _write_wiki_page(fp, _rebuild_frontmatter(fm, body))
    return {"ok": True, "issue_id": "source_drift", "message": f"Removed {len(sources) - len(new_sources)} stale source(s) from [[{page}]]"}


def _fix_missing_tags(_page: str) -> dict:
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return {"ok": False, "issue_id": "missing_tags", "message": "Wiki directory not found"}
    fixed = 0
    for fp in Path(wiki).glob("*.md"):
        try:
            c = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = _parse_frontmatter(c)
        tags = fm.get("tags", [])
        if not isinstance(tags, list) or len(tags) == 0:
            fm["tags"] = ["needs-review"]
            _write_wiki_page(str(fp), _rebuild_frontmatter(fm, body))
            fixed += 1
    return {"ok": True, "issue_id": "missing_tags", "message": f"Added 'needs-review' tag to {fixed} page(s)"}


def _fix_no_sources(page: str) -> dict:
    content, fp = _read_wiki_page(page)
    fm, body = _parse_frontmatter(content)
    fm["sources"] = []
    _write_wiki_page(fp, _rebuild_frontmatter(fm, body))
    return {"ok": True, "issue_id": "no_sources", "message": f"Added sources field to [[{page}]]"}


def _fix_no_wikilinks(page: str) -> dict:
    content, fp = _read_wiki_page(page)
    fm, body = _parse_frontmatter(content)
    body = body.rstrip() + "\n\nSee also: [[index]]\n"
    _write_wiki_page(fp, _rebuild_frontmatter(fm, body))
    return {"ok": True, "issue_id": "no_wikilinks", "message": f"Added wikilink to [[{page}]]"}


def _fix_log_missing(_page: str) -> dict:
    log_path = os.path.join(_wiki_root(), "log.md")
    _write_wiki_page(log_path, "# Wiki Log\n\n> Chronological record of all wiki actions. Append-only.\n")
    return {"ok": True, "issue_id": "log_missing", "message": "Created log.md"}


def _fix_stale_content(page: str) -> dict:
    content, fp = _read_wiki_page(page)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    new_content = re.sub(r"(updated:\s*)\S+", rf"\1{today}", content, count=1)
    if new_content == content:
        return {"ok": True, "issue_id": "stale_content", "message": f"Could not update date in [[{page}]]"}
    _write_wiki_page(fp, new_content)
    return {"ok": True, "issue_id": "stale_content", "message": f"Updated date on [[{page}]] to {today}"}


def run_lint() -> LintReport:
    """Run all lint checks and return a structured report with health score."""
    issues: list[LintIssue] = []

    issues.extend(_check_orphans())
    issues.extend(_check_broken_links())
    issues.extend(_check_index_completeness())
    issues.extend(_check_frontmatter())
    issues.extend(_check_stale_content())
    issues.extend(_check_quality_signals())
    issues.extend(_check_source_drift())
    issues.extend(_check_oversized_pages())
    issues.extend(_check_tag_audit())
    issues.extend(_check_log_rotation())

    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    infos = sum(1 for i in issues if i.severity == "info")

    # Per-category capped deductions so no single issue type destroys the score
    def count_by_check(prefix: str) -> int:
        return sum(1 for i in issues if i.check_id.startswith(prefix))

    deduction = min(40, count_by_check("broken_link") * 1)       # broken links: -1 each, cap 40
    deduction += min(15, count_by_check("orphan_page") * 1.5)    # orphans: -1.5 each, cap 15
    deduction += min(20, count_by_check("missing_frontmatter") * 2)  # missing fm: -2 each, cap 20
    deduction += min(15, count_by_check("index_") * 3)            # index issues: -3 each, cap 15
    deduction += min(15, warnings * 0.5)                          # other warnings: -0.5 each, cap 15
    deduction += min(10, infos * 0.25)                            # info: -0.25 each, cap 10
    health = max(0, 100 - int(deduction))

    return LintReport(
        health_score=health,
        total_issues=len(issues),
        errors=errors,
        warnings=warnings,
        info=infos,
        issues=issues,
        checked_at=datetime.now(UTC).isoformat(),
    )


# ── Check 1: Orphan pages ──────────────────────────────────────────

def _check_orphans() -> list[LintIssue]:
    issues = []
    for orphan in find_orphans():
        issues.append(LintIssue(
            check_id="orphan_page",
            severity="warning",
            page=orphan["page"],
            message=f"No other pages link to [[{orphan['page']}]]",
            fix_action="add_wikilink",
            fix_description=f"Add [[{orphan['page']}]] to a related page or index.md",
        ))
    return issues


# ── Check 2: Broken wikilinks ──────────────────────────────────────

def _check_broken_links() -> list[LintIssue]:
    issues = []
    for bl in find_broken_links():
        issues.append(LintIssue(
            check_id="broken_link",
            severity="error",
            page=bl["source"],
            message=f"[[{bl['target']}]] does not exist",
            fix_action="fix_or_remove_link",
            fix_description=f"Create page [[{bl['target']}]] or remove the link from [[{bl['source']}]]",
        ))
    return issues


# ── Check 3: Index completeness ────────────────────────────────────

def _check_index_completeness() -> list[LintIssue]:
    issues = []
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return issues

    wiki_stems = {fp.stem for fp in Path(wiki).glob("*.md")}
    index_path = os.path.join(_wiki_root(), "index.md")

    if not os.path.isfile(index_path):
        return [LintIssue(
            check_id="index_missing", severity="error", page="index.md",
            message="index.md is missing",
            fix_action="generate_index",
            fix_description="Run update_index() to regenerate index.md",
        )]

    index_text = Path(index_path).read_text(encoding="utf-8")
    missing = sorted(wiki_stems - {m.group(1) for m in re.finditer(r"\[\[([^\]]+)\]\]", index_text)})
    if missing:
        issues.append(LintIssue(
            check_id="index_incomplete", severity="warning", page="index.md",
            message=f"Missing {len(missing)} pages: {', '.join(missing[:10])}{'...' if len(missing) > 10 else ''}",
            fix_action="regenerate_index",
            fix_description="Run update_index() to add missing entries",
        ))

    stale = {m.group(1) for m in re.finditer(r"\[\[([^\]]+)\]\]", index_text)} - wiki_stems
    if stale:
        issues.append(LintIssue(
            check_id="index_stale_entries", severity="info", page="index.md",
            message=f"{len(stale)} stale entries referencing deleted pages",
            fix_action="clean_index",
            fix_description="Remove stale entries from index.md",
        ))

    return issues


# ── Check 4: Frontmatter validation ────────────────────────────────

def _check_frontmatter() -> list[LintIssue]:
    issues = []
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return issues

    for fp in Path(wiki).glob("*.md"):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        if not content.startswith("---"):
            issues.append(LintIssue(
                check_id="missing_frontmatter", severity="error", page=fp.stem,
                message="Missing YAML frontmatter",
                fix_action="add_frontmatter",
                fix_description=f"Add --- frontmatter block to [[{fp.stem}]]",
            ))
            continue

        fm, _ = _parse_frontmatter(content)
        for field in REQUIRED_FM_FIELDS:
            if field not in fm:
                issues.append(LintIssue(
                    check_id="missing_fm_field", severity="warning", page=fp.stem,
                    message=f"Missing required field: {field}",
                    fix_action="add_fm_field",
                    fix_description=f"Add '{field}' to frontmatter of [[{fp.stem}]]",
                ))
        ptype = fm.get("type", "")
        if ptype and ptype not in VALID_TYPES:
            issues.append(LintIssue(
                check_id="invalid_page_type", severity="warning", page=fp.stem,
                message=f"Invalid type '{ptype}'. Valid: {', '.join(sorted(VALID_TYPES))}",
                fix_action="fix_page_type",
                fix_description=f"Change type to one of: {', '.join(sorted(VALID_TYPES))}",
            ))

    return issues


# ── Check 5: Stale content ─────────────────────────────────────────

def _check_stale_content() -> list[LintIssue]:
    issues = []
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return issues

    now = datetime.now(UTC)
    for fp in Path(wiki).glob("*.md"):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _parse_frontmatter(content)
        updated_str = fm.get("updated", "")
        if not updated_str:
            continue
        try:
            updated = datetime.strptime(updated_str, "%Y-%m-%d").replace(tzinfo=UTC)
            days = (now - updated).days
            if days > 90:
                issues.append(LintIssue(
                    check_id="stale_content", severity="warning", page=fp.stem,
                    message=f"Not updated in {days} days (last: {updated_str})",
                    fix_action="review_page",
                    fix_description=f"Review [[{fp.stem}]] for accuracy",
                ))
            elif days > 30:
                issues.append(LintIssue(
                    check_id="stale_content", severity="info", page=fp.stem,
                    message=f"Not updated in {days} days",
                    fix_action="review_page",
                    fix_description=f"Consider reviewing [[{fp.stem}]]",
                ))
        except (ValueError, TypeError):
            pass
    return issues


# ── Check 6: Quality signals ───────────────────────────────────────

def _check_quality_signals() -> list[LintIssue]:
    issues = []
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return issues

    for fp in Path(wiki).glob("*.md"):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = _parse_frontmatter(content)

        if len(body.strip()) < 200:
            issues.append(LintIssue(
                check_id="thin_content", severity="info", page=fp.stem,
                message=f"Very short page ({len(body.strip())} chars)",
                fix_action="expand_page",
                fix_description=f"Add more detail to [[{fp.stem}]]",
            ))

        if not re.findall(r"\[\[([^\]]+)\]\]", body):
            issues.append(LintIssue(
                check_id="no_wikilinks", severity="info", page=fp.stem,
                message="No wikilinks — isolated content",
                fix_action="add_wikilink",
                fix_description=f"Add at least one [[link]] to connect [[{fp.stem}]]",
            ))

        if "sources" not in fm:
            issues.append(LintIssue(
                check_id="no_sources", severity="info", page=fp.stem,
                message="No sources listed in frontmatter",
                fix_action="add_sources",
                fix_description=f"Add 'sources' field to frontmatter of [[{fp.stem}]]",
            ))

    return issues


# ── Check 7: Source drift ──────────────────────────────────────────

def _check_source_drift() -> list[LintIssue]:
    issues = []
    wiki = _wiki_dir()
    raw_dir = os.path.join(_wiki_root(), "raw")
    raw_files = {fp.name for fp in Path(raw_dir).glob("*.md")} if os.path.isdir(raw_dir) else set()

    if not os.path.isdir(wiki):
        return issues

    for fp in Path(wiki).glob("*.md"):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _parse_frontmatter(content)
        sources = fm.get("sources", [])
        if not isinstance(sources, list):
            continue
        for src in sources:
            src_filename = src.replace("raw/", "").strip()
            if src_filename and src_filename not in raw_files:
                issues.append(LintIssue(
                    check_id="source_drift", severity="warning", page=fp.stem,
                    message=f"Source file no longer exists: {src}",
                    fix_action="update_or_remove_source",
                    fix_description=f"Remove stale source '{src}' from [[{fp.stem}]]",
                ))

    return issues


# ── Check 8: Oversized pages ───────────────────────────────────────

def _check_oversized_pages() -> list[LintIssue]:
    issues = []
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return issues

    for fp in Path(wiki).glob("*.md"):
        size = fp.stat().st_size
        if size > 100_000:
            issues.append(LintIssue(
                check_id="oversized_page", severity="warning", page=fp.stem,
                message=f"Very large page ({size:,} bytes), consider splitting",
                fix_action="split_page",
                fix_description=f"Split [[{fp.stem}]] into multiple smaller pages",
            ))
        elif size > 50_000:
            issues.append(LintIssue(
                check_id="oversized_page", severity="info", page=fp.stem,
                message=f"Large page ({size:,} bytes)",
                fix_action="split_page",
                fix_description=f"Consider splitting [[{fp.stem}]]",
            ))

    return issues


# ── Check 9: Tag audit ─────────────────────────────────────────────

def _check_tag_audit() -> list[LintIssue]:
    issues = []
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return issues

    tag_counts: dict[str, int] = {}
    pages_without_tags = []

    for fp in Path(wiki).glob("*.md"):
        try:
            content = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _parse_frontmatter(content)
        tags = fm.get("tags", [])
        if not isinstance(tags, list) or len(tags) == 0:
            pages_without_tags.append(fp.stem)
        else:
            for t in tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1

    if pages_without_tags:
        issues.append(LintIssue(
            check_id="missing_tags", severity="info", page="",
            message=f"{len(pages_without_tags)} pages have no tags",
            fix_action="add_tags",
            fix_description="Add relevant tags to untagged pages",
        ))

    single_use = [t for t, c in tag_counts.items() if c == 1]
    if single_use:
        issues.append(LintIssue(
            check_id="single_use_tags", severity="info", page="",
            message=f"{len(single_use)} tags used only once (possible typos): {', '.join(single_use[:15])}",
            fix_action="review_tags",
            fix_description="Review single-use tags for typos or consolidation",
        ))

    return issues


# ── Check 10: Log rotation ─────────────────────────────────────────

def _check_log_rotation() -> list[LintIssue]:
    issues = []
    log_path = os.path.join(_wiki_root(), "log.md")
    if not os.path.isfile(log_path):
        return [LintIssue(
            check_id="log_missing", severity="info", page="log.md",
            message="log.md is missing",
            fix_action="create_log",
            fix_description="Create log.md for append-only action recording",
        )]

    size = os.path.getsize(log_path)
    if size > 1_000_000:
        issues.append(LintIssue(
            check_id="log_rotation", severity="warning", page="log.md",
            message=f"log.md is {size:,} bytes (> 1MB), consider archiving",
            fix_action="rotate_log",
            fix_description="Archive older entries to meta/log-archive.md",
        ))
    elif size > 500_000:
        issues.append(LintIssue(
            check_id="log_growing", severity="info", page="log.md",
            message=f"log.md is {size:,} bytes and growing",
            fix_action="rotate_log",
            fix_description="Plan rotation when log exceeds 1MB",
        ))

    return issues
