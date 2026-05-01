from datetime import datetime

from pydantic import BaseModel


class KnowledgeDocumentCreate(BaseModel):
    title: str
    content: str
    source: str | None = None
    metadata: dict = {}


class KnowledgeDocumentOut(BaseModel):
    id: str
    title: str
    content: str
    source: str | None = None
    chunk_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class KnowledgeSearchResult(BaseModel):
    content: str
    score: float
    chunk_index: int
    document_id: str
    title: str
    source: str | None = None


class KnowledgeSearchResponse(BaseModel):
    results: list[KnowledgeSearchResult]
    context: str


class ImageUploadResponse(BaseModel):
    url: str
    filename: str


class KnowledgeUploadResponse(BaseModel):
    id: str
    title: str
    source: str | None = None
    chunk_count: int
    file_name: str
    created_at: datetime | None = None


class KnowledgeReindexResponse(BaseModel):
    documents_processed: int
    chunks_created: int


# ── Raw file schemas ────────────────────────────────────────────────

class RawFileOut(BaseModel):
    filename: str
    sha256: str = ""
    ingested: bool = False
    size: int = 0
    last_modified: str = ""
    compiled: bool = False
    wiki_pages_count: int = 0


class CompileRequest(BaseModel):
    filepath: str


class CompileResult(BaseModel):
    ok: bool
    filepath: str
    wiki_pages_created: int = 0
    error: str = ""


# ── Wiki schemas ────────────────────────────────────────────────────

class WikiTreeNode(BaseModel):
    name: str
    title: str
    path: str
    type: str = "page"  # "directory" | "page"
    count: int = 0
    children: list["WikiTreeNode"] = []


class WikiPageOut(BaseModel):
    name: str
    title: str
    content: str
    type: str = ""
    tags: list[str] = []
    sources: list[str] = []
    created: str = ""
    updated: str = ""
    links_to: list[str] = []
    linked_from: list[str] = []
    word_count: int = 0
    size: int = 0


class WikiSearchHit(BaseModel):
    name: str
    title: str
    snippet: str
    score: float = 0.0


class WikiSearchResponse(BaseModel):
    results: list[WikiSearchHit]
    total: int = 0
