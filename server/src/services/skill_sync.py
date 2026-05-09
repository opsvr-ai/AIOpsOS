"""Sync DB skill tools with filesystem SKILL.md files.

When a skill-type tool is created, updated, enabled, or deleted in the DB,
the corresponding ``data/skills/<name>/SKILL.md`` is written or removed.
This keeps the DeepAgents ``SkillsMiddleware`` in sync with the web UI.

Also provides consistency checking (DB vs filesystem hash comparison),
bidirectional sync, and version snapshot creation.
"""

import hashlib
import logging
import re
import shutil
from pathlib import Path

import yaml
from sqlalchemy import select

from src.models.agent import Tool
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "skills"

_INTERNAL_KEYS = {"_file_hash"}

# R-3.14: subdirectories under ``data/skills/`` whose first path component
# matches any of these names are EXCLUDED from every filesystem skill scan.
# ``.candidate`` is the reflection pipeline's staging area — materialised
# proposals must never be picked up as live, registerable skills by
# ``tool_manager`` / ``SkillsMiddleware`` / the control-plane sync UI.
_SCAN_EXCLUDED_DIRS: frozenset[str] = frozenset({".candidate"})


def _is_excluded_skill_md(md_path: Path) -> bool:
    """Return True if ``md_path`` lives under an excluded subdirectory.

    A SKILL.md is excluded when any segment of its path relative to
    :data:`SKILLS_DIR` matches an entry in :data:`_SCAN_EXCLUDED_DIRS`.
    Paths outside :data:`SKILLS_DIR` (e.g. a ``tmp_path`` fixture that
    hasn't rebound the module constant) fall through via the bare-parts
    check against the absolute path, preserving the same semantics for
    tests that monkeypatch ``SKILLS_DIR``.
    """
    try:
        parts = md_path.relative_to(SKILLS_DIR).parts
    except ValueError:
        parts = md_path.parts
    return any(p in _SCAN_EXCLUDED_DIRS for p in parts)


def _skill_dir(name: str) -> Path:
    return SKILLS_DIR / name


def _skill_md_path(name: str) -> Path:
    return _skill_dir(name) / "SKILL.md"


def _strip_internal_keys(cfg: dict) -> dict:
    return {k: v for k, v in cfg.items() if k not in _INTERNAL_KEYS}


def _canonical_content(tool: Tool) -> str:
    """Return the exact SKILL.md content that write_skill_file would produce."""
    frontmatter: dict = {
        "name": tool.name,
        "description": tool.description or tool.name,
    }
    cfg = tool.config or {}
    if "version" in cfg:
        frontmatter["version"] = cfg["version"]
    if "metadata" in cfg:
        frontmatter["metadata"] = cfg["metadata"]
    if "license" in cfg:
        frontmatter["license"] = cfg["license"]

    yaml_str = yaml.dump(
        frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False
    ).strip()
    body = cfg.get("skill_prompt", "") or tool.description or ""
    return f"---\n{yaml_str}\n---\n\n{body}\n"


def compute_content_hash(tool: Tool) -> str:
    """SHA-256 hex digest of the canonical SKILL.md content."""
    content = _canonical_content(tool)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_skill_file(tool: Tool) -> tuple[Path, str]:
    """Write (or overwrite) a SKILL.md file for a DB tool.

    Returns (filepath, sha256_hash).
    """
    if tool.type != "skill":
        raise ValueError(f"Tool {tool.name} is not a skill")

    skill_dir = _skill_dir(tool.name)
    skill_dir.mkdir(parents=True, exist_ok=True)

    content = _canonical_content(tool)
    filepath = _skill_md_path(tool.name)
    filepath.write_text(content, encoding="utf-8")
    hash_val = hashlib.sha256(content.encode("utf-8")).hexdigest()
    logger.info("Wrote skill file: %s (hash=%s)", filepath, hash_val[:12])
    return filepath, hash_val


def store_file_hash(tool: Tool, hash_val: str) -> None:
    """Store _file_hash in tool.config. Caller must commit."""
    if tool.config is None:
        tool.config = {}
    tool.config["_file_hash"] = hash_val


def remove_skill_file(name: str) -> bool:
    """Remove a skill directory from the filesystem. Returns True if deleted."""
    skill_dir = _skill_dir(name)
    if not skill_dir.exists():
        return False
    shutil.rmtree(skill_dir)
    logger.info("Removed skill directory: %s", skill_dir)
    return True


def sync_tool_to_filesystem(tool: Tool) -> Path | None:
    """Write or remove the SKILL.md for a tool based on its current state.

    Active skill -> write SKILL.md
    Inactive or non-skill -> remove SKILL.md
    """
    if tool.type == "skill" and tool.is_active:
        filepath, _hash_val = write_skill_file(tool)
        return filepath
    else:
        remove_skill_file(tool.name)
        return None


def batch_inconsistency_count(tools: list) -> int:
    """Fast batch consistency check — scans filesystem once, compares in memory.

    Replaces N individual file existence checks + reads with a single
    directory walk plus reads, avoiding per-file stat syscalls.
    """
    if not tools:
        return 0

    # Single pass: scan filesystem for all SKILL.md files and hash them
    fs_hashes: dict[str, str] = {}
    if SKILLS_DIR.exists():
        for md_path in SKILLS_DIR.rglob("SKILL.md"):
            if _is_excluded_skill_md(md_path):
                continue
            try:
                name = md_path.parent.name
                raw = md_path.read_text(encoding="utf-8")
                fs_hashes[name] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            except OSError:
                pass

    count = 0
    for t in tools:
        if t.type != "skill":
            continue
        db_hash = compute_content_hash(t)
        fs_hash = fs_hashes.get(t.name)
        if fs_hash is None or fs_hash != db_hash:
            count += 1
    return count


def check_tool_consistency(tool: Tool) -> dict:
    """Return consistency info for a single tool.

    Returns dict with: tool_id, tool_name, is_consistent, db_hash, fs_hash.
    is_consistent is None for non-skill tools.
    """
    result: dict = {
        "tool_id": str(tool.id),
        "tool_name": tool.name,
        "is_consistent": None,
        "db_hash": None,
        "fs_hash": None,
    }
    if tool.type != "skill":
        return result

    db_hash = compute_content_hash(tool)
    result["db_hash"] = db_hash

    skill_md = _skill_md_path(tool.name)
    if not skill_md.exists():
        result["is_consistent"] = False
        return result

    raw = skill_md.read_text(encoding="utf-8")
    fs_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    result["fs_hash"] = fs_hash
    result["is_consistent"] = (fs_hash == db_hash)
    return result


async def sync_from_filesystem(db, tool: Tool) -> Tool:
    """Read SKILL.md from disk, parse frontmatter, update DB fields.

    Updates description, config (version, license, metadata, skill_prompt),
    and _file_hash. Preserves other config keys. Caller must commit.
    """
    if tool.type != "skill":
        raise ValueError(f"Tool {tool.name} is not a skill")

    skill_md = _skill_md_path(tool.name)
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found for {tool.name}")

    parsed = _parse_skill_md(skill_md)
    tool.description = parsed.get("description", tool.description)

    cfg = dict(tool.config or {})
    if parsed.get("version"):
        cfg["version"] = parsed["version"]
    if parsed.get("license"):
        cfg["license"] = parsed["license"]
    cfg["metadata"] = parsed.get("metadata", {})
    cfg["skill_prompt"] = parsed.get("body", "") or tool.description or ""
    tool.config = cfg

    hash_val = compute_content_hash(tool)
    store_file_hash(tool, hash_val)
    db.add(tool)
    return tool


async def create_version_snapshot(db, tool: Tool) -> None:
    """Version-snapshot support for legacy skill tools is temporarily disabled.

    The old ``skill_versions`` table (tool-version history for the web UI)
    has been replaced by the evolution-pipeline ``skill_versions`` table
    (promoted skill-candidate history). Migrating skill-tool snapshots to
    the new schema is tracked separately. Callers treat this as a no-op
    so the write path of /tools endpoints stays working.
    """
    _ = (db, tool)
    return None


def list_filesystem_skills() -> list[dict]:
    """Scan data/skills/ recursively and return metadata for each valid SKILL.md found.

    Supports two layouts:
      Flat:   data/skills/<name>/SKILL.md  (user-created, no classification)
      Nested: data/skills/<source_label>/<category>/<name>/SKILL.md  (classified)

    When a skill exists in both layouts, the classified (nested) version wins.

    Returns list of dicts with name, description, version, license, metadata,
    body, _dir, category, source_label.
    """
    skills: list[dict] = []
    if not SKILLS_DIR.exists():
        return skills

    for md_path in sorted(SKILLS_DIR.rglob("SKILL.md")):
        if _is_excluded_skill_md(md_path):
            # R-3.14: ``.candidate/`` staging area is off-limits for
            # live skill discovery. Proposals are materialised there by
            # the reflection pipeline and only promoted into the main
            # tree after evaluation.
            continue
        skill_dir = md_path.parent
        try:
            metadata = _parse_skill_md(md_path)
            metadata["_dir"] = str(skill_dir)

            rel = skill_dir.relative_to(SKILLS_DIR)
            parts = rel.parts  # e.g. ("standard", "devops", "my-skill") or ("my-skill",)

            if len(parts) >= 3:
                metadata["source_label"] = parts[0]
                metadata["category"] = parts[1]
            else:
                metadata["source_label"] = None
                metadata["category"] = None

            skills.append(metadata)
        except Exception:
            logger.warning("Failed to parse skill file: %s", md_path, exc_info=True)

    # Deduplicate: classified (nested) wins over flat (unclassified)
    seen: dict[str, dict] = {}
    for s in skills:
        name = s["name"]
        if name not in seen:
            seen[name] = s
        elif s.get("source_label") and not seen[name].get("source_label"):
            seen[name] = s  # prefer classified version

    return list(seen.values())


def _parse_skill_md(filepath: Path) -> dict:
    """Parse YAML frontmatter from a SKILL.md file."""
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {"name": filepath.parent.name, "description": ""}

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {"name": filepath.parent.name, "description": ""}

    frontmatter = yaml.safe_load(parts[1]) or {}
    return {
        "name": frontmatter.get("name", filepath.parent.name),
        "description": frontmatter.get("description", ""),
        "version": frontmatter.get("version"),
        "license": frontmatter.get("license"),
        "metadata": frontmatter.get("metadata", {}),
        "body": parts[2].strip(),
    }


async def auto_register_filesystem_skills() -> int:
    """Scan data/skills/ and create DB records for any skill not yet registered.

    Also updates existing records with stale or missing source_path / category.

    Skills are registered as inactive (is_active=False) — the user must
    explicitly enable them via the web UI before they take effect.

    Called once at server startup. Returns the number of newly registered skills.
    """
    fs_skills = list_filesystem_skills()
    if not fs_skills:
        return 0

    count = 0
    updated = 0
    async with async_session_factory() as db:
        for sk in fs_skills:
            name = sk["name"]
            existing = await db.scalar(select(Tool).where(Tool.name == name))
            if existing is not None:
                sp = existing.source_path
                needs_update = (
                    not sp
                    or not str(sp).startswith(str(SKILLS_DIR))
                    or sp != sk["_dir"]
                    or existing.category != sk.get("category")
                )
                if needs_update:
                    existing.source_path = sk["_dir"]
                    existing.category = sk.get("category") or existing.category
                    cfg = dict(existing.config or {})
                    if sk.get("source_label"):
                        cfg["source_label"] = sk["source_label"]
                    existing.config = cfg
                    db.add(existing)
                    updated += 1
                continue
            cfg = {
                "version": sk.get("version"),
                "license": sk.get("license"),
                "metadata": sk.get("metadata", {}),
                "skill_prompt": sk.get("body", ""),
            }
            if sk.get("source_label"):
                cfg["source_label"] = sk["source_label"]
            tool = Tool(
                name=name,
                type="skill",
                description=sk["description"],
                category=sk.get("category"),
                source_path=sk["_dir"],
                is_active=False,
                is_approved=True,
                config=cfg,
            )
            db.add(tool)
            count += 1
            logger.info("Auto-registered filesystem skill as DB tool (inactive): %s", name)
        if count or updated:
            await db.commit()
        if updated:
            logger.info("Updated %d stale source_paths to data/skills/", updated)
    return count


# ── File Management ────────────────────────────────────────────────────────

_SKILL_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$')
_SKILL_REQUIRED_DIRS = ["templates", "examples", "references", "scripts"]


def _validate_skill_path(name: str, rel_path: str) -> Path:
    """Resolve a path within a skill directory, guarding against traversal."""
    skill_dir = _skill_dir(name).resolve()
    target = (skill_dir / rel_path).resolve()
    if not str(target).startswith(str(skill_dir) + "/") and target != skill_dir:
        raise ValueError(f"Path escapes skill directory: {rel_path}")
    return target


def list_skill_files(name: str) -> dict:
    """Recursively list all files in a skill directory as a tree dict."""
    skill_dir = _skill_dir(name)
    result: dict = {"name": name, "type": "directory", "path": "", "children": []}

    if not skill_dir.exists():
        return result

    def _walk(dir_path: Path, parent: dict) -> None:
        entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        for entry in entries:
            if entry.name.startswith(".") or entry.name.endswith("~"):
                continue
            rel = str(entry.relative_to(skill_dir))
            if entry.is_dir():
                node: dict = {"name": entry.name, "type": "directory", "path": rel, "children": []}
                parent["children"].append(node)
                _walk(entry, node)
            else:
                parent["children"].append({
                    "name": entry.name, "type": "file", "path": rel, "children": None,
                })

    _walk(skill_dir, result)
    return result


def read_skill_file(name: str, rel_path: str) -> str:
    """Read a file within a skill directory. Raises on path traversal."""
    target = _validate_skill_path(name, rel_path)
    if not target.is_file():
        raise FileNotFoundError(f"Not a file: {rel_path}")
    return target.read_text(encoding="utf-8")


def write_skill_file_content(name: str, rel_path: str, content: str) -> Path:
    """Write content to a file within a skill directory. Creates parent dirs."""
    target = _validate_skill_path(name, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    logger.info("Wrote skill file: %s", target)
    return target


def create_skill_subdir(name: str, rel_path: str) -> Path:
    """Create a subdirectory within a skill directory."""
    target = _validate_skill_path(name, rel_path)
    target.mkdir(parents=True, exist_ok=True)
    logger.info("Created skill subdir: %s", target)
    return target


def delete_skill_path(name: str, rel_path: str) -> bool:
    """Delete a file or directory within a skill. Refuses root SKILL.md."""
    skill_md = _skill_md_path(name).resolve()
    target = _validate_skill_path(name, rel_path)
    if target == skill_md:
        logger.warning("Refusing to delete root SKILL.md for %s", name)
        return False
    if not target.exists():
        return False
    if target.is_dir():
        shutil.rmtree(target)
        logger.info("Removed skill subdir: %s", target)
    else:
        target.unlink()
        logger.info("Removed skill file: %s", target)
    return True


def create_default_skill_dirs(name: str) -> Path:
    """Create the default skill directory structure (SKILL.md + required dirs)."""
    skill_dir = _skill_dir(name)
    skill_dir.mkdir(parents=True, exist_ok=True)
    for d in _SKILL_REQUIRED_DIRS:
        (skill_dir / d).mkdir(exist_ok=True)
    # Write minimal SKILL.md if it doesn't exist
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md.write_text(f"---\nname: {name}\ndescription: {name}\n---\n\n", encoding="utf-8")
    logger.info("Created default skill structure for %s", name)
    return skill_dir


def validate_skill_protocol(name: str) -> dict:
    """Validate a skill directory against the Skill protocol.

    Returns dict with 'valid' (bool) and 'errors' (list[str]).
    """
    errors: list[str] = []
    skill_dir = _skill_dir(name)

    if not skill_dir.is_dir():
        return {"valid": False, "errors": ["Skill directory not found"]}

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return {"valid": False, "errors": ["SKILL.md is required"]}

    try:
        parsed = _parse_skill_md(skill_md)
    except Exception:
        return {"valid": False, "errors": ["Failed to parse SKILL.md"]}

    # Validate name
    parsed_name = parsed.get("name", "")
    if not parsed_name:
        errors.append("SKILL.md frontmatter missing 'name' field")
    elif not _SKILL_NAME_RE.match(parsed_name):
        errors.append(f"Invalid skill name '{parsed_name}': must be lowercase alphanumeric with single hyphens")
    elif parsed_name != name:
        errors.append(f"SKILL.md name '{parsed_name}' does not match directory name '{name}'")

    # Validate description
    parsed_desc = parsed.get("description", "")
    if not parsed_desc:
        errors.append("SKILL.md frontmatter missing 'description' field")
    elif len(parsed_desc) > 1024:
        errors.append(f"Description too long ({len(parsed_desc)} chars, max 1024)")

    return {"valid": len(errors) == 0, "errors": errors}
