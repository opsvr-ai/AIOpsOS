import os
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from src.api.deps import get_current_user
from src.config import settings

router = APIRouter()


@router.get("/logs/files")
async def list_log_files(_=Depends(get_current_user)):
    log_dir = settings.log_dir
    if not os.path.isdir(log_dir):
        return []
    files = []
    for name in sorted(os.listdir(log_dir), reverse=True):
        path = os.path.join(log_dir, name)
        if os.path.isfile(path):
            stat = os.stat(path)
            files.append({
                "name": name,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return files


@router.get("/logs/view")
async def view_logs(
    file: str = Query(..., description="Log filename"),
    level: str | None = Query(None),
    search: str | None = Query(None),
    module: str | None = Query(None),
    lines: int = Query(500, ge=1, le=10000),
    tail: bool = Query(True),
    _=Depends(get_current_user),
):
    log_dir = settings.log_dir
    file_path = os.path.join(log_dir, os.path.basename(file))
    if not os.path.isfile(file_path):
        return {"lines": [], "total": 0, "file": file}

    all_lines = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.readlines()

    source_lines = raw[-lines:] if tail else raw[:lines]

    level_order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    for line in source_lines:
        line = line.rstrip("\n").rstrip("\r")
        parsed = _parse_log_line(line)
        if level and parsed["level"] not in _level_filter(level, level_order):
            continue
        if search and search.lower() not in line.lower():
            continue
        if module and module.lower() not in parsed["logger"].lower():
            continue
        all_lines.append({"raw": line, **parsed})

    return {"lines": all_lines, "total": len(raw), "shown": len(all_lines), "file": file}


def _parse_log_line(line: str) -> dict:
    if line.startswith("{"):
        import json
        try:
            obj = json.loads(line)
            return {
                "timestamp": obj.get("timestamp", ""),
                "level": obj.get("level", ""),
                "logger": obj.get("logger", ""),
                "module": obj.get("module", ""),
                "func": obj.get("funcName", ""),
                "lineno": obj.get("lineno", 0),
            }
        except json.JSONDecodeError:
            pass

    parts = line.split(" | ", 3)
    if len(parts) >= 4:
        mod = parts[2].strip()
        return {
            "timestamp": parts[0].strip(),
            "level": parts[1].strip(),
            "logger": mod,
            "module": mod.rsplit(".", 1)[-1] if "." in mod else mod,
            "func": "",
            "lineno": 0,
        }
    return {"timestamp": "", "level": "", "logger": "", "module": "", "func": "", "lineno": 0}


def _level_filter(target: str, order: list[str]) -> list[str]:
    target = target.upper()
    if target not in order:
        return order
    return order[order.index(target):]
