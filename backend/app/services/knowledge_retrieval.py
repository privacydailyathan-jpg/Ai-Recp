from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.services.embedding import embed_text

DEFAULT_RRF_K = 60
MAX_RETRIEVAL_LIMIT = 100
MAX_QUERY_LENGTH = 4000


@dataclass(frozen=True)
class KnowledgeRetrievalResult:
    chunk_id: UUID
    document_id: UUID
    business_id: UUID
    chunk_index: int
    chunk_text: str
    chunk_metadata: dict[str, object]
    document_filename: str
    document_source: str | None
    semantic_distance: float | None = None
    keyword_score: float | None = None
    semantic_rank: int | None = None
    keyword_rank: int | None = None
    rrf_score: float = 0.0


def _validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise TypeError("Query must be a string")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"Query must not exceed {MAX_QUERY_LENGTH} characters")
    return query.strip()


def _validate_limit(limit: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_RETRIEVAL_LIMIT
    ):
        raise ValueError(f"Limit must be between 1 and {MAX_RETRIEVAL_LIMIT}")


def _validate_rrf_k(k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("RRF k must be a positive integer")


def _result(
    chunk: KnowledgeChunk,
    document: KnowledgeDocument,
    *,
    semantic_distance: float | None = None,
    keyword_score: float | None = None,
    semantic_rank: int | None = None,
    keyword_rank: int | None = None,
) -> KnowledgeRetrievalResult:
    return KnowledgeRetrievalResult(
        chunk_id=chunk.id,
        document_id=document.id,
        business_id=chunk.business_id,
        chunk_index=chunk.chunk_index,
        chunk_text=chunk.chunk_text,
        chunk_metadata=chunk.chunk_metadata,
        document_filename=document.original_filename,
        document_source=document.source,
        semantic_distance=semantic_distance,
        keyword_score=keyword_score,
        semantic_rank=semantic_rank,
        keyword_rank=keyword_rank,
    )


def retrieve_semantic(
    db: Session,
    *,
    business_id: UUID,
    query: str,
    limit: int = 5,
) -> list[KnowledgeRetrievalResult]:
    _validate_limit(limit)
    normalized_query = _validate_query(query)
    if not normalized_query:
        return []

    query_embedding = embed_text(normalized_query)
    distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
    rows = db.execute(
        select(KnowledgeChunk, KnowledgeDocument, distance.label("distance"))
        .join(
            KnowledgeDocument,
            KnowledgeDocument.id == KnowledgeChunk.document_id,
        )
        .where(
            KnowledgeChunk.business_id == business_id,
            KnowledgeDocument.business_id == business_id,
            KnowledgeDocument.processing_status == "completed",
        )
        .order_by(distance.asc(), KnowledgeChunk.id.asc())
        .limit(limit)
    ).all()
    return [
        _result(
            chunk,
            document,
            semantic_distance=float(row_distance),
            semantic_rank=rank,
        )
        for rank, (chunk, document, row_distance) in enumerate(rows, start=1)
    ]


def retrieve_keyword(
    db: Session,
    *,
    business_id: UUID,
    query: str,
    limit: int = 5,
) -> list[KnowledgeRetrievalResult]:
    _validate_limit(limit)
    normalized_query = _validate_query(query)
    if not normalized_query:
        return []

    document_vector = func.to_tsvector("simple", KnowledgeChunk.chunk_text)
    query_vector = func.plainto_tsquery("simple", normalized_query)
    score = func.ts_rank_cd(document_vector, query_vector)
    rows = db.execute(
        select(KnowledgeChunk, KnowledgeDocument, score.label("score"))
        .join(
            KnowledgeDocument,
            KnowledgeDocument.id == KnowledgeChunk.document_id,
        )
        .where(
            KnowledgeChunk.business_id == business_id,
            KnowledgeDocument.business_id == business_id,
            KnowledgeDocument.processing_status == "completed",
            document_vector.bool_op("@@")(query_vector),
        )
        .order_by(score.desc(), KnowledgeChunk.id.asc())
        .limit(limit)
    ).all()
    return [
        _result(
            chunk,
            document,
            keyword_score=float(row_score),
            keyword_rank=rank,
        )
        for rank, (chunk, document, row_score) in enumerate(rows, start=1)
    ]


def reciprocal_rank_fusion(
    semantic_results: list[KnowledgeRetrievalResult],
    keyword_results: list[KnowledgeRetrievalResult],
    *,
    limit: int = 5,
    k: int = DEFAULT_RRF_K,
) -> list[KnowledgeRetrievalResult]:
    _validate_limit(limit)
    _validate_rrf_k(k)

    merged: dict[UUID, KnowledgeRetrievalResult] = {}
    scores: dict[UUID, float] = {}

    seen_semantic: set[UUID] = set()
    for rank, result in enumerate(semantic_results, start=1):
        if result.chunk_id in seen_semantic:
            continue
        seen_semantic.add(result.chunk_id)
        existing = merged.get(result.chunk_id)
        merged[result.chunk_id] = (
            replace(
                existing,
                semantic_distance=result.semantic_distance,
                semantic_rank=rank,
            )
            if existing is not None
            else replace(result, semantic_rank=rank)
        )
        scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1 / (k + rank)

    seen_keyword: set[UUID] = set()
    for rank, result in enumerate(keyword_results, start=1):
        if result.chunk_id in seen_keyword:
            continue
        seen_keyword.add(result.chunk_id)
        existing = merged.get(result.chunk_id)
        merged[result.chunk_id] = (
            replace(
                existing,
                keyword_score=result.keyword_score,
                keyword_rank=rank,
            )
            if existing is not None
            else replace(result, keyword_rank=rank)
        )
        scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1 / (k + rank)

    ranked = [
        replace(result, rrf_score=scores[chunk_id])
        for chunk_id, result in merged.items()
    ]
    ranked.sort(key=lambda result: (-result.rrf_score, str(result.chunk_id)))
    return ranked[:limit]


def retrieve_hybrid(
    db: Session,
    *,
    business_id: UUID,
    query: str,
    limit: int = 5,
    k: int = DEFAULT_RRF_K,
) -> list[KnowledgeRetrievalResult]:
    _validate_limit(limit)
    _validate_rrf_k(k)
    normalized_query = _validate_query(query)
    if not normalized_query:
        return []

    semantic_results = retrieve_semantic(
        db,
        business_id=business_id,
        query=normalized_query,
        limit=limit,
    )
    keyword_results = retrieve_keyword(
        db,
        business_id=business_id,
        query=normalized_query,
        limit=limit,
    )
    return reciprocal_rank_fusion(
        semantic_results,
        keyword_results,
        limit=limit,
        k=k,
    )
