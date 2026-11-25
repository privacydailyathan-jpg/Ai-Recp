from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

KnowledgeDocumentStatus = Literal["pending", "processing", "completed", "failed"]


class KnowledgeDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_id: UUID
    original_filename: str
    document_type: str
    mime_type: str
    file_size_bytes: int
    source: str | None
    processing_status: KnowledgeDocumentStatus
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentPage(BaseModel):
    items: list[KnowledgeDocumentRead]
    total: int
    limit: int
    offset: int


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(max_length=4000)
    top_k: int = Field(default=5, ge=1, le=100)


class KnowledgeSearchChunk(BaseModel):
    chunk_id: UUID
    document_id: UUID
    business_id: UUID
    chunk_index: int
    chunk_text: str
    chunk_metadata: dict[str, object]
    document_filename: str
    document_source: str | None
    semantic_distance: float | None
    keyword_score: float | None
    semantic_rank: int | None
    keyword_rank: int | None
    rrf_score: float


class KnowledgeSearchDocumentReference(BaseModel):
    document_id: UUID
    document_filename: str
    document_source: str | None


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[KnowledgeSearchChunk]
    documents: list[KnowledgeSearchDocumentReference]
