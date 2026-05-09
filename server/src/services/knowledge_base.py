"""Knowledge base service with LLM-WIKI retrieval pipeline.

Pipeline: query rewriting → hybrid search → reranking → context assembly
"""

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from typing import BinaryIO

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func, or_, select, text

from src.models.base import async_session_factory
from src.models.knowledge import KnowledgeChunk, KnowledgeDocument


@dataclass
class SearchResult:
    content: str
    score: float
    chunk_index: int
    document_id: str
    title: str
    source: str | None = None


class KnowledgeBaseService:
    """End-to-end knowledge retrieval with LLM-WIKI logic."""

    def __init__(self) -> None:
        self._embeddings_available: bool = False

    def _get_embeddings(self) -> None:
        """Embeddings are disabled — returns None."""
        return None

    # ── public API ─────────────────────────────────────────────────

    async def retrieve(
        self, query: str, top_k: int = 5, source: str | None = None, space_id: str | None = None
    ) -> list[SearchResult]:
        """End-to-end LLM-WIKI retrieval: rewrite → hybrid search → rerank.

        Results are cached in Redis (300s TTL) to avoid expensive re-computation
        of query rewriting + embedding + vector search + reranking.
        """
        from hashlib import sha256

        from src.core.redis import cache_get, cache_set

        cache_key = (
            f"kb:retrieve:{sha256(query.encode()).hexdigest()[:16]}"
            f":{top_k}:{source or '__all__'}:{space_id or '__none__'}"
        )
        try:
            cached = await cache_get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            return [SearchResult(**r) for r in cached]

        rewritten = await self._rewrite_queries(query)
        queries = [query] + rewritten
        raw = await self._hybrid_search(queries, top_k * 3, source, space_id)
        reranked = self._rerank(raw, top_k)

        try:
            serializable = [
                {
                    "content": r.content, "score": r.score,
                    "chunk_index": r.chunk_index, "document_id": r.document_id,
                    "title": r.title, "source": r.source,
                }
                for r in reranked
            ]
            await cache_set(cache_key, serializable, ttl=300)
        except Exception:
            pass
        return reranked

    async def retrieve_context(
        self, query: str, top_k: int = 5, source: str | None = None, space_id: str | None = None
    ) -> str:
        """Retrieve and format context for LLM injection."""
        results = await self.retrieve(query, top_k, source=source, space_id=space_id)
        return self._format_context(results)

    async def add_document(
        self,
        title: str,
        content: str,
        source: str | None = None,
        metadata: dict | None = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> KnowledgeDocument:
        """Add a document: chunk, embed, and store (SHA256 dedup)."""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        async with async_session_factory() as db:
            existing = await db.scalar(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.content_hash == content_hash
                )
            )
            if existing is not None:
                result = await db.execute(
                    select(KnowledgeDocument).where(KnowledgeDocument.id == existing)
                )
                return result.scalar_one()

            chunks = self._chunk_text(content, chunk_size, chunk_overlap)
            doc = KnowledgeDocument(
                title=title,
                content=content,
                source=source,
                doc_metadata=metadata or {},
                chunk_count=len(chunks),
                content_hash=content_hash,
            )
            db.add(doc)
            await db.flush()

            embedder = self._get_embeddings()
            if embedder:
                try:
                    embeddings = await embedder.aembed_documents(chunks)
                except Exception:
                    logger.warning("Embedding failed, storing chunks without vectors")
                    embeddings = [None] * len(chunks)
            else:
                embeddings = [None] * len(chunks)

            for i, chunk_text in enumerate(chunks):
                emb = embeddings[i] if embeddings[i] is not None else None
                db.add(
                    KnowledgeChunk(
                        document_id=doc.id,
                        content=chunk_text,
                        embedding=emb,
                        chunk_index=i,
                        chunk_metadata={
                            "title": title,
                            "source": source,
                        }
                        if source
                        else {"title": title},
                    )
                )
            await db.commit()
            await db.refresh(doc)
            await self._invalidate_cache()
            return doc

    async def add_document_from_file(
        self,
        file: BinaryIO,
        filename: str,
        source: str | None = None,
        metadata: dict | None = None,
        convert_to_markdown: bool = False,
    ) -> KnowledgeDocument:
        """Parse an uploaded file and add it as a document.

        If convert_to_markdown is True, uses MarkItDown to convert the
        file content to Markdown before storing.
        """
        if convert_to_markdown:
            content = self._convert_with_markitdown(file, filename)
        else:
            content = self._parse_document(file, filename)
        title = os.path.splitext(os.path.basename(filename))[0]
        return await self.add_document(
            title=title,
            content=content,
            source=source or filename,
            metadata=metadata,
        )

    @staticmethod
    def _convert_with_markitdown(file: BinaryIO, filename: str) -> str:
        """Convert a file to Markdown using MarkItDown."""
        import io as _io

        file.seek(0)
        raw_bytes = file.read()

        from markitdown import MarkItDown as _MarkItDown

        md_converter = _MarkItDown()
        ext = os.path.splitext(filename)[1].lower()

        # docx/pptx/xlsx/html/pdf — convert via MarkItDown with BytesIO wrapper
        if ext in (".docx", ".pptx", ".xlsx", ".html", ".htm", ".pdf"):
            result = md_converter.convert(_io.BytesIO(raw_bytes))
            return result.text_content

        # txt/md — return as-is
        return raw_bytes.decode("utf-8", errors="replace")

    async def reindex_all(self, chunk_size: int = 500, chunk_overlap: int = 50) -> tuple[int, int]:
        """Re-chunk and re-embed all documents. Returns (doc_count, chunk_count)."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(KnowledgeDocument).order_by(KnowledgeDocument.created_at)
            )
            docs = list(result.scalars().all())

        total_chunks = 0
        for doc in docs:
            new_chunks = self._chunk_text(doc.content, chunk_size, chunk_overlap)
            if not new_chunks:
                continue

            async with async_session_factory() as db:
                await db.execute(
                    text(
                        "DELETE FROM knowledge_chunks WHERE document_id = :did"
                    )
                )
                await db.flush()

                embedder = self._get_embeddings()
                if embedder:
                    try:
                        embeddings = await embedder.aembed_documents(new_chunks)
                    except Exception:
                        embeddings = [None] * len(new_chunks)
                else:
                    embeddings = [None] * len(new_chunks)
                for i, chunk_text in enumerate(new_chunks):
                    db.add(
                        KnowledgeChunk(
                            document_id=doc.id,
                            content=chunk_text,
                            embedding=embeddings[i] if embeddings[i] is not None else None,
                            chunk_index=i,
                            chunk_metadata={"title": doc.title, "source": doc.source},
                        )
                    )
                doc.chunk_count = len(new_chunks)
                await db.commit()

            total_chunks += len(new_chunks)

        await self._invalidate_cache()
        return len(docs), total_chunks

    async def delete_document(self, document_id: str) -> bool:
        """Delete a document and its chunks."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc is None:
                return False
            await db.delete(doc)
            await db.commit()
            await self._invalidate_cache()
            return True

    async def list_documents(self, space_id: str | None = None) -> list[KnowledgeDocument]:
        """List all documents, optionally filtered by space."""
        async with async_session_factory() as db:
            query = select(KnowledgeDocument)
            if space_id:
                query = query.where(or_(KnowledgeDocument.space_id == space_id, KnowledgeDocument.space_id.is_(None)))
            result = await db.execute(query.order_by(KnowledgeDocument.created_at.desc()))
            return list(result.scalars().all())

    async def get_document(self, document_id: str) -> KnowledgeDocument | None:
        """Get a single document by ID."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
            )
            return result.scalar_one_or_none()

    # ── LLM-WIKI pipeline ──────────────────────────────────────────

    async def _rewrite_queries(self, user_query: str) -> list[str]:
        """Generate 2-4 search queries from the user's original query."""
        from src.core.model_factory import get_default_model
        llm = await get_default_model()
        system = (
            "You are a search query rewriter. Given a user question, generate 2-4 "
            "distinct search queries that would help find relevant information in a "
            "knowledge base. Each query should approach the topic from a different angle. "
            "Return ONLY a JSON array of strings, no other text."
        )
        resp = await llm.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=f"Original query: {user_query}"),
        ])
        return self._parse_json_list(resp.content)

    async def _hybrid_search(
        self, queries: list[str], top_k: int = 15, source: str | None = None, space_id: str | None = None
    ) -> list[SearchResult]:
        """Run keyword + vector search for each query and merge results."""
        results: list[SearchResult] = []
        for query in queries:
            keyword_results = await self._keyword_search(query, top_k, source, space_id)
            results.extend(keyword_results)
            if self._embeddings_available:
                try:
                    vector_results = await self._vector_search(query, top_k, source, space_id)
                    results.extend(vector_results)
                except Exception:
                    logger.warning("Vector search failed, disabling embeddings")
                    self._embeddings_available = False
        return results

    async def _vector_search(
        self, query: str, top_k: int = 10, source: str | None = None, space_id: str | None = None
    ) -> list[SearchResult]:
        """Search by vector similarity with title boost."""
        try:
            emb = await asyncio.wait_for(
                self._get_embeddings().aembed_query(query), timeout=15
            )
            title_emb = await asyncio.wait_for(
                self._get_embeddings().aembed_query(f"title: {query}"), timeout=15
            )
        except (TimeoutError, Exception):
            logger.warning("Embedding API unreachable, disabling vector search")
            self._embeddings_available = False
            return []

        source_clause = ""
        params: dict = {
            "query_emb": str(emb),
            "title_emb": str(title_emb),
            "top_k": top_k,
            "threshold": 0.5,
        }
        if source:
            source_clause += "AND d.source = :source_filter"
            params["source_filter"] = source
        if space_id:
            source_clause += "AND d.space_id = :space_id"
            params["space_id"] = space_id

        async with async_session_factory() as db:
            stmt = text(
                f"""
                SELECT c.content, c.chunk_index,
                       GREATEST(
                           1 - (c.embedding <=> :query_emb),
                           1 - (c.embedding <=> :title_emb) * 0.7
                       ) AS score,
                       d.id AS doc_id, d.title, d.source
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                  AND GREATEST(
                       1 - (c.embedding <=> :query_emb),
                       1 - (c.embedding <=> :title_emb) * 0.7
                  ) > :threshold
                  {source_clause}
                ORDER BY score DESC
                LIMIT :top_k
                """
            )
            rows = await db.execute(stmt, params)
            return [
                SearchResult(
                    content=r.content,
                    chunk_index=r.chunk_index,
                    score=float(r.score),
                    document_id=str(r.doc_id),
                    title=r.title,
                    source=r.source,
                )
                for r in rows.fetchall()
            ]

    async def _keyword_search(
        self, query: str, top_k: int = 10, source: str | None = None, space_id: str | None = None
    ) -> list[SearchResult]:
        """Full-text keyword search with Chinese tokenisation and title bonus."""
        tsquery_str = self._tokenise(query)
        source_clause = ""
        params: dict = {"tsquery": tsquery_str, "top_k": top_k}
        if space_id:
            source_clause += "AND d.space_id = :space_id"
            params["space_id"] = space_id
        if source:
            source_clause += "AND d.source = :source_filter"
            params["source_filter"] = source
        if space_id:
            source_clause += "AND d.space_id = :space_id"
            params["space_id"] = space_id

        async with async_session_factory() as db:
            stmt = text(
                f"""
                SELECT c.content, c.chunk_index,
                       (ts_rank(to_tsvector('simple', c.content),
                                plainto_tsquery('simple', :tsquery)) +
                        CASE WHEN d.title ILIKE '%' || :title_query || '%'
                             THEN 0.5 ELSE 0 END
                       ) AS score,
                       d.id AS doc_id, d.title, d.source
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE to_tsvector('simple', c.content) @@
                      plainto_tsquery('simple', :tsquery)
                   OR d.title ILIKE '%' || :title_query || '%'
                  {source_clause}
                ORDER BY score DESC
                LIMIT :top_k
                """
            )
            params["title_query"] = query
            rows = await db.execute(stmt, params)
            return [
                SearchResult(
                    content=r.content,
                    chunk_index=r.chunk_index,
                    score=float(r.score),
                    document_id=str(r.doc_id),
                    title=r.title,
                    source=r.source,
                )
                for r in rows.fetchall()
            ]

    @staticmethod
    def _rerank(results: list[SearchResult], top_k: int = 5) -> list[SearchResult]:
        """Score-based fusion: deduplicate by document_id+chunk_index, keep max score."""
        seen: dict[tuple[str, int], SearchResult] = {}
        for r in results:
            key = (r.document_id, r.chunk_index)
            if key in seen:
                seen[key].score = max(seen[key].score, r.score)
            else:
                seen[key] = r
        ranked = sorted(seen.values(), key=lambda x: x.score, reverse=True)
        return ranked[:top_k]

    @staticmethod
    def _format_context(results: list[SearchResult]) -> str:
        """Assemble retrieved chunks into a readable context string."""
        parts: list[str] = []
        for i, r in enumerate(results, 1):
            header = f"[{i}] {r.title}"
            if r.source:
                header += f" (source: {r.source})"
            parts.append(f"{header}\n{r.content}\n")
        return "\n---\n".join(parts)

    # ── Chinese tokenisation ───────────────────────────────────────

    @staticmethod
    def _tokenise(text_str: str) -> str:
        """Tokenise Chinese text with jieba and return &-separated tsquery."""
        import jieba as _jieba

        words = _jieba.lcut(text_str)
        tokens = [w.strip() for w in words if w.strip() and len(w.strip()) > 1]
        return " & ".join(tokens) if tokens else text_str

    # ── File parsing ───────────────────────────────────────────────

    @staticmethod
    def _parse_document(file: BinaryIO, filename: str) -> str:
        """Parse an uploaded file into plain text by extension."""
        file.seek(0)
        ext = os.path.splitext(filename)[1].lower()

        if ext == ".txt":
            raw = file.read()
            return raw.decode("utf-8", errors="replace")

        if ext == ".md":
            raw = file.read()
            return raw.decode("utf-8", errors="replace")

        if ext == ".pdf":
            from pypdf import PdfReader as _PdfReader

            reader = _PdfReader(file)
            parts: list[str] = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
            return "\n\n".join(parts)

        raise ValueError(f"Unsupported file type: {ext}")

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _parse_json_list(text: str) -> list[str]:
        """Parse a JSON array of strings from LLM output."""
        import json as _json

        try:
            clean = text.strip()
            if "```" in clean:
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            parsed = _json.loads(clean)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        except (_json.JSONDecodeError, IndexError):
            pass
        return []

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
        """Sentence-aware text chunking on Chinese/English punctuation."""
        import re as _re

        sentences = _re.split(r"(?<=[。！？.!?\n])\s*", text)
        sentences = [s.strip() for s in sentences if s.strip()]
        chunks: list[str] = []
        current = ""
        for s in sentences:
            if len(current) + len(s) > chunk_size and current:
                chunks.append(current.strip())
                overlap_text = current[-overlap:] if overlap < len(current) else current
                current = overlap_text + s
            else:
                current += s
        if current.strip():
            chunks.append(current.strip())
        return chunks or [text.strip()]


    # ── LLM-Wiki enhanced features ──────────────────────────────────

    async def generate_index(self) -> str:
        """Generate a wiki-style index of all documents (like LLM-Wiki index.md)."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(KnowledgeDocument).order_by(KnowledgeDocument.created_at)
            )
            docs = list(result.scalars())

        if not docs:
            return "# Knowledge Base Index\n\n*(empty)*"

        lines = ["# Knowledge Base Index\n"]

        # Group by source type
        by_source: dict[str, list[KnowledgeDocument]] = {}
        for d in docs:
            src = d.source or "manual"
            by_source.setdefault(src, []).append(d)

        for source, group in sorted(by_source.items()):
            lines.append(f"\n## {source}\n")
            for d in group:
                snippet = d.content[:80].replace("\n", " ")
                lines.append(
                    f"- **{d.title}** [{d.chunk_count} chunks] "
                    f"— {snippet}..."
                )

        # Stats section
        lines.append("\n## Stats\n")
        lines.append(f"- Total documents: {len(docs)}")
        lines.append(f"- Total chunks: {sum(d.chunk_count for d in docs)}")
        lines.append(f"- Sources: {len(by_source)}")

        return "\n".join(lines)

    async def get_stats(self) -> dict:
        """Return summary statistics about the knowledge base."""
        async with async_session_factory() as db:
            total_docs = await db.scalar(select(func.count(KnowledgeDocument.id)))
            total_chunks = await db.scalar(
                text("SELECT COALESCE(SUM(chunk_count), 0) FROM knowledge_documents")
            )
            source_result = await db.execute(
                select(KnowledgeDocument.source, func.count(KnowledgeDocument.id).label("cnt"))
                .group_by(KnowledgeDocument.source)
                .order_by(text("cnt DESC"))
            )
            sources = {row.source or "manual": row.cnt for row in source_result.fetchall()}
            recent = await db.execute(
                select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()).limit(5)
            )
            recent_docs = [
                {"id": str(d.id), "title": d.title, "created_at": d.created_at.isoformat()}
                for d in recent.scalars()
            ]

        return {
            "total_documents": total_docs or 0,
            "total_chunks": total_chunks or 0,
            "sources": sources,
            "recent_documents": recent_docs,
        }

    async def find_related(self, document_id: str, top_k: int = 5) -> list[SearchResult]:
        """Find documents related to a given document by searching its title."""
        doc = await self.get_document(document_id)
        if doc is None:
            return []
        return await self.retrieve(doc.title, top_k=top_k)

    # ── cache ────────────────────────────────────────────────────────

    async def _invalidate_cache(self) -> None:
        """Invalidate all KB retrieval caches after document mutation."""
        try:
            from src.core.redis import cache_delete_pattern
            await cache_delete_pattern("kb:retrieve:*")
        except Exception:
            pass

    # ── LLM-Wiki file storage ─────────────────────────────────────────

    @property
    def _kb_dir(self) -> str:
        from src.config import settings as _s
        return _s.wiki_path

    def _ensure_wiki_dirs(self) -> None:
        """Create the wiki directory structure on disk."""
        import os as _os
        base = self._kb_dir
        for sub in ("wiki", "raw", "meta"):
            _os.makedirs(_os.path.join(base, sub), exist_ok=True)

    def _safe_slug(self, title: str) -> str:
        """Turn a document title into a safe filename slug."""
        import re as _re
        s = title.lower().replace(" ", "-")
        s = _re.sub(r"[^a-z0-9一-鿿_-]", "", s)
        return s[:80]

    async def export_to_files(self, doc: KnowledgeDocument | None = None) -> str | None:
        """Export a document as a markdown wiki page + raw source file.

        Returns the wiki page file path, or None on failure.
        """
        import os as _os
        self._ensure_wiki_dirs()
        if doc is None:
            return None

        slug = self._safe_slug(doc.title)
        base = self._kb_dir

        # Raw source (immutable copy)
        if doc.source:
            raw_dir = _os.path.join(base, "raw")
            raw_path = _os.path.join(raw_dir, f"{slug}.md")
            try:
                with open(raw_path, "w", encoding="utf-8") as f:
                    f.write(doc.content)
            except OSError:
                pass

        # Wiki page with metadata header
        wiki_content = [
            f"# {doc.title}",
            "",
            f"> Source: {doc.source or 'manual'}",
            f"> Created: {doc.created_at.isoformat() if doc.created_at else 'unknown'}",
            f"> Chunks: {doc.chunk_count}",
            "---",
            "",
            doc.content,
        ]
        wiki_path = _os.path.join(base, "wiki", f"{slug}.md")
        with open(wiki_path, "w", encoding="utf-8") as f:
            f.write("\n".join(wiki_content))

        # Update index
        return wiki_path

    async def export_all_to_files(self) -> int:
        """Export all DB documents to wiki/raw files. Returns count."""
        from src.models.knowledge import KnowledgeDocument as _KD
        async with async_session_factory() as db:
            result = await db.execute(select(_KD).order_by(_KD.created_at))
            docs = list(result.scalars().all())
        count = 0
        for d in docs:
            if await self.export_to_files(d):
                count += 1
        if count:
            await self._update_index_file()
        return count

    async def _update_index_file(self) -> None:
        """Write/update the wiki index.md from the DB."""
        self._ensure_wiki_dirs()
        index_content = await self.generate_index()
        path = os.path.join(self._kb_dir, "index.md")  # noqa: F821
        with open(path, "w", encoding="utf-8") as f:
            f.write(index_content)

    def list_wiki_files(self) -> list[str]:
        """Return paths of all wiki markdown files."""
        import os as _os
        wiki_dir = _os.path.join(self._kb_dir, "wiki")
        if not _os.path.isdir(wiki_dir):
            return []
        return sorted(
            _os.path.join(wiki_dir, fn)
            for fn in _os.listdir(wiki_dir)
            if fn.endswith(".md")
        )

    async def sync_file_to_document(self, filepath: str) -> KnowledgeDocument | None:
        """Read a wiki markdown file from disk and sync it into the DB.

        Creates or updates the corresponding KnowledgeDocument and re-chunks.
        Returns the document or None on failure.
        """
        import os as _os
        if not _os.path.isfile(filepath):
            return None

        with open(filepath, encoding="utf-8") as f:
            content = f.read()

        stem = _os.path.splitext(_os.path.basename(filepath))[0]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        async with async_session_factory() as db:
            existing = await db.scalar(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.source == filepath
                )
            )
            if existing:
                doc_result = await db.execute(
                    select(KnowledgeDocument).where(KnowledgeDocument.id == existing)
                )
                doc = doc_result.scalar_one()
                doc.title = stem
                doc.content = content
                doc.content_hash = content_hash
                doc.chunk_count = 0
                await db.flush()
                await db.execute(
                    text("DELETE FROM knowledge_chunks WHERE document_id = :did"),
                    {"did": doc.id},
                )
            else:
                doc = KnowledgeDocument(
                    title=stem,
                    content=content,
                    source=filepath,
                    content_hash=content_hash,
                    chunk_count=0,
                )
                db.add(doc)
                await db.flush()

            chunks = self._chunk_text(content)
            doc.chunk_count = len(chunks)
            embedder = self._get_embeddings()
            if embedder:
                try:
                    embeddings = await embedder.aembed_documents(chunks)
                except Exception:
                    embeddings = [None] * len(chunks)
            else:
                embeddings = [None] * len(chunks)

            for i, chunk_text in enumerate(chunks):
                db.add(KnowledgeChunk(
                    document_id=doc.id,
                    content=chunk_text,
                    embedding=embeddings[i] if embeddings[i] is not None else None,
                    chunk_index=i,
                    chunk_metadata={"title": stem, "source": filepath},
                ))

            await db.commit()
            await db.refresh(doc)
            return doc


logger = logging.getLogger(__name__)

knowledge_base = KnowledgeBaseService()
