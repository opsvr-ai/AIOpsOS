"""Knowledge base CRUD, search, file upload, image upload, reindex, raw, and wiki endpoints."""

import hashlib
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select, text

from src.api.deps import get_current_user, get_optional_space_id
from src.config import settings
from src.models.base import async_session_factory
from src.models.knowledge import KnowledgeChunk
from src.schemas.knowledge import (
    CompileRequest,
    CompileResult,
    ImageUploadResponse,
    KnowledgeDocumentCreate,
    KnowledgeDocumentOut,
    KnowledgeReindexResponse,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
    KnowledgeUploadResponse,
    RawFileOut,
    WikiPageOut,
    WikiSearchHit,
    WikiSearchResponse,
    WikiTreeNode,
)
from src.services.knowledge_base import knowledge_base
from src.schemas.kb_lint import LintFixResponse, LintReport
from src.schemas.kb_monitor import MonitorStatus, ProcessAllResult, ProcessResult, WatchedFile

router = APIRouter()


@router.post("/knowledge/documents", response_model=KnowledgeDocumentOut)
async def create_document(body: KnowledgeDocumentCreate, _=Depends(get_current_user)):
    doc = await knowledge_base.add_document(
        title=body.title,
        content=body.content,
        source=body.source,
        metadata=body.metadata,
    )
    await knowledge_base.export_to_files(doc)
    return KnowledgeDocumentOut(
        id=str(doc.id),
        title=doc.title,
        content=doc.content,
        source=doc.source,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.post("/knowledge/upload", response_model=KnowledgeUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    source: str | None = Form(None),
    convert_to_markdown: str = Form("false"),
    _=Depends(get_current_user),
):
    convert = convert_to_markdown.lower() in ("true", "1", "yes")
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    try:
        doc = await knowledge_base.add_document_from_file(
            file.file, file.filename, source=source,
            convert_to_markdown=convert,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await knowledge_base.export_to_files(doc)
    return KnowledgeUploadResponse(
        id=str(doc.id),
        title=doc.title,
        source=doc.source,
        chunk_count=doc.chunk_count,
        file_name=file.filename,
        created_at=doc.created_at,
    )


@router.post("/knowledge/images/upload", response_model=ImageUploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    _=Depends(get_current_user),
):
    """Upload an image for use in knowledge document markdown content."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    allowed = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {ext}")

    upload_path = os.path.join(settings.upload_dir, "knowledge")
    os.makedirs(upload_path, exist_ok=True)

    content = await file.read()
    name_hash = hashlib.md5(content).hexdigest()[:12]
    safe_name = f"{name_hash}{ext}"
    dest = os.path.join(upload_path, safe_name)
    with open(dest, "wb") as f:
        f.write(content)

    return ImageUploadResponse(
        url=f"/uploads/knowledge/{safe_name}",
        filename=file.filename,
    )


@router.post("/knowledge/reindex", response_model=KnowledgeReindexResponse)
async def reindex_knowledge(_=Depends(get_current_user)):
    doc_count, chunk_count = await knowledge_base.reindex_all()
    return KnowledgeReindexResponse(
        documents_processed=doc_count, chunks_created=chunk_count
    )


@router.get("/knowledge/documents", response_model=list[KnowledgeDocumentOut])
async def list_documents(
    _=Depends(get_current_user),
    space_id: str | None = Depends(get_optional_space_id),
):
    docs = await knowledge_base.list_documents(space_id=space_id)
    return [
        KnowledgeDocumentOut(
            id=str(d.id),
            title=d.title,
            content=d.content,
            source=d.source,
            chunk_count=d.chunk_count,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in docs
    ]


@router.get("/knowledge/documents/{document_id}", response_model=KnowledgeDocumentOut)
async def get_document(document_id: str, _=Depends(get_current_user)):
    doc = await knowledge_base.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return KnowledgeDocumentOut(
        id=str(doc.id),
        title=doc.title,
        content=doc.content,
        source=doc.source,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.patch("/knowledge/documents/{document_id}", response_model=KnowledgeDocumentOut)
async def update_document(
    document_id: str,
    title: str | None = None,
    content: str | None = None,
    source: str | None = None,
    _=Depends(get_current_user),
):
    doc = await knowledge_base.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    async with async_session_factory() as db:
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        db_doc = result.scalar_one()
        if title is not None:
            db_doc.title = title
        if content is not None:
            db_doc.content = content
            # re-chunk and re-embed
            await db.execute(
                text("DELETE FROM knowledge_chunks WHERE document_id = :did"),
                {"did": document_id},
            )
            from src.services.knowledge_base import knowledge_base as kb
            chunks = kb._chunk_text(content)
            db_doc.chunk_count = len(chunks)
            await db.flush()
            embedder = knowledge_base._get_embeddings()
            if embedder:
                try:
                    embeddings = await embedder.aembed_documents(chunks)
                except Exception:
                    embeddings = [None] * len(chunks)
            else:
                embeddings = [None] * len(chunks)
            for i, chunk_text in enumerate(chunks):
                db.add(KnowledgeChunk(
                    document_id=db_doc.id,
                    content=chunk_text,
                    embedding=embeddings[i] if embeddings[i] is not None else None,
                    chunk_index=i,
                    chunk_metadata={"title": db_doc.title, "source": db_doc.source},
                ))
        if source is not None:
            db_doc.source = source
        await db.commit()
        await db.refresh(db_doc)
    return KnowledgeDocumentOut(
        id=str(doc.id),
        title=doc.title,
        content=doc.content,
        source=doc.source,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.delete("/knowledge/documents/{document_id}")
async def delete_document(document_id: str, _=Depends(get_current_user)):
    deleted = await knowledge_base.delete_document(document_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"detail": "deleted"}


@router.get("/knowledge/documents/{document_id}/related", response_model=list[KnowledgeSearchResult])
async def get_related_documents(document_id: str, _=Depends(get_current_user)):
    results = await knowledge_base.find_related(document_id)
    return [
        KnowledgeSearchResult(
            content=r.content,
            score=r.score,
            chunk_index=r.chunk_index,
            document_id=r.document_id,
            title=r.title,
            source=r.source,
        )
        for r in results
    ]


@router.get("/knowledge/index")
async def get_knowledge_index(_=Depends(get_current_user)):
    index = await knowledge_base.generate_index()
    return {"index": index}


@router.get("/knowledge/stats")
async def get_knowledge_stats(_=Depends(get_current_user)):
    return await knowledge_base.get_stats()


@router.post("/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    query: str,
    top_k: int = 5,
    source: str | None = None,
    _=Depends(get_current_user),
):
    results = await knowledge_base.retrieve(query, top_k=top_k, source=source)
    context = knowledge_base._format_context(results)
    return KnowledgeSearchResponse(
        results=[
            KnowledgeSearchResult(
                content=r.content,
                score=r.score,
                chunk_index=r.chunk_index,
                document_id=r.document_id,
                title=r.title,
                source=r.source,
            )
            for r in results
        ],
        context=context,
    )


# ── KB Monitor endpoints ───────────────────────────────────────────


@router.get("/knowledge/monitor/status", response_model=MonitorStatus)
async def get_monitor_status(_=Depends(get_current_user)):
    from src.services.kb_monitor import kb_monitor
    return kb_monitor.status()


@router.get("/knowledge/monitor/files", response_model=list[WatchedFile])
async def get_monitor_files(_=Depends(get_current_user)):
    from src.services.kb_monitor import kb_monitor
    return kb_monitor.watched_files()


@router.post("/knowledge/monitor/process-document", response_model=ProcessResult)
async def process_document(filepath: str, _=Depends(get_current_user)):
    """Manually trigger llm-wiki processing for a single raw source file."""
    from src.services.kb_monitor import kb_monitor
    from src.config import settings
    full_path = os.path.join(settings.wiki_path, filepath) if not os.path.isabs(filepath) else filepath
    return await kb_monitor.process_document(full_path)


@router.post("/knowledge/monitor/process-all", response_model=ProcessAllResult)
async def process_all_documents(_=Depends(get_current_user)):
    """Manually trigger llm-wiki processing for all raw source files."""
    from src.services.kb_monitor import kb_monitor
    return await kb_monitor.process_all()


# ── Wiki helpers ────────────────────────────────────────────────────


def _wiki_root() -> str:
    return settings.wiki_path


def _raw_dir() -> str:
    return os.path.join(_wiki_root(), "raw")


def _wiki_dir() -> str:
    return os.path.join(_wiki_root(), "wiki")


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Extract YAML frontmatter dict and body from markdown content."""
    fm: dict = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
            for line in parts[1].strip().split("\n"):
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    key, val = key.strip(), val.strip()
                    if val.startswith("[") and val.endswith("]"):
                        fm[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
                    else:
                        fm[key] = val.strip("\"'")
    return fm, body


def _extract_wikilinks(text: str) -> list[str]:
    """Extract [[page-name]] links from markdown text."""
    return re.findall(r"\[\[([^\]]+)\]\]", text)


def _find_backlinks(page_name: str) -> list[str]:
    """Find all wiki pages that link to the given page."""
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return []
    backlinks = []
    target = page_name.removesuffix(".md")
    for fp in Path(wiki).glob("*.md"):
        stem = fp.stem
        if stem == target:
            continue
        try:
            text = fp.read_text(encoding="utf-8")
            if f"[[{target}]]" in text:
                backlinks.append(stem)
        except OSError:
            continue
    return sorted(backlinks)


# ── Raw file endpoints ──────────────────────────────────────────────


@router.get("/knowledge/raw", response_model=list[RawFileOut])
async def list_raw_files(_=Depends(get_current_user)):
    """List all raw source files with compile status."""
    raw = _raw_dir()
    if not os.path.isdir(raw):
        return []

    wiki_dir = _wiki_dir()
    wiki_stems: set[str] = set()
    wiki_stem_to_file: dict[str, str] = {}
    if os.path.isdir(wiki_dir):
        for fp in Path(wiki_dir).glob("*.md"):
            wiki_stems.add(fp.stem)
            wiki_stem_to_file[fp.stem] = fp.name

    result = []
    for entry in sorted(Path(raw).iterdir(), key=lambda e: e.name):
        if not entry.is_file() or not entry.suffix == ".md":
            continue
        stat = entry.stat()
        content = entry.read_text(encoding="utf-8")
        sha256 = ""
        ingested = False
        if content.startswith("---"):
            fm, _ = _parse_frontmatter(content)
            sha256 = fm.get("sha256", "")
            ingested = "ingested" in fm or "sha256" in fm

        # Count wiki pages derived from this raw file
        src_ref = f"raw/{entry.name}"
        wiki_count = 0
        for fp in Path(wiki_dir).glob("*.md") if os.path.isdir(wiki_dir) else []:
            try:
                text = fp.read_text(encoding="utf-8")
                if src_ref in text:
                    wiki_count += 1
            except OSError:
                continue

        result.append(RawFileOut(
            filename=entry.name,
            sha256=sha256,
            ingested=ingested,
            size=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            compiled=wiki_count > 0,
            wiki_pages_count=wiki_count,
        ))
    return result


@router.post("/knowledge/raw/compile", response_model=CompileResult)
async def compile_raw_file(body: CompileRequest, _=Depends(get_current_user)):
    """Trigger llm-wiki summarization for a single raw source file."""
    from src.services.kb_monitor import kb_monitor

    raw = _raw_dir()
    full_path = os.path.join(raw, body.filepath) if not os.path.isabs(body.filepath) else body.filepath
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"Raw file not found: {body.filepath}")

    result = await kb_monitor.process_document(full_path)
    return CompileResult(
        ok=result.status == "processed",
        filepath=body.filepath,
        wiki_pages_created=len(result.wiki_pages_updated),
        error=result.message if result.status == "error" else "",
    )


@router.get("/knowledge/raw/{filename:path}")
async def get_raw_file(filename: str, _=Depends(get_current_user)):
    """Read a single raw source file by filename."""
    raw = _raw_dir()
    full_path = os.path.join(raw, filename)
    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Raw file not found")
    if not full_path.startswith(os.path.realpath(raw)):
        raise HTTPException(status_code=403, detail="Path traversal denied")
    content = Path(full_path).read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(content)
    return {
        "filename": os.path.basename(full_path),
        "content": body,
        "frontmatter": fm,
        "size": os.path.getsize(full_path),
    }


# ── Wiki endpoints ──────────────────────────────────────────────────


@router.get("/knowledge/wiki", response_model=WikiTreeNode)
async def list_wiki_tree(_=Depends(get_current_user)):
    """Return wiki pages organized as a tree grouped by type."""
    wiki = _wiki_dir()
    groups: dict[str, list[dict]] = {}
    type_labels = {
        "entity": "Entities",
        "concept": "Concepts",
        "comparison": "Comparisons",
        "query": "Queries",
        "summary": "Summaries",
    }

    if not os.path.isdir(wiki):
        return WikiTreeNode(name="wiki", title="Wiki", path="wiki", type="directory", children=[])

    for fp in sorted(Path(wiki).glob("*.md")):
        try:
            text = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, _ = _parse_frontmatter(text)
        ptype = fm.get("type", "concept")
        if ptype not in groups:
            groups[ptype] = []
        groups[ptype].append({
            "name": fp.stem,
            "title": fm.get("title", fp.stem),
            "path": f"wiki/{fp.name}",
            "size": fp.stat().st_size,
        })

    children = []
    total = 0
    for ptype in ["entity", "concept", "comparison", "summary", "query"]:
        pages = groups.get(ptype, [])
        if not pages:
            continue
        total += len(pages)
        children.append(WikiTreeNode(
            name=ptype,
            title=type_labels.get(ptype, ptype),
            path=f"wiki/{ptype}",
            type="directory",
            count=len(pages),
            children=[
                WikiTreeNode(name=p["name"], title=p["title"], path=p["path"], type="page")
                for p in pages
            ],
        ))

    # Any unclassified types
    for ptype, pages in groups.items():
        if ptype in ["entity", "concept", "comparison", "summary", "query"]:
            continue
        total += len(pages)
        children.append(WikiTreeNode(
            name=ptype,
            title=ptype,
            path=f"wiki/{ptype}",
            type="directory",
            count=len(pages),
            children=[
                WikiTreeNode(name=p["name"], title=p["title"], path=p["path"], type="page")
                for p in pages
            ],
        ))

    return WikiTreeNode(
        name="wiki",
        title="Wiki",
        path="wiki",
        type="directory",
        count=total,
        children=children,
    )


@router.get("/knowledge/wiki/search", response_model=WikiSearchResponse)
async def search_wiki_pages(q: str = Query(..., min_length=1), max_results: int = 20, _=Depends(get_current_user)):
    """Full-text search across wiki page content."""
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        return WikiSearchResponse(results=[], total=0)

    try:
        result = subprocess.run(
            ["grep", "-r", "-n", "-i", "--include=*.md", q, wiki],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return WikiSearchResponse(results=[], total=0)

    lines = result.stdout.strip().splitlines()[:max_results * 2]
    hits: list[WikiSearchHit] = []
    for line in lines:
        if ":" not in line:
            continue
        filepath, lineno, *rest = line.split(":", 2)
        snippet = rest[0].strip()[:200] if rest else ""
        name = Path(filepath).stem
        # Get title from frontmatter on first occurrence
        title = name
        if not any(h.name == name for h in hits):
            try:
                text = Path(filepath).read_text(encoding="utf-8")
                fm, _ = _parse_frontmatter(text)
                title = fm.get("title", name)
            except OSError:
                pass
        hits.append(WikiSearchHit(name=name, title=title, snippet=snippet))

    return WikiSearchResponse(results=hits, total=len(hits))


@router.get("/knowledge/wiki/{page_name:path}", response_model=WikiPageOut)
async def get_wiki_page(page_name: str, _=Depends(get_current_user)):
    """Read a wiki page by name (with or without .md extension)."""
    wiki = _wiki_dir()
    if not os.path.isdir(wiki):
        raise HTTPException(status_code=404, detail="Wiki directory not found")

    clean = page_name.removesuffix(".md")
    filepath = os.path.join(wiki, f"{clean}.md")
    if not os.path.isfile(filepath):
        # Try fuzzy match
        candidates = list(Path(wiki).glob(f"{clean}*.md"))
        if len(candidates) == 1:
            filepath = str(candidates[0])
        else:
            raise HTTPException(status_code=404, detail=f"Wiki page not found: {clean}")

    if not os.path.realpath(filepath).startswith(os.path.realpath(wiki)):
        raise HTTPException(status_code=403, detail="Path traversal denied")

    content = Path(filepath).read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(content)
    name = Path(filepath).stem
    links_to = list(dict.fromkeys(_extract_wikilinks(body)))
    linked_from = _find_backlinks(name)

    return WikiPageOut(
        name=name,
        title=fm.get("title", name),
        content=body,
        type=fm.get("type", ""),
        tags=fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
        sources=fm.get("sources", []) if isinstance(fm.get("sources"), list) else [],
        created=fm.get("created", ""),
        updated=fm.get("updated", ""),
        links_to=links_to,
        linked_from=linked_from,
        word_count=len(body.split()) if body else 0,
        size=os.path.getsize(filepath),
    )


# ── Lint endpoints ──────────────────────────────────────────────────


@router.post("/knowledge/lint", response_model=LintReport)
async def run_lint_check(_=Depends(get_current_user)):
    """Run all lint checks and return a health report."""
    from src.services.kb_lint import run_lint
    return run_lint()


@router.post("/knowledge/lint/fix/{issue_id:path}", response_model=LintFixResponse)
async def fix_lint_issue(issue_id: str, page: str = "", _=Depends(get_current_user)):
    """Attempt to auto-fix a specific lint issue by check_id and optional page."""
    from src.services.kb_lint import fix_lint_issue as do_fix
    result = do_fix(issue_id, page)
    return LintFixResponse(**result)


@router.post("/knowledge/lint/fix-all")
async def fix_all_lint_issues(_=Depends(get_current_user)):
    """Run auto-fix on all fixable lint issues and return summary."""
    from src.services.kb_lint import fix_lint_issue as do_fix, run_lint
    report = run_lint()
    fixed = 0
    failed = 0
    details: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for issue in report.issues:
        key = (issue.check_id, issue.page)
        if key in seen:
            continue
        seen.add(key)
        result = do_fix(issue.check_id, issue.page)
        details.append(result)
        if result["ok"]:
            fixed += 1
        else:
            failed += 1
    # Re-run lint after fixes
    new_report = run_lint()
    return {
        "ok": True,
        "fixed": fixed,
        "failed": failed,
        "details": details,
        "health_before": report.health_score,
        "health_after": new_report.health_score,
        "issues_before": report.total_issues,
        "issues_after": new_report.total_issues,
    }


# ── Alert → Knowledge bridge ───────────────────────────────────────


@router.post("/knowledge/from-alert/{alert_id}")
async def create_knowledge_from_alert(alert_id: str, _=Depends(get_current_user)):
    """Extract knowledge from a confirmed alert and create a wiki page."""
    from src.services.kb_alert_bridge import extract_alert_knowledge

    result = await extract_alert_knowledge(alert_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    return result
