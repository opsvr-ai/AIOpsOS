"""Knowledge base CRUD, search, file upload, image upload, and reindex endpoints."""

import hashlib
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select, text

from src.api.deps import get_current_user
from src.config import settings
from src.models.base import async_session_factory
from src.models.knowledge import KnowledgeChunk
from src.schemas.knowledge import (
    ImageUploadResponse,
    KnowledgeDocumentCreate,
    KnowledgeDocumentOut,
    KnowledgeReindexResponse,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
    KnowledgeUploadResponse,
)
from src.services.knowledge_base import knowledge_base
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
    source: str | None = None,
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
async def list_documents(_=Depends(get_current_user)):
    docs = await knowledge_base.list_documents()
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
