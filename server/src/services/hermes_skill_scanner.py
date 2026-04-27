"""Scan Hermes Agent skill directories and register them in the DB.

Scans two directories:
- tmp/hermes-agent/skills/       (core skills, 74 across 25 categories)
- tmp/hermes-agent/optional-skills/ (optional skills, 59 across 16 categories)

Each category directory contains skill subdirectories with SKILL.md files.
Parses YAML frontmatter (name, description, version, license, hermes metadata).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select

from src.models.agent import Tool
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HERMES_SKILLS_DIR = PROJECT_ROOT / "tmp" / "hermes-agent" / "skills"
HERMES_OPTIONAL_DIR = PROJECT_ROOT / "tmp" / "hermes-agent" / "optional-skills"


def parse_skill_md(filepath: Path) -> dict:
    """Parse YAML frontmatter + body from a SKILL.md file.

    Returns dict with keys: name, description, version, license, author,
    metadata (full metadata dict), hermes_tags, hermes_related, body.
    """
    text = filepath.read_text(encoding="utf-8")
    result: dict = {
        "name": "",
        "description": "",
        "version": None,
        "license": None,
        "author": None,
        "metadata": {},
        "hermes_tags": [],
        "hermes_related": [],
        "body": "",
    }

    if not text.startswith("---"):
        return result

    parts = text.split("---", 2)
    if len(parts) < 3:
        return result

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except Exception:
        logger.warning("Failed to parse YAML frontmatter: %s", filepath)
        return result

    result["name"] = str(fm.get("name", "")).strip()
    result["description"] = str(fm.get("description", "")).strip()
    result["version"] = fm.get("version")
    result["license"] = fm.get("license")
    result["author"] = fm.get("author")
    result["body"] = parts[2].strip()

    metadata = fm.get("metadata", {})
    if isinstance(metadata, dict):
        result["metadata"] = metadata
        hermes = metadata.get("hermes", {})
        if isinstance(hermes, dict):
            result["hermes_tags"] = hermes.get("tags", []) or []
            result["hermes_related"] = hermes.get("related_skills", []) or []

    return result


def scan_skills_dir(base_dir: Path, source_label: str) -> list[dict]:
    """Scan a skills directory and return metadata for every valid SKILL.md found.

    Returns list of dicts, each with: name, description, version, license,
    category (parent dir name), source_path (absolute), source_label ("core"/"optional"),
    hermes_tags, hermes_related, metadata, body.
    """
    skills: list[dict] = []
    if not base_dir.exists():
        logger.warning("Skills directory not found: %s", base_dir)
        return skills

    for category_dir in sorted(base_dir.iterdir()):
        if not category_dir.is_dir():
            continue
        category = category_dir.name

        for skill_dir in sorted(category_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue

            try:
                parsed = parse_skill_md(skill_md)
                name = parsed["name"]
                if not name or not parsed["description"]:
                    logger.warning("Skipping skill with no name/description: %s", skill_md)
                    continue

                skills.append({
                    "name": name,
                    "description": parsed["description"],
                    "version": parsed["version"],
                    "license": parsed["license"],
                    "author": parsed["author"],
                    "category": category,
                    "source_path": str(skill_dir),
                    "source_label": source_label,
                    "hermes_tags": parsed["hermes_tags"],
                    "hermes_related": parsed["hermes_related"],
                    "metadata": parsed["metadata"],
                    "body": parsed["body"],
                })
            except Exception:
                logger.exception("Failed to parse skill: %s", skill_md)

    return skills


def scan_all_hermes_skills() -> list[dict]:
    """Scan both main and optional hermes skill directories."""
    all_skills: list[dict] = []

    core = scan_skills_dir(HERMES_SKILLS_DIR, "core")
    logger.info("Scanned %d core hermes skills from %s", len(core), HERMES_SKILLS_DIR)
    all_skills.extend(core)

    optional = scan_skills_dir(HERMES_OPTIONAL_DIR, "optional")
    logger.info("Scanned %d optional hermes skills from %s", len(optional), HERMES_OPTIONAL_DIR)
    all_skills.extend(optional)

    seen: set[str] = set()
    deduped: list[dict] = []
    for sk in all_skills:
        if sk["name"] not in seen:
            seen.add(sk["name"])
            deduped.append(sk)
        else:
            logger.debug("Skipping duplicate skill '%s' from optional dir", sk["name"])

    return deduped


async def sync_hermes_skills_to_db() -> dict:
    """Sync all hermes skills to the database.

    - New skills are created (inactive by default, is_approved=True).
    - Existing skills have category, source_path, and hermes metadata updated.
    - Skills no longer on filesystem are left untouched (cleanup is manual).

    Returns: {"created": N, "updated": N, "total_scanned": N}
    """
    fs_skills = scan_all_hermes_skills()
    if not fs_skills:
        return {"created": 0, "updated": 0, "total_scanned": 0}

    created = 0
    updated = 0

    async with async_session_factory() as db:
        for sk in fs_skills:
            name = sk["name"]
            existing = await db.scalar(select(Tool).where(Tool.name == name))

            if existing is not None:
                existing.category = sk["category"]
                existing.source_path = sk["source_path"]

                cfg = dict(existing.config or {})
                if sk["version"]:
                    cfg["version"] = sk["version"]
                if sk["license"]:
                    cfg["license"] = sk["license"]
                existing_meta = cfg.get("metadata", {})
                if isinstance(existing_meta, dict):
                    existing_meta.update(sk["metadata"])
                    cfg["metadata"] = existing_meta
                else:
                    cfg["metadata"] = sk["metadata"]
                if sk["hermes_tags"]:
                    cfg["hermes_tags"] = sk["hermes_tags"]
                if sk["hermes_related"]:
                    cfg["hermes_related"] = sk["hermes_related"]
                if sk["body"] and not cfg.get("skill_prompt"):
                    cfg["skill_prompt"] = sk["body"]

                existing.config = cfg
                db.add(existing)
                updated += 1
                logger.debug("Updated existing skill '%s' (category=%s)", name, sk["category"])
            else:
                tool = Tool(
                    name=name,
                    type="skill",
                    description=sk["description"],
                    category=sk["category"],
                    source_path=sk["source_path"],
                    is_active=False,
                    is_approved=True,
                    config={
                        "version": sk["version"],
                        "license": sk["license"],
                        "metadata": sk["metadata"],
                        "hermes_tags": sk["hermes_tags"],
                        "hermes_related": sk["hermes_related"],
                        "skill_prompt": sk["body"],
                        "source_label": sk["source_label"],
                        "registered_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                db.add(tool)
                created += 1
                logger.info("Auto-registered hermes skill: %s (category=%s, source=%s)",
                            name, sk["category"], sk["source_label"])

        if created or updated:
            await db.commit()

    logger.info("Hermes skill sync complete: created=%d, updated=%d, total=%d",
                created, updated, len(fs_skills))
    return {"created": created, "updated": updated, "total_scanned": len(fs_skills)}
