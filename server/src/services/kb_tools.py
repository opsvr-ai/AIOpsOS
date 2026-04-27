"""LLM-Wiki file-based knowledge tools: grep search, file read, index browse.

These tools give the agent command-line-style access to the markdown wiki
files stored on disk, following the LLM-Wiki pattern (grep/index.md/cat).
"""

import os
import subprocess
from pathlib import Path

from src.config import settings


def _wiki_dir() -> str:
    return settings.wiki_path


def _wiki_path() -> str:
    return os.path.join(_wiki_dir(), "wiki")


def grep_kb(query: str, max_results: int = 10) -> str:
    """Search knowledge base wiki files by keyword (grep)."""
    wiki = _wiki_path()
    if not os.path.isdir(wiki):
        return "Knowledge base wiki directory not found."

    try:
        result = subprocess.run(
            ["grep", "-r", "-n", "-i", "--include=*.md", query, wiki],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return "grep command not available on this system."
    except subprocess.TimeoutExpired:
        return "Search timed out."

    if result.returncode != 0 and result.returncode != 1:
        return f"Search error (exit {result.returncode}): {result.stderr[:200]}"

    lines = result.stdout.strip().splitlines()
    if not lines:
        return f"No results found for: {query}"

    # Group by file
    file_matches: dict[str, list[str]] = {}
    for line in lines:
        if ":" in line:
            filepath, lineno, *rest = line.split(":", 2)
            file_matches.setdefault(filepath, []).append(
                f"  L{lineno}: {rest[0].strip() if rest else ''}"
            )

    output_parts = [f"Search results for '{query}':\n"]
    count = 0
    for filepath, matches in file_matches.items():
        if count >= max_results:
            break
        relpath = os.path.relpath(filepath, _wiki_dir())
        output_parts.append(f"📄 {relpath}")
        for m in matches[:3]:
            if count >= max_results:
                break
            output_parts.append(m)
            count += 1
        output_parts.append("")

    if output_parts and not output_parts[-1]:
        output_parts.pop()

    output_parts.append(f"({count} matches shown)")
    return "\n".join(output_parts)


def read_wiki_file(filename: str) -> str:
    """Read a wiki page file by name (without path)."""
    wiki = _wiki_path()
    if not os.path.isdir(wiki):
        return "Knowledge base wiki directory not found."

    candidates = [
        os.path.join(wiki, filename),
        os.path.join(wiki, f"{filename}.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError as e:
                return f"Error reading file: {e}"

    all_md = list(Path(wiki).glob("*.md"))
    matches = [p for p in all_md if filename.lower() in p.stem.lower()]
    if len(matches) == 1:
        try:
            with open(matches[0], "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            return f"Error reading file: {e}"
    elif len(matches) > 1:
        names = "\n".join(f"  - {p.stem}" for p in matches)
        return f"Multiple matches for '{filename}':\n{names}"

    return f"File not found: {filename}"


def list_wiki_pages() -> str:
    """List all wiki pages currently on disk."""
    wiki = _wiki_path()
    if not os.path.isdir(wiki):
        return "Knowledge base wiki directory not found."

    all_md = sorted(Path(wiki).glob("*.md"))
    if not all_md:
        return "No wiki pages found."

    parts = ["Knowledge base wiki pages:\n"]
    for p in all_md:
        size = p.stat().st_size
        parts.append(f"  - {p.stem} ({size:,} bytes)")
    parts.append(f"\nTotal: {len(all_md)} pages")
    return "\n".join(parts)


def write_wiki_file(filename: str, content: str) -> str:
    """Write content to a wiki page file.  Creates intermediate dirs as needed.

    Security: ``filename`` is resolved inside ``wiki/`` — path traversal (``..``)
    is rejected.
    """
    wiki = _wiki_path()
    os.makedirs(wiki, exist_ok=True)

    # Resolve to an absolute path and verify it stays under wiki/
    dest = (Path(wiki) / filename).resolve()
    if not str(dest).startswith(str(Path(wiki).resolve())):
        return f"Error: path traversal detected — '{filename}' is outside wiki directory."

    # Auto-append .md if no extension
    if dest.suffix == "":
        dest = dest.with_suffix(".md")

    # Only allow .md files
    if dest.suffix != ".md":
        return f"Error: only .md files can be written to wiki."

    try:
        os.makedirs(dest.parent, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        rel = dest.relative_to(Path(_wiki_dir()).resolve())
        return f"Written to {rel} ({dest.stat().st_size} bytes)"
    except OSError as e:
        return f"Error writing file: {e}"


def write_kb_raw(filename: str, content: str) -> str:
    """Save a raw source file under ``raw/`` (immutable source storage).

    Files are stored in ``raw/YYYY/MM/DD/{filename}``.  Path traversal is rejected.
    """
    raw_dir = os.path.join(_wiki_dir(), "raw")
    os.makedirs(raw_dir, exist_ok=True)

    date_prefix = Path(_today_str())
    dest = (Path(raw_dir) / date_prefix / filename).resolve()
    if not str(dest).startswith(str(Path(raw_dir).resolve())):
        return f"Error: path traversal detected."

    try:
        os.makedirs(dest.parent, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return f"Saved to raw/{date_prefix}/{filename} ({dest.stat().st_size} bytes)"
    except OSError as e:
        return f"Error saving file: {e}"


def get_config(key: str = "") -> str:
    """Look up AIOpsOS configuration values.

    Call with a key name (e.g. ``get_config("WIKI_PATH")``) to read a single value,
    or with no arguments to list all readable config keys.
    """
    readable = {
        "WIKI_PATH": settings.wiki_path,
        "UPLOAD_DIR": settings.upload_dir,
        "SERVICE_TYPE": settings.service_type,
        "LLM_BASE_URL": settings.llm_base_url,
        "EMBEDDING_MODEL": settings.embedding_model,
        "DATABASE_URL": "[redacted — contains credentials]"
        if "@" in settings.database_url
        else settings.database_url,
        "REDIS_URL": settings.redis_url,
        "KAFKA_BOOTSTRAP_SERVERS": settings.kafka_bootstrap_servers,
    }

    if not key:
        lines = ["Available configuration keys:\n"]
        for k, v in readable.items():
            lines.append(f"  {k} = {v}")
        return "\n".join(lines)

    if key in readable:
        return f"{key} = {readable[key]}"

    similar = [k for k in readable if key.upper() in k.upper()]
    hint = f" Did you mean: {', '.join(similar)}?" if similar else ""
    return f"Unknown config key: '{key}'. Use get_config() with no arguments to see available keys.{hint}"


def _today_str() -> str:
    from datetime import date
    return date.today().strftime("%Y/%m/%d")
