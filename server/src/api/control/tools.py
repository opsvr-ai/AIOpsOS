"""Tools & MCP Servers CRUD + skill upload, batch ops, AI generation."""

import io
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import select

from src.api.deps import DbSession, get_current_user, get_optional_space_id, require_perm
from src.models.agent import MCPServer, SkillVersion, Tool
from src.schemas.agent import (
    BatchConsistencyOut,
    BatchDeleteRequest,
    BatchStatusRequest,
    ConsistencySummary,
    MCPServerCreate,
    MCPServerOut,
    SkillDirectoryCreate,
    SkillFileNode,
    SkillFileWriteRequest,
    SkillGenerateRequest,
    SkillRollbackRequest,
    SkillUploadResponse,
    SkillUploadResult,
    SkillValidationResult,
    SkillVersionOut,
    SyncAction,
    SyncExecuteOut,
    SyncExecuteRequest,
    SyncScanOut,
    SyncDiffItem,
    ToolConsistencyOut,
    ToolCreate,
    ToolListOut,
    ToolOut,
    ToolSearchParams,
    ToolUpdate,
)
from src.services.tool_manager import tool_manager
from src.services.skill_sync import (
    SKILLS_DIR,
    check_tool_consistency,
    compute_content_hash,
    create_default_skill_dirs,
    create_skill_subdir,
    create_version_snapshot,
    delete_skill_path,
    list_skill_files,
    read_skill_file,
    remove_skill_file,
    store_file_hash,
    sync_from_filesystem,
    sync_tool_to_filesystem,
    validate_skill_protocol,
    write_skill_file,
    write_skill_file_content,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _merge_skill_config(body: ToolCreate) -> dict:
    """Merge skill protocol fields into the config JSONB."""
    cfg = dict(body.config or {})
    if body.version is not None:
        cfg["version"] = body.version
    if body.license is not None:
        cfg["license"] = body.license
    if body.compatibility is not None:
        cfg["compatibility"] = body.compatibility
    if body.metadata:
        cfg["metadata"] = body.metadata
    if body.allowed_tools:
        cfg["allowed_tools"] = body.allowed_tools
    if body.skill_prompt is not None:
        cfg["skill_prompt"] = body.skill_prompt
    return cfg


@router.get("/tools", response_model=ToolListOut)
async def list_tools(
    db: DbSession,
    params: ToolSearchParams = Depends(),  # noqa: B008
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    from sqlalchemy import or_

    base_query = select(Tool)
    if params.type:
        base_query = base_query.where(Tool.type == params.type)
    if params.name:
        base_query = base_query.where(Tool.name.ilike(f"%{params.name}%"))
    if params.description:
        base_query = base_query.where(Tool.description.ilike(f"%{params.description}%"))
    if params.category:
        base_query = base_query.where(Tool.category == params.category)
    if params.space_id:
        base_query = base_query.where(Tool.space_id == params.space_id)
    elif space_id:
        base_query = base_query.where(
            or_(Tool.space_id == space_id, Tool.space_id.is_(None))
        )
    if params.status == "active":
        base_query = base_query.where(Tool.is_active == True)
    elif params.status == "inactive":
        base_query = base_query.where(Tool.is_active == False)

    from sqlalchemy import func

    # health=invalid requires filesystem checks — fetch all skill tools, filter in memory
    if params.health == "invalid":
        base_query = base_query.where(Tool.type == "skill")
        result = await db.execute(base_query.order_by(Tool.created_at.desc()))
        all_tools = list(result.scalars().all())
        for t in all_tools:
            vr = validate_skill_protocol(t.name)
            t.is_valid = vr["valid"]
        invalid_tools = [t for t in all_tools if t.is_valid is False]
        total = len(invalid_tools)
        start = (params.page - 1) * params.page_size
        tools = invalid_tools[start:start + params.page_size]
        return ToolListOut(items=tools, total=total)

    # Normal path: paginate in DB
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = base_query.order_by(Tool.created_at.desc())
    query = query.offset((params.page - 1) * params.page_size).limit(params.page_size)
    result = await db.execute(query)
    tools = list(result.scalars().all())

    # Compute is_valid for skill tools (for UI badges)
    for t in tools:
        if t.type == "skill":
            vr = validate_skill_protocol(t.name)
            t.is_valid = vr["valid"]
        else:
            t.is_valid = None

    return ToolListOut(items=tools, total=total)


@router.post("/tools", response_model=ToolOut)
async def create_tool(
    body: ToolCreate, db: DbSession, _=Depends(require_perm("tools", "create"))
):
    if body.type == "skill":
        _validate_skill_tool(body)
        body.config = _merge_skill_config(body)
    tool = Tool(**body.model_dump())
    db.add(tool)
    await db.flush()
    if tool.type == "skill":
        create_default_skill_dirs(tool.name)
        _filepath, hash_val = write_skill_file(tool)
        store_file_hash(tool, hash_val)
    await db.commit()
    await db.refresh(tool)
    return tool


@router.get("/tools/consistency-summary", response_model=ConsistencySummary)
async def consistency_summary(db: DbSession, _=Depends(get_current_user)):
    """Return a lightweight count of inconsistent skill tools (uses batch filesystem scan)."""
    from src.services.skill_sync import batch_inconsistency_count

    result = await db.execute(select(Tool).where(Tool.type == "skill"))
    tools = list(result.scalars().all())
    count = batch_inconsistency_count(tools)
    return ConsistencySummary(inconsistent_count=count)


@router.get("/tools/check-consistency", response_model=BatchConsistencyOut)
async def check_consistency(
    db: DbSession,
    tool_id: str | None = None,
    _=Depends(get_current_user),
):
    """Check skill consistency between DB and filesystem."""
    if tool_id:
        result = await db.execute(select(Tool).where(Tool.id == tool_id))
        tools = list(result.scalars().all())
    else:
        result = await db.execute(select(Tool).where(Tool.type == "skill"))
        tools = list(result.scalars().all())

    results = [check_tool_consistency(t) for t in tools]
    inconsistent = sum(1 for r in results if r["is_consistent"] is False)
    return BatchConsistencyOut(
        tools=[ToolConsistencyOut(**r) for r in results],
        inconsistent_count=inconsistent,
    )


@router.get("/tools/categories")
async def list_categories(db: DbSession, _=Depends(get_current_user)):
    """Return all categories with skill counts."""
    from sqlalchemy import func
    result = await db.execute(
        select(Tool.category, func.count(Tool.id).label("cnt"))
        .where(Tool.type == "skill", Tool.category.isnot(None))
        .group_by(Tool.category)
        .order_by(Tool.category)
    )
    rows = result.all()
    return [{"category": r[0], "count": r[1]} for r in rows]


@router.get("/tools/{tool_id}", response_model=ToolOut)
async def get_tool(tool_id: str, db: DbSession, _=Depends(get_current_user)):
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return tool


def _validate_skill_tool(body: ToolCreate) -> None:
    """Validate skill protocol fields per Agent Skills specification."""
    name = body.name
    if not name or len(name) > 64:
        raise HTTPException(status_code=422, detail="Skill name must be 1-64 characters")
    if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', name) and len(name) > 2:
        if not re.match(r'^[a-z0-9]+$', name):
            raise HTTPException(
                status_code=422,
                detail="Skill name must be lowercase alphanumeric with single hyphens only, no leading/trailing hyphens",
            )
    if "--" in name:
        raise HTTPException(status_code=422, detail="Skill name must not contain consecutive hyphens")
    if body.description and len(body.description) > 1024:
        raise HTTPException(status_code=422, detail="Skill description must be 1-1024 characters")
    if body.compatibility and len(body.compatibility) > 500:
        raise HTTPException(status_code=422, detail="Skill compatibility must be 1-500 characters")


async def _get_skill_tool(tool_id: str, db: DbSession) -> Tool:
    """Fetch a tool by ID and ensure it's a skill type."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if tool.type != "skill":
        raise HTTPException(status_code=400, detail="Not a skill type tool")
    return tool


def _merge_update_config(tool: Tool, body: ToolUpdate) -> None:
    """Merge skill protocol fields from update body into tool.config."""
    cfg = dict(tool.config or {})
    if body.version is not None:
        cfg["version"] = body.version
    if body.license is not None:
        cfg["license"] = body.license
    if body.compatibility is not None:
        cfg["compatibility"] = body.compatibility
    if body.metadata is not None:
        cfg["metadata"] = body.metadata
    if body.allowed_tools is not None:
        cfg["allowed_tools"] = body.allowed_tools
    if body.skill_prompt is not None:
        cfg["skill_prompt"] = body.skill_prompt
    tool.config = cfg


@router.patch("/tools/{tool_id}", response_model=ToolOut)
async def update_tool(
    tool_id: str, body: ToolUpdate, db: DbSession,
    _=Depends(require_perm("tools", "update"))
):
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")

    if tool.is_builtin:
        data = body.model_dump(exclude_unset=True)
        if data.get("is_active") is False:
            raise HTTPException(status_code=403, detail="Built-in tools cannot be deactivated")
        if any(k in data for k in ("name", "description", "config")):
            raise HTTPException(status_code=403, detail="Built-in tools cannot be modified")

    # Validate before enabling a skill
    if tool.type == "skill" and body.is_active is True and not tool.is_active:
        vr = validate_skill_protocol(tool.name)
        if not vr["valid"]:
            raise HTTPException(status_code=422, detail=f"Validation failed: {'; '.join(vr['errors'])}")

    # Save version snapshot before update (skill tools only)
    if tool.type == "skill":
        await create_version_snapshot(db, tool)

    for key, val in body.model_dump(exclude_unset=True).items():
        if key not in ("version", "license", "compatibility", "metadata", "allowed_tools", "skill_prompt"):
            setattr(tool, key, val)
    if tool.type == "skill":
        _merge_update_config(tool, body)

    await db.flush()
    if tool.type == "skill":
        if tool.is_active:
            _filepath, hash_val = write_skill_file(tool)
            store_file_hash(tool, hash_val)
        else:
            remove_skill_file(tool.name)
            if tool.config:
                tool.config.pop("_file_hash", None)
    await db.commit()
    await db.refresh(tool)
    return tool


@router.delete("/tools/{tool_id}")
async def delete_tool(
    tool_id: str, db: DbSession, _=Depends(require_perm("tools", "delete"))
):
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if tool.is_builtin:
        raise HTTPException(status_code=403, detail="Built-in tools cannot be deleted")
    tool_name = tool.name
    is_skill = tool.type == "skill"
    source_path = tool.source_path
    await db.delete(tool)
    await db.commit()
    if is_skill:
        remove_skill_file(tool_name)
        if source_path:
            sp = Path(source_path)
            if sp.exists() and sp.is_relative_to(SKILLS_DIR):
                shutil.rmtree(sp)
                logger.info("Removed skill source directory: %s", sp)
    return {"detail": "deleted"}


@router.post("/tools/{tool_id}/install")
async def install_tool(
    tool_id: str,
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    """Install a global/available tool into the current space."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if not space_id:
        raise HTTPException(status_code=400, detail="No space context; select a space first")
    if tool.space_id == space_id:
        return {"detail": "already installed", "tool_id": str(tool.id)}
    installed = Tool(
        name=tool.name,
        type=tool.type,
        description=tool.description,
        mcp_server_id=tool.mcp_server_id,
        category=tool.category,
        source_path=tool.source_path,
        config=tool.config,
        is_approved=tool.is_approved,
        is_active=True,
        space_id=space_id,
    )
    db.add(installed)
    await db.commit()
    await db.refresh(installed)
    return {"detail": "installed", "tool_id": str(installed.id)}


@router.post("/tools/{tool_id}/uninstall")
async def uninstall_tool(
    tool_id: str,
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    """Uninstall a space-scoped tool (delete or unlink from space)."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if not space_id or tool.space_id != space_id:
        raise HTTPException(status_code=403, detail="Can only uninstall tools in your current space")
    if tool.type == "skill" and tool.source_path:
        remove_skill_file(tool.name)
        sp = Path(tool.source_path)
        if sp.exists() and sp.is_relative_to(SKILLS_DIR):
            shutil.rmtree(sp)
    await db.delete(tool)
    await db.commit()
    return {"detail": "uninstalled"}


@router.post("/tools/reload")
async def reload_tools(_=Depends(require_perm("tools", "update"))):
    await tool_manager.reload()
    names = tool_manager.list_names()
    return {"detail": "ok", "count": len(names), "tools": names}


# ── Skill Upload ────────────────────────────────────────────

SKILL_NAME_RE = re.compile(r"^(?!.*--)[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$")


def _validate_zip_skill(zf: zipfile.ZipFile, filename: str) -> tuple[str | None, str, str]:
    """Validate a zip file contains a proper skill directory.

    Returns (skill_name, status, message).
    """
    names = zf.namelist()
    if not names:
        return None, "error", "Empty zip file"

    # Find SKILL.md — must be at <dir>/SKILL.md or at root SKILL.md
    skill_md_paths = [n for n in names if n.endswith("SKILL.md") or n.endswith("skill.md")]
    if not skill_md_paths:
        return None, "error", "No SKILL.md found in zip"

    skill_md_path = skill_md_paths[0]
    parts = skill_md_path.rstrip("/").split("/")

    # Read and parse SKILL.md frontmatter first
    content = zf.read(skill_md_path).decode("utf-8")
    if not content.startswith("---"):
        return None, "error", "SKILL.md must have YAML frontmatter"

    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        return None, "error", "SKILL.md has invalid frontmatter"

    import yaml
    try:
        fm = yaml.safe_load(frontmatter_match.group(1))
    except Exception:
        return None, "error", "SKILL.md frontmatter is not valid YAML"

    if not isinstance(fm, dict):
        return None, "error", "SKILL.md frontmatter is not a mapping"

    skill_name = str(fm.get("name", "")).strip()
    if not skill_name:
        return None, "error", "SKILL.md missing required 'name' field"

    # Validate name format
    if not SKILL_NAME_RE.match(skill_name):
        return None, "error", f"Invalid skill name '{skill_name}': must be lowercase alphanumeric with single hyphens"

    # When SKILL.md is at root level, the frontmatter name IS the directory name.
    # When in a subdirectory, the frontmatter name must match that subdirectory.
    if len(parts) == 1:
        dir_name = skill_name
    else:
        dir_name = parts[-2]
        if skill_name != dir_name:
            return None, "error", f"Skill name '{skill_name}' does not match directory name '{dir_name}'"

    desc = str(fm.get("description", "")).strip()
    if not desc:
        return None, "error", "SKILL.md missing required 'description' field"
    if len(desc) > 1024:
        return None, "error", "Description exceeds 1024 characters"

    return skill_name, "ok", ""


@router.post("/tools/upload", response_model=SkillUploadResponse)
async def upload_skills(
    db: DbSession,
    files: list[UploadFile] = File(...),
    _=Depends(require_perm("tools", "create")),
):
    """Upload skill zip files. Each zip must contain a skill directory with SKILL.md."""
    results: list[SkillUploadResult] = []
    created = updated = skipped = errors = 0

    for f in files:
        filename = f.filename or "unknown.zip"
        if not filename.endswith(".zip"):
            results.append(SkillUploadResult(filename=filename, status="error", message="Not a zip file"))
            errors += 1
            continue

        try:
            content = await f.read()
            zf = zipfile.ZipFile(io.BytesIO(content))

            skill_name, status, msg = _validate_zip_skill(zf, filename)
            if status == "error":
                results.append(SkillUploadResult(filename=filename, status="error", message=msg))
                errors += 1
                continue

            # Extract SKILL.md content for frontmatter
            skill_md_paths = [n for n in zf.namelist() if n.endswith("SKILL.md") or n.endswith("skill.md")]
            skill_md_content = zf.read(skill_md_paths[0]).decode("utf-8")

            # Parse frontmatter for metadata
            import yaml
            fm_match = re.match(r"^---\s*\n(.*?)\n---", skill_md_content, re.DOTALL)
            fm = yaml.safe_load(fm_match.group(1)) if fm_match else {}
            body = skill_md_content.split("---", 2)[-1].strip() if fm_match else ""

            # Extract to filesystem
            skills_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "skills"
            skill_dir = skills_dir / skill_name
            # Remove existing if updating
            if skill_dir.exists():
                shutil.rmtree(skill_dir)
            skill_dir.mkdir(parents=True, exist_ok=True)

            # Write all files from zip
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                # Strip the top-level directory from path
                member_parts = member.split("/")
                if member_parts[0] == skill_name or member_parts[0] == "":
                    rel_path = "/".join(member_parts[1:]) if member_parts[0] == skill_name else "/".join(member_parts)
                else:
                    rel_path = member
                target = skill_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))

            # Upsert DB record
            existing = await db.scalar(select(Tool).where(Tool.name == skill_name))
            cfg = {
                "version": fm.get("version"),
                "license": fm.get("license"),
                "compatibility": fm.get("compatibility"),
                "metadata": fm.get("metadata", {}),
                "allowed_tools": fm.get("allowed-tools", []),
                "skill_prompt": body,
            }
            if existing is not None:
                await create_version_snapshot(db, existing)
                existing.description = fm.get("description", existing.description)
                existing.config = cfg
                existing.is_active = True
                results.append(SkillUploadResult(filename=filename, name=skill_name, status="updated", message="Skill updated"))
                updated += 1
            else:
                tool = Tool(
                    name=skill_name,
                    type="skill",
                    description=fm.get("description", skill_name),
                    config=cfg,
                    is_active=True,
                    is_approved=True,
                )
                db.add(tool)
                results.append(SkillUploadResult(filename=filename, name=skill_name, status="created", message="Skill created"))
                created += 1

        except zipfile.BadZipFile:
            results.append(SkillUploadResult(filename=filename, status="error", message="Invalid zip file"))
            errors += 1
        except Exception as exc:
            logger.exception("Upload error for %s", filename)
            results.append(SkillUploadResult(filename=filename, status="error", message=str(exc)))
            errors += 1

    await db.commit()
    return SkillUploadResponse(
        total=len(files), created=created, updated=updated,
        skipped=skipped, errors=errors, results=results,
    )


# ── Batch Operations ──────────────────────────────────────

@router.post("/tools/batch-status")
async def batch_set_status(
    body: BatchStatusRequest, db: DbSession,
    _=Depends(require_perm("tools", "update")),
):
    """Batch enable or disable tools by IDs."""
    result = await db.execute(select(Tool).where(Tool.id.in_(body.tool_ids)))
    tools = result.scalars().all()

    skipped: list[str] = []

    # Skip built-in tools when deactivating
    if not body.is_active:
        non_builtin: list = []
        for t in tools:
            if t.is_builtin:
                skipped.append(t.name)
            else:
                non_builtin.append(t)
        tools = non_builtin

    # Validate skill tools before enabling — skip invalid ones gracefully
    if body.is_active:
        valid_tools: list = []
        for t in tools:
            if t.type == "skill" and not t.is_active:
                vr = validate_skill_protocol(t.name)
                if not vr["valid"]:
                    logger.warning("Skipping invalid skill %s: %s", t.name, "; ".join(vr["errors"]))
                    skipped.append(t.name)
                    continue
            valid_tools.append(t)
        tools = valid_tools

    # Save version snapshots for skill tools before status change
    for t in tools:
        if t.type == "skill":
            await create_version_snapshot(db, t)

    for t in tools:
        t.is_active = body.is_active
    await db.flush()

    # Sync to filesystem with hash
    for t in tools:
        if t.type == "skill":
            if t.is_active:
                _filepath, hash_val = write_skill_file(t)
                store_file_hash(t, hash_val)
            else:
                remove_skill_file(t.name)
                if t.config:
                    t.config.pop("_file_hash", None)
    await db.commit()
    result_detail = {"detail": "ok", "count": len(tools)}
    if skipped:
        result_detail["skipped"] = skipped
    return result_detail


@router.post("/tools/batch-delete")
async def batch_delete(
    body: BatchDeleteRequest, db: DbSession,
    _=Depends(require_perm("tools", "delete")),
):
    """Batch delete tools by IDs."""
    result = await db.execute(select(Tool).where(Tool.id.in_(body.tool_ids)))
    tools = result.scalars().all()
    deleted = 0
    for t in tools:
        if t.is_builtin:
            continue
        tool_name = t.name
        is_skill = t.type == "skill"
        source_path = t.source_path
        await db.delete(t)
        if is_skill:
            remove_skill_file(tool_name)
            if source_path:
                sp = Path(source_path)
                if sp.exists() and sp.is_relative_to(SKILLS_DIR):
                    shutil.rmtree(sp)
                    logger.info("Removed skill source directory: %s", sp)
        deleted += 1
    await db.commit()
    return {"detail": "ok", "count": deleted}


@router.post("/tools/{tool_id}/sync-from-filesystem", response_model=ToolOut)
async def sync_tool_from_fs(
    tool_id: str, db: DbSession, _=Depends(require_perm("tools", "update"))
):
    """Sync tool from filesystem: read SKILL.md and update DB record."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if tool.type != "skill":
        raise HTTPException(status_code=400, detail="Only skill tools can sync from filesystem")

    await create_version_snapshot(db, tool)
    await sync_from_filesystem(db, tool)
    await db.commit()
    await db.refresh(tool)
    return tool


@router.post("/tools/{tool_id}/sync-to-filesystem", response_model=ToolOut)
async def sync_tool_to_fs(
    tool_id: str, db: DbSession, _=Depends(require_perm("tools", "update"))
):
    """Sync tool to filesystem: overwrite SKILL.md with DB version."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    if tool.type != "skill":
        raise HTTPException(status_code=400, detail="Only skill tools can sync to filesystem")

    if tool.is_active:
        _filepath, hash_val = write_skill_file(tool)
        store_file_hash(tool, hash_val)
        await db.commit()
        await db.refresh(tool)
    return tool


# ── Version History & Rollback ──────────────────────────────


@router.get("/tools/{tool_id}/versions", response_model=list[SkillVersionOut])
async def list_versions(
    tool_id: str, db: DbSession, _=Depends(get_current_user)
):
    """List version history for a skill tool."""
    result = await db.execute(
        select(SkillVersion)
        .where(SkillVersion.tool_id == tool_id)
        .order_by(SkillVersion.created_at.desc())
    )
    return result.scalars().all()


@router.post("/tools/{tool_id}/rollback", response_model=ToolOut)
async def rollback_tool(
    tool_id: str,
    body: SkillRollbackRequest,
    db: DbSession,
    _=Depends(require_perm("tools", "update")),
):
    """Roll back a tool to a previous version. Current state is saved as a version first."""
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")

    result = await db.execute(
        select(SkillVersion).where(
            SkillVersion.id == body.version_id,
            SkillVersion.tool_id == tool_id,
        )
    )
    target_version = result.scalar_one_or_none()
    if target_version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    # Save current state as a version before rollback
    await create_version_snapshot(db, tool)

    # Restore from target version
    tool.name = target_version.name
    tool.description = target_version.description
    tool.config = dict(target_version.config)

    await db.flush()
    if tool.type == "skill" and tool.is_active:
        _filepath, hash_val = write_skill_file(tool)
        store_file_hash(tool, hash_val)

    await db.commit()
    await db.refresh(tool)
    return tool


# ── File Management for Skills ─────────────────────────────

@router.get("/tools/{tool_id}/files")
async def get_skill_files(
    tool_id: str, db: DbSession, _=Depends(get_current_user),
):
    """Get the full file tree of a skill directory."""
    tool = await _get_skill_tool(tool_id, db)
    tree = list_skill_files(tool.name)
    return tree


@router.get("/tools/{tool_id}/files/content")
async def get_skill_file_content(
    tool_id: str, path: str, db: DbSession, _=Depends(get_current_user),
):
    """Read the content of a file within a skill directory."""
    tool = await _get_skill_tool(tool_id, db)
    try:
        content = read_skill_file(tool.name, path)
        return {"path": path, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/tools/{tool_id}/files/content")
async def write_skill_file_endpoint(
    tool_id: str, body: SkillFileWriteRequest, db: DbSession,
    _=Depends(require_perm("tools", "update")),
):
    """Write content to a file within a skill directory. If SKILL.md, syncs DB."""
    tool = await _get_skill_tool(tool_id, db)
    try:
        # Save version snapshot before modifying
        await create_version_snapshot(db, tool)

        filepath = write_skill_file_content(tool.name, body.path, body.content)

        # If writing SKILL.md, sync frontmatter to DB
        if body.path == "SKILL.md" or body.path.endswith("/SKILL.md") or filepath.name == "SKILL.md":
            await sync_from_filesystem(db, tool)

        await db.commit()
        await db.refresh(tool)
        return {"path": body.path, "status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tools/{tool_id}/files/directory")
async def create_skill_directory_endpoint(
    tool_id: str, body: SkillDirectoryCreate, db: DbSession,
    _=Depends(require_perm("tools", "update")),
):
    """Create a subdirectory within a skill directory."""
    tool = await _get_skill_tool(tool_id, db)
    try:
        create_skill_subdir(tool.name, body.path)
        return {"path": body.path, "status": "created"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/tools/{tool_id}/files")
async def delete_skill_file_endpoint(
    tool_id: str, path: str, db: DbSession,
    _=Depends(require_perm("tools", "update")),
):
    """Delete a file or directory within a skill."""
    tool = await _get_skill_tool(tool_id, db)
    try:
        deleted = delete_skill_path(tool.name, path)
        if not deleted:
            # Check if it was SKILL.md refusal
            if path == "SKILL.md":
                raise HTTPException(status_code=400, detail="Cannot delete root SKILL.md")
            return {"path": path, "status": "not_found"}
        # Sync DB from remaining FS state
        await sync_from_filesystem(db, tool)
        await db.commit()
        return {"path": path, "status": "deleted"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tools/{tool_id}/files/upload")
async def upload_skill_files(
    tool_id: str, db: DbSession,
    files: list[UploadFile] = File(...),
    _=Depends(require_perm("tools", "update")),
):
    """Upload files into a skill directory."""
    tool = await _get_skill_tool(tool_id, db)
    results: list[dict] = []
    for f in files:
        if not f.filename:
            continue
        try:
            content = await f.read()
            write_skill_file_content(tool.name, f.filename, content.decode("utf-8"))
            results.append({"filename": f.filename, "status": "ok"})
        except ValueError as e:
            results.append({"filename": f.filename, "status": "error", "message": str(e)})
    await db.commit()
    return {"results": results}


@router.post("/tools/{tool_id}/validate", response_model=SkillValidationResult)
async def validate_skill_endpoint(
    tool_id: str, db: DbSession, _=Depends(get_current_user),
):
    """Validate a skill against the Skill protocol."""
    tool = await _get_skill_tool(tool_id, db)
    result = validate_skill_protocol(tool.name)
    return SkillValidationResult(**result)


# ── AI Skill Generation ───────────────────────────────────

AI_SKILL_GEN_PROMPT = """You are a skill creation expert following the Agent Skills specification (agentskills.io). Generate a complete, spec-compliant SKILL.md file.

## Request
- Skill name: {name}
- Description: {description}
- Language: {language}

## Specification (from agentskills.io)

### YAML Frontmatter

| Field | Required | Constraints |
|-------|----------|-------------|
| `name` | Yes | Max 64 chars. Lowercase letters, numbers, hyphens. No leading/trailing hyphens. No consecutive hyphens (--). Must match directory name. |
| `description` | Yes | Max 1024 chars. Must describe BOTH what the skill does AND when to use it. Include specific keywords. |
| `license` | No | Short license name or reference to bundled license file. |
| `compatibility` | No | Max 500 chars. Environment requirements if needed. Most skills don't need this. |
| `metadata` | No | Map of string→string. Use for author, version, or custom properties. Keys should be reasonably unique. |
| `allowed-tools` | No | Space-separated string of pre-approved tools. Experimental. Example: "Bash(git:*) Bash(jq:*) Read" |

### name field rules
- 1-64 characters
- Only lowercase a-z, 0-9, hyphens (-)
- No leading/trailing hyphens
- No consecutive hyphens (--)
- Valid: "pdf-processing", "data-analysis", "code-review"
- Invalid: "PDF-Processing" (uppercase), "-pdf" (leading hyphen), "pdf--processing" (consecutive hyphens)

### description field rules
- 1-1024 characters
- Describe what the skill does AND when to use it
- Include specific keywords agents can match against
- Good: "Extracts text and tables from PDF files, fills PDF forms, and merges multiple PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction."
- Poor: "Helps with PDFs."

### Markdown Body (Progressive Disclosure)
Structure for progressive loading (metadata → instructions → resources):

1. **Overview**: 1-2 sentences on what this skill enables
2. **Quick Reference**: Table mapping situations → actions
3. **When to Use**: Specific triggers, user phrases, task patterns
4. **Step-by-Step Instructions**: Numbered workflow the agent follows
5. **Examples**: Concrete inputs and expected outputs
6. **Common Edge Cases**: Pitfalls the agent should watch for

### Body Quality Rules
- Use {language} throughout
- Be specific — give exact commands, file paths, patterns
- Keep SKILL.md under 500 lines (move detailed reference to separate files)
- Reference bundled files with relative paths: `scripts/extract.py`, `references/REFERENCE.md`

Reply with ONLY the SKILL.md content. Start with --- on the first line."""


@router.post("/tools/ai-generate", response_model=dict)
async def ai_generate_skill(
    body: SkillGenerateRequest, _=Depends(require_perm("tools", "create")),
):
    """Generate a complete SKILL.md using AI based on name and description."""
    from langchain_core.messages import SystemMessage, HumanMessage

    prompt = AI_SKILL_GEN_PROMPT.format(
        name=body.name,
        description=body.description,
        language="zh" if body.language == "zh" else "en",
    )
    from src.core.model_factory import get_default_model
    llm = await get_default_model()

    max_attempts = 2
    last_content = ""
    for attempt in range(max_attempts):
        resp = await llm.ainvoke([
            SystemMessage(content="You are a skill creation expert following the Agent Skills specification. Generate complete, spec-compliant SKILL.md files. Reply ONLY with the SKILL.md content."),
            HumanMessage(content=prompt),
        ])
        content = resp.content.strip()
        last_content = content

        # Validate
        errors = _validate_generated_skill(content)
        if not errors:
            return {"content": content}

        if attempt < max_attempts - 1:
            retry_prompt = f"{prompt}\n\nPrevious attempt had validation errors:\n" + "\n".join(f"- {e}" for e in errors) + "\n\nFix these errors and regenerate."
            prompt = retry_prompt

    # Return the last attempt even with errors, but include validation info
    errors = _validate_generated_skill(last_content)
    return {"content": last_content, "warnings": errors}


def _validate_generated_skill(content: str) -> list[str]:
    """Validate a generated SKILL.md against the Agent Skills specification."""
    errors = []
    if not content.startswith("---"):
        errors.append("Missing YAML frontmatter (must start with ---)")
        return errors

    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        errors.append("Invalid frontmatter format")
        return errors

    import yaml
    try:
        fm = yaml.safe_load(frontmatter_match.group(1))
    except Exception:
        errors.append("Frontmatter is not valid YAML")
        return errors

    if not isinstance(fm, dict):
        errors.append("Frontmatter is not a mapping")
        return errors

    # Required: name
    name = str(fm.get("name", "")).strip()
    if not name:
        errors.append("Missing required 'name' field")
    elif not SKILL_NAME_RE.match(name):
        errors.append(f"Invalid name '{name}': must be lowercase alphanumeric with single hyphens, 1-64 chars")

    # Required: description
    desc = str(fm.get("description", "")).strip()
    if not desc:
        errors.append("Missing required 'description' field")
    elif len(desc) > 1024:
        errors.append(f"Description too long ({len(desc)} chars, max 1024)")

    # Optional validations
    compat = fm.get("compatibility", "")
    if compat and len(str(compat)) > 500:
        errors.append(f"Compatibility too long ({len(str(compat))} chars, max 500)")

    # metadata must be string->string map per spec
    meta = fm.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            errors.append("metadata must be a key-value mapping")
        else:
            for k, v in meta.items():
                if not isinstance(v, str):
                    errors.append(f"metadata.{k} must be a string value (got {type(v).__name__})")

    # Body
    body = content.split("---", 2)[-1].strip() if "---" in content else ""
    if len(body) < 50:
        errors.append("Body too short — must contain meaningful instructions")

    return errors


# ── Skill Sync (Filesystem ↔ Database) ─────────────────────


def _scan_all_filesystem_skills() -> dict[str, dict]:
    """Scan data/skills/ recursively and return {name: metadata} dict.

    Sources: data/skills/ (flat user-created + nested standard/extended classified).
    Each entry includes version, category, source_path, source_label.
    """
    fs_skills: dict[str, dict] = {}

    from src.services.skill_sync import list_filesystem_skills
    for s in list_filesystem_skills():
        fs_skills[s["name"]] = {
            "name": s["name"],
            "description": s.get("description", ""),
            "version": s.get("version"),
            "category": s.get("category"),
            "source_path": s.get("_dir", ""),
            "source_label": s.get("source_label"),
        }

    return fs_skills


@router.post("/tools/sync/scan", response_model=SyncScanOut)
async def sync_scan(db: DbSession, _=Depends(get_current_user)):
    """Scan filesystem and DB, return a version-based diff.

    - only_in_fs: skill exists on disk but not in DB
    - only_in_db: skill exists in DB but not on disk
    - modified: skill exists in both, but FS version differs from DB version
    - consistent: skill exists in both with matching versions
    """
    fs_skills = _scan_all_filesystem_skills()

    result = await db.execute(select(Tool).where(Tool.type == "skill"))
    db_tools = {t.name: t for t in result.scalars().all()}

    fs_names = set(fs_skills.keys())
    db_names = set(db_tools.keys())

    only_in_fs: list[SyncDiffItem] = []
    only_in_db: list[SyncDiffItem] = []
    modified: list[SyncDiffItem] = []
    consistent = 0

    for name in sorted(fs_names - db_names):
        sk = fs_skills[name]
        only_in_fs.append(SyncDiffItem(
            name=name, status="only_in_fs",
            category=sk.get("category"), fs_category=sk.get("category"),
            fs_description=sk.get("description"), fs_version=sk.get("version"),
            source_path=sk.get("source_path"), source_label=sk.get("source_label"),
        ))

    for name in sorted(db_names - fs_names):
        t = db_tools[name]
        db_cfg = t.config or {}
        only_in_db.append(SyncDiffItem(
            name=name, status="only_in_db",
            category=t.category, db_id=str(t.id),
            db_description=t.description, db_version=db_cfg.get("version"),
            is_active=t.is_active,
        ))

    for name in sorted(fs_names & db_names):
        t = db_tools[name]
        sk = fs_skills[name]
        db_cfg = t.config or {}
        db_ver = db_cfg.get("version")
        fs_ver = sk.get("version")

        # Version-driven: modified only when FS version differs from DB version
        if fs_ver and fs_ver != db_ver:
            modified.append(SyncDiffItem(
                name=name, status="modified",
                category=t.category, db_id=str(t.id),
                db_description=t.description, db_version=db_ver,
                fs_description=sk.get("description"), fs_version=fs_ver,
                fs_category=sk.get("category"), source_path=sk.get("source_path"),
                source_label=sk.get("source_label"), is_active=t.is_active,
            ))
        else:
            consistent += 1

    return SyncScanOut(
        total_fs=len(fs_names),
        total_db=len(db_names),
        only_in_db=only_in_db,
        only_in_fs=only_in_fs,
        modified=modified,
        consistent=consistent,
    )


@router.post("/tools/sync/execute", response_model=SyncExecuteOut)
async def sync_execute(
    body: SyncExecuteRequest, db: DbSession,
    _=Depends(require_perm("tools", "update")),
):
    """Execute sync actions: register new skills from FS, update modified, or delete stale."""
    registered = 0
    updated = 0
    deleted = 0
    errors: list[str] = []

    fs_skills = _scan_all_filesystem_skills()

    for action in body.actions:
        try:
            if action.action == "register":
                sk = fs_skills.get(action.name)
                if not sk:
                    errors.append(f"Skill not found on filesystem: {action.name}")
                    continue
                existing = await db.scalar(select(Tool).where(Tool.name == action.name))
                if existing is not None:
                    errors.append(f"Skill already exists: {action.name}")
                    continue
                tool = Tool(
                    name=action.name,
                    type="skill",
                    description=sk.get("description", action.name),
                    category=sk.get("category"),
                    source_path=sk.get("source_path"),
                    is_active=False,
                    is_approved=True,
                    config={
                        "version": sk.get("version"),
                        "skill_prompt": sk.get("body", ""),
                    },
                )
                db.add(tool)
                registered += 1

            elif action.action == "update":
                t = await db.scalar(select(Tool).where(Tool.name == action.name))
                if t is None:
                    errors.append(f"Skill not found in DB: {action.name}")
                    continue
                sk = fs_skills.get(action.name)
                if sk:
                    await create_version_snapshot(db, t)
                    if sk.get("category"):
                        t.category = sk["category"]
                    if sk.get("source_path"):
                        t.source_path = sk["source_path"]
                    cfg = dict(t.config or {})
                    if sk.get("version"):
                        cfg["version"] = sk["version"]
                    if sk.get("body"):
                        cfg["skill_prompt"] = sk["body"]
                    t.config = cfg
                    db.add(t)
                updated += 1

            elif action.action == "delete":
                t = await db.scalar(select(Tool).where(Tool.name == action.name))
                if t is None:
                    errors.append(f"Skill not found in DB: {action.name}")
                    continue
                sp = t.source_path
                await db.delete(t)
                remove_skill_file(action.name)
                if sp:
                    sp_path = Path(sp)
                    if sp_path.exists() and sp_path.is_relative_to(SKILLS_DIR):
                        shutil.rmtree(sp_path)
                deleted += 1

            else:
                errors.append(f"Unknown action: {action.action}")
        except Exception as exc:
            errors.append(f"Error processing {action.name}: {exc}")
            logger.exception("Sync execute error for %s", action.name)

    await db.commit()
    return SyncExecuteOut(
        registered=registered, updated=updated, deleted=deleted, errors=errors,
    )


# ── MCP Servers ───────────────────────────────────────────

@router.get("/mcp-servers", response_model=list[MCPServerOut])
async def list_mcp_servers(
    db: DbSession,
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    query = select(MCPServer)
    if space_id:
        query = query.where(MCPServer.space_id == space_id)
    result = await db.execute(query.order_by(MCPServer.created_at.desc()))
    return result.scalars().all()


@router.post("/mcp-servers", response_model=MCPServerOut)
async def create_mcp_server(
    body: MCPServerCreate, db: DbSession, _=Depends(require_perm("tools", "create"))
):
    server = MCPServer(**body.model_dump())
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return server
