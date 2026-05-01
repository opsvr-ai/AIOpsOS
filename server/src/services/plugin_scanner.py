"""Scan plugin directories and register them as Tools with type='plugin'.

Expected plugin layout:
  data/plugins/<name>/
    plugin.json   or   PLUGIN.md (YAML frontmatter)
"""

import json
import logging
from pathlib import Path

from sqlalchemy import select

from src.models.agent import Tool
from src.models.base import async_session_factory

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PLUGINS_DIR = PROJECT_ROOT / "data" / "plugins"


def _try_parse_frontmatter(filepath: Path) -> dict | None:
    text = filepath.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            import yaml
            try:
                return yaml.safe_load(parts[1]) or {}
            except Exception:
                return None
    return None


async def scan_plugins() -> list[Tool]:
    """Scan PLUGINS_DIR for plugin dirs and register them as plugin-type Tools."""
    if not PLUGINS_DIR.exists():
        logger.info("No plugins directory at %s", PLUGINS_DIR)
        return []

    registered: list[Tool] = []

    for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
        if not plugin_dir.is_dir():
            continue

        manifest = None
        manifest_path = plugin_dir / "plugin.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("Invalid plugin.json in %s", plugin_dir.name)

        if manifest is None:
            fm = _try_parse_frontmatter(plugin_dir / "PLUGIN.md")
            if fm:
                manifest = fm

        if manifest is None:
            logger.debug("Skipping %s: no plugin.json or PLUGIN.md found", plugin_dir.name)
            continue

        name = manifest.get("name", plugin_dir.name)
        desc = manifest.get("description", "")

        async with async_session_factory() as db:
            result = await db.execute(
                select(Tool).where(Tool.name == name, Tool.type == "plugin")
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.description = desc or existing.description
                existing.source_path = str(plugin_dir)
                existing.config = {"manifest": manifest, "plugin_dir": str(plugin_dir)}
                await db.commit()
                registered.append(existing)
                logger.info("Updated plugin: %s", name)
            else:
                tool = Tool(
                    name=name,
                    type="plugin",
                    description=desc,
                    source_path=str(plugin_dir),
                    config={"manifest": manifest, "plugin_dir": str(plugin_dir)},
                    is_active=True,
                    is_approved=False,
                )
                db.add(tool)
                await db.commit()
                await db.refresh(tool)
                registered.append(tool)
                logger.info("Registered plugin: %s", name)

    return registered
