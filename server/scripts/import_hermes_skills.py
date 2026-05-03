"""Import Hermes Agent skills into the AIOpsOS platform.

Scans Hermes skills/ and optional-skills/ directories, parses SKILL.md files,
maps metadata to platform format, copies supporting files, and registers
inactive Tool records in the database.

Usage:
  cd server && python scripts/import_hermes_skills.py [--hermes-path PATH] [--activate]
"""

import argparse
import asyncio
import re
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.models.agent import Tool
from src.models.base import async_session_factory

SKILL_NAME_RE = re.compile(r"^(?!.*--)[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


def parse_skill_md(path: Path) -> dict | None:
    """Parse SKILL.md file, return frontmatter dict + body."""
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return None

    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None

    try:
        fm = yaml.safe_load(match.group(1))
    except Exception:
        return None

    if not isinstance(fm, dict):
        return None

    body = content.split("---", 2)[-1].strip() if "---" in content else ""
    return {"frontmatter": fm, "body": body, "raw": content}


def map_hermes_metadata(fm: dict) -> dict:
    """Map Hermes-specific frontmatter to platform config format."""
    config = {}

    if "version" in fm:
        config["version"] = str(fm["version"])
    if "license" in fm:
        config["license"] = str(fm["license"])
    if "compatibility" in fm:
        config["compatibility"] = str(fm["compatibility"])[:500]

    # Map Hermes metadata -> platform metadata (string->string per spec)
    meta = {}
    hermes_meta = fm.get("metadata", {})
    if isinstance(hermes_meta, dict):
        hermes = hermes_meta.get("hermes", {})
        if isinstance(hermes, dict):
            tags = hermes.get("tags", [])
            if isinstance(tags, list):
                meta["tags"] = ", ".join(str(t) for t in tags)
            related = hermes.get("related_skills", [])
            if isinstance(related, list):
                meta["related_skills"] = ", ".join(str(s) for s in related)
        for k, v in hermes_meta.items():
            if k != "hermes" and isinstance(v, str):
                meta[k] = v

    if "author" in fm:
        meta["author"] = str(fm["author"])

    if meta:
        config["metadata"] = meta

    if "allowed-tools" in fm:
        config["allowed_tools"] = str(fm["allowed-tools"])

    return config


def normalize_name(name: str) -> str:
    """Normalize a skill name to be spec-compliant."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    if not name:
        return "unnamed-skill"
    if len(name) > 64:
        name = name[:64].rstrip("-")
    return name


async def import_skills(hermes_base: Path, activate: bool = False):
    """Main import routine."""
    skills_dir = Path(__file__).resolve().parent.parent / "data" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "total_found": 0,
        "imported": 0,
        "skipped_existing": 0,
        "skipped_invalid": 0,
        "errors": [],
        "names": [],
    }

    source_dirs = []
    for sub in ("skills", "optional-skills"):
        d = hermes_base / sub
        if d.exists():
            source_dirs.append(d)

    print(f"Scanning {len(source_dirs)} source directories...")

    for src_dir in source_dirs:
        is_optional = src_dir.name == "optional-skills"
        prefix = "[optional] " if is_optional else ""

        for skill_md_path in sorted(src_dir.rglob("SKILL.md")):
            report["total_found"] += 1
            skill_src_dir = skill_md_path.parent
            rel_path = skill_src_dir.relative_to(src_dir)

            parsed = parse_skill_md(skill_md_path)
            if not parsed:
                print(f"  SKIP {skill_md_path}: invalid SKILL.md")
                report["skipped_invalid"] += 1
                continue

            fm = parsed["frontmatter"]
            raw_name = fm.get("name", skill_src_dir.name)
            skill_name = normalize_name(raw_name)

            if not SKILL_NAME_RE.match(skill_name):
                print(f"  SKIP {skill_md_path}: invalid name '{skill_name}'")
                report["skipped_invalid"] += 1
                continue

            description = str(fm.get("description", "")).strip()[:1024]
            if not description:
                description = f"Imported from Hermes: {raw_name}"

            async with async_session_factory() as db:
                existing = await db.scalar(select(Tool).where(Tool.name == skill_name))
            if existing:
                print(f"  SKIP {skill_name}: already exists")
                report["skipped_existing"] += 1
                continue

            config = map_hermes_metadata(fm)
            config["skill_prompt"] = parsed["body"]
            config["hermes_source"] = str(rel_path)
            if is_optional:
                config["hermes_optional"] = True

            dest_dir = skills_dir / skill_name
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)

            for item in skill_src_dir.iterdir():
                dest = dest_dir / item.name
                if item.is_dir():
                    if item.name not in ("__pycache__", ".git", "node_modules"):
                        shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            try:
                async with async_session_factory() as db:
                    tool = Tool(
                        name=skill_name,
                        type="skill",
                        description=description,
                        config=config,
                        is_active=activate,
                        is_approved=activate,
                    )
                    db.add(tool)
                    await db.commit()
                print(f"  OK {prefix}{skill_name}: {description[:60]}...")
                report["imported"] += 1
                report["names"].append(skill_name)
            except Exception as exc:
                print(f"  ERROR {skill_name}: {exc}")
                report["errors"].append({"name": skill_name, "error": str(exc)})
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)

    return report


def print_report(report: dict):
    """Print import summary."""
    print()
    print("=" * 60)
    print("IMPORT REPORT")
    print("=" * 60)
    print(f"  Total found:    {report['total_found']}")
    print(f"  Imported:       {report['imported']}")
    print(f"  Skipped (exist): {report['skipped_existing']}")
    print(f"  Skipped (invalid): {report['skipped_invalid']}")
    print(f"  Errors:         {len(report['errors'])}")
    if report["errors"]:
        for e in report["errors"]:
            print(f"    - {e['name']}: {e['error']}")
    print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="Import Hermes skills into AIOpsOS")
    parser.add_argument(
        "--hermes-path",
        default=str(Path(__file__).resolve().parent.parent.parent / "tmp" / "hermes-agent"),
        help="Path to Hermes agent source",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Activate imported skills (default: inactive)",
    )
    args = parser.parse_args()

    hermes_base = Path(args.hermes_path)
    if not hermes_base.exists():
        print(f"Error: Hermes path not found: {hermes_base}")
        sys.exit(1)

    print(f"Importing from: {hermes_base}")
    print(f"Activate skills: {args.activate}")
    print()

    report = await import_skills(hermes_base, activate=args.activate)
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
