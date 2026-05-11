"""Seed database with built-in skills from server/data/skills directory.

Recursively scans for SKILL.md files, parses YAML frontmatter, and upserts
Tool records with type='skill' and is_builtin=True.
"""

import asyncio
import logging
import re
from pathlib import Path

import yaml
from sqlalchemy import select

from src.models.agent import Tool
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)

# Path to skills directory relative to server root
SKILLS_DIR = Path(__file__).parent.parent / "data" / "skills"


def parse_skill_frontmatter(skill_path: Path) -> dict | None:
    """Parse YAML frontmatter from a SKILL.md file.
    
    Returns dict with keys: name, description, version, metadata, content
    Returns None if parsing fails.
    """
    try:
        content = skill_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to read %s: %s", skill_path, e)
        return None
    
    # Match YAML frontmatter between --- markers
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not match:
        logger.warning("No YAML frontmatter found in %s", skill_path)
        return None
    
    frontmatter_str, body = match.groups()
    
    try:
        frontmatter = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        logger.warning("Failed to parse YAML in %s: %s", skill_path, e)
        return None
    
    if not isinstance(frontmatter, dict):
        logger.warning("Invalid frontmatter format in %s", skill_path)
        return None
    
    name = frontmatter.get("name")
    if not name:
        logger.warning("Missing 'name' in frontmatter of %s", skill_path)
        return None
    
    return {
        "name": name,
        "description": frontmatter.get("description", ""),
        "version": frontmatter.get("version", "1.0.0"),
        "metadata": frontmatter.get("metadata", {}),
        "content": body.strip(),
        "source_path": str(skill_path.relative_to(SKILLS_DIR.parent.parent)),
    }


def discover_skills(skills_dir: Path) -> list[dict]:
    """Recursively discover all SKILL.md files and parse them."""
    skills = []
    
    if not skills_dir.exists():
        logger.warning("Skills directory does not exist: %s", skills_dir)
        return skills
    
    for skill_path in skills_dir.rglob("SKILL.md"):
        # Skip hidden directories (like .hermes)
        if any(part.startswith(".") for part in skill_path.parts):
            continue
        
        skill_data = parse_skill_frontmatter(skill_path)
        if skill_data:
            # Derive category from directory structure
            rel_path = skill_path.parent.relative_to(skills_dir)
            parts = rel_path.parts
            if parts:
                skill_data["category"] = parts[0]  # Top-level category
            else:
                skill_data["category"] = "general"
            
            skills.append(skill_data)
            logger.debug("Discovered skill: %s", skill_data["name"])
    
    return skills


async def seed_skills():
    """Seed all built-in skills into the database."""
    skills = discover_skills(SKILLS_DIR)
    
    if not skills:
        logger.info("No skills found to seed")
        return
    
    logger.info("Found %d skills to seed", len(skills))
    
    async with async_session_factory() as db:
        created = 0
        updated = 0
        
        for skill_data in skills:
            # Check if skill already exists by name
            existing = await db.scalar(
                select(Tool).where(
                    Tool.name == skill_data["name"],
                    Tool.type == "skill",
                    Tool.is_builtin == True,  # noqa: E712
                )
            )
            
            config = {
                "version": skill_data["version"],
                "metadata": skill_data["metadata"],
                "content": skill_data["content"],
            }
            
            if existing:
                # Update existing skill
                existing.description = skill_data["description"]
                existing.category = skill_data["category"]
                existing.source_path = skill_data["source_path"]
                existing.config = config
                existing.is_active = True
                updated += 1
            else:
                # Create new skill
                tool = Tool(
                    name=skill_data["name"],
                    type="skill",
                    description=skill_data["description"],
                    category=skill_data["category"],
                    source_path=skill_data["source_path"],
                    config=config,
                    is_active=True,
                    is_builtin=True,
                    safety="parallel-safe",  # Skills are read-only by default
                )
                db.add(tool)
                created += 1
        
        await db.commit()
        logger.info("Skill seeding complete: %d created, %d updated", created, updated)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(seed_skills())
