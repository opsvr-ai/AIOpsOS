"""Import orphaned report HTML files from filesystem back into the DB.

Run: poetry run python scripts/recover_reports.py
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

from src.models.base import async_session_factory
from src.models.report import Report

USER_ID = uuid.UUID("d0ca6590-0794-42b7-8586-2646fad671fb")
SPACE_ID = uuid.UUID("7118075a-6223-40d2-b9c0-c6eb8e565e25")

DIR_VISIBILITY = {
    "home": "private",
    "data/reports": "space",
    "shared": "public",
}


def extract_title(html: str, filename: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
        t = soup.find("title")
        if t and t.string:
            return t.string.strip()[:500]
    except Exception:
        pass
    name = Path(filename).stem
    return re.sub(r"[-_]", " ", name)[:500]


def get_visibility(rel_path: str) -> str:
    for prefix, vis in DIR_VISIBILITY.items():
        if rel_path.startswith(prefix):
            return vis
    return "space"


async def main():
    root = Path(__file__).resolve().parent.parent
    files: list[Path] = []
    for d in ["data/reports", "home", "shared"]:
        dp = root / d
        if dp.exists():
            files.extend(dp.rglob("*.html"))

    print(f"Found {len(files)} HTML files")

    imported = 0
    async with async_session_factory() as db:
        for fp in files:
            content = fp.read_text(encoding="utf-8")
            rel = str(fp.relative_to(root))
            title = extract_title(content, fp.name)
            visibility = get_visibility(rel)
            mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=UTC)

            report = Report(
                id=uuid.uuid4(),
                user_id=USER_ID,
                space_id=SPACE_ID,
                title=title,
                description=f"Recovered from {rel}",
                html_content=content,
                theme="ops",
                status="published",
                visibility=visibility,
            )
            report.created_at = mtime
            report.updated_at = mtime
            db.add(report)
            print(f"  OK  {title[:60]} -> {visibility}")
            imported += 1

        await db.commit()
    print(f"\nDone: {imported} imported")


if __name__ == "__main__":
    asyncio.run(main())
