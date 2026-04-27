import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/docs", tags=["docs"])

DOCS_ROOT = Path(__file__).resolve().parents[4] / "docs"

CATEGORY_LABELS = {
    "user-guide": "用户指南",
    "admin-guide": "管理员指南",
    "api": "API 文档",
}


@router.get("")
async def list_docs():
    """List all documentation files grouped by category."""
    categories = []
    for entry in sorted(DOCS_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        files = sorted(
            [
                {
                    "name": f.stem,
                    "title": _extract_title(f),
                    "path": str(f.relative_to(DOCS_ROOT)),
                }
                for f in entry.glob("*.md")
            ],
            key=lambda x: x["name"],
        )
        if files:
            categories.append({
                "key": entry.name,
                "label": CATEGORY_LABELS.get(entry.name, entry.name),
                "files": files,
            })
    # Top-level files (roadmap.md etc.)
    top_files = sorted(
        [
            {
                "name": f.stem,
                "title": _extract_title(f),
                "path": f.name,
            }
            for f in DOCS_ROOT.glob("*.md")
        ],
        key=lambda x: x["name"],
    )
    if top_files:
        categories.insert(0, {
            "key": "_root",
            "label": "总览",
            "files": top_files,
        })
    return {"categories": categories}


@router.get("/{file_path:path}", response_class=PlainTextResponse)
async def get_doc(file_path: str):
    """Return the raw markdown content of a doc file."""
    full = DOCS_ROOT / file_path
    resolved = full.resolve()
    if not str(resolved).startswith(str(DOCS_ROOT.resolve())):
        raise HTTPException(status_code=403, detail="Path traversal denied")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Document not found")
    return resolved.read_text(encoding="utf-8")


def _extract_title(f: Path) -> str:
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("# ") and not s.startswith("## "):
                return s[2:].strip()
    except Exception:
        pass
    return f.stem.replace("-", " ").title()
