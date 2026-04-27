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
